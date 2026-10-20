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

import time
from dataclasses import dataclass, field

from jinx.ledger import PositionLedger, Status
from jinx.modes import ExecutionMode, get_execution_mode
from jinx.paper import ExecResult, PaperExecutor, sell_fill_id
from jinx.store import Store


class LiveExecutionUnavailable(RuntimeError):
    """Raised when live execution is requested but no live executor is wired (always, in Phase 1)."""


def _positive_int(name: str, value) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


@dataclass(frozen=True)
class EntryIntent:
    intent_id: str
    mint: str
    budget_lamports: int
    origin: str = "unspecified"
    created_ns: int = field(default_factory=time.time_ns)

    def __post_init__(self) -> None:
        if not self.intent_id or not self.mint:
            raise ValueError("entry intent needs an id and a mint")
        _positive_int("budget_lamports", self.budget_lamports)


@dataclass(frozen=True)
class ExitIntent:
    intent_id: str
    position_id: int
    token_amount: int | None = None  # None = the whole remaining position
    reason: str = "unspecified"

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise ValueError("exit intent needs an id")
        if self.token_amount is not None:
            _positive_int("token_amount", self.token_amount)


class ExecutionGateway:
    def __init__(self, store: Store, ledger: PositionLedger, paper: PaperExecutor, live=None) -> None:
        self.store, self.ledger, self.paper, self.live = store, ledger, paper, live

    def submit_entry(self, intent: EntryIntent) -> ExecResult:
        live = get_execution_mode(self.store) is ExecutionMode.LIVE
        if live and self.live is None:
            raise LiveExecutionUnavailable("live execution is not available in this build")
        r = self.ledger.reserve_entry(intent.intent_id, intent.mint, intent.budget_lamports, simulated=not live)
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
        binding = self.ledger.bind_exit_intent(intent.intent_id, intent.position_id, intent.token_amount)
        if binding == "CONFLICT":
            return ExecResult(False, intent.position_id, "INTENT_CONFLICT")
        if binding == "SAME" and self.ledger.has_fill(sell_fill_id(intent.position_id, intent.intent_id)):
            return ExecResult(True, intent.position_id, "ALREADY_APPLIED")
        r = self.ledger.begin_exit(intent.position_id, intent.intent_id)
        if not r.ok:
            return ExecResult(False, intent.position_id, r.reason)
        return self.paper.sell(intent.position_id, intent.intent_id, intent.token_amount)
