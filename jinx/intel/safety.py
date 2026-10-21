"""CHAD safety evidence (Phase 2: C01 Safety, R1 risk facts; docs/SOURCE_FIDELITY_MATRIX.md).

CHAD (docs/CHAD_BLUEPRINT.md): "Runs RugCheck, honeypot detection, and mint/freeze authority analysis on every
candidate." Each named check is a predicate with three outcomes:

- PASS     the data source says the token is clean on that check;
- FAIL     the data source says it is not: a hard safety veto, the token can never be entered;
- UNKNOWN  no data, unreadable data, or no approved source (RugCheck: B-19). Never treated as a pass.

The overall verdict is FAIL if any check fails, PASS only if every check passes, otherwise UNKNOWN; only PASS makes a
token eligible. CHAD's 0-100 score and its pass threshold are not specified (B-03), so no score is produced.
Holder concentration, taxes, flags and lock data are recorded as facts (R1) with no invented rejection threshold.

Source of the data: GMGN `getTokenSecurity` (field formats pinned by
tests/fixtures/gmgn_security_sample_20261021.json). `SafetyService` fetches security and developer history in
parallel, off the trading path, with a short bounded cache.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum

from jinx.intel.dev_reputation import DevEvidence, dev_history

SCORE_STATUS = "not computed: CHAD's 0-100 safety formula and pass threshold are unspecified (B-03)"


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Check:
    name: str
    verdict: Verdict
    value: object
    source: str
    detail: str = ""


@dataclass(frozen=True)
class SafetyEvidence:
    mint: str
    verdict: Verdict
    checks: tuple[Check, ...]
    facts: dict
    observed_ns: int
    errors: tuple[str, ...] = ()
    score: int | None = None
    score_status: str = SCORE_STATUS

    @property
    def hard_veto(self) -> bool:
        return self.verdict is Verdict.FAIL

    @property
    def eligible(self) -> bool:
        return self.verdict is Verdict.PASS

    @property
    def veto_reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.verdict is Verdict.FAIL]


def flag(v) -> bool | None:
    """GMGN reports flags as booleans, 0/1, or strings; anything else is unreadable (None)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("0", "1", "true", "false"):
        return v.strip().lower() in ("1", "true")
    return None


def number(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _honeypot(sec: dict) -> Check:
    hp = flag(sec.get("honeypot"))
    if hp is None:
        hp = flag(sec.get("is_honeypot"))
    cannot_sell = flag(sec.get("can_not_sell"))
    if hp or cannot_sell:
        return Check("honeypot", Verdict.FAIL, {"honeypot": hp, "can_not_sell": cannot_sell}, "gmgn.getTokenSecurity",
                     "honeypot reported" if hp else "token cannot be sold")
    if hp is False and cannot_sell is False:
        return Check("honeypot", Verdict.PASS, {"honeypot": hp, "can_not_sell": cannot_sell}, "gmgn.getTokenSecurity")
    return Check("honeypot", Verdict.UNKNOWN, {"honeypot": hp, "can_not_sell": cannot_sell}, "gmgn.getTokenSecurity",
                 "not reported")


def _renounced(sec: dict, name: str, key: str) -> Check:
    v = flag(sec.get(key))
    if v is None:
        return Check(name, Verdict.UNKNOWN, sec.get(key), "gmgn.getTokenSecurity", f"{key} not reported")
    if v:
        return Check(name, Verdict.PASS, True, "gmgn.getTokenSecurity")
    return Check(name, Verdict.FAIL, False, "gmgn.getTokenSecurity", f"{key} is false: authority still held")


def _rugcheck(report: dict | None) -> Check:
    if not report:
        return Check("rugcheck", Verdict.UNKNOWN, None, "none",
                     "CHAD names RugCheck; no approved RugCheck source yet (B-19)")
    v = Verdict(report["verdict"]) if report.get("verdict") in Verdict.__members__ else Verdict.UNKNOWN
    return Check("rugcheck", v, report.get("verdict"), report.get("source", "rugcheck"), report.get("detail", ""))


def assess(mint: str, security: dict | None, observed_ns: int, rugcheck: dict | None = None,
           errors: tuple[str, ...] = ()) -> SafetyEvidence:
    sec = security or {}
    checks = (_honeypot(sec), _renounced(sec, "mint_authority", "renounced_mint"),
              _renounced(sec, "freeze_authority", "renounced_freeze_account"), _rugcheck(rugcheck))
    if any(c.verdict is Verdict.FAIL for c in checks):
        verdict = Verdict.FAIL
    elif all(c.verdict is Verdict.PASS for c in checks):
        verdict = Verdict.PASS
    else:
        verdict = Verdict.UNKNOWN
    facts = {"available": security is not None}
    if security is not None:
        lock = sec.get("lock_summary") or {}
        facts.update(top_10_holder_rate=number(sec.get("top_10_holder_rate")), buy_tax=number(sec.get("buy_tax")),
                     sell_tax=number(sec.get("sell_tax")), blacklist=flag(sec.get("blacklist")),
                     flags=list(sec.get("flags") or []), hide_risk=flag(sec.get("hide_risk")),
                     dev_token_burn_ratio=number(sec.get("dev_token_burn_ratio")),
                     lp_locked=flag(lock.get("is_locked")) if isinstance(lock, dict) else None)
    return SafetyEvidence(mint, verdict, checks, facts, observed_ns, tuple(errors))


@dataclass(frozen=True)
class CandidateSafety:
    mint: str
    safety: SafetyEvidence
    dev: DevEvidence
    latency_ms: float
    fetched_ns: int = field(default_factory=time.time_ns)


class SafetyService:
    """Security and developer history for a candidate, fetched in parallel from GMGN. Off the trading path: a
    candidate waits for nothing here except its own eligibility. Failures become UNKNOWN with the error recorded.
    Results are cached for `cache_ttl_s` in a bounded LRU (owner storage rule 2)."""

    def __init__(self, gmgn, cache_ttl_s: float = 60.0, max_entries: int = 2_000, timeout_s: float = 10.0) -> None:
        self.gmgn, self.cache_ttl_s, self.max_entries, self.timeout_s = gmgn, cache_ttl_s, max_entries, timeout_s
        self._cache: OrderedDict[str, tuple[float, CandidateSafety]] = OrderedDict()
        self.stats = {"evaluations": 0, "cache_hits": 0, "errors": 0}

    async def _fetch(self, method: str, address: str):
        try:
            return await asyncio.wait_for(self.gmgn.call(method, "sol", address), self.timeout_s), None
        except Exception as e:
            self.stats["errors"] += 1
            return None, f"{method}: {type(e).__name__}"

    async def evaluate(self, mint: str, creator: str | None, decision_ts_s: float,
                       rugcheck: dict | None = None) -> CandidateSafety:
        now = time.monotonic()
        hit = self._cache.get(mint)
        if hit and now - hit[0] < self.cache_ttl_s:
            self._cache.move_to_end(mint)
            self.stats["cache_hits"] += 1
            return hit[1]
        t0 = time.perf_counter()
        (sec, sec_err), (created, dev_err) = await asyncio.gather(
            self._fetch("getTokenSecurity", mint),
            self._fetch("getCreatedTokens", creator) if creator else asyncio.sleep(0, (None, None)))
        observed = time.time_ns()
        result = CandidateSafety(mint, assess(mint, sec, observed, rugcheck, tuple(e for e in (sec_err,) if e)),
                                 dev_history(creator, created, decision_ts_s, observed,
                                             error=dev_err) if creator else dev_history(None, None, decision_ts_s, observed),
                                 latency_ms=(time.perf_counter() - t0) * 1000)
        self._cache[mint] = (now, result)
        self._cache.move_to_end(mint)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        self.stats["evaluations"] += 1
        return result
