"""CHAD safety and developer evidence (Phase 2: C01 Safety, C08 Dev Intel, R1 risk facts).

CHAD: "Runs RugCheck, honeypot detection, and mint/freeze authority analysis on every candidate." The verdict is
PASS only when every named check passes, FAIL when any fails (hard veto), UNKNOWN otherwise; missing data is never a
pass. The 0-100 score and its threshold, and the dev-reputation formula, are not in CHAD (B-03): no score is made up.
"""
import asyncio
import json
from pathlib import Path

from jinx.intel.safety import SafetyService, Verdict, assess
from jinx.intel.dev_reputation import dev_history

FIX = json.loads((Path(__file__).parent / "fixtures" / "gmgn_security_sample_20261021.json").read_text())
SEC, CREATED = FIX["security"], FIX["created_tokens_head"]


def with_(**kw):
    return {**SEC, **kw}


def test_a_real_gmgn_security_report_without_a_rugcheck_report_stays_unknown():
    ev = assess("M", SEC, observed_ns=1)
    by = {c.name: c.verdict for c in ev.checks}
    assert by == {"honeypot": Verdict.PASS, "mint_authority": Verdict.PASS, "freeze_authority": Verdict.PASS,
                  "rugcheck": Verdict.UNKNOWN}
    assert ev.verdict is Verdict.UNKNOWN and not ev.hard_veto and not ev.eligible     # never PASS with a gap
    assert ev.score is None and "B-03" in ev.score_status


def test_each_named_failure_is_a_hard_veto():
    for bad in (with_(honeypot=1), with_(can_not_sell=1), with_(renounced_mint=False),
                with_(renounced_freeze_account=False), with_(is_honeypot=True, honeypot=None)):
        ev = assess("M", bad, observed_ns=1)
        assert ev.verdict is Verdict.FAIL and ev.hard_veto and ev.veto_reasons, bad


def test_missing_or_unreadable_data_is_unknown_never_pass():
    assert assess("M", None, observed_ns=1).verdict is Verdict.UNKNOWN
    ev = assess("M", with_(renounced_mint=None, renounced_freeze_account="?"), observed_ns=1)
    by = {c.name: c.verdict for c in ev.checks}
    assert by["mint_authority"] is Verdict.UNKNOWN and by["freeze_authority"] is Verdict.UNKNOWN


def test_all_checks_passing_including_a_rugcheck_report_is_pass():
    ev = assess("M", SEC, observed_ns=1, rugcheck={"verdict": "PASS", "source": "test"})
    assert ev.verdict is Verdict.PASS and ev.eligible


def test_risk_facts_are_recorded_without_inventing_rejection_thresholds():
    ev = assess("M", with_(top_10_holder_rate="0.42", sell_tax="0.05", flags=["x"]), observed_ns=1)
    assert ev.facts["top_10_holder_rate"] == 0.42 and ev.facts["sell_tax"] == 0.05 and ev.facts["flags"] == ["x"]
    assert ev.verdict is Verdict.UNKNOWN          # facts alone change nothing (no new live rejection thresholds)


def test_developer_history_uses_only_launches_before_the_decision_time():
    created = dict(CREATED, tokens=[
        {"token_address": "OLD1", "create_timestamp": 100, "is_open": True, "token_ath_mc": "50000", "bundler_rate": 0.1},
        {"token_address": "OLD2", "create_timestamp": 200, "is_open": False, "token_ath_mc": "3000", "bundler_rate": 0},
        {"token_address": "FUTURE", "create_timestamp": 900, "is_open": True, "token_ath_mc": "9e9", "bundler_rate": 0}])
    ev = dev_history("DEV", created, decision_ts_s=500, observed_ns=1)
    assert ev.facts["launches_before_decision"] == 2 and ev.facts["graduated_before_decision"] == 1
    assert ev.facts["best_ath_mc_usd_before_decision"] == 50000.0           # the future launch is not visible
    assert ev.facts["gmgn_lifetime_counts"]["decision_time_safe"] is False   # GMGN's totals include later launches
    assert ev.score is None and "B-03" in ev.score_status


def test_incomplete_developer_history_is_flagged():
    ev = dev_history("DEV", dict(CREATED, inner_count=9803, tokens=CREATED["tokens"]), decision_ts_s=2_000_000_000,
                     observed_ns=1)
    assert ev.facts["history_complete"] is False and ev.facts["sample_size"] == 1
    assert dev_history("DEV", None, decision_ts_s=1, observed_ns=1).facts == {"available": False}


def test_the_service_fetches_in_parallel_caches_briefly_and_turns_api_failures_into_unknown():
    calls = []

    class Gmgn:
        async def call(self, method, chain, address):
            calls.append(method)
            await asyncio.sleep(0.01)
            if address == "BROKEN":
                raise TimeoutError("slow")
            return SEC if method == "getTokenSecurity" else CREATED

    async def main():
        svc = SafetyService(Gmgn(), cache_ttl_s=60, max_entries=2)
        a = await svc.evaluate("M", creator="DEV", decision_ts_s=2_000_000_000)
        b = await svc.evaluate("M", creator="DEV", decision_ts_s=2_000_000_000)      # cached
        broken = await svc.evaluate("BROKEN", creator="BROKEN", decision_ts_s=1)
        for i in range(5):
            await svc.evaluate(f"X{i}", creator="DEV", decision_ts_s=1)
        return svc, a, b, broken
    svc, a, b, broken = asyncio.run(main())
    assert a.safety.verdict is Verdict.UNKNOWN and a.dev.facts["available"] is True
    assert calls.count("getTokenSecurity") == 7 and b is a                   # the repeat was served from cache
    assert broken.safety.verdict is Verdict.UNKNOWN and "TimeoutError" in broken.safety.errors[0]
    assert len(svc._cache) <= 2                                             # bounded cache (owner storage rule 2)
    assert a.latency_ms is not None


RC = json.loads((Path(__file__).parent / "fixtures" / "rugcheck_report_sample_20261021.json").read_text())["report"]


class FakeGmgn:
    def __init__(self, sec):
        self.sec = sec

    async def call(self, method, chain, address):
        return self.sec if method == "getTokenSecurity" else CREATED


class FakeRugCheck:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def report(self, mint, priority=10):
        self.calls.append((mint, priority))
        return self.result


def test_rugcheck_completes_the_gate_and_a_full_pass_is_eligible():
    from jinx.adapters.rugcheck.client import interpret
    rc = FakeRugCheck(interpret(RC))
    out = asyncio.run(SafetyService(FakeGmgn(SEC), rugcheck=rc).evaluate("M", "DEV", 2e9, priority=0))
    assert out.safety.verdict is Verdict.PASS and out.safety.eligible
    assert out.safety.facts["rugcheck"]["danger_findings"]          # recorded as evidence, not a veto
    assert rc.calls == [("M", 0)]                                    # GMGN-clean fast-path candidate: top priority


def test_a_gmgn_hard_veto_spends_no_rugcheck_request():
    rc = FakeRugCheck({"verdict": "PASS"})
    svc = SafetyService(FakeGmgn(with_(honeypot=1)), rugcheck=rc)
    out = asyncio.run(svc.evaluate("M", "DEV", 2e9))
    assert out.safety.verdict is Verdict.FAIL and rc.calls == [] and svc.stats["rugcheck_skipped_veto"] == 1


def test_rugcheck_failures_and_rate_limits_never_pass():
    from jinx.adapters.rugcheck.client import interpret, unknown
    for result in (interpret({**RC, "rugged": True}), unknown("rate_limited"), unknown("timeout"), unknown("malformed")):
        out = asyncio.run(SafetyService(FakeGmgn(SEC), rugcheck=FakeRugCheck(result)).evaluate("M", "DEV", 2e9))
        assert not out.safety.eligible
        assert out.safety.verdict is (Verdict.FAIL if result["verdict"] == "FAIL" else Verdict.UNKNOWN)


def test_gmgn_unclear_candidates_queue_behind_clean_ones():
    rc = FakeRugCheck({"verdict": "PASS", "facts": {}})
    asyncio.run(SafetyService(FakeGmgn(with_(renounced_mint=None)), rugcheck=rc).evaluate("M", "DEV", 2e9, priority=0))
    assert rc.calls == [("M", 1)]
