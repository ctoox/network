"""Discrete-event simulation world: devices, links, forwarding, protocols.

The World owns the Scheduler (virtual/realtime), the device map, and the
link/channel set. Packet forwarding, ARP, ICMP, ping, OSPF, DHCP, VLAN/switch
switching, NAT, ACL and VRRP are all driven by scheduled events (no asyncio in
the data plane).
"""

from __future__ import annotations

from copy import copy as _copy_pkt
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .device import BROADCAST_MAC, Device, DeviceType, Packet, PacketType
from .iface import Iface, LinkState, Route
from .ip import IP, Mask
from ..core.scheduler import Scheduler, Event

DEFAULT_DELAY = 0.01  # seconds per hop
DEFAULT_TTL = 64

# A packet can traverse at most DEFAULT_TTL hops before TTL expires, so the
# worst-case round-trip is 2 * TTL * per-hop-delay. Ping timeout must exceed
# that or legitimate long paths would be reported as timeouts.
DEFAULT_PING_TIMEOUT = 2 * DEFAULT_TTL * DEFAULT_DELAY + 0.5  # 1.78s

# Backward-compatible alias so existing imports keep working.
DEFAULT_HOP_DELAY = DEFAULT_DELAY

ZERO_MAC = b"\x00\x00\x00\x00\x00\x00"


@dataclass
class Link:
    """A point-to-point channel between two interfaces.

    Delivers packets with a fixed delay; keeps endpoints for topology export.
    """
    id: str
    a: Iface
    b: Iface
    delay: float = DEFAULT_DELAY

    def other(self, iface: Iface) -> Optional[Iface]:
        if iface is self.a:
            return self.b
        if iface is self.b:
            return self.a
        return None


EventCallback = Callable[[Device, Optional[Iface], Packet, str, str], None]


class World:
    def __init__(self, on_event: Optional[EventCallback] = None, realtime: bool = True):
        self.devices: Dict[str, Device] = {}
        self.links: List[Link] = []
        self.on_event = on_event
        self._seq = 0
        self.scheduler = Scheduler(realtime=realtime)
        self.loop = self.scheduler._loop  # backward compat: asyncio loop (realtime)
        self._pending: Dict[str, dict] = {}
        self._iface_index: Dict[int, Device] = {}  # id(iface) -> device
        self.vrrp_registry: Dict[int, List[Device]] = {}  # group id -> devices

    # ---- time ----

    def now(self) -> float:
        return self.scheduler.now()

    def advance(self, step: float) -> None:
        self.scheduler.advance(step)

    def run(self) -> None:
        self.scheduler.run_forever()

    def settle(self, seconds: float = 10.0) -> None:
        """Fast-forward virtual time so protocols (OSPF hello/SPF, VRRP,
        DHCP) can converge before traffic is injected."""
        if not self.scheduler.realtime:
            self.scheduler.run(until=self.now() + seconds)

    def _schedule(self, delay: float, fn, *args) -> Event:
        return self.scheduler.schedule(delay, fn, *args)

    # ---- topology ----

    def add_device(self, dev: Device):
        self.devices[dev.id] = dev
        dev.on_packet = self._handle_packet
        for iface in dev.net.interfaces:
            self._iface_index[id(iface)] = dev

    def connect(self, a: Iface, b: Iface) -> Link:
        self._seq += 1
        link = Link(id=f"L{self._seq}", a=a, b=b)
        self.links.append(link)
        a.connected = b
        b.connected = a
        a.link = link
        b.link = link
        a.state = LinkState.UP
        b.state = LinkState.UP
        # index endpoints so forwarding is O(1) even when interfaces were
        # added to the device after add_device()
        self._index_iface(a)
        self._index_iface(b)
        self.refresh_vrrp()
        return link

    def _index_iface(self, iface: Iface) -> None:
        for d in self.devices.values():
            for i in d.net.interfaces:
                if i is iface:
                    self._iface_index[id(iface)] = d
                    return

    def get_device(self, device_id: str) -> Optional[Device]:
        return self.devices.get(device_id)

    def device_for_iface(self, iface: Iface) -> Optional[Device]:
        dev = self._iface_index.get(id(iface))
        if dev is not None:
            return dev
        for d in self.devices.values():
            for i in d.net.interfaces:
                if i is iface:
                    self._iface_index[id(iface)] = d
                    return d
        return None

    # ---- VRRP ----

    def register_vrrp(self, dev: Device, group_id: int) -> None:
        devs = self.vrrp_registry.setdefault(group_id, [])
        if dev not in devs:
            devs.append(dev)

    def refresh_vrrp(self) -> None:
        for devs in self.vrrp_registry.values():
            for d in devs:
                if d.vrrp is not None:
                    d.vrrp.apply(self)

    # ---- event log ----

    def _log(self, dev: Device, iface: Optional[Iface], pkt: Packet, action: str, detail: str):
        if self.on_event:
            self.on_event(dev, iface, pkt, action, detail)

    # ---- packet handling ----

    def _handle_packet(self, dev: Device, in_iface: Optional[Iface], pkt: Packet):
        # learn the source MAC on L2 switches for every frame (including
        # control-plane frames consumed below) so that replies such as DHCP
        # OFFER/ACK can be unicast to the right port instead of flooded.
        if dev.type == DeviceType.SWITCH and in_iface is not None:
            vlan = self._ingress_vlan(dev, in_iface, pkt)
            pkt.vlan = vlan
            if pkt.src_mac and pkt.src_mac != BROADCAST_MAC:
                dev.net.mac_table.learn(vlan, pkt.src_mac, in_iface)

        # control-plane protocols handled before any forwarding
        if pkt.type == PacketType.OSPF_HELLO:
            if dev.ospf is not None:
                dev.ospf.on_hello(self, in_iface, pkt)
            return
        if pkt.type in (PacketType.DHCP_DISCOVER, PacketType.DHCP_REQUEST):
            pool = self._dhcp_pool_for(dev, getattr(pkt, "vlan", 0))
            if pool is not None:
                pool.on_message(self, dev, in_iface, pkt)
                return
        if pkt.type in (PacketType.DHCP_OFFER, PacketType.DHCP_ACK):
            if dev.dhcp_client is not None:
                self._dhcp_client_consume(dev, in_iface, pkt)
                return

        # ingress ACL
        if dev.acl is not None and not dev.acl.check(in_iface, pkt, direction="in"):
            self._log(dev, in_iface, pkt, "drop", "ACL denied (inbound)")
            return

        if dev.type == DeviceType.SWITCH:
            return self._switch_process(dev, in_iface, pkt)

        # locally destined?
        if self._owns_ip(dev, pkt.dst_ip):
            if pkt.type == PacketType.ARP_REQUEST:
                self._process_arp_request(dev, in_iface, pkt)
            elif pkt.type == PacketType.ARP_REPLY:
                self._process_arp_reply(dev, in_iface, pkt)
            elif pkt.type == PacketType.ICMP_ECHO_REQUEST:
                self._reply_echo(dev, in_iface, pkt)
            elif pkt.type == PacketType.ICMP_ECHO_REPLY:
                self._signal_ping_reply(pkt)
            return

        # learn ARP from direct source
        if pkt.src_mac != ZERO_MAC and in_iface is not None and in_iface.has_ip():
            if pkt.src_ip.network(in_iface.mask) == in_iface.network():
                dev.net.arp.set(pkt.src_ip, pkt.src_mac, in_iface, self.now())

        # hosts are endpoints: they never relay a frame addressed to someone
        # else. Without this, a host that received a flooded broadcast would
        # _forward() it straight back onto the wire -> infinite L2 loop.
        if dev.type == DeviceType.HOST:
            self._log(dev, in_iface, pkt, "drop", "not addressed to this host")
            return

        self._forward(dev, in_iface, pkt)

    def _owns_ip(self, dev: Device, ip: IP) -> bool:
        for i in dev.net.interfaces:
            if i.owns(ip):
                return True
        return False

    # ---- switch (L2) processing ----

    def _dhcp_pool_for(self, dev: Device, vlan: int) -> Optional[object]:
        pools = getattr(dev, "dhcp_pools", None)
        if pools:
            for p in pools:
                if p.vlan == 0 or p.vlan == vlan:
                    return p
            return pools[0]
        return dev.dhcp

    def _ingress_vlan(self, dev: Device, in_iface: Optional[Iface], pkt: Packet) -> int:
        if in_iface is None:
            return getattr(pkt, "vlan", 0)
        if in_iface.is_trunk():
            return pkt.vlan or in_iface.access_vlan or 1
        return in_iface.access_vlan

    def _is_l3_switch(self, dev: Device) -> bool:
        return any(i.has_ip() for i in dev.net.interfaces)

    def _switch_process(self, dev: Device, in_iface: Optional[Iface], pkt: Packet):
        vlan = self._ingress_vlan(dev, in_iface, pkt)
        pkt.vlan = vlan
        if pkt.src_mac and pkt.src_mac != BROADCAST_MAC:
            dev.net.mac_table.learn(vlan, pkt.src_mac, in_iface)
        self._log(dev, in_iface, pkt, "switch", f"L2 vlan={vlan} mac={pkt.src_mac.hex()}")

        if self._owns_ip(dev, pkt.dst_ip):
            if pkt.type == PacketType.ARP_REQUEST:
                dev.net.arp.set(pkt.src_ip, pkt.src_mac, in_iface, self.now())
                self._process_arp_request(dev, in_iface, pkt)
            elif pkt.type == PacketType.ICMP_ECHO_REQUEST:
                self._reply_echo(dev, in_iface, pkt)
            elif pkt.type == PacketType.ICMP_ECHO_REPLY:
                self._signal_ping_reply(pkt)
            return

        # L2 broadcast / ARP frames stay on the segment (never routed), even
        # on L3 switches: they must reach e.g. a gateway SVI on another device
        if pkt.dst_mac == BROADCAST_MAC or pkt.type in (PacketType.ARP_REQUEST, PacketType.ARP_REPLY):
            self._flood_vlan(dev, in_iface, vlan, pkt)
            return

        # L3 switch (has routed / VLANIF addresses) routes instead of L2-switching
        if self._is_l3_switch(dev):
            return self._route_from_switch(dev, in_iface, pkt)

        if pkt.dst_mac == BROADCAST_MAC or not pkt.dst_mac:
            self._flood_vlan(dev, in_iface, vlan, pkt)
            return
        port = dev.net.mac_table.lookup(vlan, pkt.dst_mac)
        if port is None or port is in_iface:
            self._flood_vlan(dev, in_iface, vlan, pkt)
        else:
            self._send_out_port(dev, port, pkt)

    def _send_out_port(self, dev: Device, port: Iface, pkt: Packet):
        if port.state == LinkState.DOWN or port.link is None:
            return
        # Deliver a private copy per hop/port: a single packet object is shared
        # across a flood and every hop mutates per-path fields (vlan, ttl, ...).
        # Without copies, a later mutation corrupts frames already queued for
        # delivery elsewhere. The receiver derives its own VLAN from its port
        # type (access/trunk) in _ingress_vlan, so egress tagging is handled by
        # the copy's vlan field.
        peer_iface = port.link.other(port)
        peer_dev = self.device_for_iface(peer_iface)
        if peer_dev is not None:
            self._schedule(port.link.delay, peer_dev.on_packet, peer_dev, peer_iface, _copy_pkt(pkt))

    def _flood_vlan(self, dev: Device, exclude: Optional[Iface], vlan: int, pkt: Packet):
        for i in dev.net.interfaces:
            if i.is_virtual or i is exclude or i.state == LinkState.DOWN:
                continue
            if not i.carries_vlan(vlan):
                continue
            self._send_out_port(dev, i, pkt)

    def send_l2_frame(self, dev: Device, vlan: int, dst_mac: bytes, pkt: Packet):
        """Deliver an L2 frame toward a known MAC within a VLAN (broadcast to
        the segment when the target port is unknown)."""
        pkt.vlan = vlan
        if dev.type == DeviceType.SWITCH:
            port = dev.net.mac_table.lookup(vlan, dst_mac)
            if port is not None:
                self._send_out_port(dev, port, pkt)
                return
            self._flood_vlan(dev, None, vlan, pkt)
            return
        for i in dev.net.interfaces:
            if i.carries_vlan(vlan) and not i.is_virtual and i.state == LinkState.UP:
                self._send_out_port(dev, i, pkt)
                return
        for i in dev.net.interfaces:
            if i.state == LinkState.UP and not i.is_virtual:
                self._send_out_port(dev, i, pkt)
                return

    # ---- routing / forwarding ----

    def _route_from_switch(self, dev: Device, in_iface: Optional[Iface], pkt: Packet):
        r = dev.net.routes.lookup(pkt.dst_ip)
        if r is None:
            self._log(dev, in_iface, pkt, "drop", f"No route to {pkt.dst_ip}")
            return
        out = r.iface
        if out is None or out.state == LinkState.DOWN:
            self._log(dev, in_iface, pkt, "drop", "interface down")
            return
        if dev.nat is not None:
            dev.nat.translate(in_iface, out, pkt)
        pkt.ttl -= 1
        if pkt.ttl <= 0:
            self._log(dev, out, pkt, "drop", "TTL exceeded")
            return
        pkt.hops += 1
        pkt.through.append(dev.name)
        self._log(dev, out, pkt, "forward", f"{dev.name} -> {pkt.dst_ip} via {out.name}")
        if out.is_virtual:
            self._route_via_vlanif(dev, out, pkt, in_iface)
        else:
            pkt.vlan = 0
            self._send_via(dev, out, pkt)

    def _route_via_vlanif(self, dev: Device, out: Iface, pkt: Packet, in_iface: Optional[Iface] = None):
        out_vlan = out.access_vlan
        pkt.vlan = out_vlan
        arp = dev.net.arp.get(pkt.dst_ip)
        if arp is not None:
            port = dev.net.mac_table.lookup(out_vlan, arp.mac)
            if port is not None and port.state == LinkState.UP:
                self._send_out_port(dev, port, pkt)
                return
        # never flood a routed frame back out the interface it arrived on;
        # otherwise a switch re-injects the same broadcast forever (loop)
        self._flood_vlan(dev, in_iface, out_vlan, pkt)

    def _forward(self, dev: Device, in_iface: Optional[Iface], pkt: Packet):
        r = dev.net.routes.lookup(pkt.dst_ip)
        if r is None:
            self._log(dev, in_iface, pkt, "drop", f"No route to {pkt.dst_ip}")
            return

        out = r.iface
        if out is None or out.state == LinkState.DOWN or out.link is None:
            self._log(dev, in_iface, pkt, "drop", "interface down")
            return

        if dev.nat is not None:
            dev.nat.translate(in_iface, out, pkt)

        pkt.ttl -= 1
        if pkt.ttl <= 0:
            self._log(dev, out, pkt, "drop", "TTL exceeded")
            return

        pkt.hops += 1
        pkt.through.append(dev.name)
        self._log(dev, out, pkt, "forward", f"{dev.name} -> {pkt.dst_ip}")

        if out.is_virtual:
            self._route_via_vlanif(dev, out, pkt, in_iface)
            return

        peer_iface = out.link.other(out)
        if peer_iface is None:
            self._log(dev, out, pkt, "drop", "peer interface not found")
            return
        peer_dev = self.device_for_iface(peer_iface)
        if peer_dev is None:
            self._log(dev, out, pkt, "drop", "peer device not found")
            return

        self._schedule(out.link.delay, peer_dev.on_packet, peer_dev, peer_iface, _copy_pkt(pkt))

    def _send_via(self, dev: Device, out: Iface, pkt: Packet):
        if out is None or out.state == LinkState.DOWN or out.link is None:
            self._log(dev, out, pkt, "drop", "interface down")
            return
        peer_iface = out.link.other(out)
        if peer_iface is None:
            return
        peer_dev = self.device_for_iface(peer_iface)
        if peer_dev is None:
            return
        self._schedule(out.link.delay, peer_dev.on_packet, peer_dev, peer_iface, _copy_pkt(pkt))

    def send_via_iface(self, dev: Device, out: Iface, pkt: Packet):
        self._send_via(dev, out, pkt)

    # ---------- ARP ----------

    def _process_arp_request(self, dev: Device, in_iface: Optional[Iface], pkt: Packet):
        self._log(dev, in_iface, pkt, "arp", f"ARP request: who has {pkt.dst_ip}? tell {pkt.src_ip}")
        if in_iface is not None and in_iface.has_ip():
            dev.net.arp.set(pkt.src_ip, pkt.src_mac, in_iface, self.now())
        reply = Packet(
            type=PacketType.ARP_REPLY,
            src_ip=pkt.dst_ip,
            dst_ip=pkt.src_ip,
            src_mac=in_iface.mac if in_iface else b"",
            dst_mac=pkt.src_mac,
            ttl=DEFAULT_TTL,
        )
        self._send_via(dev, in_iface, reply)

    def _process_arp_reply(self, dev: Device, in_iface: Optional[Iface], pkt: Packet):
        if in_iface is not None and in_iface.has_ip():
            dev.net.arp.set(pkt.src_ip, pkt.src_mac, in_iface, self.now())
        self._log(dev, in_iface, pkt, "arp", f"ARP learned: {pkt.src_ip} -> {pkt.src_mac.hex()}")

    # ---------- ICMP ----------

    def _reply_echo(self, dev: Device, in_iface: Optional[Iface], pkt: Packet):
        self._log(dev, in_iface, pkt, "icmp", f"ICMP echo request from {pkt.src_ip}, replying")
        reply = Packet(
            type=PacketType.ICMP_ECHO_REPLY,
            src_ip=pkt.dst_ip,
            dst_ip=pkt.src_ip,
            src_mac=in_iface.mac if in_iface else b"",
            ttl=DEFAULT_TTL,
            seq=pkt.seq,
            payload=pkt.payload,
        )
        self._send_via(dev, in_iface, reply)

    # ---------- DHCP client ----------

    def _dhcp_client_consume(self, dev: Device, in_iface: Optional[Iface], pkt: Packet):
        if dev.dhcp_client is None:
            return
        payload = pkt.payload if isinstance(pkt.payload, dict) else {}
        if pkt.type == PacketType.DHCP_OFFER:
            yiaddr = payload.get("yiaddr")
            dev.dhcp_client["offer"] = yiaddr
            if yiaddr is not None and in_iface is not None:
                req = Packet(type=PacketType.DHCP_REQUEST, src_ip=IP(0),
                             dst_ip=IP(0xFFFFFFFF),
                             src_mac=dev.dhcp_client.get("mac", ZERO_MAC),
                             dst_mac=BROADCAST_MAC, ttl=16,
                             payload={"yiaddr": yiaddr})
                self.send_via_iface(dev, in_iface, req)
        elif pkt.type == PacketType.DHCP_ACK:
            yiaddr = payload.get("yiaddr")
            mask = payload.get("mask")
            gw = payload.get("gateway")
            if yiaddr is not None and in_iface is not None:
                in_iface.ip = yiaddr
                if mask is not None:
                    in_iface.mask = mask
                    dev.net.routes.add(Route(prefix=yiaddr.network(mask), mask=mask,
                                             iface=in_iface, proto="D", metric=0))
                if gw is not None:
                    dev.net.routes.add(Route(prefix=IP(0), mask=Mask(0), next_hop=gw,
                                             iface=in_iface, proto="S", metric=1))
                self._log(dev, in_iface, pkt, "dhcp", f"DHCP ACK applied {yiaddr}")
                dev.dhcp_client = None

    # ---------- ping ----------

    def ping(self, src: Device, dst_ip: str, count: int, on_status, on_done=None, timeout: Optional[float] = None) -> None:
        """Send ICMP echo requests.

        on_status(seq, ok, detail) called per reply/timeout.
        on_done(received, count, loss) called when all probes finish.
        timeout overrides the default (TTL-aware) per-probe timeout.
        """
        try:
            dst = IP.parse(dst_ip)
        except ValueError:
            on_status(0, False, f"ping: invalid address {dst_ip}")
            return

        src_ip = self._source_ip_for(src, dst)
        if src_ip is None:
            on_status(0, False, "ping: no source address available")
            return
        src_iface = src.net.find_iface_by_ip(src_ip)
        if src_iface is None:
            on_status(0, False, "ping: source interface not found")
            return

        pending = {
            "dst": dst,
            "count": count,
            "received": 0,
            "on_status": on_status,
            "on_done": on_done,
        }
        probe_timeout = timeout if timeout is not None else DEFAULT_PING_TIMEOUT

        for seq in range(1, count + 1):
            pkt = Packet(
                type=PacketType.ICMP_ECHO_REQUEST,
                src_ip=src_ip,
                dst_ip=dst,
                src_mac=src_iface.mac,
                ttl=DEFAULT_TTL,
                seq=seq,
                payload="ping",
            )
            key = f"{dst}|{seq}"
            self._pending[key] = {"pending": pending}
            # send probe at its scheduled time
            self._schedule(0.3 * (seq - 1), self._send_probe, src, src_iface, pkt)
            # timeout event
            self._schedule(0.3 * (seq - 1) + probe_timeout, self._probe_timeout, pending, seq)

        # statistics after the last probe + timeout window
        self._schedule(0.3 * (count - 1) + probe_timeout + 0.4, self._ping_stats, pending)

    def _send_probe(self, src: Device, src_iface: Iface, pkt: Packet):
        self._forward(src, src_iface, pkt)

    def _probe_timeout(self, pending: dict, seq: int):
        key = f"{pending['dst']}|{seq}"
        entry = self._pending.pop(key, None)
        if entry is None:
            return
        pending["on_status"](seq, False, f"Request time out, icmp_seq={seq}")

    def _ping_stats(self, pending: dict):
        count = pending["count"]
        received = pending["received"]
        loss = (count - received) / count * 100 if count else 0
        if pending["on_done"]:
            pending["on_done"](received, count, loss)

    def _signal_ping_reply(self, pkt: Packet):
        key = f"{pkt.src_ip}|{pkt.seq}"
        entry = self._pending.pop(key, None)
        if entry is None:
            return
        pending = entry["pending"]
        pending["received"] += 1
        pending["on_status"](
            pkt.seq, True,
            f"Reply from {pkt.src_ip}: bytes=56 Sequence={pkt.seq} ttl={DEFAULT_TTL} time=1 ms"
        )

    def _source_ip_for(self, src: Device, dst: IP) -> Optional[IP]:
        for i in src.net.interfaces:
            if i.has_ip() and dst.network(i.mask) == i.network():
                return i.ip
        r = src.net.routes.lookup(dst)
        if r and r.iface:
            return r.iface.ip
        for i in src.net.interfaces:
            if i.has_ip() and not i.is_loopback:
                return i.ip
        return None

    # ---------- diagnostics ----------

    def describe(self) -> str:
        lines = []
        for d in self.devices.values():
            lines.append(f"{d.name} ({d.type.label}/{d.vendor.label})")
            for i in d.net.interfaces:
                lines.append(f"  {i}")
        return "\n".join(lines)