# 华为 VRP5 命令手册（模拟器版）

实现：`netsim/vendors/huawei.py`　风格：视图 + `display`

## 视图体系

```
<设备名>                  用户视图
system-view
[设备名]                  系统视图
 interface GigabitEthernet0/0/0
[设备名-GigabitEthernet0/0/0]  接口视图
 ospf 1
[设备名-ospf-1]           OSPF 视图
 area 0.0.0.0
[设备名-ospf-1-area-0.0.0.0]  区域视图
 ip pool pool1
[设备名-ip-pool-pool1]    DHCP 地址池视图
 acl number 3001
[设备名-acl-3001]         ACL 视图
```

退出：`quit` 退一层；`return` / `Ctrl+Z` 直接回用户视图。

接口命名：`GigabitEthernet0/0/0`（模拟器内归一化为 `GE0/0/0`），也接受缩写；
逻辑口 `Vlanif10`（`Vlanif 10` / `Vlanif10`）。**不存在且非逻辑口的接口会报错。**

## 用户视图

| 命令 | 说明 |
|---|---|
| `system-view` | 进入系统视图 |
| `ping [-c N] <IP>` | ICMP 测试（`-c` 指定次数，默认 5） |
| `display version` | 版本信息 |
| `display current-configuration` | 当前配置（结构化解译渲染） |
| `display ip routing-table` | IP 路由表 |
| `display arp` | ARP 表 |
| `display interface <name>` | 指定接口详情 |
| `display brief` | 接口概要 |
| `display vlan` | VLAN 及端口归属 |
| `display ospf peer / lsdb / routing` | OSPF 邻居 / 数据库 / 路由 |
| `display vrrp` | VRRP 状态 |
| `display nat session` | NAT 会话 |
| `display acl all` | ACL 规则 |
| `display dhcp server lease` | DHCP 租约 |
| `tracert <IP>` | ⚠️ 未实现（占位） |
| `quit` | 断开 |

## 系统视图

| 命令 | 说明 |
|---|---|
| `sysname <名称>` | 改设备名 |
| `interface <名称>` | 进入接口视图（Vlanif 不存在时自动创建） |
| `ip route-static <网段> <掩码|前缀> <下一跳>` | 静态路由（如 `ip route-static 10.0.20.0 255.255.255.0 10.1.0.5`，也支持 `24` 前缀写法） |
| `undo ip route-static <网段> <掩码>` | 删除静态路由 |
| `ip pool <名称>` | 进入 DHCP 地址池视图 |
| `ospf [1]` | 进入 OSPF 视图（Router ID 取现有 IP，自动） |
| `vlan <id>[,<id>...] [batch]` | 创建 VLAN（支持 `vlan batch 10,20`） |
| `dhcp enable` | 全局使能 DHCP |
| `acl number <id>` | 进入 ACL 视图 |
| `return` / `quit` | 返回用户视图 |
| `ping` / `display ...` | 同用户视图全部可用 |

## 接口视图

| 命令 | 说明 |
|---|---|
| `ip address <IP> <掩码>` | 配置接口 IP（自动生成直连路由；OSPF 运行中会触发重算） |
| `undo ip address` | 删除接口 IP |
| `port link-type access\|trunk` | 设置端口类型 |
| `undo port link-type` | 复位为 access |
| `port default vlan <id>` | access 口归属 VLAN |
| `port access vlan <id>` | 同上（Comware 兼容写法） |
| `port trunk allow-pass vlan <id,..\|all>` | trunk 放行 VLAN |
| `port trunk pvid vlan <id>` | trunk 原生 VLAN（PVID） |
| `dhcp select global` | 该接口启用全局 DHCP 服务 |
| `vrrp vrid <组> virtual-ip <IP>` | 配置 VRRP 虚拟网关 |
| `vrrp vrid <组> priority <值>` | 调整 VRRP 优先级 |
| `nat outbound <ACL编号>` | 该接口启用源 NAT（inside 自动取其余有 IP 接口） |
| `traffic-filter inbound\|outbound acl <id>` | 接口应用 ACL |
| `shutdown` / `undo shutdown` | 接口管理状态 |
| `display this` | 本接口配置 |
| `quit` / `return` | 退出 |

## OSPF / 区域视图

| 命令 | 说明 |
|---|---|
| `area <区域号>` | 进入区域视图 |
| `network <网络> <反掩码>` | 宣告网络（如 `network 10.0.10.0 0.0.0.255`） |
| `display ospf peer` | 邻居（`Full/ -`） |
| `display ospf lsdb` | 链路状态数据库 |
| `display ospf routing` | OSPF 路由 |

> 模拟器为简化单域 OSPF：Hello 1s / Dead 4s，LSA 全量泛洪（seq 防环），按共享网段构图跑 Dijkstra，路由协议号 `O`、内部管理距离 110（VRP 显示 Pre 10）。

## DHCP 地址池视图

| 命令 | 说明 |
|---|---|
| `network <网络> mask <掩码>` | 池子网（可用地址自动为 网络+1 … 广播-1） |
| `gateway-list <IP>` | 默认网关 |
| `dns-list <IP>` | DNS 服务器 |
| `quit` / `return` | 退出 |

> 模拟器按 VLAN 选池：接口 `dhcp select global` 时记录该口 VLAN，DHCP DISCOVER 依据入口 VLAN 匹配对应池（见 `netsim/net/world.py::_dhcp_pool_for`）。

## ACL 视图

| 命令 | 说明 |
|---|---|
| `rule <序号> permit\|deny [source <IP> <反掩码>] [destination <IP> <反掩码>] [icmp\|tcp\|udp]` | 增规则 |
| `display this` | 显示本 ACL |
| `quit` / `return` | 退出并生效 |

## display current-configuration 输出样例

```
#
sysname SW-C1
#
vlan batch 10,20
#
interface GE1/0/23
 port link-type trunk
 port trunk allow-pass vlan 10,20
#
interface Vlanif10
 ip address 10.0.10.1 255.255.255.0
#
ospf 1
 area 0.0.0.0
  network 10.0.10.0 0.0.0.255
quit
return
```

## 验证要点

- `display ip routing-table` 的 `Proto` 列：`Direct`(Pre 0) / `Static`(Pre 60) / `OSPF`(Pre 10，按真实 VRP 语义渲染；内部选路管理距离 OSPF=110)。
- 内部 RIB 选路：最长前缀 → 管理距离（直连 0/静态 1/OSPF 110）→ 度量（见 `netsim/net/iface.py`）。
- OSPF 收敛后 `display ospf peer` 应显示 `Full/  -`；未收敛显示 `OSPF is not running.`。
- `display vrrp` 的 `State` 按优先级选举 `Master/Backup`（高优先级为 Master）。
- 接口 `shutdown` 后 OSPF 邻居会因链路变化重算收敛。