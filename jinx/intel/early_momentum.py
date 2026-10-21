"""CHAD Bot 10, Early Momentum (C10): event-driven buy detection over the raw trade stream.

CHAD (docs/CHAD_BLUEPRINT.md, Bot 10), quoted in `CHAD` below: "3 buys · 3 unique wallets · 30-second window";
"3 distinct fee payers required — not 3 txs from one wallet"; "0.5–10 SOL bonding curve gate" and "all below the 35
SOL bonding curve threshold"; the "Peak SOL Pump-and-Dump Gate" on all-time high virtualSolReserves; the "SOL reserve
check — Live bonding curve read"; the "Sybil funder check"; "Bypasses the normal scoring queue"; "Full safety check
still fires … in parallel" (that check is jinx/intel/safety.py, not this module).

What it does, per mint, on every event (`on_event`, synchronous, no I/O):
- keeps the pump.fun bonding-curve buys of the last `RETAIN_NS` of event time, with the fee payer (the event's
  trader is PumpAPI's `txSigner`, the account that paid the fee), event time and the curve reserves after the buy;
- detects the earliest moment three distinct fee payers have bought within 30 s (inclusive) of event time, not
  arrival time, so a feed backlog cannot compress a slow cluster and out-of-order arrival converges on the same
  window. Sells, PumpSwap trades and repeated buys by one wallet never add a wallet;
- on detection emits one EarlyMomentumEvidence to the fast path at once (`notify`) and asks for the buyers' funders
  (`request_funders`); listener failures are counted and never stop detection;
- tracks the live and the peak curve reserves. A late report counts toward the peak, never toward the current value.

Reserve policy (owner decision B-02, 2026-10-21): the current verified real SOL in the curve must be within
0.5-10 SOL, and the highest verified real SOL since creation must not exceed 10 SOL (a token that already ran past
10 and came back is not early). Real SOL is PumpAPI's `quoteInPool` on a SOL-quoted pre-migration curve
(`readings.verified_real_sol`), never virtual SOL minus 30. Missing or incomplete history, an unverified reading
after creation, or a migrated pool blocks. CHAD's "below 35 SOL" statement is retired as an entry condition and kept
as a superseded specification (`SUPERSEDED`). `reserve_check` repeats both gates on the current state at the entry
decision, so a stale snapshot never qualifies. These rules belong to this pre-graduation path only.

What it does not do: place or permit orders. Every predicate that is not PASS blocks: `trade_permitted` stays False.
Funders come only from `record_funding` (no feed supplies them yet); unknown funders are UNAVAILABLE, never assumed.
State is RAM only and bounded (mint cap, idle time, buys per mint, funding cap).
"""
from __future__ import annotations

import bisect
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

from jinx.core.events import LAMPORTS_PER_SOL, EventKind, MarketEvent, Side
from jinx.intel.bounded import MintTable
from jinx.intel.readings import (Clock, event_time, is_curve, standard_curve, traders_involved,
                                 verified_real_sol)

log = logging.getLogger(__name__)

SIGNAL_TYPE = "early_momentum"
WINDOW_NS = 30 * 1_000_000_000      # CHAD "30-second window"
REQUIRED_BUYS = 3                   # CHAD "3 buys"
REQUIRED_FEE_PAYERS = 3             # CHAD "3 distinct fee payers required"
SHARED_FUNDER_WALLETS = 3           # CHAD "If 3+ wallets share the same SOL funder"
GATE_FLOOR_SOL, GATE_CEILING_SOL = 0.5, 10.0   # CHAD "0.5–10 SOL bonding curve gate"
GATE_BELOW_SOL = 35.0                          # CHAD "all below the 35 SOL bonding curve threshold": superseded
FLOOR_LAMPORTS = int(GATE_FLOOR_SOL * LAMPORTS_PER_SOL)      # owner decision B-02: 0.5 <= current real SOL
CEILING_LAMPORTS = int(GATE_CEILING_SOL * LAMPORTS_PER_SOL)  # ... <= 10, and the verified peak real SOL <= 10
B02 = "owner decision B-02, 2026-10-21"
CHAD = {
    "window": "3 buys · 3 unique wallets · 30-second window; 3 distinct fee payers required — not 3 txs from one "
              "wallet",
    "reserve": "SOL reserve check: Live bonding curve read — confirms real money in, not fake volume",
    "gate_low": "0.5–10 SOL bonding curve gate: Token must have real traction (0.5 SOL floor) but not be already "
                "pumped (10 SOL ceiling)",
    "gate_high": "3+ unique wallets buying within 30 seconds, all below the 35 SOL bonding curve threshold",
    "peak": "Peak SOL Pump-and-Dump Gate: Bot 10 tracks all-time high virtualSolReserves, not just current value. A "
            "token that ran to 60 SOL and dumped to 25 SOL looks early on a snapshot. The peak gate catches it.",
    "sybil": "Sybil funder check: If 3+ wallets share the same SOL funder → emergency exit fires instantly",
}
CURRENT_GATE = "current_reserve_0_5_to_10_real_sol"
PEAK_GATE = "peak_reserve_max_10_real_sol"
SUPERSEDED = {
    "curve_gate_below_35_sol": {"chad": CHAD["gate_high"],
                                "status": f"superseded by {B02}: not an entry condition; the 0.5-10 real SOL "
                                          "current and peak gates apply instead"},
}

# RAM bounds (owner storage rule 2), not trading rules
RETAIN_NS = 2 * WINDOW_NS
MAX_BUYS_PER_MINT = 512
MAX_MINTS = 20_000
IDLE_TTL_S = 2 * 3600
MAX_FUNDING = 200_000


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"     # CHAD's rule is contradictory or unquantified (docs/BLOCKERS.md)
    UNAVAILABLE = "UNAVAILABLE"   # the data needed is missing


@dataclass(frozen=True)
class Predicate:
    name: str
    status: Status
    chad: str
    detail: Mapping[str, Any]
    blocker: str | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "status": str(self.status), "chad": self.chad, "blocker": self.blocker,
                "detail": dict(self.detail)}


@dataclass(frozen=True)
class Buy:
    signature: str
    fee_payer: str
    event_ns: int
    clock: Clock
    recv_ns: int
    seq: int
    slot: int | None
    sol_lamports: int
    v_sol_lamports: int | None
    real_sol_lamports: int | None
    source: str
    traders_involved: tuple[str, ...] | None

    @property
    def key(self) -> tuple[int, int]:
        return (self.event_ns, self.seq)

    def to_dict(self) -> dict:
        return {"signature": self.signature, "fee_payer": self.fee_payer, "event_ns": self.event_ns,
                "clock": str(self.clock), "recv_ns": self.recv_ns, "slot": self.slot,
                "sol_lamports": self.sol_lamports, "v_sol_lamports": self.v_sol_lamports,
                "real_sol_lamports": self.real_sol_lamports, "source": self.source,
                "traders_involved": list(self.traders_involved) if self.traders_involved is not None else None}


@dataclass(frozen=True)
class Funding:
    """One observed funding of a wallet: which account sent it SOL, per which source, observed when."""
    wallet: str
    funder: str
    source: str
    observed_ns: int
    event_ns: int | None = None


@dataclass(frozen=True)
class EarlyMomentumEvidence:
    mint: str
    window: tuple[Buy, ...]
    fee_payers: tuple[str, ...]
    creator: str | None
    creator_in_window: bool | None
    reserves: Mapping[str, Any]
    predicates: tuple[Predicate, ...]
    observed_ns: int          # receipt of the event that completed detection
    detected_ns: int          # wall clock when the evidence was first emitted
    processing_ns: int        # local time from that event's arrival in on_event to emission
    signal_type: str = SIGNAL_TYPE

    @property
    def trigger(self) -> Buy:
        return self.window[-1]

    @property
    def event_ns(self) -> int:
        return self.trigger.event_ns

    @property
    def clock(self) -> Clock:
        return self.trigger.clock

    @property
    def feed_delay_ns(self) -> int:
        """Event time of the triggering buy to JINX's receipt of the event that completed detection."""
        return self.observed_ns - self.event_ns

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({b.source for b in self.window}))

    @property
    def blocking(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.predicates if p.status is not Status.PASS)

    @property
    def trade_permitted(self) -> bool:
        return not self.blocking

    @property
    def emergency_exit(self) -> bool:
        return any(p.name == "sybil_funder" and p.status is Status.FAIL for p in self.predicates)

    def to_dict(self) -> dict:
        return {"mint": self.mint, "signal_type": self.signal_type, "sources": list(self.sources),
                "observed_ns": self.observed_ns, "event_ns": self.event_ns, "clock": str(self.clock),
                "detected_ns": self.detected_ns,
                "freshness": {"feed_delay_ms": self.feed_delay_ns / 1e6, "processing_us": self.processing_ns / 1e3,
                              "detection_delay_ms": (self.feed_delay_ns + self.processing_ns) / 1e6},
                "payload": {"window": [b.to_dict() for b in self.window], "fee_payers": list(self.fee_payers),
                            "creator": self.creator, "creator_in_window": self.creator_in_window,
                            "reserves": dict(self.reserves)},
                "predicates": [p.to_dict() for p in self.predicates], "blocking": list(self.blocking),
                "superseded": SUPERSEDED,
                "trade_permitted": self.trade_permitted, "emergency_exit": self.emergency_exit}


def _sol(lamports: int | None) -> float | None:
    return None if lamports is None else lamports / LAMPORTS_PER_SOL


def first_window(buys: list[Buy]) -> tuple[Buy, ...] | None:
    """The buys within WINDOW_NS (inclusive) ending at the earliest buy by which REQUIRED_FEE_PAYERS distinct fee
    payers have bought. `buys` is sorted by event time."""
    counts: dict[str, int] = {}
    i = 0
    for j, b in enumerate(buys):
        counts[b.fee_payer] = counts.get(b.fee_payer, 0) + 1
        while b.event_ns - buys[i].event_ns > WINDOW_NS:
            p = buys[i].fee_payer
            counts[p] -= 1
            if not counts[p]:
                del counts[p]
            i += 1
        if len(counts) >= REQUIRED_FEE_PAYERS and j - i + 1 >= REQUIRED_BUYS:
            return tuple(buys[i:j + 1])
    return None


class _Mint:
    __slots__ = ("buys", "seen", "creator", "since_creation", "current", "peak_v", "peak_real", "newest_ns",
                 "window", "detection", "sybil", "create_sig", "creation_bound", "unverified", "migrated")

    def __init__(self) -> None:
        self.buys: list[Buy] = []
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.creator: str | None = None
        self.since_creation = False
        self.current: tuple[tuple[int, int], int, int | None] | None = None   # (key, v_sol, real_sol)
        self.peak_v: int | None = None
        self.peak_real: int | None = None
        self.newest_ns: int | None = None
        self.window: tuple[Buy, ...] | None = None
        self.detection: tuple[int, int, int] | None = None   # (observed_ns, detected_ns, processing_ns)
        self.sybil: Status | None = None
        self.create_sig: str | None = None
        self.creation_bound: int | None = None   # upper bound on real SOL right after an unverified creation
        self.unverified: list[str] = []          # signatures of curve readings without verified real SOL
        self.migrated = False


class EarlyMomentum:
    def __init__(self, notify: Callable[[EarlyMomentumEvidence], Any] | None = None,
                 request_funders: Callable[[str, tuple[str, ...]], Any] | None = None, clock_ns=time.time_ns,
                 max_mints: int = MAX_MINTS, idle_ttl_s: float = IDLE_TTL_S,
                 max_buys_per_mint: int = MAX_BUYS_PER_MINT, max_funding: int = MAX_FUNDING) -> None:
        self.notify, self.request_funders, self.clock_ns = notify, request_funders, clock_ns
        self.max_buys_per_mint, self.max_funding = max_buys_per_mint, max_funding
        self._mints: MintTable[_Mint] = MintTable(lambda _mint: _Mint(), max_mints, int(idle_ttl_s * 1_000_000_000),
                                                  on_evict=self._unlink)
        self._funding: OrderedDict[str, Funding] = OrderedDict()
        self._wallet_mints: dict[str, set[str]] = {}
        self.stats = {"events": 0, "curve_buys": 0, "detections": 0, "late_beyond_retention": 0,
                      "buys_dropped_over_cap": 0, "duplicates": 0, "listener_errors": 0}

    # ---- API -----------------------------------------------------------------------------------------
    def on_event(self, ev: MarketEvent) -> list[EarlyMomentumEvidence]:
        started = time.perf_counter_ns()
        self.stats["events"] += 1
        if ev.kind is EventKind.MIGRATION:
            st = self._mints.get(ev.mint)
            if st is not None:
                st.migrated = True
            return []
        if ev.kind not in (EventKind.TOKEN_CREATED, EventKind.TRADE):
            return []
        st = self._mints.touch(ev.mint, ev.recv_ns)
        if ev.kind is EventKind.TOKEN_CREATED:
            st.creator, st.since_creation, st.create_sig = ev.trader, True, ev.signature
        if ev.kind is EventKind.TRADE and not is_curve(ev) and ev.raw.get("pool") not in (None, "pump"):
            st.migrated = True                                       # trading on the migrated pool
        if is_curve(ev):
            self._reserves(st, ev)
        if ev.kind is EventKind.TRADE and ev.side is Side.BUY and is_curve(ev):
            return self._buy(ev.mint, st, ev, started)
        return []

    def record_funding(self, f: Funding) -> list[EarlyMomentumEvidence]:
        """A funding observation for a wallet. Detected mints whose sybil check changes are pushed at once."""
        self._funding[f.wallet] = f
        self._funding.move_to_end(f.wallet)
        while len(self._funding) > self.max_funding:
            self._funding.popitem(last=False)
        out = []
        for mint in sorted(self._wallet_mints.get(f.wallet, ())):
            st = self._mints.get(mint)
            if st is None or st.window is None:
                continue
            evidence = self._build(mint, st)
            status = self._sybil_status(evidence)
            if status is not st.sybil:
                st.sybil = status
                out.append(evidence)
                self._call(self.notify, evidence)
        return out

    def evidence(self, mint: str) -> EarlyMomentumEvidence | None:
        st = self._mints.get(mint)
        return self._build(mint, st) if st is not None and st.window is not None else None

    def tracked_mints(self) -> int:
        return len(self._mints)

    def buys_held(self, mint: str) -> int:
        st = self._mints.get(mint)
        return len(st.buys) if st else 0

    # ---- internals -----------------------------------------------------------------------------------
    def _reserves(self, st: _Mint, ev: MarketEvent) -> None:
        t, _ = event_time(ev)
        key = (t, ev.seq)
        real = verified_real_sol(ev)
        st.peak_v = max(st.peak_v or 0, ev.v_sol_lamports)
        if real is not None:
            st.peak_real = max(st.peak_real or 0, real)              # never lowered by a later, lower reading
        elif st.since_creation and ev.signature == st.create_sig:
            # The creation transaction reported without a real reserve (PumpPortal's copy won the cross-feed dedup):
            # the curve starts empty, so real SOL right after creation is at most what the creator put in.
            st.creation_bound = max(st.creation_bound or 0, ev.sol_lamports or 0)
        else:
            st.unverified.append(ev.signature)
            del st.unverified[:-8]
        if st.current is None or key > st.current[0]:
            st.current = (key, ev.v_sol_lamports, real)

    def _buy(self, mint: str, st: _Mint, ev: MarketEvent, started: int) -> list[EarlyMomentumEvidence]:
        key = ev.idempotency_key
        if key in st.seen:
            self.stats["duplicates"] += 1
            return []
        st.seen[key] = None
        while len(st.seen) > 2 * self.max_buys_per_mint:
            st.seen.popitem(last=False)
        self.stats["curve_buys"] += 1
        t, clock = event_time(ev)
        real = verified_real_sol(ev)
        b = Buy(ev.signature, ev.trader, t, clock, ev.recv_ns, ev.seq, ev.slot, ev.sol_lamports, ev.v_sol_lamports,
                real, ev.source, traders_involved(ev))
        if st.newest_ns is not None and t < st.newest_ns - RETAIN_NS:
            self.stats["late_beyond_retention"] += 1
            return []
        bisect.insort(st.buys, b, key=lambda x: x.key)
        st.newest_ns = max(st.newest_ns or t, t)
        horizon = st.newest_ns - RETAIN_NS
        while st.buys and st.buys[0].event_ns < horizon:
            st.buys.pop(0)
        while len(st.buys) > self.max_buys_per_mint:
            st.buys.pop(0)
            self.stats["buys_dropped_over_cap"] += 1
        if st.window is not None:
            if b.key < st.window[-1].key:   # a late buy moves the window earlier or joins it, never later
                held = sorted(set(st.buys) | set(st.window), key=lambda x: x.key)
                window = first_window(held)
                if window is not None and window[-1].key <= st.window[-1].key and window != st.window:
                    known = {b.fee_payer for b in st.window}
                    self._set_window(mint, st, window)
                    added = tuple(dict.fromkeys(b.fee_payer for b in window if b.fee_payer not in known))
                    st.sybil = self._sybil_status(self._build(mint, st))
                    if added:
                        self._call(self.request_funders, mint, added)
            return []
        window = first_window(st.buys)
        if window is None:
            return []
        self._set_window(mint, st, window)
        st.detection = (ev.recv_ns, self.clock_ns(), time.perf_counter_ns() - started)
        self.stats["detections"] += 1
        evidence = self._build(mint, st)
        st.sybil = self._sybil_status(evidence)
        self._call(self.notify, evidence)
        self._call(self.request_funders, mint, evidence.fee_payers)
        return [evidence]

    def _set_window(self, mint: str, st: _Mint, window: tuple[Buy, ...]) -> None:
        self._unlink(mint, st)
        st.window = window
        for b in window:
            self._wallet_mints.setdefault(b.fee_payer, set()).add(mint)

    def _unlink(self, mint: str, st: _Mint) -> None:
        for b in st.window or ():
            mints = self._wallet_mints.get(b.fee_payer)
            if mints is not None:
                mints.discard(mint)
                if not mints:
                    del self._wallet_mints[b.fee_payer]

    def _call(self, fn, *args) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            self.stats["listener_errors"] += 1
            log.exception("early momentum listener failed")

    @staticmethod
    def _sybil_status(evidence: EarlyMomentumEvidence) -> Status:
        return next(p.status for p in evidence.predicates if p.name == "sybil_funder")

    def _build(self, mint: str, st: _Mint) -> EarlyMomentumEvidence:
        window = st.window
        payers = tuple(dict.fromkeys(b.fee_payer for b in window))
        trigger = window[-1]
        cur_v = st.current[1] if st.current else None
        cur_real = st.current[2] if st.current else None
        reserves = {
            "at_trigger_virtual_sol": _sol(trigger.v_sol_lamports), "at_trigger_real_sol": _sol(trigger.real_sol_lamports),
            "current_virtual_sol": _sol(cur_v), "current_real_sol": _sol(cur_real),
            "peak_virtual_sol": _sol(st.peak_v), "peak_real_sol": _sol(st.peak_real),
            "retrace_from_peak": (st.peak_v - cur_v) / st.peak_v if st.peak_v and cur_v is not None else None,
            "retrace_from_peak_real": ((st.peak_real - cur_real) / st.peak_real
                                       if st.peak_real and cur_real is not None else None),
            "observed_since_creation": st.since_creation,
        }
        observed_ns, detected_ns, processing_ns = st.detection
        return EarlyMomentumEvidence(
            mint=mint, window=window, fee_payers=payers, creator=st.creator,
            creator_in_window=(st.creator in payers) if st.creator is not None else None, reserves=reserves,
            predicates=(self._window_predicate(window, payers), self._reserve_predicate(trigger),
                        self._current_gate(st), self._peak_gate(st), self._sybil(payers)),
            observed_ns=observed_ns, detected_ns=detected_ns, processing_ns=processing_ns)

    # ---- predicates ----------------------------------------------------------------------------------
    @staticmethod
    def _window_predicate(window, payers) -> Predicate:
        return Predicate("three_buys_three_fee_payers_30s", Status.PASS, CHAD["window"],
                         {"buys": len(window), "fee_payers": len(payers),
                          "span_s": (window[-1].event_ns - window[0].event_ns) / 1e9,
                          "clocks": sorted({str(b.clock) for b in window})})

    @staticmethod
    def _reserve_predicate(trigger: Buy) -> Predicate:
        detail = {"virtual_sol": _sol(trigger.v_sol_lamports), "real_sol": _sol(trigger.real_sol_lamports),
                  "source": trigger.source, "signature": trigger.signature}
        status = Status.PASS if trigger.real_sol_lamports is not None else Status.UNAVAILABLE
        return Predicate("sol_reserve_read", status, CHAD["reserve"], detail)

    @staticmethod
    def _current_gate(st: _Mint) -> Predicate:
        """B-02: 0.5 <= current verified real SOL <= 10 (inclusive), read from the newest curve reading."""
        cur_v = st.current[1] if st.current else None
        real = st.current[2] if st.current else None
        detail = {"current_real_sol": _sol(real), "current_virtual_sol": _sol(cur_v), "rule": f"0.5 <= real SOL <= 10 ({B02})",
                  "reading_event_ns": st.current[0][0] if st.current else None,
                  "standard_curve": standard_curve(cur_v, real)}
        if st.migrated:
            return Predicate(CURRENT_GATE, Status.FAIL, CHAD["gate_low"],
                             {**detail, "reason": "migrated: this pre-graduation path does not apply"}, blocker="B-02")
        if real is None:
            return Predicate(CURRENT_GATE, Status.UNAVAILABLE, CHAD["gate_low"],
                             {**detail, "reason": "no verified real SOL in the newest reading"}, blocker="B-02")
        ok = FLOOR_LAMPORTS <= real <= CEILING_LAMPORTS
        return Predicate(CURRENT_GATE, Status.PASS if ok else Status.FAIL, CHAD["gate_low"], detail, blocker="B-02")

    @staticmethod
    def _peak_gate(st: _Mint) -> Predicate:
        """B-02: the highest verified real SOL since creation <= 10. Unprovable history blocks."""
        detail = {"peak_real_sol": _sol(st.peak_real), "peak_virtual_sol": _sol(st.peak_v),
                  "observed_since_creation": st.since_creation, "creation_bound_real_sol": _sol(st.creation_bound),
                  "unverified_readings": list(st.unverified), "rule": f"peak real SOL <= 10 ({B02})"}
        reason = None
        if st.migrated:
            status, reason = Status.FAIL, "migrated: this pre-graduation path does not apply"
        elif st.peak_real is not None and st.peak_real > CEILING_LAMPORTS:
            status, reason = Status.FAIL, "verified real SOL already exceeded 10"
        elif not st.since_creation:
            status, reason = Status.UNAVAILABLE, "history incomplete: creation not observed"
        elif st.unverified:
            status, reason = Status.UNAVAILABLE, "history incomplete: unverified curve readings since creation"
        elif st.creation_bound is not None and st.creation_bound > CEILING_LAMPORTS:
            status, reason = Status.UNAVAILABLE, "creation reading unverified and the creator's buy exceeds 10 SOL"
        elif st.peak_real is None:
            status, reason = Status.UNAVAILABLE, "no verified real SOL reading"
        else:
            status = Status.PASS
        return Predicate(PEAK_GATE, status, CHAD["peak"], {**detail, "reason": reason}, blocker="B-02")

    def reserve_check(self, mint: str, feed_live: bool) -> dict:
        """Both reserve gates on the current state, for the entry decision itself (B-02 §5). `feed_live` is the
        trade feed's own health (connected and not stalled): without it the newest reading may not be current."""
        st = self._mints.get(mint)
        if st is None:
            return {"eligible": False, "reason": "no reserve history for this mint", "current": None, "peak": None}
        cur, peak = self._current_gate(st), self._peak_gate(st)
        out = {"current": cur.to_dict(), "peak": peak.to_dict()}
        if st.migrated:
            return {**out, "eligible": False, "reason": "migrated: outside the Early Momentum reserve path"}
        if not feed_live:
            return {**out, "eligible": False, "reason": "stale: trade feed not live, reserves cannot be confirmed"}
        eligible = cur.status is Status.PASS and peak.status is Status.PASS
        return {**out, "eligible": eligible,
                "reason": "ok" if eligible else "; ".join(f"{p.name}: {p.status}" for p in (cur, peak)
                                                           if p.status is not Status.PASS)}

    def _sybil(self, payers: tuple[str, ...]) -> Predicate:
        known = {w: self._funding[w] for w in payers if w in self._funding}
        groups: dict[str, list[str]] = {}
        for w, f in known.items():
            groups.setdefault(f.funder, []).append(w)
        shared = {funder: ws for funder, ws in groups.items() if len(ws) >= SHARED_FUNDER_WALLETS}
        unknown = [w for w in payers if w not in known]
        detail = {"shared": shared, "unknown": unknown,
                  "funders": {w: {"funder": f.funder, "source": f.source, "observed_ns": f.observed_ns}
                              for w, f in known.items()}}
        if shared:
            status = Status.FAIL
        elif unknown:
            status = Status.UNAVAILABLE
        else:
            status = Status.PASS
        return Predicate("sybil_funder", status, CHAD["sybil"], detail)
