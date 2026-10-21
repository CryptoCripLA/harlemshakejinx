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

What it does not do: place or permit orders, choose between B-02's two curve gates, or define what retrace the peak
gate rejects (B-03). Every predicate that is UNRESOLVED or UNAVAILABLE blocks: `trade_permitted` stays False.
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
from jinx.intel.readings import Clock, event_time, is_curve, real_reserves, traders_involved

log = logging.getLogger(__name__)

SIGNAL_TYPE = "early_momentum"
WINDOW_NS = 30 * 1_000_000_000      # CHAD "30-second window"
REQUIRED_BUYS = 3                   # CHAD "3 buys"
REQUIRED_FEE_PAYERS = 3             # CHAD "3 distinct fee payers required"
SHARED_FUNDER_WALLETS = 3           # CHAD "If 3+ wallets share the same SOL funder"
GATE_FLOOR_SOL, GATE_CEILING_SOL = 0.5, 10.0   # CHAD "0.5–10 SOL bonding curve gate"
GATE_BELOW_SOL = 35.0                          # CHAD "all below the 35 SOL bonding curve threshold"
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
                 "last_recv", "window", "detection", "sybil")

    def __init__(self) -> None:
        self.buys: list[Buy] = []
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.creator: str | None = None
        self.since_creation = False
        self.current: tuple[tuple[int, int], int, int | None] | None = None   # (key, v_sol, real_sol)
        self.peak_v: int | None = None
        self.peak_real: int | None = None
        self.newest_ns: int | None = None
        self.last_recv = 0
        self.window: tuple[Buy, ...] | None = None
        self.detection: tuple[int, int, int] | None = None   # (observed_ns, detected_ns, processing_ns)
        self.sybil: Status | None = None


class EarlyMomentum:
    def __init__(self, notify: Callable[[EarlyMomentumEvidence], Any] | None = None,
                 request_funders: Callable[[str, tuple[str, ...]], Any] | None = None, clock_ns=time.time_ns,
                 max_mints: int = MAX_MINTS, idle_ttl_s: float = IDLE_TTL_S,
                 max_buys_per_mint: int = MAX_BUYS_PER_MINT, max_funding: int = MAX_FUNDING) -> None:
        self.notify, self.request_funders, self.clock_ns = notify, request_funders, clock_ns
        self.max_mints, self.idle_ttl_ns = max_mints, int(idle_ttl_s * 1_000_000_000)
        self.max_buys_per_mint, self.max_funding = max_buys_per_mint, max_funding
        self._mints: OrderedDict[str, _Mint] = OrderedDict()
        self._funding: OrderedDict[str, Funding] = OrderedDict()
        self._wallet_mints: dict[str, set[str]] = {}
        self._now = 0
        self.stats = {"events": 0, "curve_buys": 0, "detections": 0, "late_beyond_retention": 0,
                      "buys_dropped_over_cap": 0, "duplicates": 0, "listener_errors": 0, "evicted": 0}

    # ---- API -----------------------------------------------------------------------------------------
    def on_event(self, ev: MarketEvent) -> list[EarlyMomentumEvidence]:
        started = time.perf_counter_ns()
        self.stats["events"] += 1
        if ev.kind not in (EventKind.TOKEN_CREATED, EventKind.TRADE):
            return []
        self._now = max(self._now, ev.recv_ns)
        st = self._touch(ev.mint)
        if ev.kind is EventKind.TOKEN_CREATED:
            st.creator, st.since_creation = ev.trader, True
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
    def _touch(self, mint: str) -> _Mint:
        st = self._mints.get(mint)
        if st is None:
            st = self._mints[mint] = _Mint()
        self._mints.move_to_end(mint)
        st.last_recv = self._now
        while len(self._mints) > self.max_mints:
            self._evict(*self._mints.popitem(last=False))
        while self._mints:
            oldest_mint, oldest = next(iter(self._mints.items()))
            if self._now - oldest.last_recv <= self.idle_ttl_ns:
                break
            del self._mints[oldest_mint]
            self._evict(oldest_mint, oldest)
        return st

    def _evict(self, mint: str, st: _Mint) -> None:
        self.stats["evicted"] += 1
        self._unlink(mint, st)

    def _reserves(self, st: _Mint, ev: MarketEvent) -> None:
        t, _ = event_time(ev)
        key = (t, ev.seq)
        real, _ = real_reserves(ev)
        st.peak_v = max(st.peak_v or 0, ev.v_sol_lamports)
        if real is not None:
            st.peak_real = max(st.peak_real or 0, real)
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
        real, _ = real_reserves(ev)
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
                    self._set_window(mint, st, window)
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
            "observed_since_creation": st.since_creation,
        }
        observed_ns, detected_ns, processing_ns = st.detection
        return EarlyMomentumEvidence(
            mint=mint, window=window, fee_payers=payers, creator=st.creator,
            creator_in_window=(st.creator in payers) if st.creator is not None else None, reserves=reserves,
            predicates=(self._window_predicate(window, payers), self._reserve_predicate(trigger),
                        self._gate_low(trigger), self._gate_high(window), self._peak(st, reserves),
                        self._sybil(payers)),
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
    def _gate_low(trigger: Buy) -> Predicate:
        v, r = _sol(trigger.v_sol_lamports), _sol(trigger.real_sol_lamports)
        inside = (lambda x: None if x is None else GATE_FLOOR_SOL <= x <= GATE_CEILING_SOL)
        detail = {"virtual_sol_at_trigger": v, "real_sol_at_trigger": r, "if_virtual_sol": inside(v),
                  "if_real_sol": inside(r)}
        status = Status.UNRESOLVED if r is not None else Status.UNAVAILABLE
        return Predicate("curve_gate_0_5_to_10_sol", status, CHAD["gate_low"], detail, blocker="B-02")

    @staticmethod
    def _gate_high(window) -> Predicate:
        virtual = [_sol(b.v_sol_lamports) for b in window]
        real = [_sol(b.real_sol_lamports) for b in window]
        below = (lambda xs: None if any(x is None for x in xs) else all(x < GATE_BELOW_SOL for x in xs))
        detail = {"virtual_sol_per_buy": virtual, "real_sol_per_buy": real, "if_virtual_sol": below(virtual),
                  "if_real_sol": below(real)}
        status = Status.UNRESOLVED if None not in real else Status.UNAVAILABLE
        return Predicate("curve_gate_below_35_sol", status, CHAD["gate_high"], detail, blocker="B-02")

    @staticmethod
    def _peak(st: _Mint, reserves: Mapping[str, Any]) -> Predicate:
        detail = {k: reserves[k] for k in ("peak_virtual_sol", "peak_real_sol", "current_virtual_sol",
                                           "current_real_sol", "retrace_from_peak", "observed_since_creation")}
        status = Status.UNRESOLVED if st.since_creation else Status.UNAVAILABLE
        return Predicate("peak_sol_gate", status, CHAD["peak"], detail, blocker="B-02, B-03")

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
