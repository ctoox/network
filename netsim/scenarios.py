"""Pre-built network scenarios for the simulator.

Each scenario builds a fully addressed, routed (and often OSPF/VRRP/DHCP
enabled) World you can load instantly from the CLI or the web UI. Covers:

  smb       - 小型（≤100 人）：防火墙 + 接入交换机，NAT 上互联网
  medium    - 中型（200-1000 人）：双核心 VRRP + 接入，DHCP
  large     - 大型（1000-5000 人）：核心-汇聚-接入 三层架构 + OSPF
  campus    - 超大型园区（5000+ 人）：多区域 + 双核心 + OSPF
  datacenter- 数据中心：Spine-Leaf 扁平架构 + OSPF
  xinchuang - 信创：全国产（华为/华三/锐捷/华为USG）组网
  stress    - 压测：N 台路由器环网（虚拟时间批量仿真）
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .net.device import Device, DeviceType, Vendor
from .net.iface import Iface, LinkState, Route
from .net.ip import IP, Mask
from .net.proto import DHCPPool, OSPFInstance, VRRPConfig
from .net.world import World

SCENARIOS = ("smb", "medium", "large", "campus", "datacenter", "xinchuang", "stress")

_IFSEQ = [0]


def _mac(base: int, slot: int, port: int) -> bytes:
    return bytes([(base >> 16) & 0xFF, (base >> 8) & 0xFF, base & 0xFF, slot, 0, port])


def _mk(w: World, dev_id: str, name: str, dtype: DeviceType, vendor: Vendor,
        ports: List[Tuple[str, bytes]]) -> Device:
    dev = Device(dev_id, name, dtype, vendor, net=None)
    from .net.iface import DeviceNetwork
    dev.net = DeviceNetwork()
    for pname, mac in ports:
        dev.add_iface(Iface(name=pname, mac=mac))
    w.add_device(dev)
    return dev


def _link(w: World, a: Device, aif: str, b: Device, bif: str) -> None:
    w.connect(a.get_iface(aif), b.get_iface(bif))


def _ip(dev: Device, ifname: str, ip: str, plen: int) -> Iface:
    iface = dev.get_iface(ifname)
    if iface is None and ifname.lower().startswith("vlanif"):
        vlan = int(ifname[len("vlanif"):])
        iface = Iface(name=ifname, is_virtual=True, access_vlan=vlan,
                      mac=bytes([0x00, 0xe0, 0xfc, 0xff, 0x00, vlan]))
        dev.add_iface(iface)
        dev.vlans.add(vlan)
    if iface is None:
        raise KeyError(f"interface {ifname} not found on {dev.name}")
    o = IP.parse(ip)
    m = Mask.from_len(plen)
    iface.ip = o
    iface.mask = m
    iface.state = LinkState.UP
    dev.net.routes.add(Route(prefix=o.network(m), mask=m, iface=iface, proto="D", metric=0))
    return iface


def _static(dev: Device, net: str, plen: int, nh: Optional[str], ifname: Optional[str] = None) -> None:
    iface = dev.get_iface(ifname) if ifname else None
    if iface is None:
        for i in dev.net.interfaces:
            if not i.is_virtual and i.has_ip():
                iface = i
                break
    dev.net.routes.add(Route(prefix=IP.parse(net), mask=Mask.from_len(plen),
                             next_hop=IP.parse(nh) if nh else None, iface=iface, proto="S", metric=1))


def _access_port(dev: Device, ifname: str, vlan: int) -> None:
    iface = dev.get_iface(ifname)
    iface.mode = "access"
    iface.access_vlan = vlan
    dev.vlans.add(vlan)


def _trunk(dev: Device, ifname: str, vlans) -> None:
    iface = dev.get_iface(ifname)
    iface.mode = "trunk"
    iface.trunk_vlans = set(vlans)
    dev.vlans.update(iface.trunk_vlans)


def _ospf(w: World, dev: Device, router_id: Optional[str] = None) -> None:
    dev.ospf = OSPFInstance(dev).enable(w, router_id=router_id)


def _vrrp(w: World, group: int, devs: List[Device], viface: str, vip: str,
          priority: int = 100) -> None:
    for i, d in enumerate(devs):
        if d.vrrp is None:
            d.vrrp = VRRPConfig(d)
        d.vrrp.add(group, viface, IP.parse(vip), priority if i == 0 else priority - 10)
        w.register_vrrp(d, group)
    w.refresh_vrrp()


def _dhcp(dev: Device, subnet: str, plen: int, gw: str, vlan: int = 0) -> DHCPPool:
    net = IP.parse(subnet)
    mask = Mask.from_len(plen)
    pool = DHCPPool(net, mask, IP(net.value + 10), IP(net.broadcast(mask).value - 1),
                    gateway=IP.parse(gw), dns=IP.parse("114.114.114.114"), name="pool1")
    pool.vlan = vlan
    dev.dhcp_pools.append(pool)
    dev.dhcp = pool
    dev._dhcp_pool = pool
    return pool


def _dhcp_renew(w: World, host: Device, ifname: str, delay: float = 0.2) -> None:
    iface = host.get_iface(ifname)
    host.dhcp_client = {"mac": iface.mac}
    from .net.device import Packet, PacketType
    from .net.ip import IP as _IP
    pkt = Packet(type=PacketType.DHCP_DISCOVER, src_ip=_IP(0), dst_ip=_IP(0xFFFFFFFF),
                 src_mac=iface.mac, dst_mac=b"\xff" * 6, ttl=16)
    w._schedule(delay, w.send_via_iface, host, iface, pkt)


def _finish(w: World, settle: float = 8.0) -> World:
    """Let protocols (OSPF/VRRP) converge, then return the world."""
    w.refresh_vrrp()
    w.settle(settle)
    return w


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def build_smb() -> World:
    """小型：防火墙 + 2 台接入交换机，单 VLAN，NAT 上互联网。"""
    w = World(realtime=False)
    fw = _mk(w, "fw1", "FW1", DeviceType.FIREWALL, Vendor.HUAWEI_FW,
             [("GE1/0/0", _mac(0x001e, 1, 1)), ("GE1/0/1", _mac(0x001e, 1, 2))])
    sw1 = _mk(w, "sw1", "SW-A1", DeviceType.SWITCH, Vendor.H3C,
              [("GE1/0/1", _mac(0x000e, 2, 1)), ("GE1/0/2", _mac(0x000e, 2, 2)),
               ("GE1/0/3", _mac(0x000e, 2, 3)), ("GE1/0/23", _mac(0x000e, 2, 23)),
               ("GE1/0/24", _mac(0x000e, 2, 24))])
    sw2 = _mk(w, "sw2", "SW-A2", DeviceType.SWITCH, Vendor.H3C,
              [("GE1/0/1", _mac(0x000e, 3, 1)), ("GE1/0/2", _mac(0x000e, 3, 2)),
               ("GE1/0/3", _mac(0x000e, 3, 3)), ("GE1/0/24", _mac(0x000e, 3, 24))])
    hosts = []
    for i in range(4):
        h = _mk(w, f"pc{i}", f"PC{i}", DeviceType.HOST, Vendor.RUIJIE,
                [("Ethernet0/0", _mac(0x0022, 10 + i, 1))])
        hosts.append(h)
        t = sw1 if i < 2 else sw2
        _link(w, h, "Ethernet0/0", t, "GE1/0/1" if i == 0 else ("GE1/0/2" if i == 1 else "GE1/0/3"))
    _link(w, sw1, "GE1/0/24", sw2, "GE1/0/24")
    _link(w, fw, "GE1/0/0", sw1, "GE1/0/23")

    for h in hosts:
        t = sw1 if hosts.index(h) < 2 else sw2
        _access_port(t, "GE1/0/1" if hosts.index(h) == 0 else ("GE1/0/2" if hosts.index(h) == 1 else "GE1/0/3"), 10)
    _access_port(sw1, "GE1/0/23", 10)
    _trunk(sw1, "GE1/0/24", {10})
    _trunk(sw2, "GE1/0/24", {10})

    _ip(fw, "GE1/0/0", "10.0.10.254", 24)
    _ip(fw, "GE1/0/1", "203.0.113.1", 30)
    _static(fw, "0.0.0.0", 0, "203.0.113.2")
    from .net.proto import NATConfig
    fw.nat = NATConfig(inside=["GE1/0/0"], outside="GE1/0/1")

    for i, h in enumerate(hosts):
        _ip(h, "Ethernet0/0", f"10.0.10.{i + 2}", 24)
        _static(h, "0.0.0.0", 0, "10.0.10.254")
    _finish(w, settle=0.5)
    return w


def build_medium() -> World:
    """中型：双核心 VRRP + 接入，VLAN 10/20，核心 DHCP，核心-接入 OSPF。"""
    w = World(realtime=False)
    c1 = _mk(w, "c1", "SW-C1", DeviceType.SWITCH, Vendor.H3C,
             [("GE1/0/1", _mac(0x000c, 1, 1)), ("GE1/0/2", _mac(0x000c, 1, 2)),
              ("GE1/0/23", _mac(0x000c, 1, 23)), ("GE1/0/24", _mac(0x000c, 1, 24))])
    c2 = _mk(w, "c2", "SW-C2", DeviceType.SWITCH, Vendor.H3C,
             [("GE1/0/1", _mac(0x000c, 2, 1)), ("GE1/0/2", _mac(0x000c, 2, 2)),
              ("GE1/0/23", _mac(0x000c, 2, 23)), ("GE1/0/24", _mac(0x000c, 2, 24))])
    a1 = _mk(w, "a1", "SW-A1", DeviceType.SWITCH, Vendor.RUIJIE,
             [("GE0/1", _mac(0x000e, 1, 1)), ("GE0/2", _mac(0x000e, 1, 2)),
              ("GE0/3", _mac(0x000e, 1, 3)), ("GE0/4", _mac(0x000e, 1, 4))])
    a2 = _mk(w, "a2", "SW-A2", DeviceType.SWITCH, Vendor.RUIJIE,
             [("GE0/1", _mac(0x000e, 2, 1)), ("GE0/2", _mac(0x000e, 2, 2)),
              ("GE0/3", _mac(0x000e, 2, 3)), ("GE0/4", _mac(0x000e, 2, 4))])
    hosts = []
    for i in range(6):
        h = _mk(w, f"pc{i}", f"PC{i}", DeviceType.HOST, Vendor.HUAWEI,
                [("Ethernet0/0/0", _mac(0x0022, 20 + i, 1))])
        hosts.append(h)
    _link(w, c1, "GE1/0/23", c2, "GE1/0/23")
    _link(w, c1, "GE1/0/24", c2, "GE1/0/24")
    _link(w, c1, "GE1/0/1", a1, "GE0/1")
    _link(w, c1, "GE1/0/2", a2, "GE0/1")
    for i, h in enumerate(hosts):
        target = a1 if i < 3 else a2
        _link(w, h, "Ethernet0/0/0", target, f"GE0/{i % 3 + 2}")

    for sw in (c1, c2):
        for v in (10, 20):
            sw.vlans.add(v)
            _ip(sw, f"Vlanif{v}", f"10.0.{v}.{'1' if sw is c1 else '2'}", 24)
        _trunk(sw, "GE1/0/23", {10, 20})
        _trunk(sw, "GE1/0/24", {10, 20})
        _trunk(sw, "GE1/0/1", {10, 20})
        _trunk(sw, "GE1/0/2", {10, 20})
    _trunk(a1, "GE0/1", {10, 20})
    _trunk(a2, "GE0/1", {10, 20})
    for i, h in enumerate(hosts):
        t = a1 if i < 3 else a2
        _access_port(t, f"GE0/{i % 3 + 2}", 10 if i < 3 else 20)
        # DHCP: host starts unaddressed, obtains lease from core
        h.dhcp_client = {"mac": h.get_iface("Ethernet0/0/0").mac}

    _vrrp(w, 10, [c1, c2], "Vlanif10", "10.0.10.254", 120)
    _vrrp(w, 20, [c1, c2], "Vlanif20", "10.0.20.254", 100)
    _dhcp(c1, "10.0.10.0", 24, "10.0.10.254", vlan=10)
    _dhcp(c1, "10.0.20.0", 24, "10.0.20.254", vlan=20)
    _ospf(w, c1)
    _ospf(w, c2)
    for h in hosts:
        _dhcp_renew(w, h, "Ethernet0/0/0", delay=0.3)
    return _finish(w, settle=8.0)


def build_large() -> World:
    """大型：核心-汇聚-接入 三层，双核心 VRRP + 双汇聚，OSPF。"""
    w = World(realtime=False)
    core1 = _mk(w, "core1", "CORE-1", DeviceType.SWITCH, Vendor.HUAWEI,
                [("GE0/0/0", _mac(0x0010, 1, 0)), ("GE0/0/1", _mac(0x0010, 1, 1)),
                 ("GE0/0/2", _mac(0x0010, 1, 2)), ("GE0/0/3", _mac(0x0010, 1, 3))])
    core2 = _mk(w, "core2", "CORE-2", DeviceType.SWITCH, Vendor.HUAWEI,
                [("GE0/0/0", _mac(0x0010, 2, 0)), ("GE0/0/1", _mac(0x0010, 2, 1)),
                 ("GE0/0/2", _mac(0x0010, 2, 2)), ("GE0/0/3", _mac(0x0010, 2, 3))])
    agg1 = _mk(w, "agg1", "AGG-1", DeviceType.SWITCH, Vendor.H3C,
               [("GE1/0/1", _mac(0x0012, 1, 1)), ("GE1/0/2", _mac(0x0012, 1, 2)),
                ("GE1/0/24", _mac(0x0012, 1, 24))])
    agg2 = _mk(w, "agg2", "AGG-2", DeviceType.SWITCH, Vendor.H3C,
               [("GE1/0/1", _mac(0x0012, 2, 1)), ("GE1/0/2", _mac(0x0012, 2, 2)),
                ("GE1/0/24", _mac(0x0012, 2, 24))])
    acc1 = _mk(w, "acc1", "ACC-1", DeviceType.SWITCH, Vendor.RUIJIE,
               [("GE0/1", _mac(0x0014, 1, 1)), ("GE0/2", _mac(0x0014, 1, 2)),
                ("GE0/3", _mac(0x0014, 1, 3)), ("GE0/4", _mac(0x0014, 1, 4)),
                ("GE0/5", _mac(0x0014, 1, 5)), ("GE0/6", _mac(0x0014, 1, 6))])
    acc2 = _mk(w, "acc2", "ACC-2", DeviceType.SWITCH, Vendor.RUIJIE,
               [("GE0/1", _mac(0x0014, 2, 1)), ("GE0/2", _mac(0x0014, 2, 2)),
                ("GE0/3", _mac(0x0014, 2, 3)), ("GE0/4", _mac(0x0014, 2, 4)),
                ("GE0/5", _mac(0x0014, 2, 5)), ("GE0/6", _mac(0x0014, 2, 6))])
    hosts = [_mk(w, f"pc{i}", f"PC{i}", DeviceType.HOST, Vendor.HUAWEI,
                 [("Ethernet0/0/0", _mac(0x0024, 30 + i, 1))]) for i in range(8)]

    _link(w, core1, "GE0/0/0", core2, "GE0/0/0")
    _link(w, core1, "GE0/0/1", agg1, "GE1/0/1")
    _link(w, core1, "GE0/0/2", agg2, "GE1/0/1")
    _link(w, core2, "GE0/0/1", agg1, "GE1/0/2")
    _link(w, core2, "GE0/0/2", agg2, "GE1/0/2")
    _link(w, agg1, "GE1/0/24", acc1, "GE0/1")
    _link(w, agg2, "GE1/0/24", acc2, "GE0/1")
    for i, h in enumerate(hosts):
        t = acc1 if i < 4 else acc2
        _link(w, h, "Ethernet0/0/0", t, f"GE0/{i % 4 + 2}")

    for c in (core1, core2):
        pass  # cores are pure L3 backbone: only routed /30 links + OSPF
    # P2P routed links (each /30 is shared by both endpoints)
    _ip(core1, "GE0/0/0", "10.1.0.1", 30)
    _ip(core2, "GE0/0/0", "10.1.0.2", 30)
    _ip(core1, "GE0/0/1", "10.1.0.5", 30)
    _ip(agg1, "GE1/0/1", "10.1.0.6", 30)
    _ip(core1, "GE0/0/2", "10.1.0.9", 30)
    _ip(agg2, "GE1/0/1", "10.1.0.10", 30)
    _ip(core2, "GE0/0/1", "10.1.1.1", 30)
    _ip(agg1, "GE1/0/2", "10.1.1.2", 30)
    _ip(core2, "GE0/0/2", "10.1.1.5", 30)
    _ip(agg2, "GE1/0/2", "10.1.1.6", 30)
    for c in (core1, core2):
        _ospf(w, c)
    # each aggregate is the L3 gateway for its own user VLAN (no overlap,
    # so each VLAN's L2 segment lives under a single gateway device)
    _ip(agg1, "Vlanif10", "10.0.10.1", 24)
    agg1.vlans.add(10)
    _trunk(agg1, "GE1/0/24", {10})
    _ospf(w, agg1)
    _ip(agg2, "Vlanif20", "10.0.20.2", 24)
    agg2.vlans.add(20)
    _trunk(agg2, "GE1/0/24", {20})
    _ospf(w, agg2)
    _trunk(acc1, "GE0/1", {10})
    _trunk(acc2, "GE0/1", {20})
    for i, h in enumerate(hosts):
        t = acc1 if i < 4 else acc2
        _access_port(t, f"GE0/{i % 4 + 2}", 10 if i < 4 else 20)
        _ip(h, "Ethernet0/0/0", f"10.0.{10 if i < 4 else 20}.{i + 2}", 24)
        _static(h, "0.0.0.0", 0, f"10.0.{10 if i < 4 else 20}.{1 if i < 4 else 2}")
    return _finish(w, settle=10.0)


def build_campus() -> World:
    """超大型园区：双核心 + 多区域（办公/研发/生产），OSPF 区域互联。"""
    w = World(realtime=False)
    core_ports = [f"GE0/0/{i}" for i in range(8)]
    core1 = _mk(w, "core1", "CORE-1", DeviceType.SWITCH, Vendor.HUAWEI,
                [(p, _mac(0x0020, 1, i)) for i, p in enumerate(core_ports)])
    core2 = _mk(w, "core2", "CORE-2", DeviceType.SWITCH, Vendor.HUAWEI,
                [(p, _mac(0x0020, 2, i)) for i, p in enumerate(core_ports)])
    _link(w, core1, "GE0/0/0", core2, "GE0/0/0")
    _ip(core1, "GE0/0/0", "10.255.0.1", 30)
    _ip(core2, "GE0/0/0", "10.255.0.2", 30)

    zones = [("zone1", "办公", 0), ("zone2", "研发", 1), ("zone3", "生产", 2)]
    for zi, (zname, zlabel, idx) in enumerate(zones):
        agg1 = _mk(w, f"agg{zi}a", f"{zlabel}AGG-A", DeviceType.SWITCH, Vendor.H3C,
                   [("GE1/0/1", _mac(0x0030, 10 + zi, 1)), ("GE1/0/2", _mac(0x0030, 10 + zi, 2)),
                    ("GE1/0/3", _mac(0x0030, 10 + zi, 3)), ("GE1/0/24", _mac(0x0030, 10 + zi, 24))])
        agg2 = _mk(w, f"agg{zi}b", f"{zlabel}AGG-B", DeviceType.SWITCH, Vendor.H3C,
                   [("GE1/0/1", _mac(0x0030, 20 + zi, 1)), ("GE1/0/2", _mac(0x0030, 20 + zi, 2)),
                    ("GE1/0/3", _mac(0x0030, 20 + zi, 3)), ("GE1/0/24", _mac(0x0030, 20 + zi, 24))])
        # two uplinks per agg, one to each core (dedicated core ports)
        _link(w, agg1, "GE1/0/1", core1, f"GE0/0/{zi * 2 + 1}")
        _link(w, agg1, "GE1/0/2", core2, f"GE0/0/{zi * 2 + 1}")
        _link(w, agg2, "GE1/0/1", core1, f"GE0/0/{zi * 2 + 2}")
        _link(w, agg2, "GE1/0/2", core2, f"GE0/0/{zi * 2 + 2}")
        _ip(core1, f"GE0/0/{zi * 2 + 1}", f"10.255.{zi + 1}.{1 if idx == 0 else 5}", 30)
        _ip(core2, f"GE0/0/{zi * 2 + 1}", f"10.255.{zi + 1}.{9 if idx == 0 else 13}", 30)
        _ip(core1, f"GE0/0/{zi * 2 + 2}", f"10.255.{zi + 1}.{3 if idx == 0 else 7}", 30)
        _ip(core2, f"GE0/0/{zi * 2 + 2}", f"10.255.{zi + 1}.{11 if idx == 0 else 15}", 30)
        vlan = 10 + zi * 10
        for a in (agg1, agg2):
            a.vlans.add(vlan)
            _ip(a, f"Vlanif{vlan}", f"10.{zi + 1}.{vlan}.{'1' if a is agg1 else '2'}", 24)
            _ip(a, "GE1/0/1", f"10.255.{zi + 1}.{2 if a is agg1 else 4}", 30)
            _ip(a, "GE1/0/2", f"10.255.{zi + 1}.{10 if a is agg1 else 12}", 30)
            _trunk(a, "GE1/0/24", {vlan})
            _ospf(w, a)
        # a couple of hosts per zone
        for i in range(2):
            h = _mk(w, f"{zname}p{i}", f"{zlabel}PC{i}", DeviceType.HOST, Vendor.HUAWEI,
                    [("Ethernet0/0/0", _mac(0x0040, zi * 10 + i, 1))])
            _link(w, h, "Ethernet0/0/0", agg1 if i == 0 else agg2, "GE1/0/3")
            _access_port(agg1 if i == 0 else agg2, "GE1/0/3", vlan)
            _ip(h, "Ethernet0/0/0", f"10.{zi + 1}.{vlan}.{i + 2}", 24)
            _static(h, "0.0.0.0", 0, f"10.{zi + 1}.{vlan}.254")
        _vrrp(w, vlan, [agg1, agg2], f"Vlanif{vlan}", f"10.{zi + 1}.{vlan}.254", 110)

    for c in (core1, core2):
        _ospf(w, c)
    return _finish(w, settle=12.0)


def build_datacenter() -> World:
    """数据中心：2 台 Spine + 4 台 Leaf，服务器双挂，OSPF 动态路由。"""
    w = World(realtime=False)
    spines = []
    for i in range(2):
        s = _mk(w, f"spine{i}", f"SPINE-{i + 1}", DeviceType.ROUTER, Vendor.HUAWEI,
                [("GE0/0/0", _mac(0x0050, i + 1, 0)), ("GE0/0/1", _mac(0x0050, i + 1, 1)),
                 ("GE0/0/2", _mac(0x0050, i + 1, 2)), ("GE0/0/3", _mac(0x0050, i + 1, 3))])
        for j in range(4):
            _ip(s, f"GE0/0/{j}", f"10.254.{j}.{1 if i == 0 else 5}", 30)
        _ospf(w, s)
        spines.append(s)
    for leaf_i in range(4):
        leaf = _mk(w, f"leaf{leaf_i}", f"LEAF-{leaf_i + 1}", DeviceType.SWITCH, Vendor.RUIJIE,
                   [("GE0/0", _mac(0x0051, leaf_i + 1, 0)), ("GE0/1", _mac(0x0051, leaf_i + 1, 1)),
                    ("GE0/2", _mac(0x0051, leaf_i + 1, 2)), ("GE0/3", _mac(0x0051, leaf_i + 1, 3))])
        for s in range(2):
            _link(w, leaf, f"GE0/{s}", spines[s], f"GE0/0/{leaf_i}")
            _ip(leaf, f"GE0/{s}", f"10.254.{leaf_i}.{2 if s == 0 else 6}", 30)
        # each server lives in its own /30 routed segment off the leaf (the
        # leaf port is the gateway); never share one gateway IP across ports
        _ip(leaf, "GE0/2", f"192.168.{leaf_i + 1}.1", 30)
        _ip(leaf, "GE0/3", f"192.168.{leaf_i + 1}.5", 30)
        _ospf(w, leaf)
        for srv in range(2):
            server = _mk(w, f"srv{leaf_i}{srv}", f"SRV-{leaf_i + 1}-{srv + 1}",
                         DeviceType.HOST, Vendor.HUAWEI,
                         [("Ethernet0/0/0", _mac(0x0060, leaf_i * 2 + srv, 1))])
            _link(w, server, "Ethernet0/0/0", leaf, f"GE0/{2 + srv}")
            _ip(server, "Ethernet0/0/0", f"192.168.{leaf_i + 1}.{2 if srv == 0 else 6}", 30)
            _static(server, "0.0.0.0", 0, f"192.168.{leaf_i + 1}.{1 if srv == 0 else 5}")
    return _finish(w, settle=12.0)


def build_xinchuang() -> World:
    """信创：全国产组网。华为核心 + 华三/锐捷汇聚接入 + 华为USG + 麒麟终端。"""
    w = World(realtime=False)
    fw = _mk(w, "fw1", "FW-GW", DeviceType.FIREWALL, Vendor.HUAWEI_FW,
             [("GE1/0/0", _mac(0x000a, 1, 1)), ("GE1/0/1", _mac(0x000a, 1, 2))])
    core1 = _mk(w, "core1", "Kylin-CORE-1", DeviceType.SWITCH, Vendor.HUAWEI,
                [("GE0/0/0", _mac(0x000b, 1, 0)), ("GE0/0/1", _mac(0x000b, 1, 1)),
                 ("GE0/0/2", _mac(0x000b, 1, 2))])
    core2 = _mk(w, "core2", "Kylin-CORE-2", DeviceType.SWITCH, Vendor.HUAWEI,
                [("GE0/0/0", _mac(0x000b, 2, 0)), ("GE0/0/1", _mac(0x000b, 2, 1)),
                 ("GE0/0/2", _mac(0x000b, 2, 2))])
    agg = _mk(w, "agg1", "NEOS-AGG-1", DeviceType.SWITCH, Vendor.H3C,
              [("GE1/0/1", _mac(0x000c, 1, 1)), ("GE1/0/2", _mac(0x000c, 1, 2)),
               ("GE1/0/24", _mac(0x000c, 1, 24))])
    agg2 = _mk(w, "agg2", "NEOS-AGG-2", DeviceType.SWITCH, Vendor.H3C,
               [("GE1/0/1", _mac(0x000c, 2, 1)), ("GE1/0/2", _mac(0x000c, 2, 2)),
                ("GE1/0/24", _mac(0x000c, 2, 24))])
    acc = _mk(w, "acc1", "RG-ACC-1", DeviceType.SWITCH, Vendor.RUIJIE,
              [("GE0/1", _mac(0x000d, 1, 1)), ("GE0/2", _mac(0x000d, 1, 2)),
               ("GE0/3", _mac(0x000d, 1, 3)), ("GE0/4", _mac(0x000d, 1, 4)),
               ("GE0/5", _mac(0x000d, 1, 5)), ("GE0/6", _mac(0x000d, 1, 6))])
    hosts = [_mk(w, f"pc{i}", f"KylinPC{i}", DeviceType.HOST, Vendor.HUAWEI,
                 [("Ethernet0/0/0", _mac(0x000f, i + 1, 1))]) for i in range(4)]

    _link(w, fw, "GE1/0/0", core1, "GE0/0/0")
    _link(w, core1, "GE0/0/1", core2, "GE0/0/1")
    _link(w, core1, "GE0/0/2", agg, "GE1/0/1")
    _link(w, core2, "GE0/0/2", agg2, "GE1/0/1")
    _link(w, agg, "GE1/0/24", acc, "GE0/1")
    _link(w, agg2, "GE1/0/24", acc, "GE0/2")
    for i, h in enumerate(hosts):
        _link(w, h, "Ethernet0/0/0", acc, f"GE0/{i + 3}")

    _ip(fw, "GE1/0/0", "10.99.0.254", 30)
    _ip(core1, "GE0/0/0", "10.99.0.253", 30)
    _ip(core1, "GE0/0/1", "10.99.0.1", 30)
    _ip(core2, "GE0/0/1", "10.99.0.2", 30)
    _ip(core1, "GE0/0/2", "10.99.0.5", 30)
    _ip(agg, "GE1/0/1", "10.99.0.6", 30)
    _ip(core2, "GE0/0/2", "10.99.0.9", 30)
    _ip(agg2, "GE1/0/1", "10.99.0.10", 30)
    for c in (core1, core2):
        _ospf(w, c)  # cores are pure L3 backbone (routed links only)
    _ip(fw, "GE1/0/1", "203.0.113.1", 30)
    _static(fw, "0.0.0.0", 0, "203.0.113.2")
    _static(fw, "10.0.0.0", 16, "10.99.0.253")
    from .net.proto import NATConfig
    fw.nat = NATConfig(inside=["GE1/0/0"], outside="GE1/0/1")
    for a in (agg, agg2):
        a.vlans.add(10 if a is agg else 20)
        _ip(a, f"Vlanif{10 if a is agg else 20}", f"10.0.{10 if a is agg else 20}.{'1' if a is agg else '2'}", 24)
        _trunk(a, "GE1/0/24", {10 if a is agg else 20})
        _ospf(w, a)
    _trunk(acc, "GE0/1", {10})
    _trunk(acc, "GE0/2", {20})
    for i, h in enumerate(hosts):
        _access_port(acc, f"GE0/{i + 3}", 10 if i < 2 else 20)
        _ip(h, "Ethernet0/0/0", f"10.0.{10 if i < 2 else 20}.{i + 2}", 24)
        _static(h, "0.0.0.0", 0, f"10.0.{10 if i < 2 else 20}.{1 if i < 2 else 2}")
    return _finish(w, settle=12.0)


def build_stress(n: int = 64, ring: bool = True) -> World:
    """压测：n 台路由器（环网或带弦环），OSPF 收敛 + 全网可达。"""
    w = World(realtime=False)
    routers = []
    ports = [("GE0/0/0", 0), ("GE0/0/1", 1), ("GE0/0/2", 2),
             ("GE0/0/3", 3), ("GE0/0/4", 4)]
    for i in range(n):
        r = _mk(w, f"r{i}", f"R{i}", DeviceType.ROUTER, Vendor.HUAWEI,
                [(p, _mac(0x0070, i, k)) for p, k in ports])
        routers.append(r)
        _ip(r, "GE0/0/0", f"10.{i // 250}.{i % 250}.1", 30)  # stub / router-id
    if ring:
        for i in range(n):
            a, b = routers[i], routers[(i + 1) % n]
            _link(w, a, "GE0/0/1", b, "GE0/0/2")
            _ip(a, "GE0/0/1", f"10.200.{i}.1", 30)
            _ip(b, "GE0/0/2", f"10.200.{i}.2", 30)
    else:
        # chorded ring: link i->i+1 on GE0/0/1/2 and i->i+2 on GE0/0/3/4
        for i in range(n):
            a, b = routers[i], routers[(i + 1) % n]
            _link(w, a, "GE0/0/1", b, "GE0/0/2")
            _ip(a, "GE0/0/1", f"10.200.{i}.1", 30)
            _ip(b, "GE0/0/2", f"10.200.{i}.2", 30)
        for i in range(n):
            a, b = routers[i], routers[(i + 2) % n]
            _link(w, a, "GE0/0/3", b, "GE0/0/4")
            _ip(a, "GE0/0/3", f"10.201.{i}.1", 30)
            _ip(b, "GE0/0/4", f"10.201.{i}.2", 30)
    for r in routers:
        _ospf(w, r)
    return _finish(w, settle=12.0)


_BUILDERS = {
    "smb": build_smb,
    "medium": build_medium,
    "large": build_large,
    "campus": build_campus,
    "datacenter": build_datacenter,
    "xinchuang": build_xinchuang,
}


def load(name: str, **kwargs) -> World:
    if name not in _BUILDERS:
        raise KeyError(f"unknown scenario {name!r}; available: {', '.join(_BUILDERS)}")
    return _BUILDERS[name](**kwargs)


def describe(name: str) -> str:
    return {
        "smb": "小型（≤100人）：华为USG防火墙 + 华三接入，NAT 上网",
        "medium": "中型（200-1000人）：华三双核心 VRRP + 锐捷接入，核心 DHCP",
        "large": "大型（1000-5000人）：华为核心-华三汇聚-锐捷接入，OSPF",
        "campus": "超大型园区（5000+人）：华为双核心 + 多区域，OSPF 互联",
        "datacenter": "数据中心：华为 Spine + 锐捷 Leaf，OSPF 扁平网络",
        "xinchuang": "信创：全国产（华为/华三/锐捷/华为USG/麒麟）组网",
    }.get(name, name)