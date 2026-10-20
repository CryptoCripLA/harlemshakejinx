"""The fast-lane handler that turns an admitted event into JINX state.

Registry updates and the event's applied flag commit in one transaction, so
each stored event is applied exactly once even across crashes: a crash before
the commit leaves the event unapplied for recovery to replay; a crash after
it leaves nothing to redo.
"""
from __future__ import annotations

import time
from collections import deque

from jinx.core.events import MarketEvent
from jinx.registry import TokenRegistry, WalletRegistry
from jinx.store import Store


class CoreState:
    def __init__(self, store: Store, tokens: TokenRegistry, wallets: WalletRegistry, clock_ns=time.time_ns,
                 latency_window: int = 100_000) -> None:
        self.store, self.tokens, self.wallets = store, tokens, wallets
        self.clock_ns = clock_ns
        self.applied = 0
        self.skipped_already_applied = 0
        self._receive_latency: deque[int] = deque(maxlen=latency_window)

    def apply(self, ev: MarketEvent) -> None:
        try:
            with self.store.transaction():
                if self.store.is_applied(ev.idempotency_key):
                    self.skipped_already_applied += 1
                    return
                self.tokens.apply(ev)
                self.wallets.apply(ev)
                self.store.mark_applied(ev.idempotency_key)
        except Exception:
            # the transaction rolled back; drop any in-memory change made before the failure
            self.tokens.reload()
            self.wallets.reload()
            raise
        self.applied += 1
        self._receive_latency.append(self.clock_ns() - ev.recv_ns)

    def receive_latency_ns(self) -> list[int]:
        """Feed receive -> state committed, wall clock, for successful applies only (including retries via
        apply_pending, which therefore include the time the event waited). Local processing, not execution."""
        return list(self._receive_latency)

    def apply_pending(self) -> int:
        """Apply stored events that are still unapplied (after a handler failure). Safe to repeat."""
        rows = self.store.unapplied_events()
        for row in rows:
            self.apply(self.store.row_to_event(row))
        return len(rows)
