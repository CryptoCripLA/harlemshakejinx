"""Per-token confluence state (CHAD C12, "8-pillar board", ConfluenceRadar "10 signal badges per token with
per-signal staleness").

The board is the one place the decision reads every module's evidence for a token: each component carries its
source, observation time, event time, payload, an optional value, and whether it blocks. It records; it does not
invent. CHAD gives no weights, no global score threshold and no freshness-decay function (B-03), so without an
explicitly labelled `ConfluenceWeights` there is no score at all, and a score is never built from a partial set of
components. RAM is bounded (owner storage rule 2): at most `max_tokens` tokens, oldest first out, except tokens held
in a position, which are never evicted.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

TEST_FIXTURE_PREFIX = "TEST_FIXTURE"


@dataclass(frozen=True)
class Component:
    name: str
    value: Decimal | None
    source: str
    observed_ns: int
    event_ns: int | None
    blocking: bool = False
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ConfluenceWeights:
    """Points per component (a component's value is in [0, 1]). Only the owner sets these; tests label theirs."""
    label: str
    weights: dict[str, Decimal]

    def __post_init__(self) -> None:
        if self.label != "OWNER_CONFIG" and not self.label.startswith(TEST_FIXTURE_PREFIX):
            raise ValueError("confluence weights must be OWNER_CONFIG or a labelled TEST_FIXTURE")
        if not self.weights or any(not isinstance(w, Decimal) or w < 0 for w in self.weights.values()):
            raise ValueError("weights must be non-negative Decimals")


class ConfluenceBoard:
    def __init__(self, weights: ConfluenceWeights | None = None, max_tokens: int = 2_000) -> None:
        self.weights = weights
        self.max_tokens = max_tokens
        self._tokens: OrderedDict[str, dict[str, Component]] = OrderedDict()
        self._held: set[str] = set()

    def __len__(self) -> int:
        return len(self._tokens)

    def hold(self, mints: Iterable[str]) -> None:
        """Tokens in open positions: their state is kept whatever the cap."""
        self._held = set(mints)

    def record(self, mint: str, name: str, *, value: Decimal | None, source: str, observed_ns: int,
               event_ns: int | None, blocking: bool = False, payload: dict[str, Any] | None = None) -> None:
        comps = self._tokens.setdefault(mint, {})
        comps[name] = Component(name, value, source, observed_ns, event_ns, blocking, dict(payload or {}))
        self._tokens.move_to_end(mint)
        while len(self._tokens) > self.max_tokens:
            victim = next((m for m in self._tokens if m not in self._held), None)
            if victim is None:
                break
            del self._tokens[victim]

    def record_safety(self, mint: str, *, verdict: str, veto: list[str], observed_ns: int, source: str) -> None:
        self.record(mint, "safety", value=None, source=source, observed_ns=observed_ns, event_ns=None,
                    blocking=verdict != "PASS", payload={"verdict": verdict, "veto": list(veto)})

    def components(self, mint: str) -> dict[str, Component]:
        return dict(self._tokens.get(mint, {}))

    def hard_veto(self, mint: str) -> list[str] | None:
        """CHAD's hard safety veto: a FAIL verdict blocks the token permanently."""
        s = self._tokens.get(mint, {}).get("safety")
        return list(s.payload.get("veto") or ["FAIL"]) if s and s.payload.get("verdict") == "FAIL" else None

    def score(self, mint: str) -> Decimal | None:
        if self.weights is None:
            return None                                     # B-03: CHAD gives no weights
        comps = self._tokens.get(mint, {})
        total = Decimal(0)
        for name, w in self.weights.weights.items():
            c = comps.get(name)
            if c is None or c.value is None:
                return None                                 # never a score from a partial set
            total += w * c.value
        return total

    def badges(self, mint: str, now_ns: int) -> list[dict]:
        """Operator view: every component with its age (staleness) and blocking status."""
        return [{"signal": c.name, "source": c.source, "age_s": round((now_ns - c.observed_ns) / 1e9, 1),
                 "event_ns": c.event_ns, "blocking": c.blocking,
                 "value": None if c.value is None else str(c.value)} for c in self._tokens.get(mint, {}).values()]
