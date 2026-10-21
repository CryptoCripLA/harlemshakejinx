"""The one order gateway. Every entry and exit JINX makes passes through here.

Entries: refused before anything is reserved when execution is LIVE and no
live executor exists (Phase 1 has none). Otherwise the ledger reserves the
slot atomically and the paper executor fills it. A replayed entry intent is
never executed twice; one that crashed after reserving but before filling is
completed.

Exits: never blocked by mode or slots. They are routed by the position's own
kind, so a paper position always exits on paper.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from jinx.ledger import PositionLedger, Status
from jinx.modes import ExecutionMode, get_execution_mode
from jinx.paper import ExecResult, PaperExecutor, sell_fill_id
from jinx.store import Store


class LiveExecutionUnavailable(RuntimeError):
    """Raised when live execution is requested but no live executor is wired (always, in Phase 1)."""


def _clean_id(name: str, value) -> None:
    """Ids are stored as text and compared by SQLite; control characters (NUL above all, which SQLite's
    text functions treat as the end of the string) are refused."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{name} must not contain control characters")


def _positive_int(name: str, value) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


def _fraction(name: str, value, low_open: bool, high_open: bool) -> None:
    """A Decimal in [0, 1], with either end excluded as asked."""
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal, got {value!r}")
    if value < 0 or value > 1 or (low_open and value == 0) or (high_open and value == 1):
        raise ValueError(f"{name} out of range: {value}")


def _opt_int(name: str, value, minimum: int) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")


@dataclass(frozen=True)
class EntryIntent:
    intent_id: str
    mint: str
    budget_lamports: int
    origin: str = "unspecified"        # the strategy that owns the position (AUTO, COPY, ...); only it may exit it
    created_ns: int = field(default_factory=time.time_ns)
    exit_policy: str | None = None     # the paper exit policy assigned at entry, e.g. BIGMAC_PAPER
    exit_policy_config: dict | None = None   # that policy's explicitly configured settings, frozen with the position
    entry_score: Decimal | None = None       # confluence score at entry (None = no score: the fast path)
    entry_threshold: Decimal | None = None   # the entry threshold in force (Confluence Collapse uses 40 % of it)
    entry_filter: str | None = None          # the entry filter in force (Kelly resets when it changes)
    sizing: dict | None = None               # the sizing decision that produced budget_lamports
    signal_ns: int | None = None             # event time of the signal that triggered the entry

    def __post_init__(self) -> None:
        _clean_id("intent_id", self.intent_id)
        _clean_id("mint", self.mint)
        _positive_int("budget_lamports", self.budget_lamports)
        _clean_id("origin", self.origin)
        if self.exit_policy is not None:
            _clean_id("exit_policy", self.exit_policy)
        for name in ("entry_score", "entry_threshold"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, Decimal) or not v.is_finite()):
                raise TypeError(f"{name} must be a finite Decimal, got {v!r}")
        _opt_int("signal_ns", self.signal_ns, 0)

    def position_fields(self) -> dict:
        dumps = lambda d: None if d is None else json.dumps(d, sort_keys=True, default=str)
        text = lambda d: None if d is None else str(d)
        return {"strategy": self.origin, "exit_policy": self.exit_policy,
                "exit_policy_json": dumps(self.exit_policy_config), "entry_score": text(self.entry_score),
                "entry_threshold": text(self.entry_threshold), "entry_filter": self.entry_filter,
                "sizing_json": dumps(self.sizing), "signal_ns": self.signal_ns}


URGENCIES = ("normal", "protective")


@dataclass(frozen=True)
class ExitIntent:
    """The one exit intent for every exit (CHAD's, BigMac's, an owner's). The owner's fields (2026-10-21) are
    recorded with the exit; `intent_id` is the owner's `idempotency_id`."""
    intent_id: str
    position_id: int
    token_amount: int | None = None  # None = the whole remaining position
    reason: str = "unspecified"
    source_strategy: str | None = None          # must be the position's owning strategy (None: an owner/system exit)
    triggering_evidence: str | None = None
    required_sell_pct: Decimal | None = None    # (0, 1]
    event_timestamp: int | None = None          # ns, event time of the price update or signal that triggered it
    exit_urgency: str = "normal"                # "normal" | "protective"
    previously_executed_fraction: Decimal | None = None   # [0, 1) of the bought tokens already sold
    remaining_confirmed_balance: int | None = None        # tokens the decision saw; a different balance is refused
    reason_code: str | None = None
    venue: str | None = None
    quote_status: str | None = None
    quote_lamports: int | None = None           # proceeds quoted at the decision (net of curve fees)
    exit_policy: str | None = None              # the paper policy that produced it, e.g. BIGMAC_PAPER

    def __post_init__(self) -> None:
        _clean_id("intent_id", self.intent_id)
        if self.token_amount is not None:
            _positive_int("token_amount", self.token_amount)
        if self.exit_urgency not in URGENCIES:
            raise ValueError(f"exit_urgency must be one of {URGENCIES}, got {self.exit_urgency!r}")
        _fraction("required_sell_pct", self.required_sell_pct, low_open=True, high_open=False)
        _fraction("previously_executed_fraction", self.previously_executed_fraction, low_open=False, high_open=True)
        _opt_int("remaining_confirmed_balance", self.remaining_confirmed_balance, 0)
        _opt_int("event_timestamp", self.event_timestamp, 0)
        _opt_int("quote_lamports", self.quote_lamports, 0)
        if self.reason_code is None:
            object.__setattr__(self, "reason_code", self.reason)

    @property
    def idempotency_id(self) -> str:
        return self.intent_id

    def record(self) -> dict:
        text = lambda d: None if d is None else str(d)
        return {"source_strategy": self.source_strategy, "exit_policy": self.exit_policy,
                "reason_code": self.reason_code, "exit_urgency": self.exit_urgency,
                "required_sell_pct": text(self.required_sell_pct), "event_ns": self.event_timestamp,
                "triggering_evidence": self.triggering_evidence,
                "previously_executed_fraction": text(self.previously_executed_fraction),
                "remaining_confirmed_balance": self.remaining_confirmed_balance, "venue": self.venue,
                "quote_status": self.quote_status, "quote_lamports": self.quote_lamports}


class OrderState(StrEnum):
    FILLED = "FILLED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class OrderOutcome:
    state: OrderState
    order_id: str | None
    status: str | None
    input_amount: int | None = None
    output_amount: int | None = None
    detail: str | None = None


GMGN_SUCCESS = ("confirmed", "successful")
GMGN_FAILURE = ("failed", "expired")


def gmgn_order_outcome(report: dict) -> OrderOutcome:
    """One GMGN `swap` / `order get` response -> FILLED, FAILED or UNCERTAIN (INTEGRATION_MAP §2 rule 3).

    GMGN's skill docs (gmgn-swap SKILL.md, "swap / order get Response Fields"): status pending -> processed ->
    confirmed, or failed / expired; actual amounts only in `report.input_amount` / `report.output_amount`, present
    only on success (the same page also calls that status "successful"). So a fill is booked only for a success status
    WITH both report amounts; an acknowledgement, a pending or processed order, a success without amounts, or an
    unknown status is UNCERTAIN and is polled again, never assumed filled."""
    order_id, status = report.get("order_id"), report.get("status")
    if status in GMGN_FAILURE:
        return OrderOutcome(OrderState.FAILED, order_id, status, detail=report.get("error_status") or status)
    if status in GMGN_SUCCESS:
        r = report.get("report") or {}
        try:
            i, o = int(r["input_amount"]), int(r["output_amount"])
        except (KeyError, TypeError, ValueError):
            return OrderOutcome(OrderState.UNCERTAIN, order_id, status, detail="success without report amounts")
        if i >= 0 and o >= 0:
            return OrderOutcome(OrderState.FILLED, order_id, status, i, o)
    return OrderOutcome(OrderState.UNCERTAIN, order_id, status, detail="not final")


@dataclass
class _PendingFill:
    side: str
    intent: object
    position_id: int
    mint: str
    due_ns: int


class ExecutionGateway:
    """`fill_delay_ns` is a labelled paper simulation setting: when > 0, a paper order is priced at the reserves of the
    first price update at least that long after the decision (event time), instead of the reserves the decision saw.
    Until then the order is SUBMITTED and the position stays pending: nothing is booked. 0 (the default) fills at
    the decision's reserves, which ignores latency (CLAUDE.md: an observed price is not automatically a fill)."""

    def __init__(self, store: Store, ledger: PositionLedger, paper: PaperExecutor, live=None,
                 fill_delay_ns: int = 0) -> None:
        self.store, self.ledger, self.paper, self.live = store, ledger, paper, live
        if isinstance(fill_delay_ns, bool) or not isinstance(fill_delay_ns, int) or fill_delay_ns < 0:
            raise ValueError("fill_delay_ns must be a non-negative int")
        self.fill_delay_ns = fill_delay_ns
        self.pending: dict[str, _PendingFill] = {}           # at most one per open order (slots bound it)

    def _decision_time(self, mint: str, event_ns: int | None) -> int:
        if event_ns is not None:
            return event_ns
        tok = self.paper.registry.get(mint)
        return tok.latest_time_ns if tok is not None and tok.latest_time_ns is not None else time.time_ns()

    def on_price(self, event_ns: int, mint: str) -> int:
        """A new price for `mint` at event time `event_ns`: fill the paper orders now due. Returns fills attempted."""
        due = [k for k, p in self.pending.items() if p.mint == mint and event_ns >= p.due_ns]
        for k in due:
            self._fill(self.pending.pop(k))
        return len(due)

    def process_due(self, now_ns: int) -> int:
        """The sweep's clock: fill every paper order due by `now_ns` at the latest reserves."""
        due = [k for k, p in self.pending.items() if now_ns >= p.due_ns]
        for k in due:
            self._fill(self.pending.pop(k))
        return len(due)

    def _fill(self, p: _PendingFill) -> ExecResult:
        if p.side == "buy":
            i = p.intent
            res = self.paper.buy(p.position_id, i.intent_id, i.mint, i.budget_lamports)
        else:
            i = p.intent
            res = self.paper.sell(p.position_id, i.intent_id, i.token_amount, quote_lamports=i.quote_lamports)
        self._stage(i.intent_id, p.position_id, p.side, "FILLED" if res.ok else "FAILED", True,
                    None if res.ok else res.reason)
        return res

    def submit_entry(self, intent: EntryIntent) -> ExecResult:
        live = get_execution_mode(self.store) is ExecutionMode.LIVE
        if live and self.live is None:
            raise LiveExecutionUnavailable("live execution is not available in this build")
        r = self.ledger.reserve_entry(intent.intent_id, intent.mint, intent.budget_lamports, simulated=not live,
                                      fields=intent.position_fields())
        if not r.ok:
            self._stage(intent.intent_id, r.position_id, "buy", "REFUSED", not live, r.reason)
            return ExecResult(False, r.position_id, r.reason)
        if r.reason == "SAME_INTENT":
            pos = self.ledger.get(r.position_id)
            if pos.status is Status.FAILED:
                return ExecResult(False, pos.position_id, pos.note or "FAILED")  # the original outcome
            if pos.status is not Status.PENDING_OPEN:
                return ExecResult(True, pos.position_id, "ALREADY_EXECUTED")  # it has its buy fill
        self._stage(intent.intent_id, r.position_id, "buy", "SUBMITTED", not live)
        if self.fill_delay_ns > 0:
            due = self._decision_time(intent.mint, None) + self.fill_delay_ns
            self.pending[intent.intent_id] = _PendingFill("buy", intent, r.position_id, intent.mint, due)
            return ExecResult(False, r.position_id, "PENDING_FILL")
        res = self.paper.buy(r.position_id, intent.intent_id, intent.mint, intent.budget_lamports)
        self._stage(intent.intent_id, r.position_id, "buy", "FILLED" if res.ok else "FAILED", not live,
                    None if res.ok else res.reason)
        return res

    def submit_exit(self, intent: ExitIntent) -> ExecResult:
        pos = self.ledger.get(intent.position_id)
        if pos is None:
            return ExecResult(False, intent.position_id, "UNKNOWN_POSITION")
        if not pos.simulated and self.live is None:
            raise LiveExecutionUnavailable("a live position exists but no live executor is wired")
        refused = self._exit_refusal(intent, pos)
        if refused:
            self._stage(intent.intent_id, pos.position_id, "sell", "REFUSED", pos.simulated, refused)
            return ExecResult(False, intent.position_id, refused)
        r = self.ledger.claim_exit(intent.intent_id, intent.position_id, intent.token_amount,
                                   sell_fill_id(intent.position_id, intent.intent_id), meta=intent.record())
        if r.reason == "ALREADY_APPLIED":
            return ExecResult(r.ok, intent.position_id, r.reason)
        if not r.ok:
            self._stage(intent.intent_id, pos.position_id, "sell", "REFUSED", pos.simulated, r.reason)
            return ExecResult(r.ok, intent.position_id, r.reason)
        self._stage(intent.intent_id, pos.position_id, "sell", "SUBMITTED", pos.simulated)
        if self.fill_delay_ns > 0:
            due = self._decision_time(pos.mint, intent.event_timestamp) + self.fill_delay_ns
            self.pending[intent.intent_id] = _PendingFill("sell", intent, pos.position_id, pos.mint, due)
            return ExecResult(False, pos.position_id, "PENDING_FILL")
        res = self.paper.sell(intent.position_id, intent.intent_id, intent.token_amount,
                              quote_lamports=intent.quote_lamports)
        self._stage(intent.intent_id, pos.position_id, "sell", "FILLED" if res.ok else "FAILED", pos.simulated,
                    None if res.ok else res.reason)
        return res

    def _exit_refusal(self, intent: ExitIntent, pos) -> str | None:
        """Owner order 2026-10-21 §3: never sell another strategy's position, never apply a paper policy to a
        position it does not own, never sell on a balance the decision did not see. Checked before anything is
        bound, so a corrected intent can still run."""
        if intent.source_strategy is not None and pos.strategy is not None and intent.source_strategy != pos.strategy:
            return "FOREIGN_STRATEGY"
        if intent.exit_policy is not None and (intent.exit_policy != pos.exit_policy or not pos.simulated):
            return "FOREIGN_EXIT_POLICY"
        if intent.remaining_confirmed_balance is not None and intent.remaining_confirmed_balance != pos.token_amount \
                and not self.ledger.has_fill(sell_fill_id(intent.position_id, intent.intent_id)):
            return "STALE_BALANCE"
        return None

    def _stage(self, intent_id: str, position_id, side: str, stage: str, simulated: bool,
               detail: str | None = None) -> None:
        self.ledger.record_order_event(intent_id, position_id, side, stage, time.time_ns(), simulated, detail=detail)
