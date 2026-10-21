"""Entry decisions: SIZE then FIRE, through the one gateway.

Fast path (CHAD Bot 10, "Bypasses the normal scoring queue … Executed immediately"): an Early Momentum detection
whose own predicates pass is decided at once, on the caller's stack, as soon as its safety verdict is in. At the
decision the entry rule and every other check are evaluated again on the current state (owner decision B-02 §5,
`JinxApp.entry_check`); a hard safety veto is permanent; sizing must produce a budget; then one EntryIntent goes to
`ExecutionGateway.submit_entry`, which applies the owner's slot rule atomically. The intent id is the detection's own
(mint + triggering event time), so a detection buys at most once and a new wave is a new decision.

Standard path (CHAD C12, "Every token clearing the global score threshold"): needs CHAD's confluence weights and
global threshold, which CHAD does not give (B-03). Without them `consider_standard` refuses; it never guesses.

Modes: only AUTO makes JINX-generated entries. OFF makes none. COPY routes tracked-wallet activity, which is not an
Early Momentum entry. Nothing here exits: exits never depend on mode, sizing or safety.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from jinx.confluence.board import ConfluenceBoard
from jinx.execution import EntryIntent, ExecutionGateway
from jinx.exits.policy import BIGMAC_PAPER, load_bigmac_config
from jinx.ledger import PositionLedger, Status
from jinx.modes import ExecutionMode, Mode, get_execution_mode, get_mode
from jinx.paper import CurveFees, curve_sol_for_budget, curve_tokens_out
from jinx.registry import TokenRegistry
from jinx.sizing.kelly import QuarterKelly
from jinx.store import Store

FAST_PATH = "early_momentum"


@dataclass(frozen=True)
class EntryDecision:
    ok: bool
    reason: str | None
    mint: str
    intent_id: str | None = None
    position_id: int | None = None


class EntryCoordinator:
    def __init__(self, store: Store, ledger: PositionLedger, gateway: ExecutionGateway, sizing: QuarterKelly,
                 board: ConfluenceBoard, entry_check: Callable[[str], dict], tokens: TokenRegistry, fees: CurveFees,
                 clock_ns: Callable[[], int] = time.time_ns, reload_sizing: Callable | None = None) -> None:
        self.store, self.ledger, self.gateway, self.sizing = store, ledger, gateway, sizing
        self.reload_sizing = reload_sizing
        self.board, self.entry_check, self.tokens, self.fees = board, entry_check, tokens, fees
        self.clock_ns = clock_ns
        self.stats: dict[str, int] = {}
        self.decisions: deque[dict] = deque(maxlen=50)      # newest decisions for the operator view (RAM, bounded)

    def _done(self, d: EntryDecision, detail: dict | None = None) -> EntryDecision:
        key = ("entered" if d.reason is None else "submitted_pending_fill") if d.ok else (d.reason or "refused")
        self.stats[key] = self.stats.get(key, 0) + 1
        self.decisions.append({"mint": d.mint, "ok": d.ok, "reason": d.reason, "intent_id": d.intent_id,
                               "position_id": d.position_id, "ts_ns": self.clock_ns(), **(detail or {})})
        return d

    def consider_fast_path(self, evidence) -> EntryDecision:
        mint = evidence.mint
        mode = get_mode(self.store)
        if mode is not Mode.AUTO:
            return self._done(EntryDecision(False, f"MODE_{mode.value}", mint))
        if not evidence.trade_permitted:
            return self._done(EntryDecision(False, "EARLY_MOMENTUM_BLOCKED", mint), {"blocking": list(evidence.blocking)})
        veto = self.board.hard_veto(mint)
        if veto:
            return self._done(EntryDecision(False, "SAFETY_VETO", mint), {"veto": veto})
        check = self.entry_check(mint)                       # B-02 §5: re-checked on the current state
        if not check.get("safety_eligible"):
            return self._done(EntryDecision(False, "SAFETY_NOT_PASSED", mint), {"check": check})
        if not check.get("all_checks_pass"):
            return self._done(EntryDecision(False, "ENTRY_CHECK_FAILED", mint), {"check": _brief(check)})
        intent_id = f"AUTO:{FAST_PATH}:{mint}:{evidence.event_ns}"
        earlier = self.ledger.position_for_entry(intent_id)
        if earlier is not None:                              # this detection was already decided: never again
            failed = earlier.status is Status.FAILED
            return self._done(EntryDecision(not failed, (earlier.note or "FAILED") if failed else "ALREADY_EXECUTED",
                                            mint, intent_id, earlier.position_id))
        limit = self.ledger.max_slots()
        if limit is None or self.ledger.active_slots() >= limit:   # checked again atomically by the ledger
            return self._done(EntryDecision(False, "SLOT_LIMIT" if limit else "SLOT_STATE_INVALID", mint))
        if self.reload_sizing is not None:
            self.sizing.config = self.reload_sizing()            # the owner's current sizing settings
        decided = self.clock_ns()
        size = self.sizing.size(None, simulated=get_execution_mode(self.store) is ExecutionMode.PAPER)
        if not size.ok:
            return self._done(EntryDecision(False, size.reason, mint), {"sizing": size.detail})
        policy, policy_cfg = None, None
        cfg = load_bigmac_config(self.store)
        if cfg is not None and get_execution_mode(self.store) is ExecutionMode.PAPER:
            policy, policy_cfg = BIGMAC_PAPER, cfg.to_dict()
        intent = EntryIntent(intent_id, mint, size.budget_lamports, origin="AUTO", exit_policy=policy,
                             exit_policy_config=policy_cfg, entry_filter=self.sizing.config.entry_filter,
                             sizing=size.detail, signal_ns=evidence.event_ns)
        quote = self._quote(mint, size.budget_lamports)
        simulated = get_execution_mode(self.store) is ExecutionMode.PAPER
        prior = self.ledger.order_events(intent_id)
        if not prior:
            self.ledger.record_order_event(intent_id, None, "buy", "DECIDED", decided, simulated,
                                           detail=json.dumps({"path": FAST_PATH, "signal_ns": evidence.event_ns}))
            self.ledger.record_order_event(intent_id, None, "buy", "QUOTED", self.clock_ns(), simulated,
                                           detail=json.dumps({"expected_tokens": quote}))
        res = self.gateway.submit_entry(intent)
        accepted = res.ok or res.reason == "PENDING_FILL"   # a delayed paper fill is accepted, not yet filled
        return self._done(EntryDecision(accepted, res.reason, mint, intent_id, res.position_id),
                          {"budget_lamports": size.budget_lamports, "signal_ns": evidence.event_ns,
                           "decided_ns": decided})

    def consider_standard(self, mint: str) -> EntryDecision:
        """CHAD's scored path. Refused until the owner supplies weights and the global threshold (B-03)."""
        if self.board.score(mint) is None:
            return self._done(EntryDecision(False, "NO_CONFLUENCE_SCORE", mint), {"blocker": "B-03"})
        return self._done(EntryDecision(False, "NO_GLOBAL_THRESHOLD", mint), {"blocker": "B-03"})

    def _quote(self, mint: str, budget: int) -> int | None:
        tok = self.tokens.get(mint)
        if tok is None or not tok.v_sol_lamports or not tok.v_tokens:
            return None
        return curve_tokens_out(tok.v_sol_lamports, tok.v_tokens, curve_sol_for_budget(budget, self.fees.total_bps))


def _brief(check: dict) -> dict:
    return {k: check.get(k) for k in ("reserve_eligible", "reserve_reason", "safety_eligible",
                                      "early_momentum_blocking", "migrated", "lifecycle_state")}
