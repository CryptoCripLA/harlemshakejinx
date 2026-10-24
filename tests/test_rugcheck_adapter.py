"""RugCheck adapter (owner order 2026-10-21, B-19 resolved: the official public RugCheck API).

CHAD: "Runs RugCheck, honeypot detection, and mint/freeze authority analysis on every candidate." The adapter
reads `GET /v1/tokens/{mint}/report` unauthenticated (published free access: one request per second), parses risk
findings, raw and normalized score and liquidity, and maps only RugCheck's own determinations onto the predicate:
`rugged: true` or a present mint/freeze authority is FAIL; a valid report without them is PASS; anything missing,
late, rate-limited or malformed is UNKNOWN. RugCheck's score and risk levels are recorded, never turned into an
approval threshold (no approved mapping)."""
import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from jinx.adapters.rugcheck.client import RugCheckClient, interpret

FX = json.loads((Path(__file__).parent / "fixtures" / "rugcheck_report_sample_20261021.json").read_text())
REPORT = FX["report"]
MINT = REPORT["mint"]
OTHER = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def test_a_real_report_is_parsed_into_findings_scores_and_liquidity():
    r = interpret(REPORT)
    assert r["verdict"] == "PASS" and r["source"] == "rugcheck.report"
    f = r["facts"]
    assert f["score"] == 120167 and f["score_normalised"] == 80 and f["rugged"] is False
    assert {x["name"] for x in f["risks"]} == {"Creator history of rugged tokens", "Single holder ownership"}
    assert f["danger_findings"] == ["Creator history of rugged tokens", "Single holder ownership"]
    assert f["liquidity"]["total_market_usd"] == pytest.approx(94.397, abs=0.01)
    assert f["liquidity"]["markets"][0]["market_type"] == "pump_fun" and f["liquidity"]["markets"][0]["lp_locked_pct"] == 100
    assert "not a veto" in f["danger_status"]          # no approved mapping turns danger findings into vetoes


@pytest.mark.parametrize("change,reason", [({"rugged": True}, "rugged"), ({"mintAuthority": "Auth1"}, "mint authority"),
                                           ({"freezeAuthority": "Auth2"}, "freeze authority")])
def test_rugchecks_own_hard_findings_fail(change, reason):
    r = interpret({**REPORT, **change})
    assert r["verdict"] == "FAIL" and reason in r["detail"]


@pytest.mark.parametrize("bad", [None, "oops", {}, {"risks": []}, {k: v for k, v in REPORT.items() if k != "rugged"},
                                 {k: v for k, v in REPORT.items() if k != "mintAuthority"}])
def test_incomplete_or_malformed_reports_are_unknown(bad):
    assert interpret(bad)["verdict"] == "UNKNOWN"


def run(coro):
    return asyncio.run(coro)


def client_with(handler, **kw):
    kw.setdefault("min_interval_s", 0.0)
    return RugCheckClient(transport=httpx.MockTransport(handler), **kw)


async def with_worker(client, coro):
    task = asyncio.create_task(client.run())
    try:
        return await coro
    finally:
        task.cancel()


def test_a_200_report_passes_through_and_latency_is_recorded():
    c = client_with(lambda req: httpx.Response(200, json=REPORT))
    r = run(with_worker(c, c.report(MINT)))
    assert r["verdict"] == "PASS" and c.stats["ok"] == 1 and c.health()["latency_ms"]["n"] == 1


@pytest.mark.parametrize("response,status", [
    (httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate limited"}), "rate_limited"),
    (httpx.Response(400, json=FX["bad_mint_400"]), "http_400"),
    (httpx.Response(404, json={"error": "not found"}), "http_404"),
    (httpx.Response(500, text="boom"), "http_500"),
    (httpx.Response(200, text="<html>not json</html>"), "malformed"),
])
def test_errors_rate_limits_and_malformed_responses_are_never_pass(response, status):
    c = client_with(lambda req: response)
    r = run(with_worker(c, c.report(MINT)))
    assert r["verdict"] == "UNKNOWN" and r["error"] == status


def test_a_429_pauses_requests_for_retry_after():
    calls = []

    def handler(req):
        calls.append(time.monotonic())
        return httpx.Response(429, headers={"Retry-After": "0.3"}) if len(calls) == 1 else httpx.Response(200, json=REPORT)

    async def main(c):
        first = await c.report(MINT)
        second = await c.report(OTHER)
        return first, second
    c = client_with(handler)
    first, second = run(with_worker(c, main(c)))
    assert first["verdict"] == "UNKNOWN" and second["verdict"] == "PASS"
    assert calls[1] - calls[0] >= 0.29 and c.stats["rate_limited"] == 1


def test_timeouts_are_unknown():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)
    c = client_with(handler)
    assert run(with_worker(c, c.report(MINT)))["error"] == "timeout"


def test_requests_are_spaced_to_the_free_rate():
    times = []

    def handler(req):
        times.append(time.monotonic())
        return httpx.Response(200, json=REPORT)

    async def main(c):
        return await asyncio.gather(*(c.report(m) for m in (MINT, OTHER, "So11111111111111111111111111111111111111112")))
    c = client_with(handler, min_interval_s=0.2)
    run(with_worker(c, main(c)))
    assert len(times) == 3 and all(b - a >= 0.19 for a, b in zip(times, times[1:]))


def test_concurrent_requests_for_one_mint_share_one_call_and_fresh_reports_are_reused():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json=REPORT)

    async def main(c):
        a, b = await asyncio.gather(c.report(MINT), c.report(MINT))
        again = await c.report(MINT)
        return a, b, again
    c = client_with(handler, fresh_s=60)
    a, b, again = run(with_worker(c, main(c)))
    assert len(calls) == 1 and a is b and again["verdict"] == "PASS" and c.stats["cache_hits"] == 1


def test_stale_reports_are_fetched_again():
    calls = []
    clock = [0.0]

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=REPORT)

    async def main(c):
        await c.report(MINT)
        clock[0] += 61
        await c.report(MINT)
    c = client_with(handler, fresh_s=60, clock=lambda: clock[0])
    run(with_worker(c, main(c)))
    assert len(calls) == 2


def test_higher_priority_candidates_are_checked_first():
    order = []

    def handler(req):
        order.append(req.url.path.split("/")[3])
        return httpx.Response(200, json=REPORT)

    async def main(c):
        low = asyncio.create_task(c.report("LowLowLowLowLowLowLowLowLowLowLow1", priority=9))
        high = asyncio.create_task(c.report("HighHighHighHighHighHighHighHigh1", priority=0))
        await asyncio.sleep(0)
        worker = asyncio.create_task(c.run())
        await asyncio.gather(low, high)
        worker.cancel()
    c = client_with(handler)
    run(main(c))
    assert order == ["HighHighHighHighHighHighHighHigh1", "LowLowLowLowLowLowLowLowLowLowLow1"]


def test_invalid_mints_are_refused_without_a_request_and_the_queue_is_bounded():
    calls = []
    c = client_with(lambda req: calls.append(1) or httpx.Response(200, json=REPORT), max_queue=2)

    async def main():
        bad = await c.report("not a mint")
        futures = [asyncio.create_task(c.report(f"{i}" * 32)) for i in range(1, 6)]
        await asyncio.sleep(0)
        return bad, futures
    bad, futures = run(main())
    assert bad["error"] == "invalid_mint" and not calls
    assert c.stats["queue_full"] >= 1 and len(c._cache) <= c.max_cache


def test_protective_exits_never_depend_on_rugcheck():
    """Owner order: RugCheck failures must never prevent protective exits. The exit path does not import any safety
    or RugCheck code, and an open paper position closes while RugCheck is unreachable."""
    import ast
    root = Path(__file__).resolve().parents[1] / "jinx"
    for name in ("execution.py", "ledger.py", "paper.py"):
        mods = {getattr(n, "module", None) or "" for n in ast.walk(ast.parse((root / name).read_text()))
                if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(ast.parse((root / name).read_text()))
                                                     if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.startswith(("jinx.intel", "jinx.adapters")) for m in mods), name
    from tests.test_persistence import ledger_activity
    from jinx.store import Store
    import tempfile
    down = client_with(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down", request=req)))
    assert run(with_worker(down, down.report(MINT)))["verdict"] == "UNKNOWN"
    with tempfile.TemporaryDirectory() as d:
        store = Store(Path(d) / "l.db")
        ledger, _ = ledger_activity(store)                 # opens and fully closes a paper position
        assert ledger.active() == [] and store.conn.execute("SELECT status FROM positions").fetchone()[0] == "CLOSED"
        store.close()


def test_a_token_rugcheck_has_not_indexed_yet_is_unknown_and_asked_again():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, json={"error": "not found"}) if len(calls) == 1 else httpx.Response(200, json=REPORT)

    async def main(c):
        return await c.report(MINT), await c.report(MINT)
    c = client_with(handler)
    first, second = run(with_worker(c, main(c)))
    assert first["error"] == "not_indexed" and second["verdict"] == "PASS" and len(calls) == 2



@pytest.mark.parametrize("value", ["", False, 0, [], {}])
@pytest.mark.parametrize("key", ["mintAuthority", "freezeAuthority"])
def test_an_unreadable_authority_value_is_unknown_never_revoked(key, value):
    """Phase 4 finding P4-F13: RugCheck's revoked value is null; a non-address value is unreadable, never a PASS."""
    r = interpret({**REPORT, key: value})
    assert r["verdict"] == "UNKNOWN" and r["error"] == "unreadable_authority"
    assert interpret({**REPORT, key: value, "rugged": True})["verdict"] == "FAIL"     # a real failure still wins
