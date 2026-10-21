"""Startup recovery. Runs before JINX accepts any new event.

Market state is RAM only and starts empty (owner order 2026-10-21 §1): there is no raw event to replay. What must
survive a restart is the trading ledger:
1. Paper positions: a reservation that never filled is released; an exit that never filled returns to OPEN, so the
   position keeps its exit owner and is retried. Live positions (Phase 3) are left for chain reconciliation.
2. Open positions are reported, so the market maintenance pass protects their tokens from eviction.
3. Check that the paper wallet reconciles.
"""
from __future__ import annotations

from dataclasses import dataclass

from jinx.ledger import PositionLedger, Status
from jinx.paper import PaperWallet


@dataclass(frozen=True)
class RecoveryReport:
    released_unfilled: int
    reopened_exits: int
    open_positions: int
    wallet_reconciled: bool


def recover(ledger: PositionLedger, wallet: PaperWallet) -> RecoveryReport:
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
    return RecoveryReport(released, reopened, len(ledger.active()), wallet.reconcile().ok)
