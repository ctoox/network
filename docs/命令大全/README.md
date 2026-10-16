# 命令大全（对照 NetSim 模拟器实现）

> 本手册严格对照本项目 `netsim/vendors/` 实际实现的 CLI 命令，**不代表真实设备的全部能力**。
> 每条命令均可在模拟器中输入验证；未实现/仅占位的命令会明确标注。
>
> 覆盖厂商：
>
> | 厂商 | 模拟器类 | 风格 | 典型设备 |
> |---|---|---|---|
> | 华为 VRP5 | `vendors/huawei.py` | 视图 + `display` | AR/CE 系列路由器、S 系列交换机 |
> | 思科 IOS | `vendors/cisco.py` | 模式 + `show` | ISR/Catalyst，IOS 15.x |
> | 华三 Comware | `vendors/h3c.py`（继承 VRP） | 视图 + `display` | S5560X 等（信创） |
> | 锐捷 RGOS | `vendors/ruijie.py`（继承 IOS） | 模式 + `show` | 交换机/路由器（信创） |
> | 华为 USG | `vendors/huawei_fw.py`（继承 VRP） | 视图 + 安全域/策略 | USG6000 防火墙（信创） |
> | 深信服 | `vendors/sangfor.py` | Web 风格 CLI | AF/AC 安全网关（信创） |
>
> 通用交互（`netsim/cli/session.py`）：
> - `?` 列出当前视图可用命令
> - Tab / 前缀缩写自动补全（如 `sh ip ro` = `show ip route`）
> - 视图导航：`quit`/`exit` 退一层、`return`/`end`/`Ctrl+Z` 直接回用户/特权视图
> - `ping` 在所有厂商中真实驱动模拟器数据面，可见逐跳转发结果

## 目录

- [华为 VRP5](huawei-vrp.md)
- [思科 IOS](cisco-ios.md)
- [华三 Comware](h3c-comware.md)
- [锐捷 RGOS](ruijie-rgos.md)
- [华为 USG 防火墙](huawei-usg.md)
- [深信服 AF](sangfor.md)

## 模拟器通用能力矩阵

所有厂商共享同一套仿真模型（`netsim/net/`），命令只是语法外壳：

| 能力 | 说明 | 验证命令 |
|---|---|---|
| 接口寻址 | 物理口 + 逻辑口（Vlanif/SVI/Loopback），access/trunk | `display ip routing-table` |
| 静态路由 | 最长前缀匹配 + 管理距离选路 | `ip route-static` / `ip route` |
| OSPF | 单域、Hello 1s/Dead 4s、LSA 泛洪、SPF | `display ospf peer` / `show ip ospf neighbor` |
| DHCP 服务 | 地址池按 VLAN 选择，租约分配 | `display dhcp server lease` |
| DHCP 客户端 | 主机 `ipconfig /renew` | 仅 Host 终端 |
| VRRP | 双机冗余，虚拟网关 | `display vrrp` / `show standby` |
| NAT | 源 NAT（overload） | `display nat session` |
| ACL | 入/出方向过滤 | `display acl all` |
| VLAN | access/trunk 透传 | `display vlan` / `show vlan brief` |
| ping | 真实 ICMP 往返 | 各厂商 `ping` |