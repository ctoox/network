# 思科 IOS 命令手册（模拟器版）

实现：`netsim/vendors/cisco.py`　风格：模式 + `show`

## 模式体系

```
设备名>                用户模式（User EXEC）
enable
设备名#                特权模式（Privileged EXEC）
configure terminal
设备名(config)#        全局配置模式
 interface GigabitEthernet0/0
设备名(config-if)#     接口配置模式
 router ospf 1
设备名(config-router)# OSPF 配置模式
 ip dhcp pool pool1
设备名(config-dhcp)#   DHCP 池模式
```

退出：`exit` 退一层；`end` / `Ctrl+Z` 直接回特权模式；`disable` 回用户模式。
特权/配置模式下可用 `do <特权命令>` 直接执行 `show ...`。

接口命名：`GigabitEthernet0/0` 归一化为 `GE0/0`；`interface Vlan <id>` / `Vlan<id>` 创建 SVI；
`Loopback0` 创建环回口。**其余未知接口报 `Invalid input detected`。**

## 用户模式

| 命令 | 说明 |
|---|---|
| `enable` | 进入特权模式 |
| `ping <IP>` | ICMP 测试（5 次，`!!!!!` 为通、`.....` 为不通） |
| `show version` | 版本信息 |
| `show ip route` | 路由表 |
| `show arp` | ARP 表 |
| `show interfaces <name>` | 接口详情 |
| `show running-config` | 运行配置 |
| `traceroute <IP>` | ⚠️ 未实现（占位） |
| `quit` | 断开 |

## 特权模式

| 命令 | 说明 |
|---|---|
| `configure terminal`（`conf t`） | 进入全局配置模式 |
| `show running-config` | 运行配置 |
| `show ip route` | 路由表 |
| `show ip interface` | 接口概要（`sh ip int`） |
| `show ip nat translations` | NAT 转换 |
| `show ip dhcp binding` | DHCP 绑定 |
| `show ip ospf neighbor / database` | OSPF 邻居 / 数据库 |
| `show vlan brief` | VLAN 概要 |
| `show interfaces status` | 接口状态 |
| `show access-lists` | ACL |
| `show standby` / `show vrrp` | 冗余组状态 |
| `show arp` / `show version` | ARP / 版本 |
| `ping <IP>` | ICMP 测试 |
| `disable` | 回用户模式 |

## 全局配置模式

| 命令 | 说明 |
|---|---|
| `hostname <名称>` | 改主机名 |
| `interface <名称>` | 进入接口配置（SVI/Loopback 自动创建） |
| `ip route <网段> <掩码> <下一跳>` | 静态路由 |
| `ip dhcp pool <名称>` | 创建 DHCP 池（默认服务所有 VLAN） |
| `ip nat inside source list <ACL> interface <口> overload` | 源 NAT（overload） |
| `service dhcp` | 使能 DHCP 服务 |
| `router ospf [1]` | 进入 OSPF 配置模式 |
| `vlan <id>` | 创建 VLAN |
| `access-list <id> permit\|deny [ip\|icmp\|tcp\|udp] <src> <src-wc> [<dst> <dst-wc>]` | ACL 规则 |
| `end` / `exit` | 退出到特权模式 |
| `do <特权命令>` | 配置模式下执行 show |

## 接口配置模式

| 命令 | 说明 |
|---|---|
| `ip address <IP> <掩码>` | 配接口 IP |
| `no ip address` | 删 IP |
| `ip nat inside` / `ip nat outside` | 标记 NAT 方向（outside 自动取其余有 IP 口为 inside） |
| `ip access-group <ACL> in\|out` | 应用 ACL |
| `switchport mode access\|trunk` | 端口类型 |
| `switchport access vlan <id>` | access 归属 VLAN |
| `switchport trunk allowed vlan <id,..\|all>` | trunk 放行 |
| `switchport trunk native vlan <id>` | 原生 VLAN |
| `shutdown` / `no shutdown` | 接口状态 |
| `standby <组> ip <VIP>` | HSRP 虚拟 IP |
| `standby <组> priority <值>` | HSRP 优先级 |
| `exit` / `end` / `do` | 退出 / 回特权 / 执行 show |

## OSPF / DHCP 子模式

| 视图 | 命令 | 说明 |
|---|---|---|
| OSPF | `network <网络> <反掩码> area <区域>` | 宣告网络 |
| OSPF | `exit` / `end` / `do` | 退出 / 回特权 / show |
| DHCP | `network <网络> <掩码>` | 池子网 |
| DHCP | `default-router <IP>` | 网关 |
| DHCP | `dns-server <IP>` | DNS |
| DHCP | `exit` / `end` | 退出 |

## show running-config 输出样例

```
!
hostname R1
!
interface GE0/0/1
 ip address 10.0.0.1 255.255.255.252
!
ip route 192.168.2.0 255.255.255.0 10.0.0.2
!
router ospf 1
 network 10.0.0.0 0.0.0.3 area 0
!
end
```

## 验证要点

- `show ip route` 路由代码：`C` 直连、`S` 静态、`O` OSPF。
- ping 输出：`!!!!!` 全部通、`.....` 全部丢；`Success rate is 100 percent (5/5)`。
- `show standby` 显示 HSRP 状态（Master/Standby）；RGOS 用 `show vrrp`。