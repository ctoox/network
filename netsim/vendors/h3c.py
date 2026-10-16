"""H3C Comware V7 CLI vendor (信创 / domestic). Subclasses the Huawei VRP
vendor: the command grammar and view hierarchy are almost identical, the only
real differences are the banner, version string, and a few command names.
"""

from __future__ import annotations

from ..cli.session import Session
from ..net.iface import LinkState
from ..net.ip import IP

from .huawei import VRP


class Comware(VRP):
    # H3C uses `interface Vlan-interface10` (or `Vlan-interface 10`)
    vlanif_names = ("vlan-interface", "vlanif")

    def name(self) -> str:
        return "Comware"

    def version_info(self) -> str:
        return (
            "H3C Comware Software, Version 7.1.075, Alpha 7165\n"
            "Copyright (c) 2004-2020 New H3C Technologies Co., Ltd. All rights reserved.\n"
            "H3C S5560X-30C-EI uptime is 0 weeks, 0 days, 0 hours, 3 minutes"
        )

    def new_session(self, device_id: str, device_name: str) -> Session:
        return super().new_session(device_id, device_name)

    def _display_current(self, s):
        if s.dev is None:
            return
        s.p("#")
        s.p(f"sysname {s.dev.name}")
        s.p("#")
        if s.dev.vlans:
            s.p(f"vlan {','.join(map(str, sorted(s.dev.vlans)))}")
        for i in s.dev.net.interfaces:
            if i.is_loopback:
                continue
            if i.is_virtual:
                s.p(f"interface Vlan-interface{i.access_vlan}")
            else:
                s.p(f"interface {i.name}")
            if i.is_trunk():
                s.p(" port link-type trunk")
                if len(i.trunk_vlans) > 4000:
                    s.p(" port trunk permit vlan all")
                else:
                    s.p(" port trunk permit vlan " + ",".join(map(str, sorted(i.trunk_vlans))))
            elif not i.is_virtual:
                s.p(" port link-type access")
                if i.access_vlan != 1:
                    s.p(f" port access vlan {i.access_vlan}")
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
                s.p(f"acl basic {acl_id}")
                for r in sorted(rules, key=lambda x: x.id):
                    act = "permit" if r.permit else "deny"
                    src = "" if r.src is None else f" source {r.src[0]} {str(IP((~r.src[1].value) & 0xFFFFFFFF))}"
                    s.p(f" rule {r.id} {act}{src}")
                s.p("quit")
        pool = getattr(s.dev, "_dhcp_pool", None)
        if pool is not None and s.dev.dhcp is pool:
            s.p("dhcp enable")
            s.p(f"dhcp server ip-pool {pool.name}")
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
                s.p(f"interface Vlan-interface{st['gid']}")
                s.p(f" vrrp vrid {st['gid']} virtual-ip {st['vip']}")
                s.p(f" vrrp vrid {st['gid']} priority {st['priority']}")
                s.p("quit")
        s.p("return")

    def _display_interface(self, s, name: str):
        iface = s.dev.get_iface(name) if s.dev else None
        if iface is None:
            s.p(f"  Error: Interface {name} does not exist.")
            return
        if iface.is_virtual:
            s.p(f"Vlan-interface{iface.access_vlan} current state : Up")
            s.p("Line protocol current state : Up")
            if iface.has_ip():
                s.p(f"Internet Address is {iface.ip}/{iface.mask.prefix_len()}")
            return
        super()._display_interface(s, name)

    def _display_brief(self, s):
        if s.dev is None:
            return
        s.p(f"{'Interface':<31} {'IP Address/Mask':<20} {'Physical':<9} {'Protocol':<9}")
        for i in s.dev.net.interfaces:
            if i.is_virtual:
                continue
            phy = "up" if (i.state == LinkState.UP and i.has_ip()) else "down"
            proto = phy
            ip = "unassigned" if not i.has_ip() else f"{i.ip}/{i.mask.prefix_len()}"
            s.p(f"{i.name:<31} {ip:<20} {phy:<9} {proto:<9}")