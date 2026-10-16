# 华三 Comware 命令手册（模拟器版）

实现：`netsim/vendors/h3c.py`（继承华为 VRP）　风格：视图 + `display`

> Comware V7 与 VRP5 高度同构，模拟器只改写差异项。**本文只列与 VRP5 不同的命令/写法**，
> 其余全部命令见 [华为 VRP5](huawei-vrp.md)。

## 与 VRP5 的差异

| 项目 | 华为 VRP5 | 华三 Comware |
|---|---|---|
| 逻辑接口命名 | `Vlanif10` | `Vlan-interface10` / `Vlan-interface 10` |
| VLAN 创建 | `vlan 10` / `vlan batch 10,20` | `vlan 10`（当前配置渲染为 `vlan 10,20`） |
| trunk 放行 | `port trunk allow-pass vlan 10` | `port trunk permit vlan 10` |
| access 归属 | `port default vlan 10` | `port access vlan 10` |
| DHCP 池入口 | `ip pool pool1` | `dhcp server ip-pool pool1` |
| ACL 入口 | `acl number 3001` | `acl basic 3001` |
| ACL 规则 | `rule 5 permit source <ip> <wc> destination ...` | `rule 5 permit source <ip> <wc>`（basic 只匹配源） |
| 分隔符 | `#` | `#` |

其余（系统视图、接口视图、OSPF、VRRP、NAT、`display *`）与 VRP5 相同。

## 示例配置（对应 `build_large` 的汇聚交换机）

```
#
sysname AGG-1
#
vlan 10
#
interface GE1/0/24
 port link-type trunk
 port trunk permit vlan 10
#
interface Vlan-interface10
 ip address 10.0.10.1 255.255.255.0
#
ospf 1
 area 0.0.0.0
  network 10.0.10.0 0.0.0.255
quit
#
return
```

## DHCP 服务器写法（对照 VRP）

```
dhcp enable
dhcp server ip-pool pool1
 network 10.0.10.0 mask 255.255.255.0
 gateway-list 10.0.10.254
 dns-list 114.114.114.114
quit
```

## 验证要点

- `display current-configuration` 用 `Vlan-interface` 渲染逻辑口，`port trunk permit vlan` 渲染放行列表。
- 逻辑口 `display interface Vlan-interface10` 输出固定 `Up` 与 `Internet Address is ...`。
- 模拟器内部命令解析与 VRP 完全相同，`display ospf peer` / `display vrrp` 可直接验证收敛。