"""Host/PC CLI vendor for end devices (PC1, PC2, ...).

Hosts are end systems: no config views, no routing protocol config.
They only run diagnostics: ping, tracert, ipconfig, arp -a, route print.
"""

from __future__ import annotations

import asyncio

from ..cli.session import Cmd, Session, Vendor, View
from ..net.device import Packet, PacketType
from ..net.iface import LinkState
from ..net.ip import IP


class HostShell(Vendor):
    def name(self) -> str:
        return "Host"

    def version_info(self) -> str:
        return "NetSim Host v1.0 (simulated PC)"

    def new_session(self, device_id: str, device_name: str) -> Session:
        s = Session(device_id, device_name, self)
        tree = Cmd(name="")
        tree.add(Cmd(name="ping", help="Send echo messages to a remote host.", min_args=1,
                     handler=lambda s, a: self._ping(s, a)))
        tree.add(Cmd(name="ipconfig", help="Show IP configuration.",
                     handler=lambda s, a: self._ipconfig(s, a)))
        tree.add(Cmd(name="tracert", help="Trace route to destination.", min_args=1,
                     handler=lambda s, a: s.p("  tracert: not yet implemented")))
        tree.add(Cmd(name="arp", children=[
            Cmd(name="-a", help="Show ARP table.", handler=lambda s, a: self._arp(s, a)),
        ]))
        tree.add(Cmd(name="route", children=[
            Cmd(name="print", help="Show routing table.", handler=lambda s, a: self._route_print(s, a)),
        ]))
        tree.add(Cmd(name="help", help="Show available commands.",
                     handler=lambda s, a: [s.p(l) for l in s.current().tree.help_text()]))
        s.push(View(id="host", prompt=lambda s: f"C:\\>", tree=tree))
        return s

    # ---- handlers ----

    def _ping(self, s: Session, args):
        if s.world is None or s.dev is None:
            s.p("  ping: no simulation attached")
            return
        dst = args[0]
        count = 5
        s.p(f"Pinging {dst} with 32 bytes of data:")
        received = [0]
        s.world.ping(
            s.dev, dst, count,
            on_status=lambda seq, ok, detail: (
                s.stream("  " + detail), received.__setitem__(0, received[0] + 1) if ok else None),
            on_done=lambda recv, cnt, loss: (
                s.stream(f"  Ping statistics for {dst}:"),
                s.stream(f"    Packets: Sent = {cnt}, Received = {recv}, Lost = {cnt - recv} ({loss:.0f}% loss)")),
        )

    def _ipconfig(self, s: Session, args):
        if s.dev is None:
            return
        if args and args[0].lower() in ("/renew", "renew"):
            self._dhcp_renew(s)
            return
        s.p("Windows IP Configuration")
        for i in s.dev.net.interfaces:
            s.p("")
            s.p(f"Ethernet adapter {i.name}:")
            if i.has_ip():
                s.p(f"   IPv4 Address. . . . . . . . . . . . : {i.ip}")
                s.p(f"   Subnet Mask . . . . . . . . . . . . : {i.mask}")
            else:
                s.p("   Media State . . . . . . . . . . . . : Media disconnected")

    def _dhcp_renew(self, s: Session):
        if s.world is None or s.dev is None:
            s.p("  no simulation attached")
            return
        iface = next((i for i in s.dev.net.interfaces if not i.has_ip()), None)
        if iface is None:
            iface = s.dev.net.interfaces[0]
        if iface is None:
            return
        s.dev.dhcp_client = {"mac": iface.mac}
        pkt = Packet(type=PacketType.DHCP_DISCOVER, src_ip=IP(0), dst_ip=IP(0xFFFFFFFF),
                     src_mac=iface.mac, dst_mac=b"\xff" * 6, ttl=16)
        s.world.send_via_iface(s.dev, iface, pkt)
        s.stream(f"Requesting DHCP lease on {iface.name}...")

    def _arp(self, s: Session):
        if s.dev is None:
            return
        s.p("Interface: 0.0.0.0 --- 0x1")
        s.p("  Internet Address      Physical Address      Type")
        for e in s.dev.net.arp.all():
            mac = "-".join(f"{b:02x}" for b in e.mac)
            s.p(f"  {str(e.ip):<20} {mac:<20} dynamic")

    def _route_print(self, s: Session):
        if s.dev is None:
            return
        s.p("IPv4 Route Table")
        s.p("===========================================================================")
        s.p(f"{'Network Destination':<20} {'Netmask':<20} {'Gateway':<20} Interface")
        for r in s.dev.net.routes.sorted():
            gw = "On-link" if r.next_hop is None else str(r.next_hop)
            iface = r.iface.name if r.iface else ""
            s.p(f"{str(r.prefix):<20} {str(r.mask):<20} {gw:<20} {iface}")