"""The position monitor: the one place an open position's exit is decided.

Owner order 2026-10-21 §2, on each valid price update:
  read the open position, its entry basis and its persisted highest value;
  evaluate CHAD's mandatory protective exits first;
  if none triggers, evaluate the position's own paper exit policy (BIGMAC_PAPER);
  persist the new peak;
  emit at most ONE ExitIntent and send it through the existing ExecutionGateway (paper fill with curve fees and
  price impact, reconciled in the PositionLedger).
There is no second gateway or ledger. A price update is a trade event for a held mint that carries curve reserves;
the value is read from the token registry's newest reserves, so a late event never moves the price backwards.
`sweep` runs the time-based CHAD checks (The Executioner: at least every 10 s) on every open position; it never
re-runs a price policy on a price it has already seen.

Units for BigMac (its pump.fun path, bot.ts:1008-1023): value = SOL a sale of every held token would return now,
net of the curve fee; entry = what the held tokens cost.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Iterable, Protocol

from jinx.core.events import EventKind, MarketEvent
from jinx.execution import ExecutionGateway, ExitIntent
from jinx.exits.bigmac_exit import ExitReason, evaluate_exit
from jinx.exits.policy import BIGMAC_PAPER, BigMacPaperConfig
from jinx.ledger import Position, PositionLedger, Status
from jinx.paper import CurveFees, ExecResult, sell_proceeds
from jinx.registry import TokenRegistry

log = logging.getLogger("jinx.exits")
VENUE = "pump.fun bonding curve (paper)"


@dataclass(frozen=True)
class Context:
    """What every exit check sees for one position at one moment."""
    position: Position
    value_lamports: int          # SOL out for every held token now, net of curve fees
    entry_basis_lamports: int    # what the held tokens cost
    peak_lamports: int           # persisted highest value before this update (0 if none yet)
    event_ns: int                # event time of the price (or the sweep time)
    now_ns: int
    price_update: bool           # True for a new price, False for a sweep tick


class ExitCheck(Protocol):
    """A CHAD mandatory protective exit. Returns an ExitIntent, or None."""
    name: str

    def __call__(self, ctx: Context) -> ExitIntent | None: ...


@dataclass
class Decision:
    position_id: int
    intent_id: str
    reason_code: str
    source: str                  # which check or policy decided
    result: ExecResult
    decided_ns: int
    event_ns: int


@dataclass
class _Stats:
    price_updates: int = 0
    sweeps: int = 0
    evaluations: int = 0
    no_valid_price: int = 0
    exits_submitted: int = 0
    exits_filled: int = 0
    exits_not_filled: dict = field(default_factory=dict)


class PositionMonitor:
    def __init__(self, ledger: PositionLedger, tokens: TokenRegistry, gateway: ExecutionGateway, fees: CurveFees,
                 protective: Iterable[ExitCheck] = (), clock_ns: Callable[[], int] = time.time_ns) -> None:
        self.ledger, self.tokens, self.gateway, self.fees = ledger, tokens, gateway, fees
        self.protective = list(protective)
        self.clock_ns = clock_ns
        self.stats = _Stats()
        self.decisions: deque[dict] = deque(maxlen=50)     # newest decisions for the operator view (RAM, bounded)
        self._last_price: dict[int, tuple] = {}            # position -> reserves last evaluated (bounded by positions)

    # ---- inputs -------------------------------------------------------
    def on_event(self, ev: MarketEvent) -> Decision | None:
        """Fast-lane handler. A trade carrying reserves for a held mint is a price update."""
        if ev.kind is not EventKind.TRADE or ev.v_sol_lamports is None or ev.v_tokens is None:
            return None
        decision = None
        for pos in self.ledger.active():
            if pos.mint == ev.mint and pos.status is Status.OPEN:
                self.stats.price_updates += 1
                decision = self._evaluate(pos, ev.event_time_ns, price_update=True) or decision
        return decision

    def sweep(self, now_ns: int | None = None) -> list[Decision]:
        """Every open position, time-based checks (CHAD: "every 10 seconds"). Also prunes state of closed positions."""
        now = now_ns if now_ns is not None else self.clock_ns()
        self.stats.sweeps += 1
        open_ids, out = set(), []
        for pos in self.ledger.active():
            open_ids.add(pos.position_id)
            if pos.status is Status.OPEN:
                d = self._evaluate(pos, now, price_update=False)
                if d:
                    out.append(d)
        for pid in list(self._last_price):
            if pid not in open_ids:
                del self._last_price[pid]
        return out

    # ---- decision -----------------------------------------------------
    def _evaluate(self, pos: Position, event_ns: int, price_update: bool) -> Decision | None:
        tok = self.tokens.get(pos.mint)
        if tok is None or not tok.v_sol_lamports or not tok.v_tokens or pos.token_amount <= 0:
            self.stats.no_valid_price += 1
            return None
        reserves = (tok.v_sol_lamports, tok.v_tokens, pos.token_amount)
        new_price = self._last_price.get(pos.position_id) != reserves
        if not price_update and not self.protective:
            return None
        self.stats.evaluations += 1
        now = self.clock_ns()
        value, _fee = sell_proceeds(tok.v_sol_lamports, tok.v_tokens, pos.token_amount, self.fees.total_bps)
        basis = self.ledger.entry_basis(pos.position_id)
        if basis is None:
            return None
        peak_before = pos.peak_value_lamports or 0
        ctx = Context(pos, value, basis, peak_before, event_ns, now, price_update)
        self.ledger.mark(pos.position_id, value, now)

        intent, source = None, None
        for check in self.protective:                    # 1. CHAD's mandatory protective exits first
            intent = check(ctx)
            if intent is not None:
                source = check.name
                break
        new_peak = max(value, peak_before)
        if intent is None and price_update and new_price and pos.exit_policy == BIGMAC_PAPER and pos.simulated:
            intent, new_peak = self._bigmac(ctx)         # 2. the position's own paper policy
            source = BIGMAC_PAPER if intent else None
        if price_update:
            self._last_price[pos.position_id] = reserves
        self.ledger.raise_peak(pos.position_id, new_peak, event_ns)   # 3. persist the peak before any sell
        if intent is None:
            return None
        return self.submit(pos, intent, source, now, event_ns)        # 4. one intent, the existing gateway

    def _bigmac(self, ctx: Context) -> tuple[ExitIntent | None, int]:
        pos = ctx.position
        try:
            cfg = BigMacPaperConfig.from_dict(json.loads(pos.exit_policy_json or "{}"))
        except (ValueError, TypeError, KeyError):
            log.warning("position %s has no readable %s settings; no BigMac exit", pos.position_id, BIGMAC_PAPER)
            return None, max(ctx.value_lamports, ctx.peak_lamports)
        reason, peak = evaluate_exit(ctx.value_lamports, ctx.entry_basis_lamports, ctx.peak_lamports,
                                     cfg.take_profit_pct, cfg.stop_loss_pct, cfg.trailing_stop_pct,
                                     cfg.trailing_stop_activation_pct)
        if reason is None:
            return None, peak
        bought = self.ledger.bought_tokens(pos.position_id)
        evidence = {"value_lamports": ctx.value_lamports, "entry_basis_lamports": ctx.entry_basis_lamports,
                    "peak_before_lamports": ctx.peak_lamports, "peak_lamports": peak, "settings": cfg.to_dict(),
                    "reserves": [self.tokens.get(pos.mint).v_sol_lamports, self.tokens.get(pos.mint).v_tokens]}
        intent = ExitIntent(
            f"{BIGMAC_PAPER}:{pos.position_id}:{reason.value}", pos.position_id, None, reason=reason.value,
            source_strategy=pos.strategy, triggering_evidence=json.dumps(evidence, sort_keys=True),
            required_sell_pct=Decimal(1),        # BigMac's pump.fun path sells every held token (bot.ts:838-866)
            event_timestamp=ctx.event_ns,
            exit_urgency="protective" if reason is ExitReason.STOP_LOSS else "normal",
            previously_executed_fraction=Decimal(bought - pos.token_amount) / Decimal(bought) if bought else Decimal(0),
            remaining_confirmed_balance=pos.token_amount, reason_code=reason.value, venue=VENUE,
            quote_status="QUOTED", quote_lamports=ctx.value_lamports, exit_policy=BIGMAC_PAPER)
        return intent, peak

    def submit(self, pos: Position, intent: ExitIntent, source: str, decided_ns: int, event_ns: int) -> Decision:
        """Record the decision and send the one intent through the gateway. Every exit decider uses this."""
        self.ledger.record_order_event(intent.intent_id, pos.position_id, "sell", "DECIDED", decided_ns,
                                       pos.simulated, detail=json.dumps({"source": source, "signal_ns": event_ns,
                                                                         "reason": intent.reason_code}))
        self.stats.exits_submitted += 1
        result = self.gateway.submit_exit(intent)
        if result.ok:
            self.stats.exits_filled += 1
        else:
            self.stats.exits_not_filled[result.reason] = self.stats.exits_not_filled.get(result.reason, 0) + 1
        d = Decision(pos.position_id, intent.intent_id, intent.reason_code, source, result, decided_ns, event_ns)
        self.decisions.append({"position_id": d.position_id, "intent_id": d.intent_id, "reason": d.reason_code,
                               "source": source, "ok": result.ok, "result": result.reason, "decided_ns": decided_ns,
                               "signal_ns": event_ns})
        return d

    def health(self) -> dict:
        s = self.stats
        return {"price_updates": s.price_updates, "sweeps": s.sweeps, "evaluations": s.evaluations,
                "no_valid_price": s.no_valid_price, "exits_submitted": s.exits_submitted,
                "exits_filled": s.exits_filled, "exits_not_filled": dict(s.exits_not_filled),
                "protective_checks": [c.name for c in self.protective], "latest": list(self.decisions)[-5:]}
