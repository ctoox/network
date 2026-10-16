"""Cisco IOS CLI vendor (reference implementation; Ruijie RGOS subclasses it).

Covers: interfaces, static routes, VLAN / SVI, OSPF, DHCP server, HSRP,
NAT, ACL, and the usual show commands. All configuration mutates the
vendor-neutral device model so every feature works inside the simulation.
"""

from __future__ import annotations

from ..cli.session import Cmd, Session, Vendor, View
from ..net.ip import IP, Mask
from ..net.iface import Iface, LinkState, Route
from ..net.proto import ACLConfig, ACLRule, DHCPPool, NATConfig, OSPFInstance, VRRPConfig


def _mask_from_wildcard(wildcard: str) -> Mask:
    return Mask((~IP.parse(wildcard).value) & 0xFFFFFFFF)


class IOS(Vendor):
    #: join separator for multi-token interface names ("GigabitEthernet 0/0")
    iface_sep = ""
    #: redundancy keyword: "standby" (IOS HSRP) vs "vrrp" (RGOS)
    hsrp_keyword = "standby"

    def name(self) -> str:
        return "IOS"

    def version_info(self) -> str:
        return (
            "Cisco IOS Software, C7200 Software (C7200-ADVENTERPRISEK9-M), Version 15.2(4)M7, RELEASE SOFTWARE\n"
            "Technical Support: http://www.cisco.com/techsupport"
        )

    # ---- prompt helpers (overridable by subclasses) ----

    def _user_prompt(self, s):
        return f"{s.device_name}>"

    def _priv_prompt(self, s):
        return f"{s.device_name}#"

    def _config_prompt(self, s):
        return f"{s.device_name}(config)#"

    def _iface_prompt(self, s, name):
        return f"{s.device_name}(config-if)#"

    def new_session(self, device_id: str, device_name: str) -> Session:
        s = Session(device_id, device_name, self)
        user_tree = Cmd(name="")
        self._user_commands(s, user_tree)
        s.push(View(id="user", prompt=lambda s: self._user_prompt(s), tree=user_tree))
        return s

    # ---- interface name canonicalisation ----

    def _canon_iface(self, args) -> tuple:
        """Return (canonical_name, is_vlanif, vlan_id)."""
        if not args:
            return None, False, 0
        if args[0].lower() in ("vlan", "vlanif", "vlan-interface"):
            if len(args) >= 2 and args[1].isdigit():
                return f"Vlan{args[1]}", True, int(args[1])
        joined = self.iface_sep.join(args)
        low = joined.lower()
        if low.startswith("vlan") and low[4:].isdigit():
            return f"Vlan{low[4:]}", True, int(low[4:])
        if low.startswith(("loopback", "lo")):
            return f"Loopback{low[len('loopback') if low.startswith('loopback') else 2:] or 0}", False, 0
        return joined, False, 0

    # ---- command registration ----

    def _user_commands(self, s: Session, tree: Cmd):
        tree.add(Cmd(name="enable", help="Turn on privileged commands.",
                     handler=lambda s, a: s.push(self._priv_view(s))))
        tree.add(Cmd(name="ping", help="Send echo messages.", min_args=1,
                     handler=lambda s, a: self._ping(s, a)))
        tree.add(Cmd(name="show", aliases=["sh"], children=[
            Cmd(name="version", help="Show version information.",
                handler=lambda s, a: s.p(s.vendor.version_info())),
            Cmd(name="ip", children=[
                Cmd(name="route", help="Show IP routing table.",
                    handler=lambda s, a: self._show_route(s)),
            ]),
            Cmd(name="arp", help="Show ARP table.", handler=lambda s, a: self._show_arp(s)),
            Cmd(name="interfaces", help="Show interface details.", min_args=1,
                handler=lambda s, a: self._show_interface(s, self.iface_sep.join(a))),
            Cmd(name="running-config", help="Show running configuration.",
                handler=lambda s, a: self._show_running(s)),
        ]))
        tree.add(Cmd(name="traceroute", aliases=["trace"], help="Trace route to destination.", min_args=1,
                     handler=lambda s, a: s.p("  traceroute: not yet implemented")))
        tree.add(Cmd(name="quit", help="Exit.", handler=lambda s, a: s.p("  Connection closed by foreign host.")))

    def _priv_view(self, s: Session) -> View:
        tree = Cmd(name="")
        tree.add(Cmd(name="configure", aliases=["conf"], children=[
            Cmd(name="terminal", aliases=["t"], help="Configure from terminal.",
                handler=lambda s, a: (s.p("Enter configuration commands, one per line.  End with CNTL/Z."),
                                      s.push(self._config_view(s)))),
        ]))
        tree.add(Cmd(name="show", aliases=["sh"], children=[
            Cmd(name="running-config", help="Show running configuration.",
                handler=lambda s, a: self._show_running(s)),
            Cmd(name="ip", children=[
                Cmd(name="route", help="Show IP routing table.",
                    handler=lambda s, a: self._show_route(s)),
                Cmd(name="interface", help="Show interface summary.",
                    handler=lambda s, a: self._show_brief(s)),
                Cmd(name="nat", children=[
                    Cmd(name="translations", help="Show NAT translations.",
                        handler=lambda s, a: self._show_nat(s)),
                ]),
                Cmd(name="dhcp", children=[
                    Cmd(name="binding", help="Show DHCP bindings.",
                        handler=lambda s, a: self._show_dhcp_binding(s)),
                ]),
                Cmd(name="ospf", children=[
                    Cmd(name="neighbor", help="Show OSPF neighbors.",
                        handler=lambda s, a: self._show_ospf_neighbor(s)),
                    Cmd(name="database", help="Show OSPF database.",
                        handler=lambda s, a: self._show_ospf_database(s)),
                ]),
            ]),
            Cmd(name="vlan", children=[
                Cmd(name="brief", help="Show VLAN summary.",
                    handler=lambda s, a: self._show_vlan_brief(s)),
            ]),
            Cmd(name="interfaces", children=[
                Cmd(name="status", help="Show interface status.",
                    handler=lambda s, a: self._show_brief(s)),
            ]),
            Cmd(name="access-lists", help="Show access lists.",
                handler=lambda s, a: self._show_acl(s)),
            Cmd(name="standby", help="Show HSRP status.",
                handler=lambda s, a: self._show_redundancy(s)),
            Cmd(name="vrrp", help="Show VRRP status.",
                handler=lambda s, a: self._show_redundancy(s)),
            Cmd(name="arp", help="Show ARP table.", handler=lambda s, a: self._show_arp(s)),
            Cmd(name="version", help="Show version information.",
                handler=lambda s, a: s.p(s.vendor.version_info())),
        ]))
        tree.add(Cmd(name="ping", help="Send echo messages.", min_args=1,
                     handler=lambda s, a: self._ping(s, a)))
        tree.add(Cmd(name="disable", help="Turn off privileged commands.",
                     handler=lambda s, a: s.pop()))
        return View(id="privileged", prompt=lambda s: self._priv_prompt(s), tree=tree)

    def _config_view(self, s: Session) -> View:
        tree = Cmd(name="")
        tree.add(Cmd(name="hostname", help="Set the system hostname.", min_args=1,
                     handler=lambda s, a: self._hostname(s, a[0])))
        tree.add(Cmd(name="interface", help="Enter interface configuration mode.", min_args=1,
                     handler=lambda s, a: self._enter_iface(s, a)))
        tree.add(Cmd(name="ip", children=[
            Cmd(name="route", help="Establish static routes.", min_args=3,
                handler=lambda s, a: self._static_route(s, a)),
            Cmd(name="dhcp", children=[
                Cmd(name="pool", help="Create a DHCP pool.", min_args=1,
                    handler=lambda s, a: self._enter_dhcp_pool(s, a[0])),
            ]),
            Cmd(name="nat", children=[
                Cmd(name="inside", children=[
                    Cmd(name="source", help="Configure source NAT.", min_args=4,
                        handler=lambda s, a: self._nat_source(s, a)),
                ]),
            ]),
        ]))
        tree.add(Cmd(name="service", children=[
            Cmd(name="dhcp", help="Enable DHCP service.",
                handler=lambda s, a: self._dhcp_service(s)),
        ]))
        tree.add(Cmd(name="router", children=[
            Cmd(name="ospf", help="Enter OSPF configuration.", min_args=0,
                handler=lambda s, a: self._enter_ospf(s, int(a[0]) if a else 1)),
        ]))
        tree.add(Cmd(name="vlan", help="Create a VLAN.", min_args=1,
                     handler=lambda s, a: self._create_vlan(s, a)))
        tree.add(Cmd(name="access-list", help="Configure an access-list.", min_args=2,
                     handler=lambda s, a: self._access_list(s, a)))
        tree.add(Cmd(name="end", help="Exit to privileged mode.",
                     handler=lambda s, a: s.return_view()))
        tree.add(Cmd(name="exit", help="Exit to privileged mode.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="do", help="Run a privileged command from config mode.", min_args=1,
                     handler=lambda s, a: s.run_as(self._priv_view(s).tree, " ".join(a))))
        return View(id="config", prompt=lambda s: self._config_prompt(s), tree=tree)

    def _iface_config_view(self, s: Session, name: str) -> View:
        tree = Cmd(name="")
        tree.add(Cmd(name="ip", children=[
            Cmd(name="address", help="Set interface IP address.", min_args=2,
                handler=lambda s, a: self._ip_address(s, name, a[0], a[1])),
            Cmd(name="nat", children=[
                Cmd(name="inside", help="Mark interface as NAT inside.",
                    handler=lambda s, a: self._nat_mark(s, name, "inside")),
                Cmd(name="outside", help="Mark interface as NAT outside.",
                    handler=lambda s, a: self._nat_mark(s, name, "outside")),
            ]),
            Cmd(name="access-group", help="Apply an ACL to the interface.", min_args=2,
                handler=lambda s, a: self._ip_access_group(s, name, a[0], a[1])),
        ]))
        tree.add(Cmd(name="switchport", children=[
            Cmd(name="mode", help="Set switchport mode.", min_args=1,
                handler=lambda s, a: self._sw_mode(s, name, a[0])),
            Cmd(name="access", children=[
                Cmd(name="vlan", help="Set access VLAN.", min_args=1,
                    handler=lambda s, a: self._sw_access_vlan(s, name, int(a[0]))),
            ]),
            Cmd(name="trunk", children=[
                Cmd(name="allowed", children=[
                    Cmd(name="vlan", help="Set allowed VLANs on trunk.", min_args=1,
                        handler=lambda s, a: self._sw_trunk_allowed(s, name, a[0])),
                ]),
                Cmd(name="native", children=[
                    Cmd(name="vlan", help="Set trunk native VLAN.", min_args=1,
                        handler=lambda s, a: self._sw_trunk_native(s, name, int(a[0]))),
                ]),
            ]),
        ]))
        tree.add(Cmd(name="no", children=[
            Cmd(name="shutdown", help="Bring interface up.",
                handler=lambda s, a: self._shutdown(s, name, False)),
            Cmd(name="ip", children=[
                Cmd(name="address", help="Remove interface IP.",
                    handler=lambda s, a: self._unset_ip(s, name)),
            ]),
        ]))
        tree.add(Cmd(name="shutdown", help="Shutdown interface.",
                     handler=lambda s, a: self._shutdown(s, name, True)))
        tree.add(Cmd(name="exit", help="Return to global config mode.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="end", help="Exit to privileged mode.",
                     handler=lambda s, a: s.return_view()))
        tree.add(Cmd(name="do", help="Run a privileged command.", min_args=1,
                     handler=lambda s, a: s.run_as(self._priv_view(s).tree, " ".join(a))))
        tree.add(Cmd(name=self.hsrp_keyword, help="Configure HSRP/VRRP redundancy.",
                     min_args=2, handler=lambda s, a: self._redundancy(s, name, a)))
        return View(id="iface-config", prompt=lambda s: self._iface_prompt(s, name), tree=tree)

    def _ospf_view(self, s: Session, proc: int) -> View:
        tree = Cmd(name="")
        tree.add(Cmd(name="network", help="Enable OSPF on a network.", min_args=4,
                     handler=lambda s, a: self._ospf_network(s, proc, a[0], a[1], a[3])))
        tree.add(Cmd(name="exit", help="Return to config mode.", handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="end", help="Exit to privileged mode.",
                     handler=lambda s, a: s.return_view()))
        tree.add(Cmd(name="do", help="Run a privileged command.", min_args=1,
                     handler=lambda s, a: s.run_as(self._priv_view(s).tree, " ".join(a))))
        return View(id="ospf", prompt=lambda s: f"{s.device_name}(config-router)#", tree=tree)

    def _dhcp_view(self, s: Session, name: str) -> View:
        tree = Cmd(name="")
        tree.add(Cmd(name="network", help="Set pool subnet.", min_args=2,
                     handler=lambda s, a: self._dhcp_network(s, name, a[0], a[1])))
        tree.add(Cmd(name="default-router", help="Set pool gateway.", min_args=1,
                     handler=lambda s, a: self._dhcp_gateway(s, name, a[0])))
        tree.add(Cmd(name="dns-server", help="Set pool DNS server.", min_args=1,
                     handler=lambda s, a: self._dhcp_dns(s, name, a[0])))
        tree.add(Cmd(name="exit", help="Return to config mode.", handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="end", help="Exit to privileged mode.",
                     handler=lambda s, a: s.return_view()))
        return View(id="dhcp", prompt=lambda s: f"{s.device_name}(config-dhcp)#", tree=tree)

    # ---- handlers ----

    def _hostname(self, s: Session, name: str):
        s.device_name = name
        if s.dev is not None:
            s.dev.name = name

    def _enter_iface(self, s: Session, args):
        name, is_vlanif, vlan_id = self._canon_iface(args)
        if name is None:
            return
        if s.dev is not None:
            iface = s.dev.get_iface(name)
            if iface is None:
                if is_vlanif:
                    iface = Iface(name=name, is_virtual=True, access_vlan=vlan_id,
                                  mac=bytes([0x00, 0x50, 0x14, 0xff, 0x00, vlan_id]))
                    s.dev.add_iface(iface)
                elif name.lower().startswith("loopback"):
                    iface = Iface(name=name, is_loopback=True,
                                  mac=bytes([0x00, 0x50, 0x14, 0xff, 0xfe, 0x01]))
                    s.dev.add_iface(iface)
                else:
                    s.p("  Invalid input detected")
                    return
        s.push(self._iface_config_view(s, name))

    def _ip_address(self, s: Session, name: str, ip_str: str, mask_str: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            s.p("  Invalid input detected")
            return
        try:
            ip = IP.parse(ip_str)
            mask = Mask.parse(mask_str)
        except ValueError:
            s.p("  Invalid input detected")
            return
        iface.ip = ip
        iface.mask = mask
        iface.state = LinkState.UP
        s.dev.net.routes.add(Route(prefix=ip.network(mask), mask=mask, iface=iface, proto="D", metric=0))
        if s.dev.ospf is not None and s.dev.ospf.running:
            s.dev.ospf.on_iface_change(s.world, iface)

    def _unset_ip(self, s: Session, name: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is not None:
            iface.ip = None
            iface.mask = None

    def _shutdown(self, s: Session, name: str, down: bool):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            return
        iface.state = LinkState.DOWN if down else LinkState.UP
        s.p(f"  %LINK-5-CHANGED: Interface {name}, changed state to "
            + ("administratively down" if down else "up"))
        if s.dev.ospf is not None and s.dev.ospf.running and iface.has_ip():
            s.dev.ospf.on_iface_change(s.world, iface)
        if s.world is not None:
            s.world.refresh_vrrp()

    def _sw_mode(self, s: Session, name: str, mode: str):
        iface = s.dev.get_iface(name) if s.dev else None
        if iface is None or iface.is_virtual:
            return
        iface.mode = "trunk" if mode.lower() == "trunk" else "access"

    def _sw_access_vlan(self, s: Session, name: str, vlan: int):
        iface = s.dev.get_iface(name) if s.dev else None
        if iface is None or iface.is_virtual:
            return
        iface.access_vlan = vlan
        s.dev.vlans.add(vlan)

    def _sw_trunk_allowed(self, s: Session, name: str, vlan: str):
        iface = s.dev.get_iface(name) if s.dev else None
        if iface is None or iface.is_virtual:
            return
        iface.mode = "trunk"
        if vlan.lower() == "all":
            iface.trunk_vlans = set(range(1, 4095))
        else:
            for v in vlan.split(","):
                if v.isdigit():
                    iface.trunk_vlans.add(int(v))
        s.dev.vlans.update(iface.trunk_vlans)

    def _sw_trunk_native(self, s: Session, name: str, vlan: int):
        iface = s.dev.get_iface(name) if s.dev else None
        if iface is not None:
            iface.access_vlan = vlan

    def _create_vlan(self, s: Session, args):
        if s.dev is None:
            return
        for tok in args:
            if tok.isdigit():
                s.dev.vlans.add(int(tok))
        s.p("  Switch(config)#vlan " + " ".join(args))

    def _access_list(self, s: Session, args):
        if s.dev is None:
            return
        try:
            acl_id = int(args[0])
        except ValueError:
            return
        action = args[1].lower()
        permit = action.startswith("permit")
        rest = args[2:]
        proto = "any"
        src = dst = None
        if rest and rest[0].lower() in ("ip", "icmp", "tcp", "udp"):
            proto = "any" if rest[0].lower() == "ip" else rest[0].lower()
            rest = rest[1:]
        if len(rest) >= 2:
            try:
                src = (IP.parse(rest[0]), _mask_from_wildcard(rest[1]))
            except ValueError:
                pass
        if len(rest) >= 4:
            try:
                dst = (IP.parse(rest[2]), _mask_from_wildcard(rest[3]))
            except ValueError:
                pass
        s.dev.acl_rules.setdefault(acl_id, []).append(
            ACLRule(id=len(s.dev.acl_rules.get(acl_id, [])) + 10, permit=permit,
                    src=src, dst=dst, proto=proto))

    def _ip_access_group(self, s: Session, name: str, acl_id: str, direction: str):
        if s.dev is None:
            return
        rules = s.dev.acl_rules.get(int(acl_id), [])
        if s.dev.acl is None:
            s.dev.acl = ACLConfig()
        for r in rules:
            s.dev.acl.add(r.id, name, direction, r)
        s.p(f"  Applied access-list {acl_id} {direction}bound on {name}")

    def _nat_mark(self, s: Session, name: str, side: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            return
        if side == "outside":
            inside = [i.name for i in s.dev.net.interfaces if i.has_ip() and i is not iface]
            s.dev.nat = NATConfig(inside, name)
        s.p(f"  Interface {name} marked as NAT {side}")

    def _nat_source(self, s: Session, args):
        if s.dev is None:
            return
        # ip nat inside source list <acl> interface <iface> overload
        try:
            acl_id = int(args[2])
        except (ValueError, IndexError):
            return
        iface_parts = args[4:-1] if args[-1].lower() == "overload" else args[4:]
        if not iface_parts:
            return
        iface_name, _is_vlanif, _vlan = self._canon_iface(iface_parts)
        target = s.dev.get_iface(iface_name)
        if target is None:
            return
        inside = [i.name for i in s.dev.net.interfaces if i.has_ip() and i is not target]
        s.dev.nat = NATConfig(inside, target.name)
        s.p(f"  Source NAT list {acl_id} via {target.name} (overload)")

    def _redundancy(self, s: Session, name: str, args):
        if s.dev is None or s.world is None:
            return
        gid = int(args[0])
        sub = args[1]
        if sub == "ip":
            if len(args) < 3:
                return
            if s.dev.vrrp is None:
                s.dev.vrrp = VRRPConfig(s.dev)
            s.dev.vrrp.add(gid, name, IP.parse(args[2]))
            s.world.register_vrrp(s.dev, gid)
            s.world.refresh_vrrp()
        elif sub == "priority":
            if len(args) < 3 or s.dev.vrrp is None or gid not in s.dev.vrrp.groups:
                return
            s.dev.vrrp.groups[gid].priority = int(args[2])
            s.world.refresh_vrrp()

    def _enter_ospf(self, s: Session, proc: int):
        if s.dev is None or s.world is None:
            return
        if s.dev.ospf is None or s.dev.ospf.proc_id != proc:
            s.dev.ospf = OSPFInstance(s.dev, proc).enable(s.world, process_id=proc)
        s.push(self._ospf_view(s, proc))

    def _ospf_network(self, s: Session, proc: int, ip_str: str, wildcard: str, area: str):
        if s.dev is None or s.dev.ospf is None:
            return
        s.dev.ospf.add_network(IP.parse(ip_str), _mask_from_wildcard(wildcard))
        if s.world is not None:
            s.dev.ospf.on_iface_change(s.world, None)

    def _dhcp_service(self, s: Session):
        if s.dev is not None:
            s.dev._dhcp_enabled = True

    def _enter_dhcp_pool(self, s: Session, name: str):
        if s.dev is not None and getattr(s.dev, "_dhcp_pool", None) is None:
            s.dev._dhcp_pool = DHCPPool(IP(0), Mask(0), IP(0), IP(0), name=name)
            s.dev.dhcp = s.dev._dhcp_pool  # serve on all segments (vlan 0)
        s.push(self._dhcp_view(s, name))

    def _dhcp_network(self, s: Session, name: str, ip_str: str, mask_str: str):
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is None:
            return
        net = IP.parse(ip_str)
        mask = Mask.parse(mask_str)
        pool.subnet = net
        pool.mask = mask
        pool.start_val = net.value + 1
        pool.end_val = net.broadcast(mask).value - 1
        pool._next = pool.start_val

    def _dhcp_gateway(self, s: Session, name: str, gw: str):
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is not None:
            pool.gateway = IP.parse(gw)

    def _dhcp_dns(self, s: Session, name: str, dns: str):
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is not None:
            pool.dns = IP.parse(dns)

    def _static_route(self, s: Session, args):
        if s.dev is None:
            return
        try:
            prefix = IP.parse(args[0])
            mask = Mask.parse(args[1])
            nh = IP.parse(args[2])
        except ValueError:
            s.p("  Invalid input detected")
            return
        iface = None
        for i in s.dev.net.interfaces:
            if i.has_ip() and not i.is_loopback and nh.network(i.mask) == i.network():
                iface = i
                break
        s.dev.net.routes.add(Route(prefix=prefix.network(mask), mask=mask, next_hop=nh,
                                   iface=iface, proto="S", metric=1))

    def _ping(self, s: Session, args):
        if s.world is None or s.dev is None:
            s.p("  ping: no simulation attached")
            return
        dst = args[0]
        count = 5
        s.p("Type escape sequence to abort.")
        s.p(f"Sending {count}, 100-byte ICMP Echos to {dst}, timeout is 2 seconds:")
        received = [0]
        s.world.ping(
            s.dev, dst, count,
            on_status=lambda seq, ok, detail: (
                s.stream("!!!!!" if ok else "....."),
                received.__setitem__(0, received[0] + 1) if ok else None),
            on_done=lambda recv, cnt, loss: (
                s.stream(f"Success rate is {100 - loss:.0f} percent ({recv}/{cnt})")),
        )

    # ---- show handlers ----

    def _show_route(self, s: Session):
        if s.dev is None:
            return
        s.p("Codes: C - connected, S - static, I - IGRP, R - RIP, M - mobile, B - BGP")
        s.p("       D - EIGRP, EX - EIGRP external, O - OSPF, IA - OSPF inter area")
        s.p("       E1 - OSPF external type 1, E2 - OSPF external type 2")
        for r in s.dev.net.routes.sorted():
            code = {"S": "S", "D": "C", "O": "O", "B": "B"}.get(r.proto, "?")
            via = "is directly connected"
            via_iface = r.iface.name if r.iface else ""
            if r.next_hop is not None:
                via = f"via {r.next_hop}"
            s.p(f"{code:>2}    {r.prefix}/{r.mask.prefix_len()} [{r.metric}/{r.metric}] {via}, {via_iface}")

    def _show_arp(self, s: Session):
        if s.dev is None:
            return
        s.p("Protocol  Address          Age (min)  Hardware Addr   Type   Interface")
        for e in s.dev.net.arp.all():
            mac = ".".join(f"{e.mac[i]:02x}{e.mac[i+1]:02x}" for i in range(0, 6, 2))
            s.p(f"Internet  {str(e.ip):<15} {0:>6}  {mac}  ARPA  {e.iface.name if e.iface else ''}")

    def _show_brief(self, s: Session):
        if s.dev is None:
            return
        s.p("Interface                  IP-Address      OK? Method Status                Protocol")
        for i in s.dev.net.interfaces:
            if i.is_virtual:
                continue
            status = "up" if (i.state == LinkState.UP and i.has_ip()) else "administratively down"
            proto = "up" if status == "up" else "down"
            ip = "unassigned" if not i.has_ip() else str(i.ip)
            s.p(f"{i.name:<25} {ip:<15} YES NVRAM {status:<20} {proto}")

    def _show_interface(self, s: Session, name: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            s.p("  Invalid input detected")
            return
        st = "up" if iface.state == LinkState.UP else "down"
        s.p(f"{iface.name} is {st}, line protocol is {st}")
        if iface.has_ip():
            s.p(f"  Internet address is {iface.ip}/{iface.mask.prefix_len()}")

    def _show_vlan_brief(self, s: Session):
        if s.dev is None:
            return
        s.p("VLAN Name                             Status    Ports")
        for v in sorted(s.dev.vlans):
            s.p(f"{v:<5} VLAN{v:<30} active")
        for i in s.dev.net.interfaces:
            if i.is_virtual:
                continue
            if i.is_trunk():
                s.p(f"  Trunk {i.name}: PVID {i.access_vlan}, allowed "
                    + ('all' if len(i.trunk_vlans) > 4000 else ','.join(map(str, sorted(i.trunk_vlans)))))
            else:
                s.p(f"  Access {i.name}: VLAN {i.access_vlan}")

    def _show_ospf_neighbor(self, s: Session):
        if s.dev is None or s.dev.ospf is None:
            s.p("  OSPF is not running.")
            return
        s.p(f"Neighbor ID     Pri   State           Address         Interface")
        for n in s.dev.ospf.neighbor_table():
            s.p(f"{n['id']:<15} 1    FULL/  -        {n['ip']:<15} {n['iface']}")

    def _show_ospf_database(self, s: Session):
        if s.dev is None or s.dev.ospf is None:
            s.p("  OSPF is not running.")
            return
        s.p("OSPF Router with ID (%s)" % s.dev.ospf.router_id)
        s.p("Link State Database")
        s.p("Type      Router ID        Link count   Networks")
        for e in s.dev.ospf.lsdb_table():
            s.p(f"Router    {e['adv']:<15} {len(e['nets']):<12} {', '.join(e['nets'])}")

    def _show_dhcp_binding(self, s: Session):
        if s.dev is None:
            return
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is None:
            s.p("  DHCP not configured.")
            return
        s.p("IP address     Hardware address  Lease expiration  Type")
        for mac, ip in pool.leases.items():
            m = ".".join(f"{mac[i]:02x}{mac[i+1]:02x}" for i in range(0, 6, 2))
            s.p(f"{str(ip):<14} {m:<17} Infinite          Automatic")

    def _show_nat(self, s: Session):
        if s.dev is None or s.dev.nat is None:
            s.p("  NAT not configured.")
            return
        s.p("Pro Inside global      Inside local       Outside local")
        for orig, out in s.dev.nat.translations():
            s.p(f"icmp {out:<18} {orig:<18} 0.0.0.0")

    def _show_acl(self, s: Session):
        if s.dev is None:
            return
        for acl_id, rules in sorted(s.dev.acl_rules.items()):
            s.p(f"Standard IP access list {acl_id}")
            for r in sorted(rules, key=lambda x: x.id):
                act = "permit" if r.permit else "deny"
                if r.dst is None:
                    src = "any" if r.src is None else f"{r.src[0]} {str(IP((~r.src[1].value) & 0xFFFFFFFF))}"
                    s.p(f"    {act} {src}")
                else:
                    s.p(f"    {act} {r.src[0]} {str(IP((~r.src[1].value) & 0xFFFFFFFF))} "
                        f"{r.dst[0]} {str(IP((~r.dst[1].value) & 0xFFFFFFFF))}")

    def _show_redundancy(self, s: Session):
        if s.dev is None or s.dev.vrrp is None:
            s.p("  HSRP/VRRP not configured.")
            return
        for st in s.dev.vrrp.status():
            s.p(f"{self.hsrp_keyword.upper()} group {st['gid']} - {st['state']}")
            s.p(f"  state is {st['state'].lower()}, virtual IP address is {st['vip']}")
            s.p(f"  priority {st['priority']}")

    def _show_running(self, s: Session):
        if s.dev is None:
            return
        s.p("Building configuration...\n")
        s.p("Current configuration : 0 bytes")
        s.p("!")
        s.p("version 15.2")
        s.p(f"hostname {s.dev.name}")
        s.p("!")
        if s.dev.vlans:
            for v in sorted(s.dev.vlans):
                s.p(f"vlan {v}")
            s.p("!")
        for i in s.dev.net.interfaces:
            if i.is_loopback:
                continue
            if i.is_virtual:
                s.p(f"interface Vlan{i.access_vlan}")
            else:
                s.p(f"interface {i.name}")
            if not i.is_virtual:
                if i.is_trunk():
                    s.p(" switchport mode trunk")
                    if len(i.trunk_vlans) > 4000:
                        s.p(" switchport trunk allowed vlan all")
                    else:
                        s.p(" switchport trunk allowed vlan " + ",".join(map(str, sorted(i.trunk_vlans))))
                else:
                    s.p(" switchport mode access")
                    if i.access_vlan != 1:
                        s.p(f" switchport access vlan {i.access_vlan}")
            if i.state == LinkState.DOWN:
                s.p(" shutdown")
            if i.has_ip():
                s.p(f" ip address {i.ip} {i.mask}")
            if s.dev.nat is not None:
                if s.dev.nat.outside == i.name:
                    s.p(" ip nat outside")
                elif i.name in s.dev.nat.inside:
                    s.p(" ip nat inside")
            s.p("!")
        for r in s.dev.net.routes.sorted():
            if r.proto == "S":
                s.p(f"ip route {r.prefix} {r.mask} {r.next_hop}")
        if s.dev.ospf is not None and s.dev.ospf.running:
            s.p(f"router ospf {s.dev.ospf.proc_id}")
            for net, mask in s.dev.ospf.match_networks:
                wc = str(IP((~mask.value) & 0xFFFFFFFF))
                s.p(f" network {net} {wc} area 0")
            s.p("!")
        if s.dev.acl_rules:
            for acl_id, rules in sorted(s.dev.acl_rules.items()):
                for r in sorted(rules, key=lambda x: x.id):
                    act = "permit" if r.permit else "deny"
                    if r.dst is None:
                        s.p(f"access-list {acl_id} {act} {r.src[0]} {str(IP((~r.src[1].value) & 0xFFFFFFFF))}")
                    else:
                        s.p(f"access-list {acl_id} {act} ip {r.src[0]} {str(IP((~r.src[1].value) & 0xFFFFFFFF))} {r.dst[0]} {str(IP((~r.dst[1].value) & 0xFFFFFFFF))}")
            s.p("!")
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is not None and s.dev.dhcp is pool:
            s.p("service dhcp")
            s.p(f"ip dhcp pool {pool.name}")
            s.p(f" network {pool.subnet} {pool.mask}")
            if pool.gateway:
                s.p(f" default-router {pool.gateway}")
            if pool.dns:
                s.p(f" dns-server {pool.dns}")
            s.p("!")
        if s.dev.vrrp is not None:
            for st in s.dev.vrrp.status():
                s.p(f"interface {st['viface']}")
                s.p(f" {self.hsrp_keyword} {st['gid']} ip {st['vip']}")
                s.p(f" {self.hsrp_keyword} {st['gid']} priority {st['priority']}")
                s.p("!")
        s.p("end")