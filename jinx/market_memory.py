"""Market maintenance: one pass a minute over every RAM structure that holds market data (owner order 2026-10-21:
"RAM IS A WORKBENCH. NOT A WAREHOUSE.").

Each component bounds itself on every event (hard cap and receipt-clock expiry); this pass also evicts what went
idle while no event arrived, and hands every component the set of tokens JINX holds, which is never evicted to
meet a market-cache limit. Components: the token registry and the intelligence trackers. Each has
`sweep(now_ns, held, idle_ns=None) -> dict`; `idle_ns` overrides the idle time-out for one pass (the overload
mode above the RAM guardrail). Nothing here touches the durable trading ledger. TTLs and caps are engineering
bounds on memory, not strategy parameters.
"""
from __future__ import annotations

import logging
from typing import Iterable, Protocol

log = logging.getLogger(__name__)


class Sweepable(Protocol):
    def sweep(self, now_ns: int, held: frozenset, idle_ns: int | None = None) -> dict: ...


class MarketMemory:
    def __init__(self, components: Iterable[tuple[str, Sweepable]]) -> None:
        self.components = list(components)
        self.passes = 0
        self.last: dict = {}
        self.errors = 0

    def tick(self, now_ns: int, held: set[str] | frozenset = frozenset(), idle_ns: int | None = None) -> dict:
        """`idle_ns`, when given, replaces each component's idle time-out for this pass (overload mode)."""
        held = frozenset(held)
        report = {}
        for name, comp in self.components:
            try:
                report[name] = comp.sweep(now_ns, held, idle_ns)
            except Exception:                    # one component failing never stops the others' maintenance
                self.errors += 1
                report[name] = {"error": True}
                log.exception("market maintenance of %s failed", name)
        self.passes += 1
        self.last = report
        return report
