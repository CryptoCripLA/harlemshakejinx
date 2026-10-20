"""Startup recovery. Runs before JINX accepts any new event.

1. Replay events that were stored but not applied before the last stop, in order.
2. Continue sequence numbers after the last stored event.
3. Paper positions: a reservation that never filled is released; an exit that
   never filled returns to OPEN, so the position keeps its exit owner and is
   retried. Live positions (Phase 3) are left for chain reconciliation.
4. Check that the paper wallet reconciles.
"""
from __future__ import annotations

from dataclasses import dataclass

from jinx.core_state import CoreState
from jinx.ledger import PositionLedger, Status
from jinx.paper import PaperWallet
from jinx.store import Store


@dataclass(frozen=True)
class RecoveryReport:
    replayed_events: int
    resumed_seq_after: int
    released_unfilled: int
    reopened_exits: int
    open_positions: int
    wallet_reconciled: bool


def recover(store: Store, core: CoreState, ledger: PositionLedger, wallet: PaperWallet, bus=None) -> RecoveryReport:
    pending = store.unapplied_events()
    for row in pending:
        core.apply(store.row_to_event(row))
    last_seq = store.max_seq()
    if bus is not None:
        bus.resume_after(last_seq)
    released = reopened = 0
    for pos in ledger.active():
        if not pos.simulated:
            continue
        if pos.status is Status.PENDING_OPEN:
            ledger.mark_open_failed(pos.position_id, "RECOVERED_UNFILLED")
            released += 1
        elif pos.status is Status.PENDING_CLOSE:
            ledger.abort_exit(pos.position_id, "RECOVERED_UNFINISHED_EXIT")
            reopened += 1
    return RecoveryReport(len(pending), last_seq, released, reopened, len(ledger.active()), wallet.reconcile().ok)
