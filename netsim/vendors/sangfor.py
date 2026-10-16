"""Sangfor firewall CLI vendor (信创 / domestic security).

Sangfor's AF/AC security gateways are primarily managed through a web UI, so
this vendor exposes a minimal, web-style CLI that mutates the same
vendor-neutral device model (interfaces, routes, NAT, ACL policy).
"""

from __future__ import annotations

from ..cli.session import Cmd, Session, Vendor, View
from ..net.ip import IP, Mask
from ..net.iface import Iface, LinkState, Route
from ..net.proto import ACLConfig, ACLRule, NATConfig


class Sangfor(Vendor):
    def name(self) -> str:
        return "Sangfor"

    def version_info(self) -> str:
        return "Sangfor AF-2000 V8.0.95 (simulated security gateway)"

    def new_session(self, device_id: str, device_name: str) -> Session:
        s = Session(device_id, device_name, self)
        tree = Cmd(name="")
        tree.add(Cmd(name="set", children=[
            Cmd(name="interface", help="Set interface IP.", min_args=4,
                handler=lambda s, a: self._set_interface(s, a)),
            Cmd(name="route", help="Set a static route.", min_args=3,
                handler=lambda s, a: self._set_route(s, a)),
        ]))
        tree.add(Cmd(name="add", children=[
            Cmd(name="policy", help="Add a security policy.", min_args=2,
                handler=lambda s, a: self._add_policy(s, a)),
            Cmd(name="nat", help="Enable source NAT on an interface.", min_args=2,
                handler=lambda s, a: self._add_nat(s, a)),
        ]))
        tree.add(Cmd(name="show", children=[
            Cmd(name="config", help="Show configuration.",
                handler=lambda s, a: self._show_config(s)),
            Cmd(name="route", help="Show routing table.",
                handler=lambda s, a: self._show_route(s)),
            Cmd(name="session", help="Show NAT/session table.",
                handler=lambda s, a: self._show_session(s)),
        ]))
        tree.add(Cmd(name="ping", help="Ping a remote host.", min_args=1,
                     handler=lambda s, a: self._ping(s, a)))
        tree.add(Cmd(name="exit", help="Exit.", handler=lambda s, a: s.p("  exit")))
        s.push(View(id="user", prompt=lambda s: f"<{s.device_name}>", tree=tree))
        return s

    def _set_interface(self, s: Session, args):
        if s.dev is None:
            return
        name = args[0]
        iface = s.dev.get_iface(name)
        if iface is None:
            iface = Iface(name=name, mac=bytes([0x00, 0x1f, 0x3a, 0xff, 0x00, len(s.dev.net.interfaces) + 1]))
            s.dev.add_iface(iface)
        ip = IP.parse(args[2])
        mask = Mask.parse(args[3])
        iface.ip = ip
        iface.mask = mask
        iface.state = LinkState.UP
        s.dev.net.routes.add(Route(prefix=ip.network(mask), mask=mask, iface=iface, proto="D", metric=0))
        s.p(f"  interface {name}: {ip}/{mask.prefix_len()}")

    def _set_route(self, s: Session, args):
        if s.dev is None:
            return
        net = IP.parse(args[0])
        mask = Mask.parse(args[1])
        nh = IP.parse(args[2])
        iface = None
        for i in s.dev.net.interfaces:
            if i.has_ip() and nh.network(i.mask) == i.network():
                iface = i
                break
        s.dev.net.routes.add(Route(prefix=net.network(mask), mask=mask, next_hop=nh,
                                   iface=iface, proto="S", metric=1))

    def _add_policy(self, s: Session, args):
        if s.dev is None:
            return
        action = args[0].lower()
        permit = action == "permit"
        src = dst = None
        proto = "any"
        i = 1
        while i < len(args):
            tok = args[i].lower()
            if tok == "src" and i + 2 < len(args):
                src = (IP.parse(args[i + 1]), Mask.parse(args[i + 2]))
                i += 3
            elif tok == "dst" and i + 2 < len(args):
                if args[i + 1].lower() == "any":
                    dst = None
                    i += 2
                else:
                    dst = (IP.parse(args[i + 1]), Mask.parse(args[i + 2]))
                    i += 3
            elif tok in ("icmp", "tcp", "udp"):
                proto = tok
                i += 1
            else:
                i += 1
        if s.dev.acl is None:
            s.dev.acl = ACLConfig()
        s.dev.acl.add(100, None, "in", ACLRule(id=10, permit=permit, src=src, dst=dst, proto=proto))
        s.p(f"  policy added: {'permit' if permit else 'deny'}")

    def _add_nat(self, s: Session, args):
        if s.dev is None:
            return
        iface = s.dev.get_iface(args[0])
        if iface is None:
            return
        inside = [i.name for i in s.dev.net.interfaces if i.has_ip() and i is not iface]
        s.dev.nat = NATConfig(inside, iface.name)
        s.p(f"  source NAT enabled on {iface.name}")

    def _show_route(self, s: Session):
        if s.dev is None:
            return
        s.p(f"{'Destination/Mask':>20} {'Gateway':<16} Interface")
        for r in s.dev.net.routes.sorted():
            gw = "Direct" if r.next_hop is None else str(r.next_hop)
            iface = r.iface.name if r.iface else ""
            s.p(f"{str(r.prefix):>19}/{r.mask.prefix_len()} {gw:<16} {iface}")

    def _show_session(self, s: Session):
        if s.dev is None:
            return
        if s.dev.nat is None:
            s.p("  no active sessions")
            return
        s.p("Active NAT sessions:")
        for orig, out in s.dev.nat.translations():
            s.p(f"  {orig} -> {out}")

    def _show_config(self, s: Session):
        if s.dev is None:
            return
        s.p(f"hostname {s.dev.name}")
        for i in s.dev.net.interfaces:
            if i.has_ip():
                s.p(f"set interface {i.name} ip {i.ip} {i.mask}")
        for r in s.dev.net.routes.sorted():
            if r.proto == "S":
                s.p(f"set route {r.prefix} {r.mask} {r.next_hop}")

    def _ping(self, s: Session, args):
        if s.world is None or s.dev is None:
            s.p("  ping: no simulation attached")
            return
        dst = args[0]
        s.p(f"PING {dst}: 56 data bytes")
        s.world.ping(
            s.dev, dst, 4,
            on_status=lambda seq, ok, detail: s.stream("  " + detail),
            on_done=lambda recv, cnt, loss: s.stream(
                f"  {cnt} packets transmitted, {recv} received, {loss:.0f}% loss"),
        )