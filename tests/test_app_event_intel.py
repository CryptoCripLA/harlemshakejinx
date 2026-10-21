"""Phase 2 event intel inside the running engine (owner integration order 2026-10-21, items 1, 9).

Real captured events go through JinxApp's own bus (dedup, durable write, lanes) into Early Momentum, graduation and
migration, and their evidence lands in the app's shared state (`candidates`, `migrations`, `entry_check`,
`intel_health`). GMGN is replaced by a fake that answers with the real response shapes recorded on 2026-10-21
(tests/fixtures/gmgn_trenches_holders_sample_20261021.json). Paper mode: nothing here may reach an order."""
import asyncio
import json
import time
from itertools import count
from pathlib import Path

from jinx import config
from jinx.adapters.mako.normalize import normalize as pumpportal_normalize
from jinx.app import JinxApp
from jinx.core.lifecycle import State
from jinx.sim import capture_events, load_capture
from tests.pumpapi_msgs import buy, create, events

FIX = Path(__file__).parent / "fixtures"
CAPTURE = load_capture(FIX / "pumpapi-live-20261021.jsonl.gz")
MIGRATIONS = load_capture(FIX / "pumpapi-migrations-live-20261021.jsonl.gz")
GMGN = json.loads((FIX / "gmgn_trenches_holders_sample_20261021.json").read_text())


class FakeGmgn:
    running = True

    def __init__(self, token_info=None, holders=None):
        self.calls = []
        self.token_info, self.holders = token_info or {}, holders or {}

    async def call(self, method, *args):
        self.calls.append((method, args[1] if len(args) > 1 else None))
        if method == "getTrenches":
            return {"near_completion": GMGN["trenches"]["near_completion"], "completed": GMGN["trenches"]["completed"]}
        if method == "getTokenInfo":
            return self.token_info.get(args[1], {})
        if method == "getTokenTopHolders":
            return {"list": self.holders.get(args[1], [])}
        if method == "getWalletStats":
            return []
        return None


def make_app(tmp_path, gmgn=None):
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    app.SCREEN_RETRY_S = 0.01
    if gmgn is not None:
        app.gmgn = gmgn
    return app


def publish(app, deliveries):
    async def main():
        runner = asyncio.create_task(app.bus.run())
        for evs in deliveries:
            for ev in evs or ():
                await app.bus.publish(ev)
        await app.bus.drain()
        for _ in range(200):
            if not app._background:
                break
            await asyncio.sleep(0.01)
        runner.cancel()
    asyncio.run(main())


def close(app):
    app.store.close()


def test_real_events_reach_all_three_modules_through_the_engines_own_bus(tmp_path):
    app = make_app(tmp_path)
    publish(app, capture_events(CAPTURE, source="pumpapi"))
    assert app.bus.stats["handler_errors"] == {}
    curve_mints = {r["raw"]["mint"] for r in CAPTURE if r["raw"]["pool"] == "pump"}
    assert len(app.candidates) == 38 == app.early.stats["detections"]            # same as the offline replay
    assert app.graduation.tracked_mints() == len(curve_mints) == 118
    states = {app.migration.lifecycle(m).state for m in curve_mints}
    assert states <= {State.NEWLY_DETECTED, State.ON_BONDING_CURVE}               # no migration inferred
    intel = app.intel_health()
    assert intel["graduation"]["tracked"] == 118 and intel["migration"]["migrations"] == 0
    assert intel["early_momentum"]["candidates"] == 38
    close(app)


def test_a_detection_in_shared_state_carries_every_module_and_never_permits_an_order(tmp_path):
    app = make_app(tmp_path)
    publish(app, capture_events(CAPTURE, source="pumpapi"))
    mint, entry = next(iter(app.candidates.items()))
    check = app.entry_check(mint)
    assert check["lifecycle_state"] in ("NEWLY_DETECTED", "ON_BONDING_CURVE")
    assert check["graduation"]["progress"] is not None or check["graduation"]["standard_curve"] is False
    assert check["all_checks_pass"] is False                                    # no safety verdict in this test
    assert not list(app.ledger.active())                                         # paper mode: nothing was bought
    close(app)


def test_live_migrations_and_a_venue_only_report_merge_in_the_running_engine(tmp_path):
    """Owner item 2 end to end: PumpPortal's venue-only report arrives FIRST for one migration; the PumpAPI report
    with the pool still reaches the module through the engine's bus and the migration is verified."""
    holders = {GMGN["token_info_completed"]["address"]: GMGN["top_holders_completed"]}
    app = make_app(tmp_path, FakeGmgn(holders=holders))
    first = next(r["raw"] for r in MIGRATIONS if r["raw"]["action"] == "migrate")
    seq = count(10_000_000)
    pp = pumpportal_normalize({"signature": first["signature"], "mint": first["mint"], "txType": "migrate",
                               "pool": "pump-amm"}, (first["timestamp"] - 10) * 1_000_000, seq.__next__)
    publish(app, [pp] + capture_events(MIGRATIONS, source="pumpapi"))
    migrated = [r["raw"]["mint"] for r in MIGRATIONS if r["raw"]["action"] == "migrate"]
    assert app.bus.stats["duplicates"] == 0 and app.bus.stats["handler_errors"] == {}
    for mint in migrated:
        ev = app.migration.evidence(mint)
        assert ev.verified and app.migration.lifecycle(mint).state is State.POST_MIGRATION_MARKET
    merged = app.migration.evidence(first["mint"]).migration
    assert merged.sources == ("mako.pumpportal", "pumpapi.stream") and merged.pool_id == first["poolId"]
    assert (merged.event_ns, str(merged.clock)) == (first["timestamp"] * 1_000_000, "provider")
    assert set(app.migrations) == set(migrated)
    assert ("getTokenTopHolders", first["mint"]) in app.gmgn.calls                # verified balances requested
    assert app.intel_health()["migration"]["verified"] == len(migrated)
    close(app)


def test_the_trenches_poll_feeds_provider_status_and_closes_a_missing_pool(tmp_path):
    near = GMGN["trenches"]["near_completion"][0]
    done = GMGN["trenches"]["completed"][0]
    info = GMGN["token_info_completed"]
    assert info["address"] == done["address"]
    app = make_app(tmp_path, FakeGmgn(token_info={done["address"]: info}))
    seq, now = count(20_000_000), time.time_ns()       # receipt times on the same clock as the GMGN reads
    pp = pumpportal_normalize({"signature": "PPONLY", "mint": done["address"], "txType": "migrate",
                               "pool": "pump-amm"}, now + 3, seq.__next__)
    publish(app, [events(buy("A", 1, mint=near["address"]), recv_ns=now + 1),
                  events(buy("B", 2, mint=done["address"]), recv_ns=now + 2), pp])

    async def poll():
        await app.poll_trenches_once()
        for _ in range(200):
            if not app._background:
                break
            await asyncio.sleep(0.01)
    asyncio.run(poll())
    assert app.graduation.evidence(near["address"]).near_threshold is True
    assert app.migration.lifecycle(near["address"]).state is State.APPROACHING_GRADUATION
    record = app.migration.evidence(done["address"]).migration
    assert record.pool_id == info["migrated_pool"]                                # GMGN closed the gap
    assert set(record.sources) == {"mako.pumpportal", "gmgn.trenches", "gmgn.getTokenInfo"}
    assert record.clock == "provider"
    untracked = {i["address"] for i in GMGN["trenches"]["near_completion"][1:]} - {done["address"]}
    assert not any(app.migration.tracks(m) for m in untracked)                    # listings never create state
    close(app)


def test_detection_events_are_measured_on_the_engines_fast_lane(tmp_path):
    app = make_app(tmp_path)
    publish(app, capture_events(CAPTURE, source="pumpapi"))
    lat = app.intel_health()["fast_lane_admission_to_handled_ms"]
    assert lat["n"] > 1000 and lat["p50"] is not None and lat["p99"] >= lat["p50"]
    close(app)


def test_creation_then_three_wallets_through_the_engine_reaches_screening_once(tmp_path):
    app = make_app(tmp_path)
    publish(app, [events(m) for m in [create(initial_buy=0.0, v_quote=30.0), buy("A", 1), buy("B", 2),
                                      buy("C", 3), buy("D", 4)]])
    [(mint, entry)] = app.candidates.items()
    assert entry["trade_permitted"] is False and entry["evidence"]["payload"]["fee_payers"] == ["A", "B", "C"]
    close(app)


def test_intel_health_reports_each_modules_own_processing_and_the_detection_delay(tmp_path):
    """Owner delivery item: measured processing delays, per module, in the running engine (local processing only,
    not order execution)."""
    app = make_app(tmp_path)
    deliveries = capture_events(CAPTURE, source="pumpapi")
    publish(app, deliveries)
    intel = app.intel_health()
    events_seen = sum(len(evs) for evs in deliveries if evs)
    for name in ("core_state", "early_momentum", "graduation", "migration"):
        t = intel["handler_processing_us"][name]
        assert t["n"] == events_seen and 0 < t["p50"] <= t["p99"]
    delay = intel["early_momentum"]["detection_delay_ms"]
    assert delay["n"] == 38 and delay["p50"] > 0
    assert "signal_delay_ms" in intel["migration"]
    close(app)
