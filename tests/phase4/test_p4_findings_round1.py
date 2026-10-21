"""Defects found by the Codex Phase 4 review, round 1 (docs/audits/phase4-codex-20261021-round1.md), pinned as strict
xfail tests in the files' owners' names (docs/AGENT_COORDINATION.md). Each turns red (XPASS) the moment its fix lands,
so the owner removes the marker in the fixing commit. Phase 4 does not edit these jinx/ files."""
import asyncio
import dataclasses
import os
import time

import pytest

from jinx.core.events import EventKind, Side
from jinx.intel.bundles import BundleTracker
from jinx.intel.dev_reputation import dev_history
from jinx.intel.whales import WhaleBook
from jinx.persistence.object_store import LedgerBackups
from jinx.store import Store
from tests.conftest import make_trade
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import DISTINCT, LiveFeed, M, baseline_msgs
from tests.pumpapi_msgs import events

STORAGE = "storage session local_f5744230"
PHASE2 = "Phase 2 session local_f5744230"
LEAD = "lead session local_b73d1072"


def buys(n, mint="GrowMint", trader=lambda i: "W", slot=7):
    return [dataclasses.replace(make_trade(mint=mint, trader=trader(i), sig=f"G{i}", side=Side.BUY), slot=slot)
            for i in range(n)]


# ---- P4-F5: unbounded per-token lists (speed rule S6) -------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F5a (Codex round 1, finding 1): jinx/intel/whales.py appends every "
                   f"tracked-wallet buy to a per-mint list; the cap bounds the number of mints, not the list. Owner: "
                   f"{STORAGE}.")
def test_S6_tracked_wallet_buys_per_token_stop_growing():
    book = WhaleBook()
    book.track("W", "test", {"why": "labelled test fixture"})
    for ev in buys(10_000):
        book.on_event(ev)
    at_10k = len(book._tracked_buys.get("GrowMint", []))
    for ev in buys(20_000)[10_000:]:
        book.on_event(ev)
    assert len(book._tracked_buys.get("GrowMint", [])) == at_10k          # a plateau, not linear growth


@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F5b (Codex round 1, finding 1): jinx/intel/bundles.py keeps every "
                   f"buy that arrives before its creation in a per-(mint, slot) list; the cap bounds the keys, not the "
                   f"list. Owner: {STORAGE}.")
def test_S6_buys_parked_before_their_creation_stop_growing():
    tracker = BundleTracker()
    for ev in buys(10_000, trader=lambda i: f"B{i}"):
        tracker.on_event(ev)
    at_10k = len(tracker._pending.get(("GrowMint", 7), []))
    for ev in buys(20_000, trader=lambda i: f"B{i}")[10_000:]:
        tracker.on_event(ev)
    assert len(tracker._pending.get(("GrowMint", 7), [])) == at_10k


# ---- P4-F9: the daily backup blocks the event loop (speed rules S3/S4) --------------------------------------------

class FakeS3:
    def list_buckets(self):
        return {"Buckets": [{"Name": "jinx-backups"}]}

    def put_object(self, **kw):
        return {}

    def list_objects_v2(self, **kw):
        return {"Contents": [], "IsTruncated": False}


@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F9 (Codex round 1, finding 5): jinx/persistence/object_store.py "
                   f"LedgerBackups.backup builds the gzip snapshot (snapshot(store): a full copy plus compression) on the "
                   f"event loop before asyncio.to_thread, so ingestion, detection and exits stall for the whole copy. "
                   f"Owner: {STORAGE}.")
def test_S4_the_daily_ledger_backup_never_stalls_the_event_loop(tmp_path):
    store = Store(tmp_path / "jinx.db")
    with store.transaction() as c:
        c.execute("CREATE TABLE p4_bulk (x BLOB)")          # a large ledger (labelled test data, deleted with tmp_path)
        for _ in range(24):
            c.execute("INSERT INTO p4_bulk VALUES (?)", (os.urandom(1_000_000),))
    backups = LedgerBackups(FakeS3())

    async def main():
        gaps, stop = [], asyncio.Event()

        async def heartbeat():
            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.005)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now
        hb = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.02)
        report = await backups.backup(store)
        stop.set()
        await hb
        return report, max(gaps)
    report, worst_gap = asyncio.run(main())
    store.close()
    assert report is not None and report["bytes"] > 20_000_000
    assert worst_gap < 0.1, f"the event loop was blocked for {worst_gap:.2f} s"


# ---- P4-F6: a safety PASS never expires in the app -------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F6 (Codex round 1, finding 2): jinx/app.py stores the candidate's "
                   f"safety verdict without its observation time and entry_check trusts it indefinitely; the permanent "
                   f"'screening' flag stops any re-screen, so SafetyService's freshness window is bypassed and a token "
                   f"vetoed later still reads as safe. Owner: {PHASE2} (read by Phase 3's EntryCoordinator).")
def test_a_safety_pass_older_than_its_freshness_window_is_not_trusted_at_the_decision(tmp_path, monkeypatch):
    gmgn = h.FakeGmgn(wallet_stats=DISTINCT)
    app = h.build_app(tmp_path, gmgn=gmgn, rugcheck=h.FakeRugCheck())
    app.pumpapi = LiveFeed()
    h.run(app, [events(m) for m in baseline_msgs()])
    assert app.entry_check(M)["all_checks_pass"] is True
    gmgn.security = {**gmgn.security, "renounced_mint": False}          # the provider now reports a hard veto
    later = 2 * app.safety.cache_ttl_s
    for name, real in (("time", time.time), ("time_ns", time.time_ns), ("monotonic", time.monotonic)):
        offset = later * (1e9 if name == "time_ns" else 1)
        monkeypatch.setattr(time, name, lambda real=real, offset=offset: type(real())(real() + offset))
    h.run(app, [], settle_s=1.0)
    check = app.entry_check(M)
    h.close(app)
    assert check["all_checks_pass"] is False


# ---- P4-F2b: a transient network error ends screening for good -------------------------------------------------------

class FlakyRugCheck(h.FakeRugCheck):
    """RugCheck's network is down for the first request, then answers PASS."""

    async def report(self, mint, priority=10):
        self.calls.append(mint)
        if len(self.calls) == 1:
            return {"verdict": "UNKNOWN", "source": "rugcheck", "error": "network", "detail": "connection reset"}
        return {"verdict": "PASS", "source": "rugcheck", "detail": "", "facts": {}}


@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F2b (Codex round 1, finding 7): jinx/app.py _screen retries only "
                   f"'not_indexed', 'rate_limited' and 'timeout', although jinx/intel/safety.py TRANSIENT_RUGCHECK also "
                   f"lists network and 5xx errors; one dropped connection leaves the candidate UNKNOWN for good (a missed "
                   f"entry). Owner: {PHASE2}.")
def test_screening_resumes_after_a_transient_rugcheck_network_error(tmp_path):
    rc = FlakyRugCheck()
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn(wallet_stats=DISTINCT), rugcheck=rc)
    app.pumpapi = LiveFeed()
    h.run(app, [events(m) for m in baseline_msgs()])
    verdict = app.candidates[M]["safety"]["verdict"]
    h.close(app)
    assert verdict == "PASS" and len(rc.calls) >= 2


# ---- P4-F7: developer history reads outcomes observed after the decision ---------------------------------------------

@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F7 (Codex round 1, finding 3): jinx/intel/dev_reputation.py filters "
                   f"launches by creation time but counts each launch's present-day is_open (graduation) and ATH as "
                   f"facts 'before decision'. In a replay or backtest (observation after the decision) that is "
                   f"lookahead. Owner: {LEAD}.")
def test_developer_history_never_presents_later_observed_outcomes_as_known_at_the_decision():
    decision_s, observed_ns = 101.0, 10_000 * 1_000_000_000          # observed long after the decision
    created = {"inner_count": 1, "tokens": [{"token_address": "earlier-launch", "create_timestamp": 100,
                                              "is_open": True, "token_ath_mc": "9000000"}]}
    facts = dev_history("DEV", created, decision_s, observed_ns).facts
    leaked = facts["graduated_before_decision"] == 1 or facts["best_ath_mc_usd_before_decision"] == 9_000_000.0
    flagged = facts.get("outcomes_decision_time_safe") is False
    assert not leaked or flagged
