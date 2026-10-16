"""Scheduler: virtual-time / real-time dual-mode discrete-event engine.

The scheduler is the heart of the simulator (ns-3 Simulator/Scheduler/Event
style). Events are delayed function calls; time between events is virtual.

Two modes:
  * realtime  - virtual clock tracks wall-clock via asyncio; used for
                interactive use (CLI/WebSocket/UI).
  * virtual   - pure-CPU heap loop; time jumps instantly to the next event.
                Used for fast-forwarding convergence and bulk simulation.
"""

from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(order=True)
class Event:
    time: float
    seq: int
    fn: Callable[..., Any]
    args: tuple = field(default_factory=tuple)
    cancelled: bool = field(default=False, compare=False)

    def cancel(self) -> None:
        self.cancelled = True


class Scheduler:
    def __init__(self, realtime: bool = True, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.realtime = realtime
        self._loop = loop
        self._heap: List[Event] = []
        self._seq = 0
        self._virtual_now = 0.0
        self._t0 = time.monotonic() if realtime else 0.0
        self._handles: Dict[int, Any] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the asyncio loop used for realtime scheduling."""
        self._loop = loop

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        return self._loop

    # ---- time ----

    def now(self) -> float:
        if self.realtime:
            return time.monotonic() - self._t0
        return self._virtual_now

    def advance(self, delta: float) -> None:
        """In virtual mode, jump the clock forward (used for fast-forward)."""
        self._virtual_now += delta

    # ---- scheduling ----

    def schedule(self, delay: float, fn: Callable[..., Any], *args) -> Event:
        ev = Event(time=self.now() + delay, seq=self._seq, fn=fn, args=args)
        self._seq += 1
        if self.realtime:
            loop = self._ensure_loop()
            handle = loop.call_later(delay, self._dispatch, ev)
            self._handles[ev.seq] = handle
        else:
            heapq.heappush(self._heap, ev)
        return ev

    def schedule_at(self, t: float, fn: Callable[..., Any], *args) -> Event:
        return self.schedule(t - self.now(), fn, *args)

    def cancel(self, ev: Event) -> None:
        if ev is None or ev.cancelled:
            return
        ev.cancelled = True
        if self.realtime:
            handle = self._handles.pop(ev.seq, None)
            if handle is not None:
                try:
                    handle.cancel()
                except Exception:
                    pass

    def _dispatch(self, ev: Event) -> None:
        self._handles.pop(ev.seq, None)
        if ev.cancelled:
            return
        ev.fn(*ev.args)

    # ---- running ----

    def step(self) -> bool:
        """Execute the next due event (virtual mode). Returns False when empty."""
        while self._heap:
            ev = heapq.heappop(self._heap)
            if ev.cancelled:
                continue
            self._virtual_now = ev.time
            ev.fn(*ev.args)
            return True
        return False

    def run(self, until: Optional[float] = None) -> None:
        """Virtual-mode main loop. Stops when heap empty or time reached."""
        while self._heap:
            ev = self._heap[0]
            if ev.cancelled:
                heapq.heappop(self._heap)
                continue
            if until is not None and ev.time > until:
                break
            heapq.heappop(self._heap)
            self._virtual_now = ev.time
            ev.fn(*ev.args)

    def run_forever(self) -> None:
        """Realtime-mode drive (asyncio loop already running)."""
        if self.realtime and self._loop is not None:
            self._loop.run_forever()

    # ---- introspection ----

    def pending(self) -> int:
        return len([e for e in self._heap if not e.cancelled])

    def drain(self) -> None:
        """Run until the queue is empty (virtual mode)."""
        self.run()