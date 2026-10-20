"""Central event bus: one ingest point, two bounded lanes.

FAST lane: core state (store, registries, ledger) and, in later phases, the
early-momentum path. It never drops: a full fast queue makes publish() wait,
so pressure reaches the source instead of silently losing events.

BACKGROUND lane: best-effort intelligence and capture. When full it drops and
counts the drop, so slow background work can never stall the fast lane.

Each event passes the in-memory dedup and then the durable `persist` hook
(which returns False for an event already stored, including before a restart)
before it is dispatched. A handler that raises is counted and skipped; it
never stops other handlers or later events.
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import time
from collections import defaultdict, deque
from enum import StrEnum
from typing import Any, Callable

from jinx.core.dedup import Deduplicator
from jinx.core.events import MarketEvent

log = logging.getLogger(__name__)

Handler = Callable[[MarketEvent], Any]


class Lane(StrEnum):
    FAST = "fast"
    BACKGROUND = "background"


class EventBus:
    def __init__(
        self,
        fast_capacity: int = 10_000,
        background_capacity: int = 10_000,
        dedup: Deduplicator | None = None,
        persist: Callable[[MarketEvent], bool] | None = None,
        latency_window: int = 100_000,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._queues = {
            Lane.FAST: asyncio.Queue(maxsize=fast_capacity),
            Lane.BACKGROUND: asyncio.Queue(maxsize=background_capacity),
        }
        self._subs: dict[Lane, list[tuple[str, Handler]]] = {Lane.FAST: [], Lane.BACKGROUND: []}
        self._dedup = dedup or Deduplicator()
        self._persist = persist
        self._latency = {lane: deque(maxlen=latency_window) for lane in Lane}
        self._seq = itertools.count(1)
        self.clock_ns = clock_ns
        self._handler_errors: dict[str, int] = defaultdict(int)
        self._counts = defaultdict(int)

    def next_seq(self) -> int:
        return next(self._seq)

    def resume_after(self, last_seq: int) -> None:
        """After a restart, continue numbering after the last stored event."""
        self._seq = itertools.count(last_seq + 1)

    def subscribe(self, lane: Lane, handler: Handler, name: str | None = None) -> None:
        self._subs[lane].append((name or getattr(handler, "__qualname__", repr(handler)), handler))

    async def publish(self, event: MarketEvent) -> bool:
        """Admit an event. Returns False when it is a duplicate."""
        if not self._dedup.first_time(event.idempotency_key):
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
        enq = time.perf_counter_ns()
        await self._queues[Lane.FAST].put((event, enq))
        try:
            self._queues[Lane.BACKGROUND].put_nowait((event, enq))
        except asyncio.QueueFull:
            self._counts["background_dropped"] += 1
        return True

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
                for name, handler in self._subs[lane]:
                    try:
                        result = handler(event)
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        self._handler_errors[name] += 1
                        log.exception("handler %s failed on %s", name, event.idempotency_key)
                self._latency[lane].append(time.perf_counter_ns() - enq)
                self._counts[f"dispatched_{lane}"] += 1
            finally:
                queue.task_done()

    def latency_ns(self, lane: Lane) -> list[int]:
        return list(self._latency[lane])

    def queue_depth(self, lane: Lane) -> int:
        return self._queues[lane].qsize()

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "published": self._counts["published"],
            "duplicates": self._counts["duplicates"],
            "dispatched_fast": self._counts["dispatched_fast"],
            "dispatched_background": self._counts["dispatched_background"],
            "fast_dropped": 0,
            "background_dropped": self._counts["background_dropped"],
            "persist_errors": self._counts["persist_errors"],
            "handler_errors": dict(self._handler_errors),
        }
