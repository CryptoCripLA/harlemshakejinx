"""Central event bus: one ingest point, two bounded lanes.

FAST lane: core state, Early Momentum, graduation, migration, and later the position monitor and exits. It never
drops: a full fast queue makes publish() wait, so pressure reaches the source instead of silently losing events.
Each wait is counted (`fast_backpressure_waits`).

BACKGROUND lane: best-effort intelligence (bundles, whales). When full it drops, counts the drop, and tells each
background subscriber which event it missed (`on_drop`), so the subscriber marks that token's evidence incomplete
instead of presenting it as clean data (owner order 2026-10-21 §1). Slow background work can never stall the fast
lane.

Each event passes the expiring duplicate filter and then the optional durable `persist` hook (which returns False for
an event already stored) before it is dispatched. A handler that raises is counted and skipped; it never stops other
handlers or later events.

Diagnostics are constant memory (owner order 2026-10-21): latency per lane in a rolling histogram
(jinx/core/latency.py), and queue depth with its high-water mark. Queue capacities are finite. The defaults are sized
from the live measurement: the largest fast-lane backlog behind a 3.9 s stall at ~94 events/s was about 370 events,
and 2,048 holds more than five times that.
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import time
from collections import defaultdict
from enum import StrEnum
from typing import Any, Callable

from jinx.core.dedup import Deduplicator
from jinx.core.events import MarketEvent
from jinx.core.latency import RollingLatency

log = logging.getLogger(__name__)

Handler = Callable[[MarketEvent], Any]


class Lane(StrEnum):
    FAST = "fast"
    BACKGROUND = "background"


class EventBus:
    def __init__(
        self,
        fast_capacity: int = 2_048,
        background_capacity: int = 2_048,
        dedup: Deduplicator | None = None,
        persist: Callable[[MarketEvent], bool] | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._queues = {
            Lane.FAST: asyncio.Queue(maxsize=fast_capacity),
            Lane.BACKGROUND: asyncio.Queue(maxsize=background_capacity),
        }
        self._subs: dict[Lane, list[tuple[str, Handler]]] = {Lane.FAST: [], Lane.BACKGROUND: []}
        self._on_drop: list[tuple[str, Handler]] = []
        self._dedup = dedup or Deduplicator()
        self._persist = persist
        self._latency = {lane: RollingLatency() for lane in Lane}
        self._high_water = {lane: 0 for lane in Lane}
        self._seq = itertools.count(1)
        self.clock_ns = clock_ns
        self._handler_errors: dict[str, int] = defaultdict(int)
        self._counts = defaultdict(int)

    def next_seq(self) -> int:
        return next(self._seq)

    def resume_after(self, last_seq: int) -> None:
        """After a restart, continue numbering after the last stored event."""
        self._seq = itertools.count(last_seq + 1)

    def subscribe(self, lane: Lane, handler: Handler, name: str | None = None,
                  on_drop: Handler | None = None) -> None:
        """`on_drop` (background lane): called synchronously with an event this subscriber will never see because
        the background queue was full, so it can mark that token's evidence incomplete."""
        name = name or getattr(handler, "__qualname__", repr(handler))
        self._subs[lane].append((name, handler))
        if on_drop is not None:
            if lane is not Lane.BACKGROUND:
                raise ValueError("only the background lane drops events")
            self._on_drop.append((name, on_drop))

    async def publish(self, event: MarketEvent) -> bool:
        """Admit an event. Returns False when it is a duplicate."""
        admitted_at = time.perf_counter_ns()  # latency covers dedup, the durable write, queueing and handlers
        if not self._dedup.first_time(event.idempotency_key, event.recv_ns):
            self._counts["duplicates"] += 1
            return False
        if self._persist is not None:
            try:
                stored = self._persist(event)
            except Exception:
                self._dedup.forget(event.idempotency_key)
                self._counts["persist_errors"] += 1
                raise
            if not stored:
                self._counts["duplicates"] += 1
                return False
        self._counts["published"] += 1
        fast = self._queues[Lane.FAST]
        if fast.full():
            self._counts["fast_backpressure_waits"] += 1
        await fast.put((event, admitted_at))
        self._mark_depth(Lane.FAST)
        try:
            self._queues[Lane.BACKGROUND].put_nowait((event, admitted_at))
            self._mark_depth(Lane.BACKGROUND)
        except asyncio.QueueFull:
            self._counts["background_dropped"] += 1
            for name, on_drop in self._on_drop:
                try:
                    on_drop(event)
                except Exception:
                    self._handler_errors[f"{name}.on_drop"] += 1
                    log.exception("on_drop of %s failed on %s", name, event.idempotency_key)
        return True

    def _mark_depth(self, lane: Lane) -> None:
        depth = self._queues[lane].qsize()
        if depth > self._high_water[lane]:
            self._high_water[lane] = depth

    async def run(self) -> None:
        await asyncio.gather(self._consume(Lane.FAST), self._consume(Lane.BACKGROUND))

    async def drain(self) -> None:
        """Wait until everything queued so far has been handled."""
        for lane in Lane:
            await self._queues[lane].join()

    async def _consume(self, lane: Lane) -> None:
        queue = self._queues[lane]
        while True:
            event, enq = await queue.get()
            try:
                ok = True
                for name, handler in self._subs[lane]:
                    try:
                        result = handler(event)
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        ok = False
                        self._handler_errors[name] += 1
                        log.exception("handler %s failed on %s", name, event.idempotency_key)
                if ok:  # a failed dispatch is not a completed one; it is counted in handler_errors instead
                    self._latency[lane].record(time.perf_counter_ns() - enq)
                self._counts[f"dispatched_{lane}"] += 1
            finally:
                queue.task_done()

    def latency(self, lane: Lane) -> RollingLatency:
        """Admission (publish called) -> every handler on this lane succeeded: dedup, durable write, queue,
        handlers. Events whose dispatch failed are not sampled. Rolling window, constant memory."""
        return self._latency[lane]

    def queue_depth(self, lane: Lane) -> int:
        return self._queues[lane].qsize()

    def capacity(self, lane: Lane) -> int:
        return self._queues[lane].maxsize

    def dedup_stats(self) -> dict:
        return self._dedup.stats()

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "published": self._counts["published"],
            "duplicates": self._counts["duplicates"],
            "dispatched_fast": self._counts["dispatched_fast"],
            "dispatched_background": self._counts["dispatched_background"],
            "fast_dropped": 0,
            "fast_backpressure_waits": self._counts["fast_backpressure_waits"],
            "background_dropped": self._counts["background_dropped"],
            "persist_errors": self._counts["persist_errors"],
            "handler_errors": dict(self._handler_errors),
            "queue_depth": {str(lane): self._queues[lane].qsize() for lane in Lane},
            "queue_high_water": {str(lane): self._high_water[lane] for lane in Lane},
            "queue_capacity": {str(lane): self._queues[lane].maxsize for lane in Lane},
        }
