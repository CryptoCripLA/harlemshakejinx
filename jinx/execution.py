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


class ExecutionGateway:
    def __init__(self, store: Store, ledger: PositionLedger, paper: PaperExecutor, live=None) -> None:
        self.store, self.ledger, self.paper, self.live = store, ledger, paper, live

    def submit_entry(self, intent: EntryIntent) -> ExecResult:
        live = get_execution_mode(self.store) is ExecutionMode.LIVE
        if live and self.live is None:
            raise LiveExecutionUnavailable("live execution is not available in this build")
        r = self.ledger.reserve_entry(intent.intent_id, intent.mint, intent.budget_lamports, simulated=not live,
                                      fields=intent.position_fields())
        if not r.ok:
            return ExecResult(False, r.position_id, r.reason)
        if r.reason == "SAME_INTENT":
            pos = self.ledger.get(r.position_id)
            if pos.status is Status.FAILED:
                return ExecResult(False, pos.position_id, pos.note or "FAILED")  # the original outcome
            if pos.status is not Status.PENDING_OPEN:
                return ExecResult(True, pos.position_id, "ALREADY_EXECUTED")  # it has its buy fill
        return self.paper.buy(r.position_id, intent.intent_id, intent.mint, intent.budget_lamports)

    def submit_exit(self, intent: ExitIntent) -> ExecResult:
        pos = self.ledger.get(intent.position_id)
        if pos is None:
            return ExecResult(False, intent.position_id, "UNKNOWN_POSITION")
        if not pos.simulated and self.live is None:
            raise LiveExecutionUnavailable("a live position exists but no live executor is wired")
        # owner order 2026-10-21 §3: never sell another strategy's position, never on a balance the decision did
        # not see. Refused before anything is bound, so a corrected intent can still run.
        if intent.source_strategy is not None and pos.strategy is not None and intent.source_strategy != pos.strategy:
            return ExecResult(False, intent.position_id, "FOREIGN_STRATEGY")
        if intent.exit_policy is not None and intent.exit_policy != pos.exit_policy:
            return ExecResult(False, intent.position_id, "FOREIGN_EXIT_POLICY")
        if intent.remaining_confirmed_balance is not None and intent.remaining_confirmed_balance != pos.token_amount \
                and not self.ledger.has_fill(sell_fill_id(intent.position_id, intent.intent_id)):
            return ExecResult(False, intent.position_id, "STALE_BALANCE")
        r = self.ledger.claim_exit(intent.intent_id, intent.position_id, intent.token_amount,
                                   sell_fill_id(intent.position_id, intent.intent_id), meta=intent.record())
        if not r.ok or r.reason == "ALREADY_APPLIED":
            return ExecResult(r.ok, intent.position_id, r.reason)
        return self.paper.sell(intent.position_id, intent.intent_id, intent.token_amount,
                               quote_lamports=intent.quote_lamports)
