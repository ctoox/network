"""Huawei USG firewall CLI vendor (信创 / domestic security).

Subclasses the Huawei VRP vendor and adds security-zone + security-policy
configuration. Policies are translated into the vendor-neutral ACL engine, so
zone-constrained traffic is actually enforced in the simulation.
"""

from __future__ import annotations

from ..cli.session import Cmd, Session, View
from ..net.ip import IP, Mask
from ..net.proto import ACLConfig, ACLRule
from .huawei import VRP, _mask_from_wildcard


class HuaweiUSG(VRP):
    def name(self) -> str:
        return "USG"

    def version_info(self) -> str:
        return (
            "Huawei USG6000 V500R001C20SPC200\n"
            "Copyright (C) 2012-2020 Huawei Technologies Co., Ltd."
        )

    def new_session(self, device_id: str, device_name: str) -> Session:
        s = Session(device_id, device_name, self)
        user_tree = Cmd(name="")
        self._user_commands(s, user_tree)
        s.push(View(id="user", prompt=lambda s: f"<{s.device_name}>", tree=user_tree))
        return s

    def _system_tree(self, s: Session) -> Cmd:
        tree = super()._system_tree(s)
        tree.add(Cmd(name="firewall", children=[
            Cmd(name="zone", help="Enter firewall zone view.", min_args=1,
                handler=lambda s, a: self._enter_zone(s, a[0])),
        ]))
        tree.add(Cmd(name="security-policy", help="Enter security policy view.",
                     handler=lambda s, a: self._enter_security_policy(s)))
        return tree

    def _zone_tree(self, s: Session, zone: str) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="add", children=[
            Cmd(name="interface", help="Bind an interface to this zone.", min_args=1,
                handler=lambda s, a: self._zone_add_iface(s, zone, a[0])),
        ]))
        tree.add(Cmd(name="quit", help="Return to system view.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: s.return_view()))
        return tree

    def _policy_tree(self, s: Session) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="rule", help="Enter a security rule.", min_args=2,
                     handler=lambda s, a: self._enter_rule(s, a[1])))
        tree.add(Cmd(name="quit", help="Return to system view.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: s.return_view()))
        return tree

    def _rule_tree(self, s: Session, rule: str) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="source-zone", help="Set source zone.", min_args=1,
                     handler=lambda s, a: self._rule_src_zone(s, rule, a[0])))
        tree.add(Cmd(name="destination-zone", help="Set destination zone.", min_args=1,
                     handler=lambda s, a: self._rule_dst_zone(s, rule, a[0])))
        tree.add(Cmd(name="source-address", help="Set source address.", min_args=2,
                     handler=lambda s, a: self._rule_src_addr(s, rule, a[0], a[1])))
        tree.add(Cmd(name="destination-address", help="Set destination address.", min_args=2,
                     handler=lambda s, a: self._rule_dst_addr(s, rule, a[0], a[1])))
        tree.add(Cmd(name="service", help="Set service.", min_args=1,
                     handler=lambda s, a: self._rule_service(s, rule, a[0])))
        tree.add(Cmd(name="action", help="Set action.", min_args=1,
                     handler=lambda s, a: self._rule_action(s, rule, a[0])))
        tree.add(Cmd(name="quit", help="Return to policy view.",
                     handler=lambda s, a: (self._rule_commit(s, rule), s.pop())))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: (self._rule_commit(s, rule), s.return_view())))
        return tree

    def _enter_zone(self, s: Session, zone: str):
        if s.dev is not None:
            s.dev._zones = getattr(s.dev, "_zones", {})
            s.dev._zones.setdefault(zone, [])
        s.p("  Enter firewall zone view.")
        s.push(View(id="zone", prompt=lambda s: f"[{s.device_name}-zone-{zone}]",
                    tree=self._zone_tree(s, zone)))

    def _zone_add_iface(self, s: Session, zone: str, iface: str):
        if s.dev is None:
            return
        zones = getattr(s.dev, "_zones", {})
        for z, ifs in zones.items():
            if iface in ifs:
                ifs.remove(iface)
        zones.setdefault(zone, []).append(iface)
        s.dev._zones = zones
        s.p(f"  Interface {iface} added to zone {zone}.")

    def _enter_security_policy(self, s: Session):
        if s.dev is not None:
            s.dev._policy = getattr(s.dev, "_policy", {})
        s.push(View(id="policy", prompt=lambda s: f"[{s.device_name}-policy-security]",
                    tree=self._policy_tree(s)))

    def _enter_rule(self, s: Session, rule: str):
        if s.dev is not None:
            s.dev._policy.setdefault(rule, {})
        s.p(f"  Info: rule named {rule} created.")
        s.push(View(id="rule", prompt=lambda s: f"[{s.device_name}-policy-security-rule-{rule}]",
                    tree=self._rule_tree(s, rule)))

    def _rule_src_zone(self, s: Session, rule: str, zone: str):
        s.dev._policy[rule]["src_zone"] = zone

    def _rule_dst_zone(self, s: Session, rule: str, zone: str):
        s.dev._policy[rule]["dst_zone"] = zone

    def _rule_src_addr(self, s: Session, rule: str, ip: str, mask: str):
        s.dev._policy[rule]["src"] = (IP.parse(ip), Mask.parse(mask))

    def _rule_dst_addr(self, s: Session, rule: str, ip: str, mask: str):
        if ip.lower() == "any":
            s.dev._policy[rule]["dst"] = None
        else:
            s.dev._policy[rule]["dst"] = (IP.parse(ip), Mask.parse(mask))

    def _rule_service(self, s: Session, rule: str, svc: str):
        s.dev._policy[rule]["service"] = svc.lower()

    def _rule_action(self, s: Session, rule: str, action: str):
        s.dev._policy[rule]["action"] = action.lower()

    def _rule_commit(self, s: Session, rule: str):
        """Translate the rule into ACL rules bound to source-zone interfaces."""
        if s.dev is None:
            return
        cfg = s.dev._policy.get(rule, {})
        action = cfg.get("action", "permit")
        proto = cfg.get("service", "any")
        if proto not in ("any", "icmp", "tcp", "udp"):
            proto = "any"
        rule_obj = ACLRule(id=10, permit=action == "permit",
                           src=cfg.get("src"), dst=cfg.get("dst"), proto=proto)
        src_zone = cfg.get("src_zone", "untrust")
        zones = getattr(s.dev, "_zones", {})
        ifaces = zones.get(src_zone, [])
        if s.dev.acl is None:
            s.dev.acl = ACLConfig()
        for iface in ifaces:
            s.dev.acl.add(10, iface, "in", rule_obj)
        s.p(f"  Policy {rule} committed: {action} from zone {src_zone}.")

    def _display_current(self, s: Session):
        super()._display_current(s)
        if getattr(s.dev, "_zones", None):
            for zone, ifaces in s.dev._zones.items():
                s.p(f"firewall zone {zone}")
                for i in ifaces:
                    s.p(f" add interface {i}")
                s.p("quit")
        if getattr(s.dev, "_policy", None):
            s.p("security-policy")
            for name, cfg in s.dev._policy.items():
                s.p(f" rule name {name}")
                s.p(f"  action {'permit' if cfg.get('action') == 'permit' else 'deny'}")
                s.p("  quit")
            s.p("quit")