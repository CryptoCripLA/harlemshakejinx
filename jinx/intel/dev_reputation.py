"""CHAD Dev Intel evidence (Phase 2: C08; R1 deployer history).

CHAD: "Scores the token deployer's full on-chain history — prior launch success rates, rug patterns, and wallet
connections. Runs on tokens already clearing the safety score threshold." The score formula, its cut-off and the
safety threshold it runs behind are not specified (B-03), so this module records decision-time-safe facts only.

Decision-time safety (owner order, no future leakage): only launches created strictly before the decision time are
counted. GMGN's lifetime totals (`inner_count`, `open_count`, `open_ratio`) include launches after the decision and
are kept separately, marked `decision_time_safe: False`. GMGN returns a page of recent launches, so when its lifetime
count exceeds the page the history is marked incomplete rather than extrapolated.
Source: GMGN `getCreatedTokens` (formats pinned by tests/fixtures/gmgn_security_sample_20261021.json).
"""
from __future__ import annotations

from dataclasses import dataclass

SCORE_STATUS = "not computed: CHAD's dev-reputation formula and cut-off are unspecified (B-03)"


@dataclass(frozen=True)
class DevEvidence:
    creator: str | None
    facts: dict
    observed_ns: int
    error: str | None = None
    score: int | None = None
    score_status: str = SCORE_STATUS


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def dev_history(creator: str | None, created: dict | None, decision_ts_s: float, observed_ns: int,
                error: str | None = None) -> DevEvidence:
    if not isinstance(created, dict):
        return DevEvidence(creator, {"available": False}, observed_ns, error)
    tokens = [t for t in created.get("tokens") or [] if isinstance(t, dict)]
    before = [t for t in tokens if (_num(t.get("create_timestamp")) or float("inf")) < decision_ts_s]
    graduated = [t for t in before if t.get("is_open") is True]
    aths = [a for a in (_num(t.get("token_ath_mc")) for t in before) if a is not None]
    bundler = [b for b in (_num(t.get("bundler_rate")) for t in before) if b is not None]
    lifetime = _num(created.get("inner_count"))
    facts = {
        "available": True,
        "sample_size": len(tokens),
        "launches_before_decision": len(before),
        "graduated_before_decision": len(graduated),
        "graduation_rate_before_decision": (len(graduated) / len(before)) if before else None,
        "best_ath_mc_usd_before_decision": max(aths) if aths else None,
        "mean_bundler_rate_before_decision": (sum(bundler) / len(bundler)) if bundler else None,
        "last_launch_before_decision_ts": max((_num(t.get("create_timestamp")) for t in before), default=None),
        "history_complete": lifetime is not None and lifetime <= len(tokens),
        "gmgn_lifetime_counts": {"launches": lifetime, "graduated": _num(created.get("open_count")),
                                 "graduation_ratio": _num(created.get("open_ratio")), "decision_time_safe": False},
        "chad_gate": "CHAD runs Dev Intel after the safety score threshold (B-03); collected as evidence, not gating",
    }
    return DevEvidence(creator, facts, observed_ns, error)
