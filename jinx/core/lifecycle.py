"""Token lifecycle interface (owner mandate 2026-10-20, section 3): one state machine per mint, from launch
through bonding-curve graduation and migration to the post-migration market.

Phase 1 defines the interface and its rules; Phase 2 translates provider data into Evidence (GMGN Trenches and
token info, the Pump.fun stream; mapping in docs/GMGN_ADAPTER_CONTRACT.md §5). The rules are about evidence
handling, not trading:
- Every transition keeps its source, observation time, event time and evidence payload. The mandated lifecycle
  record (§3.1) is kept in `fields`, each value with the source and times of the evidence that set it. A migration
  first seen on JINX's receipt clock takes the provider's time when a provider report of it arrives, never the
  reverse (as for creation times).
- Ordering is per fact, because reports carry different clocks: curve reads carry the read (or trade) time,
  migration reports carry the provider's migration time. A report is late when it is older than the latest
  accepted report of the same kind, when it is curve data no newer than the observed migration, or when it would
  move the curve stage backwards with older evidence than the one that set it. A late report never moves the
  state, never replaces a recorded value and never refreshes freshness; it still counts toward the reserve peak,
  because a real past reading is part of the all-time high. How recorded values are replaced is defined per kind
  of value (see CURRENT_READINGS below), never by comparing times on different clocks.
- A repeated report makes no second transition.
- Migration is confirmed only with the destination pool, the destination venue and the provider's migration
  time. Post-migration market data is accepted only after that confirmation. There is deliberately no evidence
  kind for price or for a token missing from a query result.
- Evidence newer than the observed migration that contradicts it (curve data, another pool) moves the token to
  CONFLICTING once; every contradicting report is kept in `conflicts`.
- A creation report is a fact about the past. It is merged into the record (creation time, launchpad,
  pre-migration pool), never ordered against the current stage and never a contradiction. While the token is
  still at its creation stage, a successful creation observation is current evidence: it refreshes freshness
  and clears STALE or FAILED. At a later stage it only fills in history. A creation time on the provider's clock replaces one taken from JINX's receipt
  time (a feed without chain time), never the reverse.
- STALE (caller's age limit) and FAILED (a failed observation) are exceptional states. The last consistent
  state is kept, and the next accepted consistent evidence returns the token to it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class State(Enum):
    UNKNOWN = 0
    NEWLY_DETECTED = 1
    ON_BONDING_CURVE = 2
    APPROACHING_GRADUATION = 3
    MIGRATION_OBSERVED = 4
    MIGRATION_CONFIRMED = 5
    POST_MIGRATION_MARKET = 6
    STALE = 100        # exceptional: no accepted evidence within the caller's age limit
    FAILED = 101       # exceptional: the latest observation attempt failed
    CONFLICTING = 102  # exceptional: newer evidence contradicts the observed migration


EXCEPTIONAL = (State.STALE, State.FAILED, State.CONFLICTING)
# RAM bounds per token (owner storage rule 2): the newest entries are kept and the totals counted, so an active token
# cannot grow without limit (Codex Phase 2 round 1, F3)
MAX_HISTORY = MAX_CONFLICTS = MAX_FAILURES = 64


class Kind(Enum):
    CREATED = State.NEWLY_DETECTED
    ON_CURVE = State.ON_BONDING_CURVE
    NEAR_COMPLETION = State.APPROACHING_GRADUATION
    MIGRATION_SIGNAL = State.MIGRATION_OBSERVED
    MIGRATION_CONFIRMED = State.MIGRATION_CONFIRMED
    POST_MIGRATION_MARKET = State.POST_MIGRATION_MARKET


class MissingEvidence(ValueError):
    pass


CLOCKS = ("provider", "receipt")


@dataclass(frozen=True)
class Evidence:
    kind: Kind
    source: str
    observed_ns: int                  # when JINX received it
    event_ns: int                     # when the provider says it happened (= observed_ns if not supplied)
    clock: str = "provider"           # "provider": event_ns is the provider's time; "receipt": JINX's receipt time
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.clock not in CLOCKS:
            raise ValueError(f"evidence clock must be one of {CLOCKS}, got {self.clock!r}")


@dataclass(frozen=True)
class Transition:
    from_state: State
    to_state: State
    kind: Kind | None
    source: str
    observed_ns: int
    event_ns: int
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Failure:
    source: str
    observed_ns: int
    reason: str


@dataclass(frozen=True)
class Recorded:
    """One value of the lifecycle record and the evidence that set it."""
    value: Any
    source: str
    observed_ns: int
    event_ns: int
    clock: str = "provider"


# The record (mandate §3.1) holds three kinds of values, each with its own replacement rule, so no value is
# ever chosen by comparing times taken on different clocks:
# - creation facts (`created`, and `launchpad`/`pre_pool` from a creation report): the first value is kept,
#   except that a value on the provider's clock replaces one on JINX's receipt clock;
# - migration facts (`post_pool`, `exchange`, `first_migration_signal`): set once; another pool is a conflict;
# - current readings (below, plus valid `price` and `liquidity`): the latest accepted report wins. Late reports
#   are never accepted, so arrival order among accepted reports is the order of the readings.
CURRENT_READINGS = ("launchpad", "category", "progress", "pre_pool")
CREATION_FACTS = ("launchpad", "pre_pool")


def _text(v) -> bool:
    return isinstance(v, str) and v != ""


def _number(v, minimum_exclusive: float | None = None, minimum: float | None = None) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return False
    if minimum_exclusive is not None and v <= minimum_exclusive:
        return False
    return minimum is None or v >= minimum


class TokenLifecycle:
    def __init__(self, mint: str) -> None:
        self.mint = mint
        self.state = State.UNKNOWN
        self.last_consistent_state = State.UNKNOWN
        self.history: list[Transition] = []
        self.failures: list[Failure] = []
        self.conflicts: list[Evidence] = []
        self.fields: dict[str, Recorded] = {}
        self.peak_v_sol_lamports: int | None = None
        self.late_reports = 0
        self.history_total = self.conflicts_total = self.failures_total = 0
        self.last_received_ns: int | None = None     # any report, late ones included
        self.last_accepted_ns: int | None = None     # reports that were not late
        self._latest_by_kind: dict[Kind, int] = {}    # per-kind ordering watermark (event time)
        self._migration_event_ns: int | None = None   # earliest accepted migration fact time
        self._state_event_ns: int | None = None       # event time of the evidence that set the current stage

    @property
    def pre_pool(self) -> str | None:
        r = self.fields.get("pre_pool")
        return r.value if r else None

    @property
    def post_pool(self) -> str | None:
        r = self.fields.get("post_pool")
        return r.value if r else None

    def observe(self, e: Evidence) -> Transition | None:
        self._validate(e)
        self.last_received_ns = max(self.last_received_ns or 0, e.observed_ns)
        v_sol = e.payload.get("v_sol_lamports")
        if _number(v_sol, minimum=0):
            self.peak_v_sol_lamports = max(self.peak_v_sol_lamports or 0, v_sol)
        if e.kind is Kind.CREATED:
            return self._observe_creation(e)
        target = e.kind.value
        base = self.last_consistent_state
        migrated = base.value >= State.MIGRATION_OBSERVED.value
        if e.kind is Kind.MIGRATION_SIGNAL and self._provider_migration_time(e):
            return self._move(base, e) if self.state in EXCEPTIONAL else None
        if self._is_late(e, base, migrated):
            self.late_reports += 1
            return None
        self._latest_by_kind[e.kind] = max(self._latest_by_kind.get(e.kind, e.event_ns), e.event_ns)
        self.last_accepted_ns = max(self.last_accepted_ns or 0, e.observed_ns)
        if e.kind in (Kind.MIGRATION_SIGNAL, Kind.MIGRATION_CONFIRMED):
            self._migration_event_ns = min(self._migration_event_ns or e.event_ns, e.event_ns)
        if self._contradicts(e, migrated):
            return self._conflict(e)
        self._record(e)
        if target is base:
            return self._move(target, e) if self.state in EXCEPTIONAL else None
        if target.value > base.value or not migrated:
            # forward progress, or curve progress moving between pre-migration states
            return self._move(target, e)
        if self.state in EXCEPTIONAL:
            # fresh evidence consistent with the migration already observed: back to the last consistent state
            return self._move(base, e)
        return None

    def mark_stale_if_older_than(self, now_ns: int, max_age_ns: int) -> Transition | None:
        if self.state is State.STALE or self.last_accepted_ns is None:
            return None
        if now_ns - self.last_accepted_ns <= max_age_ns:
            return None
        return self._enter(State.STALE, None, "lifecycle", now_ns, now_ns)

    def record_failure(self, source: str, observed_ns: int, reason: str) -> Transition | None:
        self.failures.append(Failure(source, observed_ns, reason))
        self.failures_total += 1
        del self.failures[:-MAX_FAILURES]
        if self.state is State.FAILED:
            return None
        return self._enter(State.FAILED, None, source, observed_ns, observed_ns, {"reason": reason})

    def _observe_creation(self, e: Evidence) -> Transition | None:
        base = self.last_consistent_state
        self._record_creation(e)
        if base not in (State.UNKNOWN, State.NEWLY_DETECTED):
            return None   # a later stage: the report only fills in history; it never moves a token back
        # the token is (still) at its creation stage, so a successful creation observation is current evidence
        self._record_readings(e)
        self._latest_by_kind[e.kind] = max(self._latest_by_kind.get(e.kind, e.event_ns), e.event_ns)
        self.last_accepted_ns = max(self.last_accepted_ns or 0, e.observed_ns)
        if base is State.UNKNOWN or self.state in EXCEPTIONAL:
            return self._move(State.NEWLY_DETECTED, e)
        return None

    # ---- rules ---------------------------------------------------------------------------------------
    def _validate(self, e: Evidence) -> None:
        """Evidence that cannot support its kind is refused before anything is recorded."""
        if e.kind is Kind.MIGRATION_CONFIRMED:
            if not (_text(e.payload.get("post_pool")) and _text(e.payload.get("exchange")) and e.event_ns > 0
                    and e.clock == "provider"):
                raise MissingEvidence("a migration is confirmed only with the destination pool, the destination "
                                      "venue and the provider's migration time (clock='provider')")
        if e.kind is Kind.POST_MIGRATION_MARKET and not (
                self.last_consistent_state.value >= State.MIGRATION_CONFIRMED.value and self.post_pool):
            raise MissingEvidence("post-migration market data is accepted only after a confirmed migration")

    def _provider_migration_time(self, e: Evidence) -> bool:
        """The migration first seen on JINX's receipt clock, now reported on the provider's clock: the provider's
        time replaces the receipt-time approximation (the creation-time rule, applied to the migration fact). Never
        the reverse; no second transition."""
        first = self.fields.get("first_migration_signal")
        if e.clock != "provider" or first is None or first.clock != "receipt":
            return False
        self._put("first_migration_signal", e.event_ns, e)
        self._migration_event_ns = min(self._migration_event_ns or e.event_ns, e.event_ns)
        self.last_accepted_ns = max(self.last_accepted_ns or 0, e.observed_ns)
        self._record_readings(e)
        return True

    def _is_late(self, e: Evidence, base: State, migrated: bool) -> bool:
        latest = self._latest_by_kind.get(e.kind)
        if latest is not None and e.event_ns < latest:
            return True   # older than the latest accepted report of the same kind
        curve = e.kind in (Kind.ON_CURVE, Kind.NEAR_COMPLETION)
        if curve and migrated and self._migration_event_ns is not None and e.event_ns <= self._migration_event_ns:
            return True   # curve data from before the migration: history, not a contradiction
        if curve and not migrated and e.kind.value.value < base.value and self._state_event_ns is not None \
                and e.event_ns < self._state_event_ns:
            return True   # would move the curve stage backwards with older evidence
        return False

    def _contradicts(self, e: Evidence, migrated: bool) -> bool:
        if migrated and e.kind.value.value < State.MIGRATION_OBSERVED.value:
            return True   # curve-stage data after a migration was observed
        confirmed = self.post_pool
        if e.kind is Kind.MIGRATION_CONFIRMED and confirmed and e.payload["post_pool"] != confirmed:
            return True   # another destination pool than the confirmed one
        pool = e.payload.get("pool")
        return e.kind is Kind.POST_MIGRATION_MARKET and pool is not None and pool != confirmed

    def _conflict(self, e: Evidence) -> Transition | None:
        self.conflicts.append(e)
        self.conflicts_total += 1
        del self.conflicts[:-MAX_CONFLICTS]
        if self.state is State.CONFLICTING:
            return None   # one transition; every contradicting report is kept in `conflicts`
        return self._enter(State.CONFLICTING, e.kind, e.source, e.observed_ns, e.event_ns, e.payload)

    def _put(self, name: str, value, e: Evidence) -> None:
        self.fields[name] = Recorded(value, e.source, e.observed_ns, e.event_ns, e.clock)

    def _fact(self, name: str, value, e: Evidence) -> None:
        current = self.fields.get(name)
        if current is None or (current.clock == "receipt" and e.clock == "provider"):
            self._put(name, value, e)

    def _record_creation(self, e: Evidence) -> None:
        self._fact("created", e.event_ns, e)
        for name in CREATION_FACTS:
            if e.payload.get(name) is not None:
                self._fact(name, e.payload[name], e)

    def _record_readings(self, e: Evidence) -> None:
        names = CURRENT_READINGS if e.kind is not Kind.CREATED else tuple(
            n for n in CURRENT_READINGS if n not in CREATION_FACTS)   # creation facts follow the fact rule
        for name in names:
            if e.payload.get(name) is not None:
                self._put(name, e.payload[name], e)
        if _number(e.payload.get("price"), minimum_exclusive=0):
            self._put("price", e.payload["price"], e)
        if _number(e.payload.get("liquidity"), minimum=0):
            self._put("liquidity", e.payload["liquidity"], e)

    def _record(self, e: Evidence) -> None:
        """An accepted, non-creation report: current readings, then migration facts."""
        self._record_readings(e)
        if e.kind is Kind.MIGRATION_SIGNAL and "first_migration_signal" not in self.fields:
            self._put("first_migration_signal", e.event_ns, e)
        if e.kind is Kind.MIGRATION_CONFIRMED and "post_pool" not in self.fields:
            self._put("post_pool", e.payload["post_pool"], e)
            self._put("exchange", e.payload["exchange"], e)

    def _move(self, to: State, e: Evidence) -> Transition:
        t = self._enter(to, e.kind, e.source, e.observed_ns, e.event_ns, e.payload)
        self.last_consistent_state = to
        self._state_event_ns = e.event_ns
        return t

    def _enter(self, to: State, kind: Kind | None, source: str, observed_ns: int, event_ns: int,
               payload: Mapping[str, Any] | None = None) -> Transition:
        t = Transition(self.state, to, kind, source, observed_ns, event_ns, dict(payload or {}))
        self.history.append(t)
        self.history_total += 1
        del self.history[:-MAX_HISTORY]
        self.state = to
        return t
