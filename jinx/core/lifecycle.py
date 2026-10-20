"""Token lifecycle interface (owner mandate 2026-10-20, section 3): one state machine per mint, from launch
through bonding-curve graduation and migration to the post-migration market.

Phase 1 defines the interface and its transition rules. Phase 2 translates provider data into Evidence:
GMGN Trenches categories (new_creation / near_completion / completed), pool and curve fields, and the
Pump.fun event stream. The rules below are about evidence handling, not trading:
- every transition records its source, observation time and event time;
- older evidence never moves the state backwards; a repeated report produces no second transition;
- migration is confirmed only with post-migration pool identity, never inferred from price or from a token
  missing in a query result (there is deliberately no evidence kind for either);
- newer evidence that contradicts an observed migration is flagged CONFLICTING, not silently applied;
- STALE is declared by the caller with its own age limit; failures are recorded without changing state.
"""
from __future__ import annotations

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
    STALE = 100        # exceptional: no evidence within the caller's age limit
    CONFLICTING = 101  # exceptional: newer evidence contradicts an observed migration


EXCEPTIONAL = (State.STALE, State.CONFLICTING)


class Kind(Enum):
    CREATED = State.NEWLY_DETECTED
    ON_CURVE = State.ON_BONDING_CURVE
    NEAR_COMPLETION = State.APPROACHING_GRADUATION
    MIGRATION_SIGNAL = State.MIGRATION_OBSERVED
    MIGRATION_CONFIRMED = State.MIGRATION_CONFIRMED
    POST_MIGRATION_MARKET = State.POST_MIGRATION_MARKET


class MissingEvidence(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    kind: Kind
    source: str
    observed_ns: int                  # when JINX received it
    event_ns: int                     # when the provider says it happened (= observed_ns if not supplied)
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Transition:
    from_state: State
    to_state: State
    kind: Kind | None
    source: str
    observed_ns: int
    event_ns: int


@dataclass(frozen=True)
class Failure:
    source: str
    observed_ns: int
    reason: str


class TokenLifecycle:
    def __init__(self, mint: str) -> None:
        self.mint = mint
        self.state = State.UNKNOWN
        self.last_consistent_state = State.UNKNOWN
        self.history: list[Transition] = []
        self.failures: list[Failure] = []
        self.peak_v_sol_lamports: int | None = None
        self.pre_pool: str | None = None
        self.post_pool: str | None = None
        self._latest_event_ns: int | None = None
        self._last_observed_ns: int | None = None

    def observe(self, e: Evidence) -> Transition | None:
        if e.kind is Kind.MIGRATION_CONFIRMED and not e.payload.get("post_pool"):
            raise MissingEvidence("migration is confirmed only with the post-migration pool identity")
        v_sol = e.payload.get("v_sol_lamports")
        if v_sol is not None:
            self.peak_v_sol_lamports = max(self.peak_v_sol_lamports or 0, v_sol)
        if e.payload.get("pre_pool"):
            self.pre_pool = e.payload["pre_pool"]
        if e.payload.get("post_pool"):
            self.post_pool = e.payload["post_pool"]
        self._last_observed_ns = max(self._last_observed_ns or 0, e.observed_ns)
        if self._latest_event_ns is not None and e.event_ns < self._latest_event_ns:
            return None  # a late report of an earlier fact never moves the state
        self._latest_event_ns = e.event_ns

        target = e.kind.value
        base = self.last_consistent_state
        migrated = base.value >= State.MIGRATION_OBSERVED.value
        if migrated and target.value < State.MIGRATION_OBSERVED.value:
            return self._move(State.CONFLICTING, e)
        if target is base:
            return self._move(target, e) if self.state in EXCEPTIONAL else None
        if target.value > base.value or not migrated:
            # forward progress, or curve progress moving between pre-migration states
            return self._move(target, e)
        return None

    def mark_stale_if_older_than(self, now_ns: int, max_age_ns: int) -> Transition | None:
        if self.state is State.STALE or self._last_observed_ns is None:
            return None
        if now_ns - self._last_observed_ns <= max_age_ns:
            return None
        t = Transition(self.state, State.STALE, None, "lifecycle", now_ns, now_ns)
        self.history.append(t)
        self.state = State.STALE
        return t

    def record_failure(self, source: str, observed_ns: int, reason: str) -> None:
        self.failures.append(Failure(source, observed_ns, reason))

    def _move(self, to: State, e: Evidence) -> Transition:
        t = Transition(self.state, to, e.kind, e.source, e.observed_ns, e.event_ns)
        self.history.append(t)
        self.state = to
        if to not in EXCEPTIONAL:
            self.last_consistent_state = to
        return t
