# 深信服 AF 命令手册（模拟器版）

实现：`netsim/vendors/sangfor.py`　风格：Web 风格 CLI（`set`/`add`/`show`）

> 深信服 AF/AC 主要通过 Web 控制台管理，模拟器提供最小化命令行外壳，操作同一套仿真模型
> （接口 / 路由 / NAT / ACL 策略），因此策略在模拟器中真实生效。

## 视图

单一视图（提示符 `<设备名>`），无子模式。命令为全局式。

| 命令 | 说明 |
|---|---|
| `set interface <接口名> ip <IP> <掩码>` | 配置接口 IP（接口不存在时自动创建） |
| `set route <网段> <掩码> <下一跳>` | 静态路由 |
| `add policy permit\|deny [src <IP> <掩码>] [dst <IP> <掩码>] [icmp\|tcp\|udp]` | 安全策略 |
| `add nat <接口名>` | 在该接口启用源 NAT（inside 自动取其余有 IP 接口） |
| `show config` | 当前配置 |
| `show route` | 路由表 |
| `show session` | NAT 会话表 |
| `ping <IP>` | ICMP 测试（4 次） |
| `exit` | 断开 |

## 示例

```
<FW1>set interface GE1/0/0 ip 10.0.10.254 255.255.255.0
<FW1>set interface GE1/0/1 ip 203.0.113.1 255.255.255.252
<FW1>set route 0.0.0.0 0.0.0.0 203.0.113.2
<FW1>add policy permit src 10.0.10.0 255.255.255.0 dst any icmp
<FW1>add nat GE1/0/1
<FW1>show route
```

## 验证要点

- `add policy` 生成的规则以固定序号 100 挂到入方向（`netsim/vendors/sangfor.py::_add_policy`），
  不匹配即丢弃——策略收紧前/后的 `ping` 结果可验证。
- `show session` 只在配置了 `add nat` 且有流量时才显示翻译条目（`原始IP -> 转换IP`）。
- `show config` 输出可回放的 `set`/`add` 命令流。