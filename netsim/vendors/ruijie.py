"""Ruijie RGOS CLI vendor (信创 / domestic). Subclasses the Cisco IOS vendor.

Key differences: prompts carry the interface name, interface names are typed
with a space ("GigabitEthernet 0/0"), and redundancy uses VRRP instead of HSRP.
"""

from __future__ import annotations

from ..cli.session import Session
from ..net.iface import LinkState
from ..net.ip import IP

from .cisco import IOS


class RGOS(IOS):
    iface_sep = " "
    hsrp_keyword = "vrrp"

    def name(self) -> str:
        return "RGOS"

    def version_info(self) -> str:
        return (
            "Ruijie RGOS 10.3(4b12), Release (36252)\n"
            "Copyright (c) by Ruijie Networks. All rights reserved.\n"
            "Compiled Wed 2024-11-20 09:18:18 by DEV"
        )

    def _user_prompt(self, s):
        return f"{s.device_name}>"

    def _priv_prompt(self, s):
        return f"{s.device_name}#"

    def _config_prompt(self, s):
        return f"{s.device_name}(config)#"

    def _iface_prompt(self, s, name):
        return f"{s.device_name}(config-{name})#"

    def _show_running(self, s: Session):
        if s.dev is None:
            return
        s.p("Building configuration...")
        s.p("Current configuration: 0 bytes")
        s.p("!")
        s.p("hostname " + s.dev.name)
        s.p("!")
        if s.dev.vlans:
            for v in sorted(s.dev.vlans):
                s.p(f"vlan {v}")
            s.p("!")
        for i in s.dev.net.interfaces:
            if i.is_loopback:
                continue
            if i.is_virtual:
                s.p(f"interface vlan {i.access_vlan}")
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
                s.p(f"interface vlan {st['gid']}")
                s.p(f" vrrp {st['gid']} ip {st['vip']}")
                s.p(f" vrrp {st['gid']} priority {st['priority']}")
                s.p("!")
        s.p("end")