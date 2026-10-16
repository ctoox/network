# NetSim - 多厂商网络模拟器（Python 版）

纯 Python 事件驱动的多厂商网络模拟器：真实转发、OSPF 收敛、VRRP/DHCP/NAT/ACL，
内置 7 套分级场景与 Web 终端。定位轻量替代 Packet Tracer / eNSP，可跑大规模拓扑压测。

## 能力一览

- **双时间模式**：Realtime（Web 交互）/ 虚拟时间（`--simulate`/`--stress` 快进，千台级收敛）
- **多厂商 CLI**：华为 VRP5、思科 IOS、华三 Comware、锐捷 RGOS、华为 USG（防火墙）、深信服 AF、Host 终端
- **协议与特性**：静态路由、OSPF（单域收敛）、DHCP 服务/客户端、VRRP、NAT（源 NAT）、ACL、VLAN（access/trunk）
- **7 套分级场景**（`netsim/scenarios.py`，全部互通验证通过）：

| 场景 | 规模 | 架构 |
|---|---|---|
| `smb` | ≤100 人 | 防火墙 + 接入，NAT 上网 |
| `medium` | 200-1000 人 | 双核心 VRRP + 接入，DHCP |
| `large` | 1000-5000 人 | 核心-汇聚-接入 三层，OSPF |
| `campus` | 5000+ 人 | 双核心 + 多区域，OSPF 互联 |
| `datacenter` | 服务器规模 | Spine-Leaf 扁平，OSPF |
| `xinchuang` | 机关/国企 | 全国产（华为/华三/锐捷/华为USG/麒麟） |
| `stress` | 64+ 台 | N 路由器环/带弦环 OSPF 压测 |

## 运行

```bash
python main.py                              # Web UI（演示拓扑）
python main.py --scenario medium            # Web UI 加载"中型"场景
python main.py --scenario xinchuang         # Web UI 加载"信创"场景
python main.py --list-scenarios             # 列出全部场景
python main.py --simulate 10.0.20.5 --scenario medium   # 无头：虚拟时间 ping 收敛
python main.py --stress 128                 # 128 台路由器 OSPF 压测
```

打开 `http://127.0.0.1:8080`，点击设备进入 CLI 终端：

```
<Kylin-CORE-1>display ospf peer
<Kylin-CORE-1>system-view
[Kylin-CORE-1]display ip routing-table
```

## 架构

```
netsim/
  net/          # 仿真内核：Device/Iface/Rib/ARP/IP + World（拓扑、泛洪、ARP、DHCP、ACL）
  core/         # Scheduler（虚拟/实时双模式）
  vendors/      # huawei/cisco/h3c/ruijie/huawei_fw/sangfor/host  CLI 实现
  cli/          # session（视图栈、? 帮助、Tab 补全、前缀缩写）
  scenarios.py  # 7 套分级场景构建器
  server.py     # HTTP + WebSocket 服务
  main.py       # 入口
```

关键设计：

- **厂商语法只在 CLI 边界**：`vendors/*` 解析/渲染命令，内部统一操作同一套设备/RIB/接口模型
- **厂商继承**：Comware 继承 VRP、RGOS 继承 IOS、USG 继承 VRP——差异最小化
- **真实数据面**：ping 逐跳转发、OSPF 收敛、VRRP 选举、DHCP 租约都在仿真内核里真实发生
- **虚拟时间规模化**：`World(realtime=False)` + 事件堆快进，收敛研究与压测与墙钟解耦

## 回归与验证

```bash
python -m compileall netsim main.py          # 语法检查
python main.py --simulate 192.168.2.1       # 双厂商 demo 回归（5/5）
python main.py --stress 64                  # 64 台环网压测 + 可达性
python <temp>/test_scenarios.py             # 7 场景互通回归（脚本见下）
```

7 场景互通回归脚本：`C:\Users\ctooc\AppData\Local\Temp\opencode\test_scenarios.py`

## 文档

| 目录 | 内容 |
|---|---|
| [docs/命令大全/](docs/命令大全/README.md) | 六大厂商命令手册（对照模拟器实现） |
| [docs/架构规划/](docs/架构规划/README.md) | 小型→中型→大型→超大型园区→数据中心 五级方案 |
| [docs/信创/](docs/信创/README.md) | 全国产化组网方案 |
| [ARCHITECTURE_REPORT.md](ARCHITECTURE_REPORT.md) | 仿真核心架构调研与落地说明 |

## 技术说明

- 语言：Python 3.12（零第三方依赖）
- 模拟方式：离散事件（非真实协议栈），每跳转发 = 事件调度，可扩展到数千台设备
- 前端：内置 HTTP + WebSocket，终端流式输出，零 CDN