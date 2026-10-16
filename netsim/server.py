"""HTTP + WebSocket server for the simulator."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import websockets

from .net.device import Device, DeviceType, Vendor
from .net.ip import IP, Mask
from .net.iface import Iface, LinkState, Route
from .net.world import World
from .vendors.huawei import VRP
from .vendors.cisco import IOS
from .vendors.h3c import Comware
from .vendors.ruijie import RGOS
from .vendors.huawei_fw import HuaweiUSG
from .vendors.sangfor import Sangfor
from .vendors.host import HostShell


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def find_free_ports(start: int, count: int = 2, host: str = "127.0.0.1") -> list:
    """Return `count` consecutive free ports beginning at or after `start`."""
    port = start
    while True:
        block = list(range(port, port + count))
        if all(not port_in_use(host, p) for p in block):
            return block
        port += 1

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def mac_hex(s: str) -> bytes:
    """Parse 'aabb.ccdd.eeff' into 6 bytes."""
    cleaned = s.replace(".", "").replace(":", "")
    return bytes.fromhex(cleaned)


def build_demo_topology(w: World) -> None:
    # Host1 (Huawei PC) -- AR1 (Huawei) -- R2 (Cisco) -- Host2 (Cisco PC)
    host1 = Device("host1", "PC1", DeviceType.HOST, Vendor.HUAWEI, net=None)
    from .net.iface import DeviceNetwork
    host1.net = DeviceNetwork()
    h1i = host1.add_iface(Iface(name="Ethernet0/0/0", mac=mac_hex("00e0.fc00.0001")))

    r1 = Device("r1", "AR1", DeviceType.ROUTER, Vendor.HUAWEI, net=DeviceNetwork())
    r1ge0 = r1.add_iface(Iface(name="GE0/0/0", mac=mac_hex("00e0.fc00.0011")))
    r1ge1 = r1.add_iface(Iface(name="GE0/0/1", mac=mac_hex("00e0.fc00.0012")))

    r2 = Device("r2", "R2", DeviceType.ROUTER, Vendor.CISCO, net=DeviceNetwork())
    r2ge0 = r2.add_iface(Iface(name="GigabitEthernet0/0", mac=mac_hex("0050.1400.0021")))
    r2ge1 = r2.add_iface(Iface(name="GigabitEthernet0/1", mac=mac_hex("0050.1400.0022")))

    host2 = Device("host2", "PC2", DeviceType.HOST, Vendor.CISCO, net=DeviceNetwork())
    h2i = host2.add_iface(Iface(name="Ethernet0/0/0", mac=mac_hex("0050.1400.0031")))

    for d in (host1, r1, r2, host2):
        w.add_device(d)

    w.connect(h1i, r1ge0)
    w.connect(r1ge1, r2ge0)
    w.connect(r2ge1, h2i)

    # pre-seed addressing and routes so the demo works immediately
    def seed(dev, iface, ip, mask_len):
        ip_obj = IP.parse(ip)
        mask = Mask.from_len(mask_len)
        iface.ip = ip_obj
        iface.mask = mask
        dev.net.routes.add(Route(prefix=ip_obj.network(mask), mask=mask, iface=iface, proto="D", metric=0))
        return ip_obj

    seed(host1, h1i, "192.168.1.1", 24)
    seed(r1, r1ge0, "192.168.1.254", 24)
    seed(r1, r1ge1, "10.0.0.1", 30)
    seed(r2, r2ge0, "10.0.0.2", 30)
    seed(r2, r2ge1, "192.168.2.254", 24)
    seed(host2, h2i, "192.168.2.1", 24)

    host1.net.routes.add(Route(prefix=IP.parse("192.168.2.0"), mask=Mask.from_len(24),
                               next_hop=IP.parse("192.168.1.254"), iface=h1i, proto="S", metric=1))
    r1.net.routes.add(Route(prefix=IP.parse("192.168.2.0"), mask=Mask.from_len(24),
                            next_hop=IP.parse("10.0.0.2"), iface=r1ge1, proto="S", metric=1))
    r2.net.routes.add(Route(prefix=IP.parse("192.168.1.0"), mask=Mask.from_len(24),
                            next_hop=IP.parse("10.0.0.1"), iface=r2ge0, proto="S", metric=1))
    host2.net.routes.add(Route(prefix=IP.parse("192.168.1.0"), mask=Mask.from_len(24),
                               next_hop=IP.parse("192.168.2.254"), iface=h2i, proto="S", metric=1))


class SimServer:
    def __init__(self, world: World):
        self.world = world

    def _vendor_for(self, dev: Device):
        # Hosts always use the HostShell regardless of vendor.
        if dev.type == DeviceType.HOST:
            return HostShell()
        if dev.vendor == Vendor.HUAWEI:
            return VRP()
        if dev.vendor == Vendor.CISCO:
            return IOS()
        if dev.vendor == Vendor.H3C:
            return Comware()
        if dev.vendor == Vendor.RUIJIE:
            return RGOS()
        if dev.vendor == Vendor.HUAWEI_FW:
            return HuaweiUSG()
        if dev.vendor == Vendor.SANGFOR:
            return Sangfor()
        return None

    async def _ws_handler(self, ws):
        try:
            init = json.loads(await ws.recv())
        except Exception:
            return
        if init.get("cmd") != "connect":
            await ws.send(json.dumps({"type": "error", "text": "expected connect"}))
            return

        dev = self.world.get_device(init.get("device", ""))
        if dev is None:
            await ws.send(json.dumps({"type": "error", "text": "device not found"}))
            return

        vendor = self._vendor_for(dev)
        if vendor is None:
            await ws.send(json.dumps({"type": "error", "text": "unsupported vendor"}))
            return

        sess = vendor.new_session(dev.id, dev.name)
        sess.dev = dev
        sess.world = self.world

        async def _push_stream(text: str):
            try:
                await ws.send(json.dumps({
                    "type": "stream",
                    "text": text,
                    "prompt": sess.prompt_str(),
                }))
            except Exception:
                pass

        sess.on_output = lambda text: asyncio.create_task(_push_stream(text))

        # banner
        sess.p(f"{vendor.name()} simulator connected to {dev.name}")
        sess.p("")
        sess.p(f"Device: {dev.name}  Type: {dev.type.label}  Vendor: {dev.vendor.label}")
        sess.p("Type '?' for command help.")
        await ws.send(json.dumps({
            "type": "banner",
            "text": sess.flush(),
            "prompt": sess.prompt_str(),
        }))

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            cmd = msg.get("cmd")
            if cmd == "exec":
                out = sess.execute(msg.get("line", ""))
                await ws.send(json.dumps({
                    "type": "exec",
                    "text": out,
                    "prompt": sess.prompt_str(),
                }))
            elif cmd == "tab":
                expanded = sess.tab_expand_line(msg.get("line", ""))
                await ws.send(json.dumps({"type": "tab", "text": expanded}))

    async def _ws_serve(self, host: str, port: int):
        async with websockets.serve(self._ws_handler, host, port, max_size=1 << 20):
            await asyncio.Future()

    def _http_handler(self, *args, **kwargs):
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(WEB_DIR), **kw)

            def do_GET(self):
                if self.path == "/api/topology":
                    self._send_json(self._topology_json())
                else:
                    super().do_GET()

            def _send_json(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _topology_json(self):
                nodes = []
                for d in self.server.world.devices.values():
                    ifaces = [i.name for i in d.net.interfaces]
                    ips = []
                    for i in d.net.interfaces:
                        ips.append(f"{i.ip}/{i.mask.prefix_len()}" if i.has_ip() else "")
                    nodes.append({
                        "id": d.id, "name": d.name, "type": d.type.label,
                        "vendor": d.vendor.label, "ifaces": ifaces, "ips": ips,
                    })
                links = []
                for l in self.server.world.links:
                    links.append({
                        "id": l.id,
                        "a": {"device": self.server.world.device_for_iface(l.a).id if self.server.world.device_for_iface(l.a) else "?", "iface": l.a.name},
                        "b": {"device": self.server.world.device_for_iface(l.b).id if self.server.world.device_for_iface(l.b) else "?", "iface": l.b.name},
                    })
                return {"nodes": nodes, "links": links, "ws_port": getattr(self.server, "ws_port", 0)}

        return Handler

    def start(self, host: str = "127.0.0.1", port: int = 8080):
        # pick a free port pair for http + ws
        http_port, ws_port = find_free_ports(port)
        self.ws_port = ws_port

        async def _serve():
            loop = asyncio.get_running_loop()
            self.world.loop = loop
            self.world.scheduler.bind_loop(loop)
            async with websockets.serve(self._ws_handler, host, ws_port, max_size=1 << 20):
                await asyncio.Future()

        # run the shared event loop in a background thread; both the world sim
        # and the websocket server live on this single loop.
        ws_thread = threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True)
        ws_thread.start()

        # HTTP server (blocking, own thread)
        handler = self._http_handler()
        httpd = ThreadingHTTPServer((host, http_port), handler)
        httpd.world = self.world
        httpd.ws_port = ws_port
        print(f"[netsim] http on http://localhost:{http_port}")
        print(f"[netsim] ws  on ws://localhost:{ws_port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass