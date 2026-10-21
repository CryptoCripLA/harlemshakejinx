"""V08 reconnects, dropped messages and provider outages; V17 missing data fails safely.

Owner rule (CLAUDE.md storage section): "If a service is down, trading continues and health says so." Mandate
2026-10-20: safety "fails closed when mandatory evidence is unknown". Every case uses the real engine pieces: the
PumpAPI feed over a real local websocket, the Mako/PumpPortal feed against an unreachable endpoint, GMGN and RugCheck
through fakes with failure modes, and Neon / Railway / Redis / object storage pointed at a closed local port (nothing
leaves the machine). After each fault the test checks: no exception escaped, no position or fill was created by the
fault, the paper wallet reconciles, and the entry decision refuses what it cannot prove."""
import asyncio
import dataclasses

import pytest
import websockets

from jinx.adapters.pumpapi.feed import PumpApiFeed
from jinx.execution import EntryIntent, ExitIntent
from jinx.ledger import Status
from jinx.modes import Mode, set_mode
from jinx.sim import load_capture
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import DISTINCT, LiveFeed, M, baseline_msgs
from tests.pumpapi_msgs import buy, events

CLOSED = "127.0.0.1:1"          # nothing listens on port 1: every connection is refused at once


def no_trading_side_effects(app):
    assert app.ledger.all() == [] and app.wallet.reconcile().ok
    assert app.wallet.balance() == app.wallet.reconcile().starting_lamports


# ---- PumpAPI (the Early Momentum trade feed) -------------------------------------------------------------------------

def live_now(msgs):
    """The same messages stamped as if they happened just now (PumpAPI's `timestamp` is event time in ms)."""
    import time
    shift = int(time.time() * 1000) - msgs[0]["timestamp"]
    return [{**m, "timestamp": m["timestamp"] + shift} for m in msgs]


def test_a_pumpapi_reconnect_makes_every_tracked_history_incomplete_and_blocks_entry(tmp_path):
    """Trades sent while the feed was down are lost, so a peak can no longer be proven (B-02 §7)."""
    import json
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn(wallet_stats=DISTINCT), rugcheck=h.FakeRugCheck())

    async def main():
        conns, judged, done = [], asyncio.Event(), asyncio.Event()

        async def server(ws):
            conns.append(ws)
            if len(conns) == 1:
                await asyncio.sleep(0.05)                     # events happen after the client is connected
                for m in live_now(baseline_msgs()):          # live wire format: compact JSON, current timestamps
                    await ws.send(json.dumps(m, separators=(",", ":")))
                await judged.wait()
                await ws.close(code=1001)                     # the provider drops the connection
            else:
                await done.wait()

        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            feed = PumpApiFeed(app.bus, ws_url=f"ws://127.0.0.1:{port}", reconnect_initial_s=0.01,
                               on_connect=app._on_trade_feed_connect, stall_timeout_s=30)
            app.pumpapi = feed
            bus = asyncio.create_task(app.bus.run())
            task = asyncio.create_task(feed.run())
            for _ in range(300):                              # detection, safety and funders while connected
                await asyncio.sleep(0.01)
                c = app.candidates.get(M) or {}
                if c.get("safety") and not app._background:
                    break
            await app.bus.drain()
            before = app.entry_check(M)
            judged.set()
            for _ in range(300):
                await asyncio.sleep(0.01)
                if feed.stats["connections"] >= 2 and feed.connected:
                    break
            after = app.entry_check(M)
            done.set()
            task.cancel()
            bus.cancel()
            return before, after, feed.stats

    before, after, stats = h.loop_of(app).run_until_complete(main())
    assert stats["connections"] >= 2 and stats["disconnects"] >= 1
    assert before is not None and before["all_checks_pass"] is True        # eligible while the feed was whole
    assert after["all_checks_pass"] is False and "gap" in after["reserve_reason"]
    no_trading_side_effects(app)
    h.close(app)


def test_an_unreachable_pumpapi_keeps_retrying_and_the_reserve_check_fails_closed(tmp_path):
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn(wallet_stats=DISTINCT), rugcheck=h.FakeRugCheck())
    attempts = []

    def refuse(url):
        attempts.append(url)
        raise ConnectionRefusedError("PumpAPI unreachable")

    feed = PumpApiFeed(app.bus, connect=refuse, reconnect_initial_s=0.01, reconnect_max_s=0.02)
    app.pumpapi = feed
    h.run(app, [events(m) for m in baseline_msgs()])            # the same facts heard earlier

    async def outage():
        task = asyncio.create_task(feed.run())
        await asyncio.sleep(0.3)
        task.cancel()
    h.loop_of(app).run_until_complete(outage())
    check = app.entry_check(M)
    assert len(attempts) >= 3 and feed.connected is False
    assert check["reserve_eligible"] is False and check["reserve_reason"].startswith("stale")
    assert check["all_checks_pass"] is False
    no_trading_side_effects(app)
    h.close(app)


@pytest.mark.xfail(strict=True, reason="FINDING P4-F1 (2026-10-21): jinx/health.py::build_health judges feed health from "
                   "the PumpPortal (mako) adapter only, so /health stays 'ok' while the PumpAPI trade feed that Early "
                   "Momentum depends on is down. Owner of jinx/health.py: storage session local_f5744230. Remove this "
                   "marker when fixed.")
def test_health_reports_degraded_when_the_pumpapi_trade_feed_is_down(tmp_path):
    """The trade feed is the one Early Momentum depends on. While it is down every entry is refused (above); the
    top-level health status must say so too, not only a nested adapter field."""
    from jinx.adapters.d3ad.adapter import D3adExecutionAdapter
    from jinx.adapters.datanomad.adapter import DataNomadResearchAdapter
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn())
    app.feed = LiveMako()
    app.d3ad = D3adExecutionAdapter(app.s.upstream_dir / "d3ad")
    app.datanomad = DataNomadResearchAdapter(app.s.upstream_dir / "datanomad")
    app.pumpapi = PumpApiFeed(app.bus, connect=lambda url: (_ for _ in ()).throw(ConnectionRefusedError()))
    try:
        health = h.loop_of(app).run_until_complete(app.health())
    finally:
        h.close(app)
    pumpapi = next(a for a in health["adapters"] if a["adapter"] == "pumpapi.stream")
    assert pumpapi["connected"] is False
    assert health["status"] != "ok", "PumpAPI trade feed down but top-level health is ok"


class LiveMako:
    """A connected PumpPortal feed (health only), so the PumpAPI case is isolated."""

    def health(self):
        import time
        return {"adapter": "mako", "connected": True, "last_message_ns": time.time_ns()}

    async def sync_subscriptions(self):
        return None


# ---- PumpPortal (Mako) -----------------------------------------------------------------------------------------------

def test_an_unreachable_pumpportal_keeps_retrying_without_raising_and_health_degrades(tmp_path):
    from jinx.adapters.mako.feed import MakoPumpPortalFeed
    from jinx.adapters.d3ad.adapter import D3adExecutionAdapter
    from jinx.adapters.datanomad.adapter import DataNomadResearchAdapter
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn())
    app.feed = MakoPumpPortalFeed(app.bus, app.s.upstream_dir / "mako", ws_url=f"ws://{CLOSED}")
    app.d3ad = D3adExecutionAdapter(app.s.upstream_dir / "d3ad")
    app.datanomad = DataNomadResearchAdapter(app.s.upstream_dir / "datanomad")

    async def outage():
        task = asyncio.create_task(app.feed.run())
        await asyncio.sleep(0.5)
        alive = not task.done()
        task.cancel()
        return alive, await app.health()
    alive, health = h.loop_of(app).run_until_complete(outage())
    assert alive, "the PumpPortal feed task ended instead of retrying"
    assert health["status"] == "degraded" and health["adapters"][0]["connected"] is False
    no_trading_side_effects(app)
    h.close(app)


# ---- dropped messages ------------------------------------------------------------------------------------------------

LIVE = load_capture(h.FIXTURES / "pumpapi-live-20261021.jsonl.gz")


@pytest.mark.parametrize("drop_every", [2, 3, 7])
def test_lost_messages_never_create_a_detection_the_full_stream_does_not_have(tmp_path, drop_every):
    """A lost buy can only remove a buyer; it can never add one. Detections under loss are a subset of the full
    stream's, and nothing fails (no handler error, no position)."""
    full = h.build_app(tmp_path / "full")
    h.run(full, h.deliveries(LIVE, "pumpapi"))
    lossy = h.build_app(tmp_path / "lossy")
    st = h.run(lossy, h.deliveries(LIVE, "pumpapi", drop_every=drop_every))
    assert st.handler_errors == {}
    full_detected = set(h.state_view(full)["early_momentum"])
    lossy_detected = set(h.state_view(lossy)["early_momentum"])
    assert lossy_detected <= full_detected and len(lossy_detected) < len(full_detected)
    no_trading_side_effects(lossy)
    h.close(full)
    h.close(lossy)


# ---- GMGN and RugCheck -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["down", "timeout", "garbage", "rate_limited"])
def test_gmgn_failures_leave_safety_unknown_and_the_detection_intact(tmp_path, mode):
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn(mode=mode, wallet_stats=DISTINCT), rugcheck=h.FakeRugCheck())
    app.pumpapi = LiveFeed()
    st = h.run(app, [events(m) for m in baseline_msgs()], settle_s=1.0)
    check = app.entry_check(M)
    assert st.handler_errors == {}
    assert app.early.evidence(M) is not None                     # hearing the market does not depend on GMGN
    assert check["safety_eligible"] is False and check["all_checks_pass"] is False
    no_trading_side_effects(app)
    h.close(app)


@pytest.mark.parametrize("mode", ["down", "timeout", "garbage", "rate_limited"])
def test_rugcheck_failures_leave_safety_unknown_never_pass(tmp_path, mode):
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn(wallet_stats=DISTINCT), rugcheck=h.FakeRugCheck(mode=mode))
    app.pumpapi = LiveFeed()
    h.run(app, [events(m) for m in baseline_msgs()])
    check = app.entry_check(M)
    assert app.candidates[M]["safety"]["verdict"] == "UNKNOWN" and check["all_checks_pass"] is False
    no_trading_side_effects(app)
    h.close(app)


# ---- Neon, Railway, Redis, object storage ----------------------------------------------------------------------------

def test_with_every_remote_service_down_paper_trading_continues_and_health_says_so(tmp_path, monkeypatch):
    from jinx.adapters.d3ad.adapter import D3adExecutionAdapter
    from jinx.adapters.datanomad.adapter import DataNomadResearchAdapter
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "p4-test-not-a-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "p4-test-not-a-secret")
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn())
    app.s = dataclasses.replace(app.s, database_url=f"postgresql://p4:p4@{CLOSED}/p4",
                                research_database_url=f"postgresql://p4:p4@{CLOSED}/p4",
                                redis_url=f"redis://{CLOSED}/0", s3_endpoint=f"http://{CLOSED}", s3_region="us-east-2")
    app._build_services()
    app.feed = LiveMako()
    app.d3ad = D3adExecutionAdapter(app.s.upstream_dir / "d3ad")
    app.datanomad = DataNomadResearchAdapter(app.s.upstream_dir / "datanomad")
    h.run(app, [events(m) for m in baseline_msgs()])
    set_mode(app.store, Mode.AUTO, by="phase4-test")

    async def day():
        repl = asyncio.create_task(app.replicator.run(interval_s=0.01, retry_max_s=0.02))
        entry = app.gateway.submit_entry(EntryIntent("p4-outage-entry", M, 10_000_000, origin="phase4-test"))
        await asyncio.sleep(0.2)
        await app._status_tick()                         # Redis snapshot, Railway flush, storage sample: all fail
        report = await app.end_of_day(1_792_600_000_000_000_000)   # ledger backup to object storage fails
        exit_ = app.gateway.submit_exit(ExitIntent("p4-outage-exit", entry.position_id))
        repl.cancel()
        return entry, exit_, report, await app.health()

    entry, exit_, report, health = h.loop_of(app).run_until_complete(day())
    assert entry.ok and exit_.ok                                       # the order path never waited on a service
    assert app.ledger.get(entry.position_id).status is Status.CLOSED
    assert app.wallet.reconcile().ok
    svc = health["services"]
    assert svc["postgres"]["failures"] >= 1 and svc["postgres"]["backlog"] > 0     # rows wait in the outbox
    assert svc["redis"]["failures"] >= 1
    assert svc["research_postgres"].get("failures", 0) >= 1 or svc["research_postgres"].get("last_error")
    assert report["backup"] is None or report["backup"].get("error") or app.backups.health().get("failures")
    h.loop_of(app).run_until_complete(app.redis_state.client.aclose())
    h.close(app)
