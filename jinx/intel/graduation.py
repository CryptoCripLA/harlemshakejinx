"""CHAD Bot 5, Graduation Signals (C05): bonding-curve reserve and graduation-progress observations.

CHAD (docs/CHAD_BLUEPRINT.md, Bot 5): "Monitors every pump.fun bonding curve in real time via Helius gRPC. Scores
each token's progress toward graduation (0–20 pts) and feeds it into the Confluence board. Tokens near the threshold
get elevated signal weight."

Source: every curve trade on the PumpAPI stream carries the virtual reserves and the real ones (`quoteInPool`,
`tokensInPool`), with a median receipt lag of ~50 ms: the same data CHAD takes from Helius gRPC, free (B-07).
PumpPortal's curve events carry virtual reserves only, so their readings have no real reserves and no progress:
nothing is derived. Live finding (tests/test_intel_graduation.py): 26 of 118 curve mints in the capture are not
standard 30-SOL curves (virtual SOL as low as 0.014, a virtual-minus-real SOL gap that changes trade by trade), while
the token gap is D3AD's standard offset on every reading; 5 of them hold more than the standard 793.1M sellable
tokens. Each reading says whether its SOL gap is the standard 30 SOL (`standard_curve`). Progress is computed for
standard curves only; for the others it is None (their curve parameters are not known), never a reinterpretation.

Readings per curve event (both kept, each with its provenance):
- `progress`: share of the curve's sellable tokens already bought, 1 − real_tokens / INITIAL_REAL_TOKENS. The curve
  completes when its real token reserve is exhausted: every buy is capped at the real token reserve (D3AD
  pumpfun.rs:448). INITIAL_REAL_TOKENS is D3AD's recorded mainnet global-account value (pumpfun.rs:540); Phase 3
  reads the live global account (B-12).
- `progress_sol_mako`: Mako's reading, real SOL in the curve / 85 SOL, capped at 1 (mako src/onchain.py:546-550).

Score: CHAD fixes the range (0–20 points) but not the mapping from progress to points, what "near the threshold"
is, or the "elevated signal weight" (all B-03). No points are produced; the evidence blocks on `graduation_score`
until the owner supplies a mapping, whose output is accepted only inside 0–20. Ordering: the latest reading is the
newest by event time; a late reading counts toward the peaks but never replaces it. State is RAM only and bounded.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from jinx.core.events import LAMPORTS_PER_SOL, EventKind, MarketEvent
from jinx.intel.bounded import MintTable
from jinx.intel.readings import INITIAL_VIRTUAL_SOL, Clock, event_time, is_curve, real_reserves, standard_curve

SIGNAL_TYPE = "graduation"
SCORE_MIN, SCORE_MAX = 0.0, 20.0                    # CHAD "(0–20 pts)"
INITIAL_VIRTUAL_TOKENS = 1_073_000_000_000_000      # D3AD pumpfun.rs:539
INITIAL_REAL_TOKENS = 793_100_000_000_000           # D3AD pumpfun.rs:540
MAKO_GRADUATION_SOL = 85.0                          # Mako src/onchain.py:546 GRADUATION_SOL
CHAD_TEXT = ("Monitors every pump.fun bonding curve in real time via Helius gRPC. Scores each token's progress "
             "toward graduation (0–20 pts) and feeds it into the Confluence board. Tokens near the threshold get "
             "elevated signal weight.")
SCORE_UNRESOLVED = "not computed: CHAD gives the 0–20 range but not the progress-to-points mapping (B-03)"
NEAR_UNRESOLVED = "not computed: CHAD does not quantify \"near the threshold\" or the elevated weight (B-03)"
MAX_MINTS = 20_000
IDLE_TTL_S = 2 * 3600


@dataclass(frozen=True)
class CurveReading:
    signature: str
    source: str
    slot: int | None
    event_ns: int
    clock: Clock
    observed_ns: int
    seq: int
    v_sol_lamports: int
    v_tokens: int | None
    real_sol_lamports: int | None
    real_tokens: int | None
    reserves_basis: str              # "provider": real reserves reported by the feed; "virtual only": none reported
    progress: float | None           # 1 - real_tokens / INITIAL_REAL_TOKENS
    progress_sol_mako: float | None  # min(real SOL / 85 SOL, 1)
    complete: bool | None            # real token reserve exhausted
    standard_curve: bool | None      # virtual minus real SOL is the standard 30 SOL (D3AD pumpfun.rs:538)

    @property
    def key(self) -> tuple[int, int]:
        return (self.event_ns, self.seq)

    def to_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Clock) else v) for k, v in self.__dict__.items()}


def reading(ev: MarketEvent) -> CurveReading:
    t, clock = event_time(ev)
    real_sol, real_tokens = real_reserves(ev)
    basis = "provider" if real_sol is not None or real_tokens is not None else "virtual only"
    standard = standard_curve(ev.v_sol_lamports, real_sol)
    progress = mako = None
    if standard:   # the progress formulas assume the standard curve's parameters
        if real_tokens is not None and real_tokens <= INITIAL_REAL_TOKENS:
            progress = 1 - real_tokens / INITIAL_REAL_TOKENS
        mako = min(real_sol / (MAKO_GRADUATION_SOL * LAMPORTS_PER_SOL), 1.0)
    return CurveReading(ev.signature, ev.source, ev.slot, t, clock, ev.recv_ns, ev.seq, ev.v_sol_lamports, ev.v_tokens,
                        real_sol, real_tokens, basis, progress, mako,
                        None if real_tokens is None else real_tokens == 0, standard)


@dataclass(frozen=True)
class GraduationEvidence:
    mint: str
    latest: CurveReading
    peak_v_sol_lamports: int
    peak_progress: float | None
    readings: int
    late_readings: int
    observed_since_creation: bool
    complete_observed: bool
    score_points: float | None
    score_status: str
    near_threshold: bool | None = None
    near_threshold_status: str = NEAR_UNRESOLVED
    signal_type: str = SIGNAL_TYPE

    @property
    def event_ns(self) -> int:
        return self.latest.event_ns

    @property
    def observed_ns(self) -> int:
        return self.latest.observed_ns

    @property
    def blocking(self) -> tuple[str, ...]:
        return () if self.score_points is not None else ("graduation_score",)

    def to_dict(self) -> dict:
        return {"mint": self.mint, "signal_type": self.signal_type, "sources": [self.latest.source],
                "observed_ns": self.observed_ns, "event_ns": self.event_ns, "clock": str(self.latest.clock),
                "freshness": {"feed_delay_ms": (self.observed_ns - self.event_ns) / 1e6},
                "chad": CHAD_TEXT,
                "payload": {"latest": self.latest.to_dict(), "peak_v_sol_lamports": self.peak_v_sol_lamports,
                            "peak_progress": self.peak_progress, "readings": self.readings,
                            "late_readings": self.late_readings, "observed_since_creation": self.observed_since_creation,
                            "complete_observed": self.complete_observed},
                "score_points": self.score_points, "score_status": self.score_status,
                "near_threshold": self.near_threshold, "near_threshold_status": self.near_threshold_status,
                "blocking": list(self.blocking)}


class _Curve:
    __slots__ = ("latest", "peak_v", "peak_progress", "readings", "late", "since_creation", "complete")

    def __init__(self) -> None:
        self.latest: CurveReading | None = None
        self.peak_v = 0
        self.peak_progress: float | None = None
        self.readings = self.late = 0
        self.since_creation = self.complete = False


class GraduationTracker:
    def __init__(self, score_mapping: Callable[[float], float] | None = None, max_mints: int = MAX_MINTS,
                 idle_ttl_s: float = IDLE_TTL_S) -> None:
        self.score_mapping = score_mapping
        self._curves: MintTable[_Curve] = MintTable(lambda _mint: _Curve(), max_mints, int(idle_ttl_s * 1_000_000_000))
        self.stats = {"readings": 0, "late_readings": 0, "score_errors": 0}

    def on_event(self, ev: MarketEvent) -> list[GraduationEvidence]:
        if ev.kind not in (EventKind.TOKEN_CREATED, EventKind.TRADE) or not is_curve(ev):
            return []
        c = self._curves.touch(ev.mint, ev.recv_ns)
        if ev.kind is EventKind.TOKEN_CREATED:
            c.since_creation = True
        r = reading(ev)
        self.stats["readings"] += 1
        c.readings += 1
        c.peak_v = max(c.peak_v, r.v_sol_lamports)
        if r.progress is not None:
            c.peak_progress = max(c.peak_progress or 0.0, r.progress)
        c.complete = c.complete or bool(r.complete)
        if c.latest is None or r.key > c.latest.key:
            c.latest = r
        else:
            c.late += 1
            self.stats["late_readings"] += 1
        return [self._build(ev.mint, c)]

    def evidence(self, mint: str) -> GraduationEvidence | None:
        c = self._curves.get(mint)
        return self._build(mint, c) if c is not None and c.latest is not None else None

    def tracked_mints(self) -> int:
        return len(self._curves)

    def _score(self, progress: float | None) -> tuple[float | None, str]:
        if self.score_mapping is None:
            return None, SCORE_UNRESOLVED
        if progress is None:
            return None, "not computed: no progress reading"
        points = self.score_mapping(progress)
        if isinstance(points, bool) or not isinstance(points, (int, float)) or not math.isfinite(points) \
                or not SCORE_MIN <= points <= SCORE_MAX:
            self.stats["score_errors"] += 1
            return None, f"rejected: owner mapping returned {points!r}, outside 0–20"
        return float(points), "owner mapping"

    def _build(self, mint: str, c: _Curve) -> GraduationEvidence:
        points, status = self._score(c.latest.progress)
        return GraduationEvidence(mint, c.latest, c.peak_v, c.peak_progress, c.readings, c.late, c.since_creation,
                                  c.complete, points, status)
