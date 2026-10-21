"""The fast-lane handler that turns an admitted event into JINX's current token state.

Market state is RAM only (owner order 2026-10-21 §1: "Normal raw market chatter is transient"): no raw event is
stored, nothing is replayed after a restart, and the market view starts empty. The bus's expiring duplicate filter
admits each event once inside its window. Trading records, which must survive restarts, live in the durable ledger
store (jinx/store.py, jinx/ledger.py).
"""
from __future__ import annotations

import time

from jinx.core.events import MarketEvent
from jinx.core.latency import RollingLatency
from jinx.registry import TokenRegistry


class CoreState:
    def __init__(self, tokens: TokenRegistry, clock_ns=time.time_ns) -> None:
        self.tokens = tokens
        self.clock_ns = clock_ns
        self.applied = 0
        self._receive_latency = RollingLatency()

    def apply(self, ev: MarketEvent) -> None:
        self.tokens.apply(ev)
        self.applied += 1
        self._receive_latency.record(self.clock_ns() - ev.recv_ns)

    def receive_latency(self) -> RollingLatency:
        """Feed receive -> token state applied, wall clock, for successful applies only. Local processing, not
        execution. Rolling histogram, constant memory."""
        return self._receive_latency
