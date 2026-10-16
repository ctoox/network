#!/usr/bin/env python3
"""NetSim - multi-vendor network simulator entry point.

Run the web UI (default) or a headless virtual-time simulation:

  python main.py                        # web UI (demo topology)
  python main.py --scenario medium      # web UI with the 中型 network
  python main.py --simulate 10.0.20.5 --scenario medium   # headless ping
  python main.py --stress 128           # 128-router OSPF ring, timing report
"""

import argparse
import time

from netsim.net.ip import IP, Mask
from netsim.net.iface import Iface, Route
from netsim.net.device import Device, DeviceType, Vendor
from netsim.net.world import World
from netsim.server import SimServer, build_demo_topology


def simulate(dst: str, count: int = 5, routers: int = 0, scenario: str = None):
    """Run a ping in virtual time (fast-forward) and print results."""
    if scenario:
        from netsim import scenarios
        w = scenarios.load(scenario)
        src = next(iter(w.devices.values()))
        print(f"[scenario] {scenarios.describe(scenario)}")
        print(f"  topology: {len(w.devices)} devices, {len(w.links)} links")
        _ping_from(w, src, dst, count)
        return

    from netsim.net.iface import DeviceNetwork
    w = World(realtime=False)
    if routers:
        N = routers
        host = Device("h1", "H1", DeviceType.HOST, Vendor.HUAWEI, DeviceNetwork())
        w.add_device(host)
        hif = host.add_iface(Iface("eth0", ip=IP.parse("10.0.0.1"), mask=Mask.from_len(24)))
        rts = [Device(f"r{i}", f"R{i}", DeviceType.ROUTER, Vendor.HUAWEI, DeviceNetwork()) for i in range(N)]
        for d in rts:
            w.add_device(d)
            d.add_iface(Iface("ge0", mac=bytes([0x20, 0, 0, 0, 0, 0])))
            d.add_iface(Iface("ge1", mac=bytes([0x21, 0, 0, 0, 0, 0])))
        w.connect(hif, rts[0].get_iface("ge0"))
        hif.ip = IP.parse("10.0.0.1")
        rts[0].get_iface("ge0").ip = IP.parse("10.0.0.254")
        for i in range(N - 1):
            a = rts[i].get_iface("ge1")
            b = rts[i + 1].get_iface("ge0")
            w.connect(a, b)
            a.ip = IP.from_bytes(bytes([10, (i >> 8) & 0xff, i & 0xff, 1]))
            a.mask = Mask.from_len(30)
            b.ip = IP.from_bytes(bytes([10, (i >> 8) & 0xff, i & 0xff, 2]))
            b.mask = Mask.from_len(30)
        tail = rts[-1].get_iface("ge1")
        tail.ip = IP.from_bytes(bytes([192, 168, 0, 1]))
        tail.mask = Mask.from_len(24)
        rts[-1].net.routes.add(Route(IP.parse("192.168.0.0"), Mask.from_len(24), iface=tail, proto="D"))
        for i, r in enumerate(rts):
            r.net.routes.add(Route(IP.parse("192.168.0.0"), Mask.from_len(24),
                                   next_hop=r.get_iface("ge1").ip, iface=r.get_iface("ge1"), proto="S"))
            if i > 0:
                r.net.routes.add(Route(IP.parse("0.0.0.0"), Mask.from_len(0),
                                       next_hop=r.get_iface("ge0").ip, iface=r.get_iface("ge0"), proto="S"))
        host.net.routes.add(Route(IP.parse("0.0.0.0"), Mask.from_len(0),
                                  next_hop=IP.parse("10.0.0.254"), iface=hif, proto="S"))
        src = host
        dst_ip = dst
        print(f"topology: {len(w.devices)} devices, {len(w.links)} links (chain of {N} routers)")
    else:
        build_demo_topology(w)
        src = w.get_device("host1")
        dst_ip = dst

    _ping_from(w, src, dst_ip, count)


def _ping_from(w: World, src: Device, dst_ip: str, count: int):
    results = []
    t0 = time.monotonic()
    w.ping(src, dst_ip, count,
           on_status=lambda s, ok, d: results.append(d),
           on_done=lambda recv, cnt, loss: results.append(f"DONE {recv}/{cnt}"))
    w.settle(30)
    elapsed_ms = (time.monotonic() - t0) * 1000
    for r in results:
        print("  " + r)
    print(f"--- virtual time {w.now():.2f}s simulated in {elapsed_ms:.1f} ms real time ---")


def stress(n: int, ring: bool = True):
    """Build an n-router OSPF topology and measure convergence + reachability."""
    from netsim import scenarios
    t0 = time.monotonic()
    w = scenarios.build_stress(n, ring=ring)
    build_ms = (time.monotonic() - t0) * 1000
    ospf = sum(1 for d in w.devices.values() if d.ospf is not None)
    routes = sum(len(d.net.routes) for d in w.devices.values())
    print(f"[stress] {n} routers, {len(w.links)} links, {ospf} OSPF instances")
    print(f"[stress] built in {build_ms:.0f} ms, {routes} total route entries")

    # ping the last router across the whole ring after OSPF converged
    src = w.get_device("r0")
    tgt = w.get_device(f"r{n - 1}")
    t_if = tgt.get_iface("GE0/0/1")
    results = []
    t1 = time.monotonic()
    w.ping(src, str(t_if.ip), 3,
           on_status=lambda s, ok, d: results.append("OK" if ok else "FAIL"),
           on_done=lambda recv, cnt, loss: results.append(f"DONE {recv}/{cnt}"))
    w.scheduler.run()
    ping_ms = (time.monotonic() - t1) * 1000
    print(f"[stress] ping r0 -> r{n - 1}: {results}")
    print(f"[stress] simulated {w.now():.2f}s in {ping_ms:.1f} ms ({(w.now() / max(ping_ms / 1000, 1e-9)):.0f}x realtime)")


def main():
    parser = argparse.ArgumentParser(description="NetSim multi-vendor network simulator")
    parser.add_argument("--port", type=int, default=8080, help="preferred listen port (auto-picks next free if busy)")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--simulate", metavar="IP", help="run a virtual-time ping to IP and exit")
    parser.add_argument("--routers", type=int, default=0, help="with --simulate, build an N-router chain")
    parser.add_argument("--scenario", default=None,
                        help="load a pre-built scenario: smb/medium/large/campus/datacenter/xinchuang")
    parser.add_argument("--stress", type=int, default=0,
                        help="run an N-router OSPF stress benchmark and exit")
    parser.add_argument("--list-scenarios", action="store_true", help="list available scenarios and exit")
    args = parser.parse_args()

    from netsim import scenarios

    if args.list_scenarios:
        for name in scenarios.SCENARIOS:
            print(f"{name:<12} {scenarios.describe(name)}")
        return

    if args.stress:
        stress(args.stress, ring=True)
        return

    if args.simulate:
        simulate(args.simulate, routers=args.routers, scenario=args.scenario)
        return

    if args.scenario:
        world = scenarios.load(args.scenario)
        print(f"[netsim] scenario: {scenarios.describe(args.scenario)} "
              f"({len(world.devices)} devices, {len(world.links)} links)")
    else:
        world = World()
        build_demo_topology(world)

    server = SimServer(world)
    server.start(host=args.host, port=args.port)


if __name__ == "__main__":
    main()