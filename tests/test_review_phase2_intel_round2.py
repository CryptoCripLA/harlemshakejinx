"""Codex review of the Phase 2 event intel, round 2 (docs/audits/phase2-intel-codex-20261021-round2.md, 6b2ec43).
One reproduction per finding; each failed before its fix."""
from itertools import count

import pytest

from jinx.adapters.pumpapi.normalize import MalformedEvent
from jinx.core.events import EventKind, MarketEvent
from jinx.core.lifecycle import Evidence, Kind, State, TokenLifecycle
from jinx.intel.early_momentum import EarlyMomentum
from jinx.intel.migration import MigrationTracker
from jinx.intel.readings import Clock
from jinx.intel.replay import replay_events
from tests.pumpapi_msgs import T0_MS, amm_trade, buy, create, events, migrate, one, sell

MIG_T = T0_MS + 60_000
POOL = "AMM1"
_seq = count(1_000_000)


def feed(target, msgs, **kw):
    out = []
    for m in msgs:
        for ev in events(m, **kw):
            out += target.on_event(ev)
    return out


def pool_trade(dt_ms, slot, pool_id=POOL, **kw):
    return amm_trade(kw.pop("action", "buy"), payer=kw.pop("payer", "S1"), t_ms=MIG_T + dt_ms, slot=slot,
                     pool_id=pool_id, **kw)


# ---- R2-1 (HIGH): an event received before a feed gap, processed after it, still marks its token ----------------
def test_r2_1_a_creation_queued_before_the_gap_is_still_behind_it():
    em = EarlyMomentum()
    gap_ns = (T0_MS + 2_500) * 1_000_000
    em.mark_feed_gap(gap_ns, "pumpapi reconnected")              # the callback runs before the queue drains
    feed(em, [create(initial_buy=0.0, v_quote=30.0)])             # received at T0 + 50 ms: before the gap
    feed(em, [buy("A", 3, v_quote=31.0), buy("B", 4, v_quote=32.0), buy("C", 5, v_quote=35.0)])
    check = em.reserve_check("MintApump", True)
    assert check["eligible"] is False and "feed gap" in check["reason"]
    # a token whose every event was received after the gap is unaffected
    feed(em, [create(mint="After", initial_buy=0.0, v_quote=30.0, t_ms=T0_MS + 3_000),
              *(buy(p, 4 + i, v_quote=31.0 + i, mint="After") for i, p in enumerate("DEF"))])
    assert em.reserve_check("After", True)["eligible"] is True


def test_r2_1_migration_flow_received_before_the_gap_is_incomplete():
    m = MigrationTracker()
    m.mark_feed_gap("reconnected", gap_ns=(T0_MS + 2_500) * 1_000_000)
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    assert m.evidence("MintApump").holder_distribution["observed_trade_flow"]["complete"] is False


# ---- R2-2 (MEDIUM): a better report from the same source enriches the record -----------------------------------
def test_r2_2_a_pumpapi_migrate_without_its_timestamp_is_refused_by_the_adapter():
    msg = migrate(pool_id=POOL, t_ms=MIG_T, slot=499)
    del msg["timestamp"]
    with pytest.raises(MalformedEvent):
        events(msg, recv_ns=1)


def migration_event(timestamp_ms):
    raw = {"signature": "MIGTX", "action": "migrate", "pool": "pump-amm", "poolId": POOL, "mint": "MintApump",
           "block": 499, "leg": f"pool:{POOL}"}
    if timestamp_ms is not None:
        raw["timestamp"] = timestamp_ms
    return MarketEvent(kind=EventKind.MIGRATION, source="pumpapi.stream", mint="MintApump", signature="MIGTX",
                       slot=499, recv_ns=(MIG_T + 40) * 1_000_000, seq=next(_seq), raw=raw)


def test_r2_2_the_same_sources_report_with_a_provider_time_replaces_a_receipt_time():
    m = MigrationTracker()
    feed(m, [create(dev="DEV", slot=100)])
    m.on_event(migration_event(None))                              # receipt clock only
    feed(m, [pool_trade(300, slot=500)])
    assert m.evidence("MintApump").verified is False
    m.on_event(migration_event(MIG_T))                             # the same report, now with PumpAPI's time
    ev = m.evidence("MintApump")
    assert ev.verified is True and (ev.migration.event_ns, ev.migration.clock) == (MIG_T * 1_000_000, "provider")


# ---- R2-3 (MEDIUM): the pool-trade cap never hides verification evidence ---------------------------------------
def test_r2_3_a_qualifying_trade_after_the_cap_still_verifies():
    m = MigrationTracker(max_pool_trades=2)
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    for i in range(2):
        bare = pool_trade(100 + i, slot=500, payer=f"N{i}")
        bare["tokensInPool"] = None
        feed(m, [bare])
    feed(m, [pool_trade(300, slot=501, payer="FULL")])
    assert m.evidence("MintApump").verified is True
    assert m.evidence("MintApump").first_verified_liquidity["fee_payer"] == "FULL"


def test_r2_3_an_earlier_trade_delivered_after_the_cap_corrects_the_first_liquidity():
    m = MigrationTracker(max_pool_trades=2)
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499),
             pool_trade(500, slot=502, payer="L1"), pool_trade(600, slot=502, payer="L2"),
             pool_trade(700, slot=503, payer="L3"), pool_trade(100, slot=500, payer="EARLY")])
    assert m.evidence("MintApump").first_verified_liquidity["fee_payer"] == "EARLY"


# ---- R2-4 (MEDIUM): a conflicting pool is a conflict whatever its timestamp precision --------------------------
def test_r2_4_an_earlier_whole_second_report_of_another_pool_makes_the_lifecycle_conflicting():
    lc = TokenLifecycle("M")
    lc.observe(Evidence(Kind.MIGRATION_CONFIRMED, "pumpapi.stream", 20, 10_300, "provider",
                        {"post_pool": "A", "exchange": "pump-amm"}))
    lc.observe(Evidence(Kind.MIGRATION_CONFIRMED, "gmgn.getTokenInfo", 30, 10_000, "provider",
                        {"post_pool": "B", "exchange": "pump_amm"}))
    assert lc.state is State.CONFLICTING and lc.conflicts_total == 1


def test_r2_4_the_same_case_through_the_migration_tracker():
    m = MigrationTracker()
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T + 300, slot=499),
             pool_trade(600, slot=500)])
    m.record_report("MintApump", "gmgn.getTokenInfo", MIG_T * 1_000_000, Clock.PROVIDER, (MIG_T + 5_000) * 1_000_000,
                    pool="OTHERPOOL")
    assert m.lifecycle("MintApump").state is State.CONFLICTING and m.evidence("MintApump").verified is False


# ---- R2-5 (MEDIUM): accounting and annotations converge -----------------------------------------------------
def test_r2_5_a_duplicate_older_than_the_dedup_horizon_is_never_counted_twice():
    m = MigrationTracker(max_curve_trades=4, max_pool_trades=4)     # small identity window for the test
    creator_sell = sell("DEV", 1, v_quote=31.0, slot=101, sol=0.5)
    feed(m, [create(dev="DEV", slot=100), creator_sell])
    feed(m, [buy(f"W{i}", 2 + i * 0.01, v_quote=31.0, slot=102) for i in range(40)])
    feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    for ev in events(creator_sell):                                 # the old sell delivered again
        m.on_event(ev)
    ev = m.evidence("MintApump")
    assert ev.developer_selling["before"]["sells"] == 1
    assert ev.holder_distribution["observed_trade_flow"]["complete"] is False   # it cannot be told apart: flagged


def test_r2_5_the_creator_flag_on_a_ranked_sell_follows_the_creation_whatever_the_order():
    m = MigrationTracker()
    feed(m, [sell("DEV", 1, v_quote=31.0, slot=101, sol=0.5), migrate(pool_id=POOL, t_ms=MIG_T, slot=499),
             create(dev="DEV", slot=100)])
    [row] = m.evidence("MintApump").sells_around_graduation
    assert row["seller_is_creator"] is True


# ---- R2-6 (MEDIUM): the replay report counts the final verification ---------------------------------------------
def test_r2_6_a_revoked_verification_is_not_reported_as_verified():
    msgs = [create(dev="DEV", slot=100), buy("W1", 1, v_quote=114.0, slot=480),
            migrate(pool_id=POOL, t_ms=MIG_T, slot=499, sig="MIGA"), pool_trade(300, slot=500),
            migrate(pool_id="OTHERPOOL", t_ms=MIG_T, slot=499, sig="MIGB")]
    run = replay_events([events(m) for m in msgs])
    assert run.migration.evidence("MintApump").verified is False
    assert run.report()["migration"]["verified"] == 0
