"""Optional network protocols for the simulator.

Lightweight, event-driven implementations of the control planes users expect
from real network devices:

  * OSPFInstance  - single-area link-state routing (Hello / LSA flooding / SPF)
  * DHCPPool      - per-segment DHCP server (DISCOVER/OFFER/REQUEST/ACK)
  * NATConfig     - source NAT (overload) on a firewall / router edge
  * ACLConfig     - simple inbound/outbound permit/deny rules
  * VRRPConfig    - virtual gateway redundancy (floating VIP)

All protocols are driven by the World scheduler (virtual or realtime). They
only touch the vendor-neutral device model (interfaces / RIB / ARP), so they
work identically across Huawei / Cisco / H3C / Ruijie / firewall CLI vendors.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .device import BROADCAST_MAC, Device, Packet, PacketType
from .iface import Iface, LinkState, Route
from .ip import IP, Mask

HELLO_INTERVAL = 1.0
DEAD_INTERVAL = 4.0
INF = float("inf")


# --------------------------------------------------------------------------
# OSPF (simplified single-area, link-state with LSA flooding + Dijkstra)
# --------------------------------------------------------------------------

class OSPFInstance:
    def __init__(self, device: Device, process_id: int = 1):
        self.device = device
        self.proc_id = process_id
        self.router_id: str = ""
        self.seq = 0
        self.lsdb: Dict[str, dict] = {}
        self.adj: Dict[str, dict] = {}
        self.running = False
        self._hello_ev = None
        self.match_networks: List[Tuple[IP, Mask]] = []  # network statements

    @property
    def enabled_ifaces(self) -> List[Iface]:
        out = []
        for i in self.device.net.interfaces:
            if not (i.has_ip() and not i.is_loopback):
                continue
            if self.match_networks:
                if not any(i.network() == net for net, _ in self.match_networks):
                    continue
            out.append(i)
        return out

    def add_network(self, ip: IP, mask: Mask) -> None:
        self.match_networks.append((ip.network(mask), mask))

    # ---- lifecycle ----

    def enable(self, world, router_id: Optional[str] = None,
               process_id: int = 1) -> "OSPFInstance":
        self.proc_id = process_id
        self.router_id = router_id or self._auto_router_id()
        self.running = True
        self.lsdb[self.router_id] = self._build_lsa()
        self._schedule_hello(world)
        self._send_lsa_to_all(world, self.lsdb[self.router_id])
        return self

    def stop(self, world) -> None:
        self.running = False
        if self._hello_ev is not None:
            world.scheduler.cancel(self._hello_ev)
        for r in list(self.device.net.routes.routes_for_proto("O")):
            self.device.net.routes.remove(r.prefix, r.mask, "O")

    def _auto_router_id(self) -> str:
        loop = [i for i in self.device.net.interfaces if i.is_loopback and i.has_ip()]
        if loop:
            return str(loop[0].ip)
        phys = [i for i in self.device.net.interfaces if i.has_ip()]
        if phys:
            return str(max(phys, key=lambda i: i.ip.value).ip)
        return self.device.id

    def _build_lsa(self) -> dict:
        self.seq += 1
        nets = []
        for i in self.device.net.interfaces:
            if i.is_loopback:
                continue
            if i.has_ip() and i.state == LinkState.UP:
                nets.append({"prefix": i.network(), "plen": i.mask.prefix_len(), "cost": 1})
        return {"adv": self.router_id, "seq": self.seq, "networks": nets}

    def on_iface_change(self, world, iface: Iface) -> None:
        if not self.running:
            return
        self.lsdb[self.router_id] = self._build_lsa()
        self._send_lsa_to_all(world, self.lsdb[self.router_id])
        self._recompute(world)

    # ---- hello ----

    def _schedule_hello(self, world) -> None:
        self._hello_ev = world.scheduler.schedule(HELLO_INTERVAL, self._hello_tick, world)

    def _hello_tick(self, world) -> None:
        if not self.running:
            return
        self._send_hellos(world)
        before = len(self.adj)
        self._prune_dead(world)
        if len(self.adj) != before:
            self._recompute(world)
        self._schedule_hello(world)

    def _send_hellos(self, world) -> None:
        lsa = self.lsdb.get(self.router_id)
        if lsa is None:
            lsa = self._build_lsa()
            self.lsdb[self.router_id] = lsa
        for i in self.enabled_ifaces:
            self._send_lsa(world, i, lsa)

    def on_hello(self, world, in_iface: Iface, pkt: Packet) -> None:
        if not self.running or in_iface is None:
            return
        payload = pkt.payload
        nid = payload["router_id"]
        if nid == self.router_id:
            return
        self.adj[nid] = {"iface": in_iface, "ip": IP.parse(payload["iface_ip"]),
                         "last": world.now()}
        lsa = payload["lsa"]
        if lsa.get("adv") == self.router_id:
            return
        if self._merge_lsa(world, lsa, exclude=in_iface):
            self._recompute(world)

    def _merge_lsa(self, world, lsa: dict, exclude: Optional[Iface] = None) -> bool:
        adv = lsa["adv"]
        cur = self.lsdb.get(adv)
        if cur is not None and cur["seq"] >= lsa["seq"]:
            return False
        self.lsdb[adv] = {"adv": adv, "seq": lsa["seq"], "networks": lsa["networks"]}
        for i in self.enabled_ifaces:
            if i is exclude:
                continue
            self._send_lsa(world, i, lsa)
        return True

    def _send_lsa(self, world, iface: Iface, lsa: dict) -> None:
        pkt = Packet(type=PacketType.OSPF_HELLO, src_ip=iface.ip,
                     dst_ip=IP.parse("224.0.0.5"), src_mac=iface.mac, ttl=1,
                     payload={"router_id": self.router_id, "iface_ip": str(iface.ip),
                              "lsa": lsa})
        if iface.is_virtual:
            # Vlanif / SVI: deliver within the VLAN segment via L2 flooding
            world.send_l2_frame(self.device, iface.access_vlan, BROADCAST_MAC, pkt)
        else:
            world.send_via_iface(self.device, iface, pkt)

    def _send_lsa_to_all(self, world, lsa: dict, exclude: Optional[Iface] = None) -> None:
        for i in self.enabled_ifaces:
            if i is exclude:
                continue
            self._send_lsa(world, i, lsa)

    def _prune_dead(self, world) -> None:
        now = world.now()
        for nid in [k for k, v in self.adj.items() if now - v["last"] > DEAD_INTERVAL]:
            del self.adj[nid]

    # ---- SPF ----

    def _recompute(self, world) -> None:
        routers = set(self.lsdb.keys()) | {self.router_id}
        edges: Dict[str, List[Tuple[str, int]]] = {r: [] for r in routers}
        groups: Dict[tuple, List[str]] = {}
        for adv, lsa in self.lsdb.items():
            for n in lsa["networks"]:
                groups.setdefault((str(n["prefix"]), n["plen"]), []).append(adv)
        for advs in groups.values():
            for a in advs:
                for b in advs:
                    if a != b:
                        edges.setdefault(a, []).append((b, 1))

        dist, parent = self._dijkstra(routers, edges, self.router_id)

        for r in list(self.device.net.routes.routes_for_proto("O")):
            self.device.net.routes.remove(r.prefix, r.mask, "O")
        for adv, lsa in self.lsdb.items():
            if adv == self.router_id or dist.get(adv, INF) >= INF:
                continue
            nh_ip, nh_iface = self._resolve_nexthop(parent, adv)
            if nh_ip is None:
                continue
            for n in lsa["networks"]:
                if n["cost"] == 0 or self._is_connected(n["prefix"], n["plen"]):
                    continue
                mask = Mask.from_len(n["plen"])
                cost = int(dist[adv]) + n["cost"]
                self.device.net.routes.add(Route(prefix=n["prefix"], mask=mask,
                                                 next_hop=nh_ip, iface=nh_iface,
                                                 proto="O", metric=cost))

    def _dijkstra(self, routers, edges, start):
        dist = {r: INF for r in routers}
        parent: Dict[str, str] = {}
        dist[start] = 0
        pq = [(0, start)]
        seen = set()
        while pq:
            d, r = heapq.heappop(pq)
            if r in seen:
                continue
            seen.add(r)
            for nb, c in edges.get(r, []):
                nd = d + c
                if nd < dist[nb]:
                    dist[nb] = nd
                    parent[nb] = r
                    heapq.heappush(pq, (nd, nb))
        return dist, parent

    def _resolve_nexthop(self, parent: Dict[str, str], target: str) -> Tuple[Optional[IP], Optional[Iface]]:
        node = target
        while parent.get(node, node) != self.router_id:
            if node not in parent:
                return None, None
            node = parent[node]
        a = self.adj.get(node)
        if a is None:
            return None, None
        return a["ip"], a["iface"]

    def _is_connected(self, prefix: IP, plen: int) -> bool:
        for i in self.device.net.interfaces:
            if i.has_ip() and i.mask.prefix_len() == plen and i.network() == prefix:
                return True
        return False

    # ---- introspection for CLI ----

    def neighbor_table(self) -> List[dict]:
        return [{"id": nid, "ip": str(v["ip"]), "iface": v["iface"].name}
                for nid, v in self.adj.items()]

    def lsdb_table(self) -> List[dict]:
        out = []
        for adv, lsa in self.lsdb.items():
            out.append({"adv": adv, "seq": lsa["seq"],
                        "nets": [f"{n['prefix']}/{n['plen']}" for n in lsa["networks"]]})
        return out

    def ospf_routes(self) -> List[Route]:
        return self.device.net.routes.routes_for_proto("O")


# --------------------------------------------------------------------------
# DHCP server
# --------------------------------------------------------------------------

class DHCPPool:
    def __init__(self, subnet: IP, mask: Mask, start: IP, end: IP,
                 gateway: Optional[IP] = None, dns: Optional[IP] = None,
                 name: str = "pool1"):
        self.name = name
        self.subnet = subnet
        self.mask = mask
        self.start_val = start.value
        self.end_val = end.value
        self.gateway = gateway
        self.dns = dns
        self._next = self.start_val
        self.leases: Dict[bytes, IP] = {}
        self.offered: Dict[bytes, IP] = {}
        self.used: set = set()
        self.known_ips: set = set()

    def _alloc(self, mac: bytes) -> Optional[IP]:
        if mac in self.leases:
            return self.leases[mac]
        if mac in self.offered:
            return self.offered[mac]
        bc = self.subnet.broadcast(self.mask).value
        ip = None
        v = self._next
        while v <= self.end_val:
            if v not in self.used and v not in self.known_ips \
                    and v != self.subnet.value and v != bc:
                ip = IP(v)
                self._next = v + 1
                break
            v += 1
        if ip is None:
            for v in range(self.start_val, self.end_val + 1):
                if v not in self.used and v not in self.known_ips \
                        and v != self.subnet.value and v != bc:
                    ip = IP(v)
                    break
        if ip is None:
            return None
        self.used.add(ip.value)
        self.offered[mac] = ip
        return ip

    def on_message(self, world, dev: Device, in_iface: Optional[Iface], pkt: Packet) -> None:
        mac = pkt.src_mac
        vlan = getattr(pkt, "vlan", 0)
        if pkt.type == PacketType.DHCP_DISCOVER:
            ip = self._alloc(mac)
            if ip is None:
                return
            world._log(dev, in_iface, pkt, "dhcp",
                       f"DHCP OFFER {ip} to {mac.hex()}")
            offer = self._reply(world, dev, mac, vlan, PacketType.DHCP_OFFER, ip, in_iface)
            world.send_l2_frame(dev, vlan, mac, offer)
        elif pkt.type == PacketType.DHCP_REQUEST:
            want = pkt.payload.get("yiaddr") if isinstance(pkt.payload, dict) else None
            ip = self.offered.get(mac) or self.leases.get(mac)
            if want is not None:
                ip = want
            if ip is not None:
                self.used.add(ip.value)
                self.offered.pop(mac, None)
                self.leases[mac] = ip
                world._log(dev, in_iface, pkt, "dhcp",
                           f"DHCP ACK {ip} to {mac.hex()}")
                ack = self._reply(world, dev, mac, vlan, PacketType.DHCP_ACK, ip, in_iface)
                world.send_l2_frame(dev, vlan, mac, ack)

    def _reply(self, world, dev: Device, mac: bytes, vlan: int,
               ptype: PacketType, yiaddr: IP, in_iface: Optional[Iface]) -> Packet:
        src = None
        for i in dev.net.interfaces:
            if i.has_ip() and i.carries_vlan(vlan) and not i.is_trunk():
                src = i
                break
        if src is None:
            for i in dev.net.interfaces:
                if i.has_ip():
                    src = i
                    break
        return Packet(type=ptype, src_ip=src.ip if src else IP(0),
                      dst_ip=IP(0xFFFFFFFF), src_mac=src.mac if src else BROADCAST_MAC,
                      dst_mac=mac, ttl=16, payload={
                          "yiaddr": yiaddr, "mask": self.mask,
                          "gateway": self.gateway, "dns": self.dns,
                      })


# --------------------------------------------------------------------------
# NAT (source overload) and ACL
# --------------------------------------------------------------------------

class NATConfig:
    def __init__(self, inside: List[str], outside: str):
        self.inside = set(inside)
        self.outside = outside
        self.map: Dict[IP, IP] = {}     # outside_ip -> original src
        self.reverse: Dict[IP, IP] = {} # original src -> outside_ip

    def translate(self, in_iface: Optional[Iface], out_iface: Optional[Iface], pkt: Packet) -> None:
        in_name = in_iface.name if in_iface else ""
        out_name = out_iface.name if out_iface else ""
        if in_name in self.inside and out_name == self.outside:
            if pkt.src_ip not in self.reverse and out_iface is not None and out_iface.has_ip():
                mapped = out_iface.ip
                self.map[mapped] = pkt.src_ip
                self.reverse[pkt.src_ip] = mapped
            mapped = self.reverse.get(pkt.src_ip)
            if mapped is not None:
                pkt.src_ip = mapped
        elif in_name == self.outside and pkt.dst_ip in self.map:
            pkt.dst_ip = self.map[pkt.dst_ip]

    def translations(self) -> List[tuple]:
        return [(str(src), str(out)) for src, out in self.reverse.items()]


@dataclass
class ACLRule:
    id: int
    permit: bool
    src: Optional[Tuple[IP, Mask]] = None
    dst: Optional[Tuple[IP, Mask]] = None
    proto: str = "any"

    def match(self, pkt: Packet) -> bool:
        if self.proto != "any":
            want = {"icmp": PacketType.ICMP_ECHO_REQUEST,
                    "icmp_req": PacketType.ICMP_ECHO_REQUEST,
                    "icmp_rep": PacketType.ICMP_ECHO_REPLY}.get(self.proto)
            if want is not None and pkt.type != want:
                return False
        if self.src is not None:
            ip, mask = self.src
            if pkt.src_ip.network(mask) != ip.network(mask):
                return False
        if self.dst is not None:
            ip, mask = self.dst
            if pkt.dst_ip.network(mask) != ip.network(mask):
                return False
        return True


class ACLConfig:
    def __init__(self):
        self.rules: List[Tuple[Optional[str], str, ACLRule]] = []

    def add(self, rule_id: int, iface: Optional[str], direction: str, rule: ACLRule) -> None:
        self.rules.append((iface, direction, rule))

    def check(self, in_iface: Optional[Iface], pkt: Packet, direction: str) -> bool:
        for iface_name, d, rule in self.rules:
            if d != direction:
                continue
            if iface_name is not None and (in_iface is None or in_iface.name != iface_name):
                continue
            if rule.match(pkt):
                return rule.permit
        return True


# --------------------------------------------------------------------------
# VRRP (floating virtual IP)
# --------------------------------------------------------------------------

@dataclass
class VRRPGroup:
    gid: int
    viface: str
    vip: IP
    priority: int = 100
    master: bool = False


class VRRPConfig:
    def __init__(self, device: Device):
        self.device = device
        self.groups: Dict[int, VRRPGroup] = {}

    def add(self, gid: int, viface: str, vip: IP, priority: int = 100) -> VRRPGroup:
        g = VRRPGroup(gid, viface, vip, priority)
        self.groups[gid] = g
        return g

    def apply(self, world) -> None:
        for gid, g in self.groups.items():
            best = None
            for d in world.vrrp_registry.get(gid, []):
                if d.vrrp is None:
                    continue
                gg = d.vrrp.groups.get(gid)
                if gg is None:
                    continue
                vif = d.net.find_iface_by_name(gg.viface)
                if vif is None or vif.state == LinkState.DOWN:
                    continue
                key = (gg.priority, vif.ip.value if vif.has_ip() else -1)
                if best is None or key > best[0]:
                    best = (key, d, gg, vif)
            for d in world.vrrp_registry.get(gid, []):
                if d.vrrp is None:
                    continue
                gg = d.vrrp.groups.get(gid)
                if gg is None:
                    continue
                vif = d.net.find_iface_by_name(gg.viface)
                is_master = best is not None and d is best[1]
                gg.master = is_master
                if vif is None:
                    continue
                if is_master:
                    if gg.vip not in vif.secondary_ips:
                        vif.secondary_ips.append(gg.vip)
                        world._log(d, vif, None, "vrrp",
                                   f"VRRP group {gid} master, VIP {gg.vip} active")
                else:
                    if gg.vip in vif.secondary_ips:
                        vif.secondary_ips.remove(gg.vip)
                        world._log(d, vif, None, "vrrp",
                                   f"VRRP group {gid} backup, VIP {gg.vip} withdrawn")

    def status(self) -> List[dict]:
        return [{"gid": g.gid, "vip": str(g.vip), "priority": g.priority,
                 "viface": g.viface, "state": "Master" if g.master else "Backup"}
                for g in self.groups.values()]