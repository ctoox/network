# 华为 USG 防火墙命令手册（模拟器版）

实现：`netsim/vendors/huawei_fw.py`（继承华为 VRP）　风格：视图 + `display` + 安全域

> USG 在 VRP5 全部命令之上，增加**安全域（firewall zone）**与**安全策略（security-policy）**。
> 策略在下发时被翻译成厂商无关的 ACL 引擎，**在模拟器中真实生效**（不匹配即丢弃）。

## 新增视图

```
[设备名]
 firewall zone untrust
[设备名-zone-untrust]        安全域视图
 security-policy
[设备名-policy-security]     安全策略视图
 rule name r1
[设备名-policy-security-rule-r1]  策略规则视图
```

## 安全域视图

| 命令 | 说明 |
|---|---|
| `firewall zone <域名>` | 进入安全域视图 |
| `add interface <接口名>` | 把接口加入该域（自动从其它域移除） |
| `quit` / `return` | 退出 |

## 安全策略视图

| 命令 | 说明 |
|---|---|
| `security-policy` | 进入策略视图 |
| `rule name <规则名>` | 创建规则并进入规则视图 |
| `quit` | 返回策略视图 |

## 策略规则视图

| 命令 | 说明 |
|---|---|
| `source-zone <域名>` | 源安全域 |
| `destination-zone <域名>` | 目的安全域 |
| `source-address <IP> <掩码>` | 源地址 |
| `destination-address <IP> <掩码>` | 目的地址（`any` 表示不限制） |
| `service <any\|icmp\|tcp\|udp>` | 服务 |
| `action permit\|deny` | 动作 |
| `quit` | 退出并**提交**（翻译为 ACL 绑到源域接口的入方向） |

> 提交逻辑：`action=permit` 且 `service` 归一化，生成一条 ACL 规则绑定到**源安全域全部接口**的入方向
> （`netsim/vendors/huawei_fw.py::_rule_commit`）。数据面在 `netsim/net/world.py` 入口 ACL 处强制检查。

## 完整示例（对应 `build_smb` / `build_xinchuang` 防火墙）

```
firewall zone untrust
 add interface GE1/0/1
quit
firewall zone trust
 add interface GE1/0/0
quit
security-policy
 rule name internet
  source-zone trust
  destination-zone untrust
  action permit
  quit
quit
interface GE1/0/0
 ip address 10.0.10.254 255.255.255.0
quit
interface GE1/0/1
 ip address 203.0.113.1 255.255.255.252
quit
ip route-static 0.0.0.0 0 203.0.113.2
```

## 验证要点

- `display current-configuration` 会在 VRP 配置后追加 `firewall zone` 与 `security-policy` 段。
- 策略未放行的流量在数据面被 ACL 丢弃，可用 `ping` 实测（策略 permit 之前不通、之后通）。
- NAT 仍用 VRP 的 `nat outbound <acl>`（inside/outside 自动推断）。