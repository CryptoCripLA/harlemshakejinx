"""CHAD's mandatory protective exits that the position monitor runs before any paper policy.

Sybil funder check (CHAD Bot 10): "If 3+ wallets share the same SOL funder → emergency exit fires instantly". The
evidence comes from Phase 2's Early Momentum tracker (`EarlyMomentumEvidence.emergency_exit`: the sybil_funder
predicate is FAIL because three or more buyers share one GMGN-reported funder). An UNAVAILABLE funder is not a FAIL
and fires nothing: missing data is never treated as either clean or dirty here; it already blocks entries.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Callable

from jinx.execution import ExitIntent
from jinx.exits.monitor import VENUE, Context


class SybilEmergencyExit:
    name = "CHAD_SYBIL_EMERGENCY"

    def __init__(self, evidence_for: Callable[[str], object | None]) -> None:
        self.evidence_for = evidence_for

    def __call__(self, ctx: Context) -> ExitIntent | None:
        pos = ctx.position
        ev = self.evidence_for(pos.mint)
        if ev is None or not getattr(ev, "emergency_exit", False):
            return None
        return ExitIntent(
            f"{self.name}:{pos.position_id}", pos.position_id, None, reason="sybil_funder_emergency",
            source_strategy=pos.strategy,
            triggering_evidence=json.dumps({"fee_payers": list(getattr(ev, "fee_payers", ()))}),
            required_sell_pct=Decimal(1), event_timestamp=ctx.event_ns, exit_urgency="protective",
            remaining_confirmed_balance=pos.token_amount, venue=VENUE, quote_status="QUOTED",
            quote_lamports=ctx.value_lamports)
