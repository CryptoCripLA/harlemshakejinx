"""CHAD Bot 9, The Executioner (docs/CHAD_BLUEPRINT.md; matrix rows C09 and X1).

CHAD, verbatim:
- "Watches the live Confluence Score for every open position every 10 seconds. Auto-exits on Confluence Collapse —
  when the score drops below 40% of the entry threshold."
- "A 10-minute grace window applies after entry."
- Negative Divergence: "Score-adaptive partial exit": "Score 75+ 30% exit", "Score 65–74 40% exit",
  "Score 60–64 50% exit", "Score < 60 60% exit"; "Only fires when you're in profit … it never triggers a loss
  exit"; "Re-entry allowed immediately — No exit cooldown".

Every number here is CHAD's. What CHAD does not give stays out (B-03): the confluence weights and the entry threshold
(so in production no live score exists yet, and Confluence Collapse reports UNAVAILABLE rather than firing), and the
momentum "stall" and "sustained volume" quantities that produce a Negative Divergence event (the producer is Phase 2's
momentum module, C02; `Executioner.on_divergence` is the interface it feeds). Score buckets read CHAD's integer
ranges as half-open: 75 and above, 65 up to 75, 60 up to 65, below 60 (so 74.5 is in the 65–74 bucket).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from jinx.execution import ExitIntent
from jinx.exits.monitor import VENUE, Context, Decision, PositionMonitor
from jinx.ledger import PositionLedger, Status
from jinx.paper import sell_fill_id, sell_proceeds

COLLAPSE_FRACTION = Decimal("0.40")     # "below 40% of the entry threshold"
GRACE_S = 600                           # "A 10-minute grace window applies after entry"
CHECK_INTERVAL_S = 10                   # "every 10 seconds"
DIVERGENCE_BUCKETS = ((Decimal(75), Decimal("0.30")), (Decimal(65), Decimal("0.40")), (Decimal(60), Decimal("0.50")))
DIVERGENCE_BELOW_60 = Decimal("0.60")


def divergence_exit_fraction(entry_score: Decimal | None) -> Decimal | None:
    if entry_score is None:
        return None
    for floor, fraction in DIVERGENCE_BUCKETS:
        if entry_score >= floor:
            return fraction
    return DIVERGENCE_BELOW_60


class ConfluenceCollapse:
    """A protective check run by the monitor on every sweep and price update."""
    name = "CHAD_CONFLUENCE_COLLAPSE"

    def __init__(self, score_for: Callable[[str], Decimal | None], ledger: PositionLedger) -> None:
        self.score_for, self.ledger = score_for, ledger
        self.unavailable = 0          # no live score for a position past its grace window
        self.no_threshold = 0         # a position entered without a threshold (the fast path) cannot collapse

    def __call__(self, ctx: Context) -> ExitIntent | None:
        pos = ctx.position
        if pos.entry_threshold is None:
            self.no_threshold += 1
            return None
        if ctx.now_ns - pos.opened_ns < GRACE_S * 1_000_000_000:
            return None
        score = self.score_for(pos.mint)
        if score is None:
            self.unavailable += 1
            return None
        boundary = Decimal(pos.entry_threshold) * COLLAPSE_FRACTION
        if not score < boundary:
            return None
        return ExitIntent(
            f"{self.name}:{pos.position_id}", pos.position_id, None, reason="confluence_collapse",
            source_strategy=pos.strategy,
            triggering_evidence=json.dumps({"score": str(score), "entry_threshold": pos.entry_threshold,
                                            "boundary": str(boundary),
                                            "age_s": (ctx.now_ns - pos.opened_ns) / 1e9}),
            required_sell_pct=Decimal(1), event_timestamp=ctx.event_ns, exit_urgency="protective",
            remaining_confirmed_balance=pos.token_amount, venue=VENUE, quote_status="QUOTED",
            quote_lamports=ctx.value_lamports)


@dataclass(frozen=True)
class Divergence:
    """One Negative Divergence event from the momentum module ("price stalls on sustained volume")."""
    mint: str
    event_id: str
    event_ns: int
    source: str
    evidence: dict = field(default_factory=dict)


class Executioner:
    name = "CHAD_NEGATIVE_DIVERGENCE"

    def __init__(self, monitor: PositionMonitor, ledger: PositionLedger, network_fee_lamports: int) -> None:
        self.monitor, self.ledger, self.network_fee = monitor, ledger, network_fee_lamports
        self.stats = {"events": 0, "not_in_profit": 0, "no_entry_score": 0, "repeat_event": 0, "no_price": 0,
                      "exits": 0}

    def on_divergence(self, ev: Divergence) -> list[Decision]:
        """Score-adaptive partial exit on each open position in `ev.mint` that is in profit. One event sells at most
        once per position: its intent id is the event's id, and the ledger applies a fill once."""
        self.stats["events"] += 1
        out = []
        for pos in self.ledger.active():
            if pos.mint != ev.mint or pos.status is not Status.OPEN:
                continue
            fraction = divergence_exit_fraction(None if pos.entry_score is None else Decimal(pos.entry_score))
            if fraction is None:
                self.stats["no_entry_score"] += 1
                continue
            intent_id = f"{self.name}:{pos.position_id}:{ev.event_id}"
            if self.ledger.has_fill(sell_fill_id(pos.position_id, intent_id)):
                self.stats["repeat_event"] += 1
                continue
            tok = self.monitor.tokens.get(pos.mint)
            if tok is None or not tok.v_sol_lamports or not tok.v_tokens:
                self.stats["no_price"] += 1
                continue
            value, _ = sell_proceeds(tok.v_sol_lamports, tok.v_tokens, pos.token_amount, self.monitor.fees.total_bps)
            # "in profit": the whole position, closed now, after every cost (both network fees, curve fees)
            pnl_if_closed = pos.proceeds_lamports + value - pos.cost_lamports - pos.network_fees_lamports \
                - self.network_fee
            if pnl_if_closed <= 0:
                self.stats["not_in_profit"] += 1
                continue
            bound = self.ledger.exit_binding(intent_id)   # a retry keeps the amount it was first decided with
            amount = bound if bound is not None else int(pos.token_amount * fraction)
            if amount <= 0:
                continue
            bought = self.ledger.bought_tokens(pos.position_id)
            intent = ExitIntent(
                intent_id, pos.position_id, amount, reason="negative_divergence", source_strategy=pos.strategy,
                triggering_evidence=json.dumps({"event_id": ev.event_id, "source": ev.source,
                                                "entry_score": pos.entry_score, "fraction": str(fraction),
                                                "pnl_if_closed_lamports": pnl_if_closed, **ev.evidence},
                                               default=str),
                required_sell_pct=fraction, event_timestamp=ev.event_ns, exit_urgency="normal",
                previously_executed_fraction=(Decimal(bought - pos.token_amount) / Decimal(bought)) if bought
                else Decimal(0),
                remaining_confirmed_balance=pos.token_amount, venue=VENUE, quote_status="QUOTED",
                quote_lamports=sell_proceeds(tok.v_sol_lamports, tok.v_tokens, amount,
                                             self.monitor.fees.total_bps)[0])
            d = self.monitor.submit(pos, intent, self.name, self.monitor.clock_ns(), ev.event_ns)
            self.stats["exits"] += d.result.ok
            out.append(d)
        return out
