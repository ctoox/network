# 多厂商网络模拟器（VRP5 + IOS）开源项目架构调研报告

> 目标：为 Python 事件驱动、双厂商（华为 VRP5 + 思科 IOS）、面向数千台设备的 Web 网络模拟器提供可落地的仿真**核心**架构建议。
> 调研对象：GNS3 / EVE-NG / VIRL / ns-3 / Mininet / FRRouting(Bird·Quagga) / Packet Tracer·eNSP / VPCS / netlab(netsim-tools) / Batfish / Ansible。
> 重点：**事件循环与虚拟时间、设备模型、报文模型、协议模型、CLI 与配置抽象、模块组织、规模化方案**。UI 只做简述。

---

## 0. 实施状态（2026-08 已落地）

> 核心引擎重构已完成并通过回归验证（双厂商混合 ping 5/5、Tab 补全、WebSocket 流式输出）。

| 模块 | 文件 | 状态 |
|---|---|---|
| 双模式 Scheduler（虚拟/实时） | `netsim/core/scheduler.py` | ✅ 已实现，`World(realtime=False)` 快进 |
| Link/Channel 抽象 | `netsim/net/world.py` `Link` + `Iface.link` | ✅ 已实现（P2P；多接入段留待 P5） |
| 前缀 Trie RIB + 管理距离 | `netsim/net/iface.py` `RouteTable` | ✅ 已实现，LPM + proto 分桶 + 距离/度量选路 |
| ARP dict 化 + iface→device 反向索引 | `netsim/net/iface.py` / `world.py` | ✅ 已实现 |
| 虚拟时间规模化 | `main.py --simulate [IP] --routers N` | ✅ 511 路由树 ping 5/5 仅 0ms |
| TTL 语义 | `world._forward` | ✅ 超 64 跳正确丢包（1000 跳链不可达是真实行为） |

未做（下一阶段）：协议模块化 `proto/`（OSPF/BGP）、CLI 解析引擎分离（parser/render）、配置模型（running/startup）、YAML 拓扑导入。

---

## 1. 各项目架构分析

| 决策点 | 推荐方案 | 参考来源 |
|---|---|---|
| 时间模型 | **双模式**：Realtime（asyncio + 墙钟）用于交互；Simulation（虚拟时间 + `heapq` 优先级队列）用于收敛研究与大拓扑压测 | Packet Tracer 双模式、ns-3、SimPy |
| 事件循环 | 自研轻量 `Scheduler`：`(time, seq, callback)` 堆，禁止在数据面依赖 `asyncio.sleep` | ns-3 `Simulator/Scheduler/Event` |
| 设备模型 | `Node(Device)` = 协议栈 + 接口(NetDevice) + 服务；`Channel` 负责传输与延迟；接口↔链路解耦 | ns-3、GNS3、Mininet |
| 报文模型 | 轻量 `Packet` + 只读转发 + 克隆（多播时），仿真元数据走"Tags/附件" | ns-3 `Buffer+Tags` |
| 协议模型 | **Zebra 模式**：单一 RIB（路由管理器）+ 各协议进程（static/ospf/bgp）通过统一接口增删路由 | FRRouting/Quagga |
| 路由表 | 前缀 Trie（最长前缀匹配），替代当前 O(n) 线性表；按 proto 分桶 + 管理距离选路 | IOS/VRP 语义、ns-3 思路 |
| CLI 抽象 | 厂商无关解析引擎（Trie + 前缀缩写 + `?` 帮助 + Tab 补全）＋ 厂商命令注册表 ＋ 厂商输出渲染器 | Cisco IOS / Huawei VRP 设计、netlab |
| 配置模型 | 结构化 running-config（有序命令流）＋ 可渲染/可回放的配置对象；`show running-config` / `display current-configuration` 由渲染器生成 | netlab、Batfish、ciscoconfparse |
| 多厂商模板 | 设备能力清单（YAML：接口命名/特性）+ 每模块每厂商 Jinja2 渲染（netlab 模式），配置意图与厂商语法分离 | netlab(netsim-tools) |
| 规模化 | 虚拟时间批量模式 + Trie 路由表 + O(1) ARP 字典 + 反向映射表 + 按需收敛 | ns-3、Mininet 4096 节点、CORE |
| 前端 | 维持 WebSocket 事件流（PDU/接口状态/RIB 变更），客户端画布动画；拓扑文件用 YAML 描述 | GNS3 REST/WS、netlab YAML |

> 上表为决策速览；落地状态见 §0。

---

## 1. 各项目架构分析

### 1.1 GNS3（gns3-server + gns3-gui）

**核心架构**
- 四层划分：**GUI**（Qt 客户端 gns3-gui / gns3-web）→ **Controller**（gns3-server）→ **Compute**（gns3-server 的一部分，可远程）→ **Emulators**（QEMU/Dynamips/VirtualBox/Docker/VPCS/IOU）。
- Controller 管理项目状态，通过 **HTTP + JSON REST API** 暴露给 GUI；通知用 **HTTP 流或 WebSocket** 推送。状态落 SQLite。
- 服务端为 **Python asyncio (aiohttp)**，节点级锁控制并发；同一 Compute 内每个节点对应一个仿真器进程；节点间包互连由 **uBridge** 通过 UDP 完成。
- 包结构（值得抄）：`gns3server/` 下分 `controller/{project,node,link,topology,template,appliance,udp_link}`、`compute/{base_node,base_manager,adapters/{ethernet_adapter,serial_adapter},builtin,docker,dynamips,iou,qemu,virtualbox,vmware,vpcs}`、`schemas`、`web`、`handlers`。

**建模要点**
- 节点 = `BaseNode` 子类 + 每类仿真器的 `Manager`（工厂）；`adapters` 抽象槽位/端口（以太网口、串口），`port_manager` 统一分配 UDP/console 端口——**槽位→端口→NIO 的抽象便于模块化设备**。
- 内置节点（hub/switch/cloud/NAT）也走同一抽象，与真实仿真器节点同构。
- **链路 = 虚拟连接**：数据面靠 uBridge 把两侧仿真器 NIO 桥接，链路支持挂起/恢复、抓包（smart capture）。

**可借鉴**
- Controller/Compute 分层：**控制面（拓扑/项目）与执行面（设备实例）分离**。
- REST + WebSocket 通知模型：事件从引擎异步推给前端。
- 接口/端口（adapter）抽象：为"可插卡设备"预留扩展点。

### 1.2 VIRL / EVE-NG

**核心架构**
- VIRL（Cisco）：拓扑以 **XML** 描述，节点/链路为一级对象，跑 IOSv/IOSvL2/IOS-XRv/CSR1000v 等镜像。
- EVE-NG（UnetLab 的 fork）：**Web UI（PHP）**，Lab 文件即描述节点、坐标、连接的 **XML**；节点 = VM 对象，后端三类：**Dynamips**（老 IOS）、**IOL/IOU**（Linux 可执行二进制）、**QEMU/KVM**（任意多厂商镜像：IOSv、vEOS、vSRX、FortiGate…）。
- 目录约定固定：`/opt/unetlab/addons/{qemu,iol/bin,dynamips}`、`/opt/unetlab/labs`、`/opt/unetlab/tmp`；节点模板定义 CPU/RAM/端口数/启动参数。
- 配置管理：**startup-config + NVRAM** 分离，启动时按 NVRAM 恢复；HTML5 / native 双控制台。

**可借鉴**
- 多厂商本质 = **统一节点抽象 + 不同后端/镜像**：你的 Python 版同理——统一 `Device`，后端是"内置仿真逻辑"，而非外部进程。
- 拓扑文件即配置（XML/YAML），节点坐标只是附加属性——**拓扑与运行状态分离**。
- 节点模板（template）集中定义"某型号设备长什么样"，实例化时克隆参数——适合做设备型号库。

### 1.3 ns-3（离散事件核心——最重要的参照）

**核心架构（src/core + src/simulator 两层，零依赖）**
- 四个对象：**Simulator / Scheduler / Event / Time**。
  - `Simulator::Schedule(delay, fn, args)` 返回 `EventId`，可 `Cancel`（设标志，省开销）或 `Remove`（出队）。
  - `Simulator::Run()` 主循环按时间序执行事件；**事件之间状态不变，事件执行零耗时**；同刻事件按唯一递增 ID（FIFO）执行。
  - `Time` 为 64.64 定点数，保证跨平台确定性与精度可扩展。
  - **Scheduler 可插拔**：堆/日历队列等实现，为大规模优化预留。
  - `ScheduleNow` / `ScheduleDestroy` / `ScheduleWithContext`（把接收节点 id 记入事件，便于日志与上下文）。
- 回调（Callback）用于**解耦**：设备只注册回调，不直接依赖上层协议。

**建模要点**
- `Node`：唯一 id + NetDevice 列表 + Application 列表；`Node::Initialize` 逐对象传播启动。
- `NetDevice`：物理接口抽象，含 **Send(packet, dest, proto)**、`Receive` 回调、输出队列、MTU、up/down 状态、Channel 指针；`PointToPointNetDevice` 与 `PointToPointChannel` 类型配对（**类型安全**）。
- `Channel`：耦合两端 NetDevice，负责延迟/带宽交付。
- `Packet`：序列化字节 **Buffer** + **Tags** 容器（仿真专用元数据放 Tags，不污染字节流）。
- 应用（Application）挂到 Node 上，调度流量事件。

**可借鉴（直接对应你的代码）**
- 事件 = "延迟的函数调用"；`_schedule(delay, fn, *args)` 已有雏形，但**时间基准要换成虚拟时间**（见 §3.1）。
- NetDevice/Channel 分离：你现在的 `Iface.connected ↔ Iface` 点对点绑定，升级为 `Iface → Link/Channel`，才能支撑多接入段、VLAN、STP。
- Tags 机制：仿真专用数据（QoS 戳、仿真时间戳）不该塞进 `Packet` 字段。
- Callback 解耦：`dev.on_packet` 已是回调，继续用。

### 1.4 Mininet

**核心架构**
- **进程级虚拟化 + Linux network namespace**：Host = 命名空间里的进程；Switch = OVS/OpenFlow 软件交换机；**Link = veth 对**（内核态），`tc` 加带宽/延迟/丢包（TCIntf）。
- 类结构：`mininet.topo.Topo`（声明式 build()）→ `mininet.net.Mininet`（实例化）→ `mininet.node.{Node,Host,Switch,Controller}` → `mininet.cli.CLI`。单机可跑 4096 节点。

**可借鉴**
- **拓扑对象（声明式）与运行对象（命令式）分离**：`Topo.build()` 描述关系，`net.start()` 实体化。你的 `build_demo_topology` 已是此模式，建议加 YAML 拓扑文件（netlab 风格）。
- 极简抽象：Host/Switch/Link 三类即可表达网络；你的 `ROUTER/SWITCH/HOST/FIREWALL` 枚举同理。
- 仿真层不仿真真实协议栈——**协议是"装进去的应用"**，这与"纯代码模拟 + 内置协议"理念一致。

### 1.5 FRRouting / Bird / Quagga（路由协议架构——最重要的协议参照）

**核心架构**
- 每个协议一个**守护进程**：`bgpd/ospfd/isisd/ripd/staticd/bfdd/…`，统一向**中间人 zebra** 上报/安装路由；zebra 负责 RIB 决策并下发数据面（内核/远程数据面），通信走 **ZAPI**（UNIX socket）。
- **vtysh** 通过各守护进程的 VTY（TCP 2600+ 或 UNIX socket）聚合出"一个路由器的完整 CLI"；一体化配置模式可写入单一配置文件。
- 进程隔离带来弹性：一个协议崩溃不影响其他；支持运行时插件。

**可借鉴（这是你 P4 动态路由最该抄的）**
- **RIB 与协议分离**：一个 `RoutingManager`（zebra）持有路由表；每个协议模块是"事件驱动状态机"，把路由**安装/撤回**到 RIB；RIB 负责**管理距离 + 度量选路**（IOS/VRP 语义：`S` vs `O` vs `B`）。
- 你的 `RouteTable.clear_proto(proto)` 已经是 zebra 式清路由的原型，方向正确。
- 协议计时器（hello/dead、keepalive、retry）统一由调度器管理（可取消），协议状态机驱动收敛。

### 1.6 Cisco Packet Tracer / 华为 eNSP（CLI 与双时间模式参照）

**CLI 模型（IOS）**
- 模式层次：`User EXEC (>)` → `enable` → `Privileged EXEC (#)` → `configure terminal` → `Global Config (config)#` → 子模式 `interface → (config-if)#`、`line → (config-line)#`、`router → (config-router)#`、`vlan → (config-vlan)#`。
- 导航：`exit` 退一层、`end`/`Ctrl-Z` 直接回特权模式、`disable` 回用户模式。
- 交互特性：**上下文敏感 `?` 帮助、前缀缩写 + Tab 补全、`^` 错误定位、`no`/`default` 形式、`--More--` 分页、`| include/begin` 过滤、命令历史**。
- VRP5（eNSP）对应：`<Huawei>` 用户视图 → `system-view` → `[Huawei]` → `interface GigabitEthernet0/0/0` → `[Huawei-GigabitEthernet0/0/0]`；`quit` 退一层、`return`/`Ctrl+Z` 回用户视图；`display *`、`save`。

**双时间模式（最关键）**
- **Realtime 模式**：按墙钟运行。
- **Simulation 模式**：**非线性时间**——PDU 在设备间"走一步记一步"，事件列表按逻辑顺序呈现，支持过滤器、ACL 过滤、QoS 戳。切换模式/改配置/重启会重置仿真。

**可借鉴**
- CLI = **模式栈 + 每模式命令集 + 上下文帮助**；解析引擎（缩写/补全/`?`）与厂商语义解耦（§3.5）。
- **Realtime ⇄ Simulation 双模式**天然适配"交互教学"与"收敛分析/压测"两种场景，是规模化与真实感兼顾的关键架构（§3.1）。

### 1.7 Python 系模拟器：VPCS / netlab(netsim-tools) / ns.py 等

- **VPCS（GNS3 Virtual PC）**：C 程序，单实例仅 2MB 内存，实现 DHCP/ping/trace 与简化协议栈，配置存 `startup.vpc`。启示：**终端设备可以极轻**，不必完整协议栈。
- **netlab / netsim-tools**（多厂商配置生成，重点，详见 §5）：
  - YAML 拓扑 → 转换流水线（`netsim/augment/{nodes,links,devices,addressing,groups,config}`）→ 输出（provider 配置 + Ansible inventory）→ **Jinja2 厂商模板**渲染设备配置。
  - 设备能力：`netsim/devices/<vendor>.yml`（`interface_name`、`mgmt_if`、`features`、`roles`、`group_vars`、provider 参数、`parent` 继承，如 `csr parent: iosv`）。
  - 协议为**模块**：`netsim/modules/{ospf,bgp,isis,evpn,vlan,vrf,...}.py + .yml`；模板按 `templates/<module>/<vendor>.j2` 组织。
- **ns.py**：SimPy 之上的 Pythonic 离散事件网络仿真器（PacketGenerator/Sink、Port、WFQ/DRR 调度、emulation 代理模式）。证明"Python 纯仿真 + 事件驱动"完全可行。
- **CORE / IMUNES**：Linux namespace 虚拟化，数百至数千节点，说明**规模化的瓶颈在时间模型与数据结构，不在语言**。
- **SimPy**：生成器式进程 DES，天然支持"虚拟时间 + 实时/手动步进"，有成熟的 heapq 内核；另有 asimpy（async/await 版）、SerializableSimpy（回调式、可并行/序列化）。

### 1.8 Ansible Network / Batfish（配置建模参照）

- **Batfish**：为每厂商写 **ANTLR 语法** → 解析成厂商私有表示 → **归一化为厂商无关（VI）数据模型**（`Configuration / Interface / Vrf / RoutingPolicy / IpAccessList`，在 `batfish-common-protocol`）→ 用**增量数据面模型（IBDP）**做迭代收敛计算 → 支持可达性等查询。
- 关键洞见：**厂商相关只出现在"边界解析/渲染"，分析逻辑只对 VI 模型做一次**。
- Ansible 网络模块：用 `ansible_network_os` + `network_cli` 连接层抽象多厂商 CLI 交互，netlab 的 `group_vars` 正是同一思路。
- ciscoconfparse2：把 IOS 风格配置解析成**父/子层级**树，可复用于"导入真实设备配置"。

**可借鉴**
- 你的双厂商最终目标是：**厂商语法在"进/出"边界（CLI 解析与输出渲染）消耗掉，内部一律操作同一套设备/RIB/接口模型**（Batfish 式 VI 模型）——这正是"配置生成"与"配置回读校验"能同时成立的原因。

---

## 2. 仿真核心架构设计建议（落地版）

> 下面给出可直接实施的方案，已对齐现有 `netsim/` 代码结构（`netsim/net/{world,device,iface,ip}.py`、`netsim/vendors/*`、`netsim/cli/session.py`、`netsim/server.py`）。

### 2.1 事件循环与虚拟时间（最高优先级改造）

现状：`World` 用 `time.time()` 做时间基准、`loop.call_later` 调度 → **只能实时运行**，无法快进、无法确定性复现，也无法做千台设备压测。

方案：**双时间模式 + 统一 `Scheduler`**

```
Realtime 模式（默认，交互用）   Simulation 模式（收敛研究 / 压测用）
  虚拟时钟 ≈ 墙钟                   虚拟时钟 = 事件堆的队首时间
  驱动 = asyncio 事件循环            驱动 = while heap: 取最早事件执行
  用于 CLI / WebSocket / UI          用于 n 倍加速、批量跑拓扑
```

统一抽象 `Scheduler`（ns-3 的 `Simulator/Scheduler/Event` 缩略版）：

```python
# netsim/core/scheduler.py
@dataclass(order=True)
class Event:
    time: float            # 虚拟时间（float 即可；ns-3 用 64.64 定点，python 用 float 足够）
    seq: int               # 同刻事件 FIFO 序号
    fn: Callable
    args: tuple
    cancelled: bool = False

class Scheduler:
    def __init__(self, realtime: bool):
        self._heap: list[Event] = []
        self._seq = 0
        self._now = 0.0
        self._realtime = realtime
        self._loop = asyncio.new_event_loop() if realtime else None

    def now(self) -> float: ...
    def schedule(self, delay: float, fn, *args) -> Event:   # 统一入口
    def cancel(self, ev: Event) -> None:                     # 标志位即可
    def step(self) -> None:      # Simulation 模式：执行最早事件（可步进调试）
    def run(self, until: float = None) -> None:              # Simulation 模式：主循环
    def run_forever(self) -> None:                           # Realtime 模式：asyncio 事件循环
```

要点：
- **数据面（转发、协议）只依赖 `Scheduler.now()` / `schedule()`，绝不出现 `asyncio.sleep` 与 `time.time()`**。`asyncio` 仅用于 Realtime 模式的 I/O 驱动，可整个替换为"一次 tick 驱动调度器"。
- Simulation 模式下 `run()` 是纯 CPU 循环（heap 取队首、执行），**虚拟时间直接跳**：收敛分析几秒跑完，压测可跑数百虚拟秒。
- 事件 = 延迟函数调用（ns-3 哲学）："两个事件之间系统状态不变"，天然可步进、可录制回放、可确定性复现（种子化 RNG）。
- `_pending` ping 回执目前依赖 asyncio Future——Simulation 模式下改为"超时事件 + 回执事件"，不用 Future。

### 2.2 设备模型（Node = 协议栈 + 接口 + 服务）

现有 `Device` 已含 `net`（接口/路由/ARP），改造为 ns-3 风格：

```
Node (Device)
 ├── 接口列表 [Iface = NetDevice]     # MTU/MAC/管理态(admin)/操作态(oper)/队列/速率
 ├── 协议栈 [ProtocolStack]           # L2(LAN/转发) + L3(IP/ARP) 由协议模块实现
 ├── 服务 [Application]               # ping/HTTP 生成器等（VPCS 式轻终端）
 ├── 路由管理器 [rib]                 # zebra，见 §2.4
 └── on_packet 回调                   # 保持现有 callback 解耦
```

- **接口 ↔ 链路解耦（关键）**：当前 `Iface.connected = Iface` 只支持点对点。升级为 `Iface → Link(Channel)`：
  - `Link`：两端 `endpoints: [Iface, Iface]`，带 `delay / bandwidth / loss`（Mininet TCIntf 的仿真版），`deliver(pkt, from_iface, delay)` 由 Scheduler 调度到对端。
  - 预留**多接入 Channel**（广播域）为 P5 VLAN/STP 打基础：`EthernetSegment(ports: [Iface])`，泛洪/学习表内置于该 Channel 或独立 `SwitchingEngine`。
- 接口三态演进：`admin (shutdown/no shutdown)`、`oper`、`link (物理链路上下)`，与 IOS/VRP `show interfaces` 语义对齐。
- 设备型号化：仿 EVE-NG 模板 / netlab 能力清单，`DeviceModel(型号, 槽位数, 端口模板)` → 实例化生成接口。初期可延迟，但接口命名（`GigabitEthernet0/0/0` vs `g0/0`）应纳入厂商能力清单（§2.6）。

### 2.3 报文模型

- 保持 `Packet` dataclass，但**转发链路只读**（除非克隆），避免多播/泛洪时共享可变状态：`clone()` 已具备。
- 仿真专用元数据走 **Tags/附件**（ns-3）：QoS 戳、仿真进入时间、hop 记录、`through`（只在需要 UI 动画时记录，默认惰性）。不要在数据路径上无条件 append。
- 增加**封装视图**（encapsulation）概念：L2/L3/L4 头分层（`eth → ip → icmp/tcp/udp`），为 ACL、NAT、DHCP（P6）和抓包导出（pcap）做准备。
- **PDU/事件轨迹**由 World 统一生成（`on_event` 已存在），供前端动画，不侵入报文。

### 2.4 协议模型（Zebra 模式，为 P4 OSPF/BGP 铺路）

```
                 ┌───────────── RoutingManager (RIB) ─────────────┐
                 │  路由表(前缀Trie) + 管理距离 + 度量 选路        │
                 └──────┬───────────┬───────────┬──────────┬──────┘
                        │ install   │ install   │ install  │
                   ┌────▼───┐  ┌────▼───┐  ┌────▼───┐  ┌───▼────┐
                   │static  │  │ ospfd   │  │ bgpd    │  │  ...   │
                   └────────┘  └────────┘  └────────┘  └────────┘
                       每个协议 = 事件驱动状态机，只通过 RIB 接口操作路由
```

- `RoutingManager`（zebra 对应物）：
  - 每 proto 一份前缀表（`static/ospf/bgp/connected`），`install/propagate` 后做**选路**：管理距离（`0(直连)/1(静态)/110(OSPF)/20(BGP)` 与 VRP 语义对应）→ 度量 → 前缀长度已由 Trie 保证。
  - 接口：`rib.install(proto, prefix, nexthop, iface, metric)`、`rib.withdraw(proto, ...)`、`rib.clear_proto(proto)`（现有 `RouteTable.clear_proto` 就是这个）。
  - `lookup` 用 **Trie（最长前缀匹配）** 替代当前线性列表；前缀数量大时差距显著（P8 压测）。
- **协议基类**：
  ```python
  class Protocol:
      def __init__(self, device, name): self.device = device; self._timers = []
      def start(self): ...        # 注册到设备协议栈
      def stop(self): ...         # 取消全部定时器、清路由
      def on_packet(self, iface, pkt): ...   # 由设备分发到本协议
      def _timer(self, delay, fn): return self.device.scheduler.schedule(delay, fn)  # 统一定时器
  ```
- **收敛是事件驱动的**：链路 down → 设备广播 `link_state_changed(iface)` → 各协议响应（OSPF 重算 SPF、BGP 撤销路由）→ 重新 install → RIB 变化再触发 `route_changed` 事件 → 数据面生效。这正是"可观察收敛过程"的 Promise 的实现方式。
- `icmp/arp` 也做成协议模块（当前已散落在 World，收敛到 proto/）。

### 2.5 CLI 抽象（多厂商解析引擎与渲染分离）

现状：`vendors/{cisco,huawei}.py` 各自硬编码解析与输出。升级为**引擎 + 注册表 + 渲染器**：

```
netsim/cli/
  parser.py    # 厂商无关：模式栈、Trie 命令匹配、前缀缩写、?帮助、Tab补全、^错误定位
  render.py    # 输出：--More-- 分页、| include/begin 过滤、命令历史（IOS/VRP 通用）
  session.py   # 会话（现有，接 parser）
netsim/vendors/{cisco,huawei}/
  cmds.py      # 命令注册表：View -> [(tokens, arg_parser, handler)]
  views.py     # 模式定义与切换（> / # / (config)# / (config-if)# / (config-router)#…）
  render.py    # 输出格式（show ip route vs display ip routing-table）
  capabilities.yml  # 接口命名、特性（§2.6）
```

- **命令 = 数据，不是代码**：每条命令描述为 `(关键字序列, 参数模式, 回调)`，注册到该模式。`show ?` / Tab 补全 / 缩写解析全部由 `parser.py` 用同一棵 Trie 完成——两个厂商白赚这些交互特性（`config-if` 的 `?` 列表、`conf`→`configure` 补全）。
- **执行与显示分离**：handler 改设备模型（接口 IP、路由）；输出交给 `render.py`。`display ip routing-table` 与 `show ip route` 只是对同一 RIB 的不同渲染。
- 模式栈：`push_view/subview/exit/end` 的通用实现，各厂商只声明模式树的父子关系（IOS 与 VRP5 高度同构，唯一差异是 VRP 的 `system-view` 入口与 `return` 行为）。
- 保留现有"回车执行→返回多行字符串"的 `session` 契约，前端无需改动。

### 2.6 配置模型（running / startup + 结构化对象）

- `running-config` 不在引擎层存文本，而是**结构化配置对象**：接口表、路由表、协议配置（OSPF area、BGP peer 等），外加一条**有序命令流**（用于 `show running-config` / `display current-configuration` 的精确回显与 `save`）。
- `startup-config`（NVRAM 模拟）：保存命令流；设备重启按它重建 running。
- 双厂商渲染：`vendor.render.running_config(device)` 生成各自语法的文本（netlab 式"意图 → 厂商文本"的单向渲染即够）；若要做"导入真实配置"，再引入 ciscoconfparse2/Batfish 式反向解析（P 阶段之后）。
- **Vendor 无关内部模型**：CLI 解析与输出渲染是唯一的厂商边界，内部（RIB/接口/ARP）厂商无关（Batfish VI 模型思想）——这是"多厂商不重复造分析逻辑"的根。

### 2.7 模块/目录组织（建议目标结构）

```
netsim/
  core/                 # 厂商无关仿真内核
    scheduler.py        # 虚拟/实时双模式调度器（§2.1）
    event.py            # Event 定义
    world.py            # 拓扑 + 分发（由 net/world.py 迁入）
    channel.py          # Link(P2P) / EthernetSegment(多接入)（§2.2）
    pkt.py              # Packet + 封装视图 + Tags（由 net/device.py 迁出）
    trace.py            # PDU/事件轨迹，供 UI 与回放
  node/
    device.py           # Node
    iface.py            # NetDevice（MTU/队列/状态）
    rib.py              # zebra 式 RIB + Trie（改 RouteTable）
    arp.py              # ARP（dict 化）
    ip.py
  proto/                # 协议“守护进程”
    base.py             # Protocol 基类（定时器/启停/路由接口）
    static.py icmp.py arp.py ospf.py bgp.py ...
  vendors/
    cisco/  huawei/  host/
      cmds.py views.py render.py capabilities.yml
  cli/
    parser.py render.py session.py
  server.py  web/
  topology/             # YAML 拓扑导入/导出、批量生成（P8 压测入口）
```

> 兼容策略：`netsim/net/` 保持为薄封装，逐步把逻辑迁入 `core/` `node/` `proto/`，避免一次性大爆炸重构。

### 2.8 设计模式清单

| 模式 | 用途 | 来源 |
|---|---|---|
| Scheduler + Event（延迟函数调用） | 虚拟时间主循环 | ns-3 / SimPy |
| Callback 解耦 | 设备↔协议、接口↔链路、引擎↔前端 | ns-3 / GNS3 |
| Zebra（RIB 集中 + 协议进程分散） | 路由架构 | FRR / Quagga / Bird |
| 命令注册表 + Trie 解析（数据驱动命令） | 多厂商 CLI | IOS/VRP、cmd2 类工具 |
| 策略渲染（意图→厂商文本） | 配置生成 | netlab |
| 双边界 VI 模型（解析/渲染在边界） | 厂商无关分析 | Batfish |
| 声明式拓扑对象 → 命令式实例 | 拓扑管理 | Mininet / netlab |
| 模板（设备型号→实例） | 设备库 | EVE-NG |
| 事件溯源 / 轨迹 | 回放、确定性、UI 动画 | ns-3 trace、GNS3 通知 |

---

## 3. 大规模仿真（数千台设备）具体方案

1. **时间模型**：Simulation 模式 + `heapq`。事件量级 ≈ O(每包跳数) + O(收敛计算)，全 CPU 无 I/O，千台规模轻松；压测入口为"批量加载 YAML 拓扑 → `run()`"。
2. **事件批量与惰性**：同刻同类事件（如一批 OSPF Hello）合并/批量回调；不产生事件就不推进（`run(until)` 空转也是 O(1)）。
3. **数据结构**：
   - 路由表：前缀 Trie（LPM），替代 O(n) 线性 `RouteTable.lookup`。
   - ARP：`dict[ip → entry]`（当前是线性 list）。
   - `device_for_iface`：建立 `iface_id → device` 反向索引，避免每包全表扫描（当前 `_forward` 每次线性找对端设备）。
   - 设备/接口/链路一律用 id 索引，前端交互按 id 查询。
4. **只读转发 + 按需记录**：数据面不 append `through`，只在 UI 需要时按 trace 事件记录。
5. **确定性**：PRNG 统一种子；`trace.py` 可回放，支持"同一拓扑+配置 → 同一事件序列"的回归测试。
6. **CPU 取舍**：核心数据面**不用 asyncio**；asyncio 只留在 Realtime/UI 层（对比：ns.py 用 SimPy 纯 CPU 内核、GNS3 用 asyncio 但那是 I/O 密集的进程编排）。
7. **性能预估**：千台路由器 + 静态路由的 ping 洪泛 ≈ 千级设备 × 数跳事件/秒量级；路由收敛（OSPF/BGP）的事件数远小于数据包事件，虚拟时间跳转后可"瞬间"收敛——这正好对应 P8 压测目标。

---

## 4. netlab 多厂商 CLI/配置模板机制落地（§1.7 展开）

netlab 的机制（可直接照搬思想）：

1. **设备能力清单**：`vendors/<vendor>/capabilities.yml`
   ```yaml
   interface_name: "GigabitEthernet{ifindex}"     # 华为: "GE{ifindex}" / "XGE{ifindex}"
   mgmt_if: "MEth0/0/0"                            # VRP 管理口
   features: { initial: { system_mtu: true }, ospf: true, bgp: { local_as: true } }
   group_vars: { ansible_network_os: ios }         # 你这里是"渲染器选择器"
   parent: iosv                                     # 型号继承（如 VRP5 基类 → 子型号）
   ```
2. **配置意图与厂商语法分离**：拓扑/配置意图用 YAML/结构化对象描述（"这台设备是 area 0、BGP peer 1.1.1.1"），渲染器按厂商生成文本。netlab 用 Jinja2：`templates/<module>/<vendor>.j2`（`initial/iosv.j2`、`ospf/eos.j2`…）；你的 CLI 端到端场景下，**渲染目标从"配置文件"换成"CLI 命令流"**——同一个意图对象驱动 `show running-config` 输出与启动配置下发，天然多厂商。
3. **模块化协议**：每个协议（OSPF/BGP/VLAN/STP…）一个"模块"，声明它需要哪些设备特性、往配置意图里注入什么；设备能力清单声明"我支持哪些模块/特性"，拓扑校验据此拒绝不支持组合（netlab `features` 就是干这个）。
4. **继承**：`parent:` 让"VRP5 基类"与"AR 系列子型号"共享 90% 配置模板，只覆写接口命名/特性差异（netlab `csr parent: iosv`）。

> 结论：把"CLI 命令表"视为"每厂商每视图每模块的模板 + 能力清单"，而不是散落的 if/else。这是多厂商可持续扩展的关键。

---

## 5. 前端 / 拓扑接入（简述）

- 维持现状：**WebSocket 推送事件流**（PDU 轨迹、接口状态、RIB 变更），前端画布做包动画与状态着色（GNS3 notification 模式）。服务端保持 `on_event` 回调式 API，**仿真内核不依赖前端**。
- 拓扑文件：YAML（netlab 风格）作为导入/导出与压测生成格式：
  ```yaml
  devices: { AR1: {vendor: huawei, model: AR}, R2: {vendor: cisco} }
  links: [ AR1-eth0: R2-eth0 ]
  ```
- 控制台：CLI 会话已是纯文本流，可同时支持 Web 终端与未来 SSH 入口（eNSP/GNS3 都做双重控制台）。

---

## 6. 参考来源（主要链接）

- GNS3 Architecture：docs.gns3.com/docs/using-gns3/design/architecture；gns3-server（github.com/GNS3/gns3-server）
- ns-3：Events and Simulator（nsnam.org/docs/manual/html/events.html）、architecture.pdf
- Mininet：github.com/mininet/mininet、mininet.org/overview
- FRRouting：docs.frrouting.org Overview（daemon/zebra/vtysh/ZAPI）、man 页 frr-zebra
- Cisco IOS CLI：cisco.com 官方 CLI 文档；Packet Tracer Simulation Mode（ptnetacad 帮助）
- netlab：netlab.tools、netlab.tools/dev/devices（Contributing New Devices）、DeepWiki ipspace/netlab
- VPCS：github.com/GNS3/vpcs、GNS3 docs（2MB/实例、startup.vpc）
- ns.py：github.com/TL-System/ns.py；SimPy：pypi.org/project/simpy；SerializableSimpy：joss 10.21105/joss.08502
- Batfish：github.com/batfish/batfish/docs/architecture、networktocode.com “Developing Batfish”
- EVE-NG：eve-ng.net（UnetLab fork、IOL/Dynamips/QEMU、XML lab）、EVE-NG Cookbook 6.2
- ciscoconfparse2：pypi.org/project/ciscoconfparse2