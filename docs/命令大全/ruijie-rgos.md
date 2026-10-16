# 锐捷 RGOS 命令手册（模拟器版）

实现：`netsim/vendors/ruijie.py`（继承思科 IOS）　风格：模式 + `show`

> RGOS 与 IOS 高度同构，模拟器只改写差异项。**本文只列与 IOS 不同的命令/写法**，
> 其余全部命令见 [思科 IOS](cisco-ios.md)。

## 与 IOS 的差异

| 项目 | 思科 IOS | 锐捷 RGOS |
|---|---|---|
| 接口命名 | `GigabitEthernet0/1` | `GigabitEthernet 0/1`（**接口名带空格**，如 `interface GigabitEthernet 0/1`） |
| 冗余协议 | HSRP：`standby 10 ip 10.0.10.254` | VRRP：`vrrp 10 ip 10.0.10.254` |
| 接口提示符 | `SW(config-if)#` | `SW(config-GigabitEthernet 0/1)#`（**含接口名**） |
| SVI 渲染 | `interface Vlan10` | `interface vlan 10` |
| 冗余渲染 | `standby <gid> ip <vip>` | `vrrp <gid> ip <vip>` |
| 版本 | IOS 15.2 | RGOS 10.3(4b12) |

其余（`enable`/`configure terminal`/`show *`/`switchport`/`ip route`/`router ospf`/`access-list` 等）与 IOS 相同。

## 冗余组配置示例

```
设备名(config-if)#vrrp 10 ip 10.0.10.254
设备名(config-if)#vrrp 10 priority 110
设备名(config-if)#show vrrp
```

## 验证要点

- 提示符 `(config-接口名)#` 用于确认当前配置对象。
- `show vrrp` 显示 VRRP 组状态（Master/Backup）与虚拟 IP、优先级。
- `show running-config` 以 `interface vlan 10` 渲染 SVI、`vrrp <gid> ip <vip>` 渲染冗余组。