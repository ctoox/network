"""Interface, routing table (Trie LPM), and ARP cache."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .ip import IP, Mask

# Admin distance by protocol (Cisco/VRP semantics)
ADMIN_DISTANCE = {
    "C": 0,    # connected
    "D": 0,    # direct
    "S": 1,    # static
    "R": 120,  # RIP
    "O": 110,  # OSPF
    "I": 115,  # IS-IS
    "B": 20,   # BGP (eBGP) / 200 (iBGP)
    "H": 255,  # unreachable / host
}


class LinkState:
    DOWN = 0
    UP = 1


@dataclass(eq=False)
class Iface:
    name: str
    mac: bytes = field(default=b"\x00\x00\x00\x00\x00\x00")
    ip: IP = field(default=None)
    mask: Mask = field(default=None)
    state: int = LinkState.DOWN
    connected: Optional["Iface"] = None
    is_loopback: bool = False
    # link (channel) this interface attaches to; None for isolated
    link: Optional["Link"] = None
    # L2 switching attributes
    mode: str = "access"          # "access" | "trunk"
    access_vlan: int = 1          # access-port VLAN
    trunk_vlans: set = field(default_factory=set)  # permitted VLANs on trunk
    is_virtual: bool = False      # Vlanif / SVI logical interface
    secondary_ips: List[IP] = field(default_factory=list)  # VIP / VRRP float IP

    def has_ip(self) -> bool:
        return self.ip is not None and not self.ip.is_zero()

    def is_trunk(self) -> bool:
        return self.mode == "trunk"

    def owns(self, ip: IP) -> bool:
        return (self.has_ip() and self.ip == ip) or any(v == ip for v in self.secondary_ips)

    def carries_vlan(self, vlan: int) -> bool:
        if self.is_trunk():
            return vlan in self.trunk_vlans
        return self.access_vlan == vlan

    def network(self) -> IP:
        return self.ip.network(self.mask)

    def oper_state(self) -> int:
        if self.state == LinkState.DOWN or not self.has_ip():
            return LinkState.DOWN
        return LinkState.UP

    def __str__(self) -> str:
        if not self.has_ip():
            return f"{self.name} unnumbered"
        return f"{self.name} {self.ip}/{self.mask.prefix_len()}"


@dataclass
class Route:
    prefix: IP
    mask: Mask
    next_hop: Optional[IP] = None
    iface: Optional[Iface] = None
    proto: str = "S"
    metric: int = 1
    distance: int = 1

    def __post_init__(self):
        if self.distance == 1:
            self.distance = ADMIN_DISTANCE.get(self.proto, 1)

    def __str__(self) -> str:
        nh = "Direct" if self.next_hop is None else str(self.next_hop)
        return f"{self.prefix}/{self.mask.prefix_len()}  {self.proto}  {nh}  {self.metric}"


class RouteTable:
    """Prefix trie for longest-prefix-match lookup + per-protocol buckets.

    Each trie node holds a list of routes for that exact prefix (one per
    protocol). Lookup walks the trie to the deepest matching prefix and picks
    the best route by admin distance then metric (zebra/RIB semantics).
    """

    def __init__(self):
        self._root = TrieNode()
        self._count = 0

    def add(self, r: Route) -> None:
        node = self._walk(self._root, r.prefix, r.mask.prefix_len(), create=True)
        # replace the route for this protocol at this exact prefix
        for i, existing in enumerate(node.routes):
            if existing.proto == r.proto:
                node.routes[i] = r
                self._count += 0  # replacement, count unchanged
                break
        else:
            node.routes.append(r)
            self._count += 1

    def remove(self, prefix: IP, mask: Mask, proto: str = "") -> None:
        plen = mask.prefix_len()
        node = self._walk(self._root, prefix, plen, create=False)
        if node is None:
            return
        if proto:
            before = len(node.routes)
            node.routes = [r for r in node.routes if r.proto != proto]
            removed = before - len(node.routes)
            self._count -= removed
        else:
            self._count -= len(node.routes)
            node.routes = []
        self._prune(self._root, prefix, plen)

    def clear_proto(self, proto: str) -> None:
        # collect all prefixes carrying this protocol, then remove them
        prefixes = []
        def visit(node, bits, depth):
            if node.routes:
                for r in node.routes:
                    if r.proto == proto:
                        prefixes.append((r.prefix, r.mask))
            for bit, child in node.children.items():
                visit(child, bits | (bit << (31 - depth)), depth + 1)
        visit(self._root, 0, 0)
        for prefix, mask in prefixes:
            self.remove(prefix, mask, proto)

    def lookup(self, dst: IP) -> Optional[Route]:
        """Longest-prefix-match: deepest matching prefix wins; within that
        prefix, tie-break by admin distance then metric (zebra/RIB semantics)."""
        best: Optional[Route] = None
        best_depth = -1
        node = self._root
        net = dst.value
        for depth in range(33):
            if node.routes:
                cand = min(node.routes, key=lambda r: (r.distance, r.metric))
                if depth > best_depth or best is None:
                    best = cand
                    best_depth = depth
                elif depth == best_depth and (cand.distance, cand.metric) < (best.distance, best.metric):
                    best = cand
            if depth == 32:
                break
            bit = (net >> (31 - depth)) & 1
            node = node.children.get(bit)
            if node is None:
                break
        return best

    def _walk(self, node: TrieNode, prefix: IP, plen: int, create: bool) -> Optional[TrieNode]:
        net = prefix.value
        for i in range(plen):
            bit = (net >> (31 - i)) & 1
            nxt = node.children.get(bit)
            if nxt is None:
                if not create:
                    return None
                nxt = TrieNode()
                node.children[bit] = nxt
            node = nxt
        return node

    def _prune(self, node: TrieNode, prefix: IP, plen: int) -> None:
        if plen == 0:
            return
        net = prefix.value
        bits = [ (net >> (31 - i)) & 1 for i in range(plen) ]
        path = [self._root]
        cur = self._root
        for bit in bits:
            cur = cur.children.get(bit)
            if cur is None:
                return
            path.append(cur)
        for depth in range(plen, 0, -1):
            child = path[depth]
            if child.routes or child.children:
                break
            parent = path[depth - 1]
            bit = bits[depth - 1]
            del parent.children[bit]

    def all(self) -> List[Route]:
        out: List[Route] = []
        def visit(node: TrieNode):
            out.extend(node.routes)
            for child in node.children.values():
                visit(child)
        visit(self._root)
        return out

    def sorted(self) -> List[Route]:
        return sorted(
            self.all(),
            key=lambda r: (-r.mask.prefix_len(), r.prefix.value),
        )

    def __len__(self) -> int:
        return self._count

    def routes_for_proto(self, proto: str) -> List[Route]:
        return [r for r in self.all() if r.proto == proto]


class TrieNode:
    __slots__ = ("routes", "children")

    def __init__(self):
        self.routes: List[Route] = []
        self.children: Dict[int, TrieNode] = {}


@dataclass
class ARPEntry:
    ip: IP
    mac: bytes
    iface: Optional[Iface]
    expires: float


class ARPCache:
    def __init__(self, ttl: float = 1200.0):
        self.ttl = ttl
        self._entries: Dict[IP, ARPEntry] = {}

    def set(self, ip: IP, mac: bytes, iface: Optional[Iface], now: float) -> None:
        e = self._entries.get(ip)
        if e is not None:
            e.mac = mac
            e.iface = iface
            e.expires = now + self.ttl
        else:
            self._entries[ip] = ARPEntry(ip, mac, iface, now + self.ttl)

    def get(self, ip: IP) -> Optional[ARPEntry]:
        e = self._entries.get(ip)
        return e if e is not None else None

    def delete(self, ip: IP) -> None:
        self._entries.pop(ip, None)

    def prune(self, now: float) -> None:
        for k in [k for k, v in self._entries.items() if now >= v.expires]:
            del self._entries[k]

    def all(self) -> List[ARPEntry]:
        return list(self._entries.values())

    def has_mac(self, mac: bytes) -> bool:
        return any(e.mac == mac for e in self._entries.values())


class MacTable:
    """Layer-2 MAC address table for switches: (vlan, mac) -> egress iface."""

    def __init__(self):
        self._entries: Dict[tuple, Iface] = {}
        self._port_map: Dict[Iface, List[tuple]] = {}

    def learn(self, vlan: int, mac: bytes, iface: Iface) -> None:
        key = (vlan, mac)
        old = self._entries.get(key)
        if old is not None and old is not iface:
            self._port_map.get(old, []).remove(key)
        self._entries[key] = iface
        self._port_map.setdefault(iface, [])
        if key not in self._port_map[iface]:
            self._port_map[iface].append(key)

    def lookup(self, vlan: int, mac: bytes) -> Optional[Iface]:
        return self._entries.get((vlan, mac))

    def delete_port(self, iface: Iface) -> None:
        for key in self._port_map.pop(iface, []):
            self._entries.pop(key, None)

    def all(self) -> List[tuple]:
        return [(k[0], k[1], v) for k, v in self._entries.items()]


class DeviceNetwork:
    def __init__(self):
        self.interfaces: List[Iface] = []
        self.routes = RouteTable()
        self.arp = ARPCache()
        self.mac_table = MacTable()

    def add_interface(self, iface: Iface) -> None:
        self.interfaces.append(iface)

    def find_iface_by_name(self, name: str) -> Optional[Iface]:
        n = name.lower()
        for i in self.interfaces:
            if i.name.lower() == n:
                return i
        return None

    def find_iface_by_ip(self, ip: IP) -> Optional[Iface]:
        for i in self.interfaces:
            if i.has_ip() and i.ip == ip:
                return i
        return None

    def find_iface_containing(self, ip: IP) -> Optional[Iface]:
        for i in self.interfaces:
            if i.has_ip() and i.network() == ip.network(i.mask):
                return i
        return None

    def set_iface_ip(self, iface: Iface, ip: IP, mask: Mask) -> None:
        iface.ip = ip
        iface.mask = mask