"""Huawei VRP5 CLI vendor (reference implementation; H3C Comware subclasses it).

Covers: interfaces, static routes, VLAN / Vlanif, OSPF, DHCP server, VRRP,
NAT, ACL, and the usual display commands. All configuration mutates the
vendor-neutral device model so every feature works inside the simulation.
"""

from __future__ import annotations

from ..cli.session import Cmd, Session, Vendor, View
from ..net.ip import IP, Mask
from ..net.iface import Iface, LinkState, Route
from ..net.proto import ACLConfig, ACLRule, DHCPPool, NATConfig, OSPFInstance, VRRPConfig


def _mask_from_wildcard(wildcard: str) -> Mask:
    """0.0.0.255 -> /24."""
    return Mask((~IP.parse(wildcard).value) & 0xFFFFFFFF)


class VRP(Vendor):
    #: prefix(es) that denote a logical VLAN interface
    vlanif_names = ("vlanif", "vlan-interface")

    def name(self) -> str:
        return "VRP"

    def version_info(self) -> str:
        return (
            "Huawei Versatile Routing Platform Software\n"
            "VRP (R) software, Version 5.130 (AR2200 V200R003C00)\n"
            "Copyright (C) 2011-2012 HUAWEI TECH CO., LTD"
        )

    def new_session(self, device_id: str, device_name: str) -> Session:
        s = Session(device_id, device_name, self)
        user_tree = Cmd(name="")
        self._user_commands(s, user_tree)
        s.push(View(id="user", prompt=lambda s: f"<{s.device_name}>", tree=user_tree))
        return s

    # ---- interface name canonicalisation ----

    def _canon_iface(self, args) -> tuple:
        """Return (canonical_name, is_vlanif, vlan_id) for an interface arg list."""
        if not args:
            return None, False, 0
        joined = "".join(args)
        low = joined.lower()
        for p in self.vlanif_names:
            if low.startswith(p):
                d = low[len(p):].lstrip()
                if d.isdigit():
                    return f"Vlanif{d}", True, int(d)
        return joined, False, 0

    # ---- command registration ----

    def _user_commands(self, s: Session, tree: Cmd):
        tree.add(Cmd(name="system-view", help="Enter system view, return user view with Ctrl+Z.",
                     handler=lambda s, a: self._enter_system(s)))
        tree.add(Cmd(name="ping", help="Ping a remote host.", min_args=1,
                     handler=lambda s, a: self._ping(s, a)))
        tree.add(Cmd(name="display", children=[
            Cmd(name="version", help="Display version information.",
                handler=lambda s, a: s.p(s.vendor.version_info())),
            Cmd(name="current-configuration", help="Display current configuration.",
                handler=lambda s, a: self._display_current(s)),
            Cmd(name="ip", children=[
                Cmd(name="routing-table", help="Display IP routing table.",
                    handler=lambda s, a: self._display_routing(s)),
            ]),
            Cmd(name="arp", help="Display ARP table.", handler=lambda s, a: self._display_arp(s)),
            Cmd(name="interface", help="Display interface information.", min_args=1,
                handler=lambda s, a: self._display_interface(s, "".join(a))),
            Cmd(name="brief", help="Display interface brief.", handler=lambda s, a: self._display_brief(s)),
            Cmd(name="vlan", help="Display VLAN information.", handler=lambda s, a: self._display_vlan(s)),
            Cmd(name="ospf", children=[
                Cmd(name="peer", help="Display OSPF neighbors.", handler=lambda s, a: self._display_ospf_peer(s)),
                Cmd(name="lsdb", help="Display OSPF LSDB.", handler=lambda s, a: self._display_ospf_lsdb(s)),
                Cmd(name="routing", help="Display OSPF routes.", handler=lambda s, a: self._display_ospf_route(s)),
            ]),
            Cmd(name="vrrp", help="Display VRRP status.", handler=lambda s, a: self._display_vrrp(s)),
            Cmd(name="nat", children=[
                Cmd(name="session", help="Display NAT translations.",
                    handler=lambda s, a: self._display_nat(s)),
            ]),
            Cmd(name="acl", children=[
                Cmd(name="all", help="Display ACL rules.", handler=lambda s, a: self._display_acl(s)),
            ]),
            Cmd(name="dhcp", children=[
                Cmd(name="server", children=[
                    Cmd(name="lease", help="Display DHCP server leases.",
                        handler=lambda s, a: self._display_dhcp(s)),
                ]),
            ]),
        ]))
        tree.add(Cmd(name="tracert", help="Trace route to a remote host.", min_args=1,
                     handler=lambda s, a: s.p("  tracert: not yet implemented")))
        tree.add(Cmd(name="quit", help="Exit.", handler=lambda s, a: s.p("  quit")))

    def _system_tree(self, s: Session) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="sysname", help="Configure system name.", min_args=1,
                     handler=lambda s, a: self._sysname(s, a[0])))
        tree.add(Cmd(name="interface", help="Enter interface view.", min_args=1,
                     handler=lambda s, a: self._enter_interface(s, a)))
        tree.add(Cmd(name="ip", children=[
            Cmd(name="route-static", help="Configure static route.", min_args=3,
                handler=lambda s, a: self._static_route(s, a)),
            Cmd(name="pool", help="Configure an IP address pool.", min_args=1,
                handler=lambda s, a: self._enter_pool(s, a[0])),
        ]))
        tree.add(Cmd(name="ospf", help="Enter OSPF view.", min_args=0,
                     handler=lambda s, a: self._enter_ospf(s, int(a[0]) if a else 1)))
        tree.add(Cmd(name="vlan", help="Create VLAN.", min_args=1,
                     handler=lambda s, a: self._create_vlan(s, a)))
        tree.add(Cmd(name="dhcp", children=[
            Cmd(name="enable", help="Enable DHCP globally.",
                handler=lambda s, a: self._dhcp_enable(s)),
        ]))
        tree.add(Cmd(name="acl", help="Enter ACL view.", min_args=1,
                     handler=lambda s, a: self._enter_acl(s, a[0])))
        tree.add(Cmd(name="undo", children=[
            Cmd(name="ip", children=[
                Cmd(name="route-static", help="Remove a static route.", min_args=3,
                    handler=lambda s, a: self._undo_static_route(s, a)),
            ]),
        ]))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: s.return_view()))
        tree.add(Cmd(name="quit", help="Return to user view.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="display", children=[
            Cmd(name="current-configuration", help="Display current configuration.",
                handler=lambda s, a: self._display_current(s)),
            Cmd(name="ip", children=[
                Cmd(name="routing-table", help="Display IP routing table.",
                    handler=lambda s, a: self._display_routing(s)),
            ]),
            Cmd(name="arp", help="Display ARP table.", handler=lambda s, a: self._display_arp(s)),
            Cmd(name="interface", help="Display interface information.", min_args=1,
                handler=lambda s, a: self._display_interface(s, "".join(a))),
            Cmd(name="brief", help="Display interface brief.", handler=lambda s, a: self._display_brief(s)),
            Cmd(name="vlan", help="Display VLAN information.", handler=lambda s, a: self._display_vlan(s)),
            Cmd(name="version", help="Display version information.",
                handler=lambda s, a: s.p(s.vendor.version_info())),
            Cmd(name="ospf", children=[
                Cmd(name="peer", help="Display OSPF neighbors.", handler=lambda s, a: self._display_ospf_peer(s)),
                Cmd(name="lsdb", help="Display OSPF LSDB.", handler=lambda s, a: self._display_ospf_lsdb(s)),
                Cmd(name="routing", help="Display OSPF routes.", handler=lambda s, a: self._display_ospf_route(s)),
            ]),
            Cmd(name="vrrp", help="Display VRRP status.", handler=lambda s, a: self._display_vrrp(s)),
            Cmd(name="nat", children=[
                Cmd(name="session", help="Display NAT translations.",
                    handler=lambda s, a: self._display_nat(s)),
            ]),
            Cmd(name="acl", children=[
                Cmd(name="all", help="Display ACL rules.", handler=lambda s, a: self._display_acl(s)),
            ]),
            Cmd(name="dhcp", children=[
                Cmd(name="server", children=[
                    Cmd(name="lease", help="Display DHCP server leases.",
                        handler=lambda s, a: self._display_dhcp(s)),
                ]),
            ]),
        ]))
        tree.add(Cmd(name="ping", help="Ping a remote host.", min_args=1,
                     handler=lambda s, a: self._ping(s, a)))
        return tree

    def _interface_tree(self, s: Session, name: str, iface: Iface) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="ip", children=[
            Cmd(name="address", help="Configure interface IP address.", min_args=2,
                handler=lambda s, a: self._ip_address(s, name, a[0], a[1])),
        ]))
        tree.add(Cmd(name="port", children=[
            Cmd(name="link-type", help="Set port link type (access/trunk).", min_args=1,
                handler=lambda s, a: self._port_linktype(s, name, a[0])),
            Cmd(name="default", children=[
                Cmd(name="vlan", help="Set access port VLAN.", min_args=1,
                    handler=lambda s, a: self._port_access_vlan(s, name, int(a[0]))),
            ]),
            Cmd(name="access", children=[
                Cmd(name="vlan", help="Set access port VLAN (Comware style).", min_args=1,
                    handler=lambda s, a: self._port_access_vlan(s, name, int(a[0]))),
            ]),
            Cmd(name="trunk", children=[
                Cmd(name="allow-pass", help="Allow VLANs on trunk.", min_args=2,
                    handler=lambda s, a: self._trunk_allow(s, name, a[1])),
                Cmd(name="pvid", children=[
                    Cmd(name="vlan", help="Set trunk native VLAN.", min_args=1,
                        handler=lambda s, a: self._trunk_pvid(s, name, int(a[0]))),
                ]),
            ]),
        ]))
        tree.add(Cmd(name="dhcp", children=[
            Cmd(name="select", children=[
                Cmd(name="global", help="Enable DHCP on this interface.",
                    handler=lambda s, a: self._dhcp_select(s, name)),
            ]),
        ]))
        tree.add(Cmd(name="vrrp", children=[
            Cmd(name="vrid", help="Configure VRRP.", min_args=2,
                handler=lambda s, a: self._vrrp_vrid(s, name, a)),
        ]))
        tree.add(Cmd(name="nat", children=[
            Cmd(name="outbound", help="Enable source NAT on this interface.", min_args=1,
                handler=lambda s, a: self._nat_outbound(s, name, int(a[0]))),
        ]))
        tree.add(Cmd(name="traffic-filter", children=[
            Cmd(name="inbound", help="Apply ACL inbound.", min_args=2,
                handler=lambda s, a: self._traffic_filter(s, name, "in", int(a[1]))),
            Cmd(name="outbound", help="Apply ACL outbound.", min_args=2,
                handler=lambda s, a: self._traffic_filter(s, name, "out", int(a[1]))),
        ]))
        tree.add(Cmd(name="shutdown", help="Shutdown interface.",
                     handler=lambda s, a: self._shutdown(s, name, True)))
        tree.add(Cmd(name="undo", children=[
            Cmd(name="shutdown", help="Undo shutdown.",
                handler=lambda s, a: self._shutdown(s, name, False)),
            Cmd(name="ip", children=[
                Cmd(name="address", help="Remove interface IP.",
                    handler=lambda s, a: self._unset_ip(s, name)),
            ]),
            Cmd(name="port", children=[
                Cmd(name="link-type", help="Reset link type.",
                    handler=lambda s, a: self._reset_port(s, name)),
            ]),
        ]))
        tree.add(Cmd(name="quit", help="Return to system view.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: s.return_view()))
        tree.add(Cmd(name="display", children=[
            Cmd(name="this", help="Display this interface config.",
                handler=lambda s, a: self._display_this(s, name)),
        ]))
        return tree

    def _ospf_tree(self, s: Session, proc_id: int) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="area", help="Enter OSPF area view.", min_args=1,
                     handler=lambda s, a: self._enter_area(s, proc_id, a[0])))
        tree.add(Cmd(name="quit", help="Return to system view.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: s.return_view()))
        tree.add(Cmd(name="display", children=[
            Cmd(name="ospf", children=[
                Cmd(name="peer", help="Display OSPF neighbors.",
                    handler=lambda s, a: self._display_ospf_peer(s)),
                Cmd(name="lsdb", help="Display OSPF LSDB.",
                    handler=lambda s, a: self._display_ospf_lsdb(s)),
                Cmd(name="routing", help="Display OSPF routes.",
                    handler=lambda s, a: self._display_ospf_route(s)),
            ]),
        ]))
        return tree

    def _area_tree(self, s: Session, proc_id: int, area: str) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="network", help="Enable OSPF on a network.", min_args=2,
                     handler=lambda s, a: self._ospf_network(s, a[0], a[1])))
        tree.add(Cmd(name="quit", help="Return to OSPF view.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: s.return_view()))
        tree.add(Cmd(name="display", children=[
            Cmd(name="ospf", children=[
                Cmd(name="peer", help="Display OSPF neighbors.",
                    handler=lambda s, a: self._display_ospf_peer(s)),
                Cmd(name="lsdb", help="Display OSPF LSDB.",
                    handler=lambda s, a: self._display_ospf_lsdb(s)),
                Cmd(name="routing", help="Display OSPF routes.",
                    handler=lambda s, a: self._display_ospf_route(s)),
            ]),
        ]))
        return tree

    def _pool_tree(self, s: Session, name: str) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="network", help="Set pool subnet.", min_args=3,
                     handler=lambda s, a: self._pool_network(s, name, a[0], a[2])))
        tree.add(Cmd(name="gateway-list", help="Set pool gateway.", min_args=1,
                     handler=lambda s, a: self._pool_gateway(s, name, a[0])))
        tree.add(Cmd(name="dns-list", help="Set pool DNS server.", min_args=1,
                     handler=lambda s, a: self._pool_dns(s, name, a[0])))
        tree.add(Cmd(name="quit", help="Return to system view.",
                     handler=lambda s, a: s.pop()))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: s.return_view()))
        return tree

    def _acl_tree(self, s: Session, acl_id: int) -> Cmd:
        tree = Cmd(name="")
        tree.add(Cmd(name="rule", help="Add an ACL rule.", min_args=2,
                     handler=lambda s, a: self._acl_rule(s, acl_id, a)))
        tree.add(Cmd(name="quit", help="Return to system view.",
                     handler=lambda s, a: (self._acl_done(s, acl_id), s.pop())))
        tree.add(Cmd(name="return", help="Return to user view.",
                     handler=lambda s, a: (self._acl_done(s, acl_id), s.return_view())))
        tree.add(Cmd(name="display", children=[
            Cmd(name="this", help="Display this ACL.",
                handler=lambda s, a: self._display_acl(s)),
        ]))
        return tree

    # ---- view handlers ----

    def _enter_system(self, s: Session):
        s.p(self.banner_system)
        s.push(View(id="system", prompt=lambda s: f"[{s.device_name}]", tree=self._system_tree(s)))

    def _enter_interface(self, s: Session, args):
        name, is_vlanif, vlan_id = self._canon_iface(args)
        if name is None:
            return
        if s.dev is not None:
            iface = s.dev.get_iface(name)
            if iface is None:
                if is_vlanif:
                    iface = Iface(name=name, is_virtual=True, access_vlan=vlan_id,
                                  mac=bytes([0x00, 0xe0, 0xfc, 0xff, 0x00, vlan_id]))
                    s.dev.add_iface(iface)
                else:
                    s.p(f"  Error: Interface {name} does not exist.")
                    return
        s.push(View(id="interface", prompt=lambda s: f"[{s.device_name}-{name}]",
                    tree=self._interface_tree(s, name, s.dev.get_iface(name) if s.dev else None)))

    def _enter_ospf(self, s: Session, proc_id: int):
        if s.dev is None or s.world is None:
            s.p("  OSPF needs an attached simulation.")
            return
        if s.dev.ospf is None or s.dev.ospf.proc_id != proc_id:
            s.dev.ospf = OSPFInstance(s.dev, proc_id).enable(s.world, process_id=proc_id)
        s.p(f"  OSPF process {proc_id} with Router ID {s.dev.ospf.router_id}")
        s.push(View(id="ospf", prompt=lambda s: f"[{s.device_name}-ospf-{proc_id}]",
                    tree=self._ospf_tree(s, proc_id)))

    def _enter_area(self, s: Session, proc_id: int, area: str):
        s.p("  OSPF area is not a basic config element; use `network` to advertise.")
        s.push(View(id="area", prompt=lambda s: f"[{s.device_name}-ospf-{proc_id}-area-{area}]",
                    tree=self._area_tree(s, proc_id, area)))

    def _enter_pool(self, s: Session, name: str):
        if s.dev is None:
            return
        if getattr(s.dev, "_dhcp_pool", None) is None:
            s.dev._dhcp_pool = DHCPPool(IP(0), Mask(0), IP(0), IP(0), name=name)
        s.p("  DHCP address pool created.")
        s.push(View(id="pool", prompt=lambda s: f"[{s.device_name}-ip-pool-{name}]",
                    tree=self._pool_tree(s, name)))

    def _enter_acl(self, s: Session, acl_id: str):
        if s.dev is None:
            return
        s.p("  Enter ACL view.")
        s.push(View(id="acl", prompt=lambda s: f"[{s.device_name}-acl-{acl_id}]",
                    tree=self._acl_tree(s, int(acl_id))))

    # ---- handlers ----

    def _sysname(self, s: Session, name: str):
        s.device_name = name
        if s.dev is not None:
            s.dev.name = name

    def _ip_address(self, s: Session, name: str, ip_str: str, mask_str: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            s.p(f"  Error: Interface {name} does not exist.")
            return
        try:
            ip = IP.parse(ip_str)
            mask = Mask.parse(mask_str)
        except ValueError:
            s.p("  Error: invalid IP address or mask.")
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
        s.p(f"  Interface {name} has been {'shut down' if down else 'brought up'}.")
        if s.dev.ospf is not None and s.dev.ospf.running and iface.has_ip():
            s.dev.ospf.on_iface_change(s.world, iface)
        if s.world is not None:
            s.world.refresh_vrrp()

    def _port_linktype(self, s: Session, name: str, mode: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None or iface.is_virtual:
            return
        iface.mode = "trunk" if mode.lower() == "trunk" else "access"

    def _port_access_vlan(self, s: Session, name: str, vlan: int):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None or iface.is_virtual:
            return
        iface.access_vlan = vlan
        if s.dev is not None:
            s.dev.vlans.add(vlan)

    def _trunk_allow(self, s: Session, name: str, vlan: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None or iface.is_virtual:
            return
        iface.mode = "trunk"
        if vlan.lower() == "all":
            iface.trunk_vlans = set(range(1, 4095))
        else:
            for v in vlan.split(","):
                if v.isdigit():
                    iface.trunk_vlans.add(int(v))
        if s.dev is not None:
            iface.trunk_vlans and s.dev.vlans.update(iface.trunk_vlans)

    def _trunk_pvid(self, s: Session, name: str, vlan: int):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is not None:
            iface.access_vlan = vlan

    def _reset_port(self, s: Session, name: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is not None:
            iface.mode = "access"
            iface.trunk_vlans = set()

    def _create_vlan(self, s: Session, args):
        if s.dev is None:
            return
        for tok in args:
            if tok.lower() == "batch":
                continue
            for part in tok.split(","):
                if part.isdigit():
                    s.dev.vlans.add(int(part))
        s.p("  Info: It is a successful creation.")

    def _dhcp_enable(self, s: Session):
        if s.dev is None:
            return
        s.dev._dhcp_enabled = True
        s.p("  Info: DHCP enabled.")

    def _dhcp_select(self, s: Session, name: str):
        if s.dev is None:
            return
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is None:
            s.p("  Error: no IP pool configured.")
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            return
        pool.vlan = iface.access_vlan if not iface.is_trunk() else 0
        s.dev.dhcp = pool
        s.p("  Info: DHCP global pool selected on this interface.")

    def _pool_network(self, s: Session, name: str, ip_str: str, mask_str: str):
        if s.dev is None:
            return
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is None:
            return
        net = IP.parse(ip_str)
        mask = Mask.parse(mask_str)
        pool.subnet = net
        pool.mask = mask
        first = net.value + 1
        last = net.broadcast(mask).value - 1
        pool.start_val = first
        pool.end_val = last
        pool._next = first

    def _pool_gateway(self, s: Session, name: str, gw: str):
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is not None:
            pool.gateway = IP.parse(gw)

    def _pool_dns(self, s: Session, name: str, dns: str):
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is not None:
            pool.dns = IP.parse(dns)

    def _vrrp_vrid(self, s: Session, name: str, args):
        if s.dev is None or s.world is None:
            return
        if len(args) < 2:
            s.p("  Incomplete command.")
            return
        gid = int(args[0])
        sub = args[1]
        if sub == "virtual-ip":
            if len(args) < 3:
                s.p("  Incomplete command.")
                return
            if s.dev.vrrp is None:
                s.dev.vrrp = VRRPConfig(s.dev)
            vip = IP.parse(args[2])
            s.dev.vrrp.add(gid, name, vip)
            s.world.register_vrrp(s.dev, gid)
            s.world.refresh_vrrp()
            s.p(f"  Info: VRRP group {gid} virtual IP {vip}.")
        elif sub == "priority":
            if len(args) < 3:
                s.p("  Incomplete command.")
                return
            if s.dev.vrrp is not None and gid in s.dev.vrrp.groups:
                s.dev.vrrp.groups[gid].priority = int(args[2])
            s.world.refresh_vrrp()

    def _nat_outbound(self, s: Session, name: str, acl_id: int):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            return
        inside = [i.name for i in s.dev.net.interfaces if i.has_ip() and i is not iface]
        s.dev.nat = NATConfig(inside, name)
        s.p(f"  Info: source NAT enabled on {name} (ACL {acl_id}).")

    def _traffic_filter(self, s: Session, name: str, direction: str, acl_id: int):
        if s.dev is None:
            return
        rules = s.dev.acl_rules.get(acl_id, [])
        if s.dev.acl is None:
            s.dev.acl = ACLConfig()
        for r in rules:
            s.dev.acl.add(r.id, name, direction, r)
        s.p(f"  Info: ACL {acl_id} applied {direction}bound on {name}.")

    def _acl_rule(self, s: Session, acl_id: int, args):
        if s.dev is None:
            return
        try:
            rid = int(args[0])
        except ValueError:
            return
        action = args[1].lower()
        permit = action.startswith("permit")
        src = dst = None
        proto = "any"
        i = 2
        while i < len(args):
            tok = args[i].lower()
            if tok == "source" and i + 2 < len(args):
                src = (IP.parse(args[i + 1]), _mask_from_wildcard(args[i + 2]))
                i += 3
            elif tok == "destination" and i + 2 < len(args):
                dst = (IP.parse(args[i + 1]), _mask_from_wildcard(args[i + 2]))
                i += 3
            elif tok in ("icmp", "tcp", "udp"):
                proto = tok
                i += 1
            else:
                i += 1
        s.dev.acl_rules.setdefault(acl_id, []).append(
            ACLRule(id=rid, permit=permit, src=src, dst=dst, proto=proto))

    def _acl_done(self, s: Session, acl_id: int):
        pass

    def _static_route(self, s: Session, args):
        if s.dev is None:
            return
        try:
            prefix = IP.parse(args[0])
            mask = Mask.parse(args[1])
            nh = IP.parse(args[2])
        except ValueError:
            s.p("  Error: invalid parameter.")
            return
        iface = None
        for i in s.dev.net.interfaces:
            if i.has_ip() and not i.is_loopback and nh.network(i.mask) == i.network():
                iface = i
                break
        s.dev.net.routes.add(Route(prefix=prefix.network(mask), mask=mask, next_hop=nh,
                                   iface=iface, proto="S", metric=1))

    def _undo_static_route(self, s: Session, args):
        if s.dev is None:
            return
        try:
            prefix = IP.parse(args[0])
            mask = Mask.parse(args[1])
        except ValueError:
            return
        s.dev.net.routes.remove(prefix.network(mask), mask, "S")

    def _ospf_network(self, s: Session, ip_str: str, wildcard: str):
        if s.dev is None or s.dev.ospf is None:
            return
        net = IP.parse(ip_str)
        mask = _mask_from_wildcard(wildcard)
        s.dev.ospf.add_network(net, mask)
        if s.world is not None:
            s.dev.ospf.on_iface_change(s.world, None)

    def _ping(self, s: Session, args):
        if s.world is None or s.dev is None:
            s.p("  ping: no simulation attached")
            return
        dst = args[0]
        count = 5
        if len(args) >= 3 and args[1] == "-c":
            try:
                count = int(args[2])
            except ValueError:
                pass
        s.p(f"  PING {dst}: 56 data bytes, press CTRL_C to break")
        received = [0]
        s.world.ping(
            s.dev, dst, count,
            on_status=lambda seq, ok, detail: (
                s.stream("  " + detail),
                received.__setitem__(0, received[0] + 1) if ok else None),
            on_done=lambda recv, cnt, loss: (
                s.stream(f"  --- {dst} ping statistics ---"),
                s.stream(f"  {cnt} packet(s) transmitted, {recv} packet(s) received, {loss:.1f}% packet loss")),
        )

    # ---- display handlers ----

    def _display_routing(self, s: Session):
        if s.dev is None:
            return
        s.p("Route Flags: R - relay, D - download to fib")
        s.p("-" * 78)
        s.p("Routing Tables:")
        s.p(f"{'Destination/Mask':>19}    {'Proto':<6} {'Pre':>3}  {'Cost':>3}  {'Flags':<7} {'NextHop':<15} Interface")
        proto_label = {"S": "Static", "D": "Direct", "O": "OSPF", "B": "BGP"}
        for r in s.dev.net.routes.sorted():
            proto = proto_label.get(r.proto, r.proto)
            pre = {"S": 60, "D": 0, "O": 10, "B": 255}.get(r.proto, 0)
            nh = "Direct" if r.next_hop is None else str(r.next_hop)
            iface = r.iface.name if r.iface else ""
            s.p(f"{str(r.prefix):>19}/{r.mask.prefix_len()}    {proto:<6} {pre:>3}  {r.metric:>3}      R     {nh:<15} {iface}")

    def _display_arp(self, s: Session):
        if s.dev is None:
            return
        s.p("  IP ADDRESS      MAC ADDRESS     EXPIRE")
        for e in s.dev.net.arp.all():
            mac = ":".join(f"{b:02x}" for b in e.mac)
            s.p(f"  {str(e.ip):<15} {mac}")

    def _display_vlan(self, s: Session):
        if s.dev is None:
            return
        s.p("The total number of VLANs is : %d" % len(s.dev.vlans))
        s.p(f"{'VLAN ID':<10} {'Type':<10} Status")
        for v in sorted(s.dev.vlans):
            s.p(f"{v:<10} common   up")
        for i in s.dev.net.interfaces:
            if i.is_virtual:
                continue
            if i.is_trunk():
                s.p(f"  Trunk {i.name} : PVID {i.access_vlan} , allowed {'all' if len(i.trunk_vlans) > 4000 else ','.join(map(str, sorted(i.trunk_vlans)))}")
            else:
                s.p(f"  Access {i.name} : VLAN {i.access_vlan}")

    def _display_ospf_peer(self, s: Session):
        if s.dev is None or s.dev.ospf is None:
            s.p("  OSPF is not running.")
            return
        s.p("OSPF Process 1 with Router ID %s" % s.dev.ospf.router_id)
        s.p(f"{'Neighbor ID':<15} {'Pri':<5} {'State':<12} {'Address':<15} Interface")
        for n in s.dev.ospf.neighbor_table():
            s.p(f"{n['id']:<15} {'1':<5} {'Full/  -':<12} {n['ip']:<15} {n['iface']}")

    def _display_ospf_lsdb(self, s: Session):
        if s.dev is None or s.dev.ospf is None:
            s.p("  OSPF is not running.")
            return
        s.p("Link State Database")
        s.p(f"{'Type':<8} {'Router Id':<15} {'Link count':<12} Networks")
        for e in s.dev.ospf.lsdb_table():
            s.p(f"{'Router':<8} {e['adv']:<15} {len(e['nets']):<12} {', '.join(e['nets'])}")

    def _display_ospf_route(self, s: Session):
        if s.dev is None or s.dev.ospf is None:
            s.p("  OSPF is not running.")
            return
        s.p("OSPF Process 1 routing table")
        s.p(f"{'Destination/Mask':>19}    {'Cost':>4}  {'Type':<6} {'NextHop':<15} Interface")
        for r in s.dev.ospf.ospf_routes():
            nh = str(r.next_hop) if r.next_hop else "Direct"
            iface = r.iface.name if r.iface else ""
            s.p(f"{str(r.prefix):>19}/{r.mask.prefix_len()}    {r.metric:>4}  {'Intra':<6} {nh:<15} {iface}")

    def _display_vrrp(self, s: Session):
        if s.dev is None or s.dev.vrrp is None:
            s.p("  VRRP is not configured.")
            return
        s.p(f"{'VRID':<6} {'State':<8} {'Priority':<9} {'Virtual IP':<16} Interface")
        for st in s.dev.vrrp.status():
            s.p(f"{st['gid']:<6} {st['state']:<8} {st['priority']:<9} {st['vip']:<16} {st['viface']}")

    def _display_nat(self, s: Session):
        if s.dev is None or s.dev.nat is None:
            s.p("  NAT is not configured.")
            return
        s.p("  NAT Session Table")
        s.p(f"{'Original IP':<15} {'Translated IP':<15}")
        for orig, out in s.dev.nat.translations():
            s.p(f"  {orig:<15} {out:<15}")

    def _display_acl(self, s: Session):
        if s.dev is None:
            return
        for acl_id, rules in sorted(s.dev.acl_rules.items()):
            s.p(f"Advanced ACL {acl_id}")
            for r in sorted(rules, key=lambda x: x.id):
                act = "permit" if r.permit else "deny"
                src = "any" if r.src is None else f"{r.src[0]} {r.src[1]}"
                dst = "any" if r.dst is None else f"{r.dst[0]} {r.dst[1]}"
                s.p(f"  rule {r.id} {act} source {src} destination {dst}")

    def _display_dhcp(self, s: Session):
        if s.dev is None:
            return
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is None:
            s.p("  DHCP is not configured.")
            return
        s.p(f"  DHCP IP pool ({pool.name}): {pool.subnet}/{pool.mask.prefix_len()}")
        s.p(f"{'MAC Address':<20} {'IP Address':<15}")
        for mac, ip in pool.leases.items():
            m = "-".join(f"{b:02x}" for b in mac)
            s.p(f"  {m:<20} {ip:<15}")

    def _display_current(self, s: Session):
        if s.dev is None:
            return
        s.p("#")
        s.p(f"sysname {s.dev.name}")
        s.p("#")
        if s.dev.vlans:
            s.p(f"vlan batch {','.join(map(str, sorted(s.dev.vlans)))}")
        for i in s.dev.net.interfaces:
            if i.is_loopback:
                continue
            s.p(f"interface {i.name}")
            if i.is_virtual:
                pass
            elif i.is_trunk():
                s.p(" port link-type trunk")
                if len(i.trunk_vlans) > 4000:
                    s.p(" port trunk allow-pass vlan all")
                else:
                    s.p(" port trunk allow-pass vlan " + ",".join(map(str, sorted(i.trunk_vlans))))
            else:
                s.p(f" port link-type access")
                if i.access_vlan != 1:
                    s.p(f" port default vlan {i.access_vlan}")
            if i.has_ip():
                s.p(f" ip address {i.ip} {i.mask}")
            if i.state == LinkState.DOWN:
                s.p(" shutdown")
            s.p("#")
        for r in s.dev.net.routes.sorted():
            if r.proto == "S":
                s.p(f"ip route-static {r.prefix} {r.mask.prefix_len()} {r.next_hop}")
        if s.dev.ospf is not None and s.dev.ospf.running:
            s.p(f"ospf {s.dev.ospf.proc_id}")
            for net, mask in s.dev.ospf.match_networks:
                wc = str(IP((~mask.value) & 0xFFFFFFFF))
                s.p(f" area 0.0.0.0")
                s.p(f"  network {net} {wc}")
            s.p("quit")
        if s.dev.acl_rules:
            for acl_id, rules in sorted(s.dev.acl_rules.items()):
                s.p(f"acl number {acl_id}")
                for r in sorted(rules, key=lambda x: x.id):
                    act = "permit" if r.permit else "deny"
                    src = "" if r.src is None else f" source {r.src[0]} {str(IP((~r.src[1].value) & 0xFFFFFFFF))}"
                    dst = "" if r.dst is None else f" destination {r.dst[0]} {str(IP((~r.dst[1].value) & 0xFFFFFFFF))}"
                    s.p(f" rule {r.id} {act}{src}{dst}")
                s.p("quit")
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is not None and s.dev.dhcp is pool:
            s.p("dhcp enable")
            s.p(f"ip pool {pool.name}")
            s.p(f" network {pool.subnet} mask {pool.mask}")
            if pool.gateway:
                s.p(f" gateway-list {pool.gateway}")
            if pool.dns:
                s.p(f" dns-list {pool.dns}")
            s.p("quit")
        if s.dev.nat is not None:
            s.p(f"nat outbound (ACL on {s.dev.nat.outside})")
        if s.dev.vrrp is not None:
            for st in s.dev.vrrp.status():
                s.p(f"interface {st['viface']}")
                s.p(f" vrrp vrid {st['gid']} virtual-ip {st['vip']}")
                s.p(f" vrrp vrid {st['gid']} priority {st['priority']}")
                s.p("quit")
        s.p("return")

    def _display_interface(self, s: Session, name: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            s.p(f"  Error: Interface {name} does not exist.")
            return
        st = "up" if (iface.state == LinkState.UP and iface.has_ip()) else "down"
        s.p(f"{iface.name} current state : {st}")
        s.p(f"Line protocol current state : {st}")
        if iface.has_ip():
            s.p(f"Internet Address is {iface.ip}/{iface.mask.prefix_len()}")
        else:
            s.p("Internet Address is unnumbered")
        if iface.is_trunk():
            s.p(f"Port link-type: trunk")
            s.p(f"Allowed VLANs: {'all' if len(iface.trunk_vlans) > 4000 else ','.join(map(str, sorted(iface.trunk_vlans)))}")
        else:
            s.p(f"Port link-type: access")
            s.p(f"Default VLAN: {iface.access_vlan}")

    def _display_brief(self, s: Session):
        if s.dev is None:
            return
        s.p(f"{'Interface':<31} {'IP Address/Mask':<20} {'Physical':<9} {'Protocol':<9}")
        for i in s.dev.net.interfaces:
            phy = "up" if (i.state == LinkState.UP and i.has_ip()) else "down"
            proto = phy
            ip = "unassigned" if not i.has_ip() else f"{i.ip}/{i.mask.prefix_len()}"
            s.p(f"{i.name:<31} {ip:<20} {phy:<9} {proto:<9}")

    def _display_this(self, s: Session, name: str):
        if s.dev is None:
            return
        iface = s.dev.get_iface(name)
        if iface is None:
            return
        s.p(f"interface {iface.name}")
        if iface.is_trunk():
            s.p(" port link-type trunk")
        if iface.has_ip():
            s.p(f" ip address {iface.ip} {iface.mask}")