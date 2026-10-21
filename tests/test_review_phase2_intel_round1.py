"""Codex review of the Phase 2 event intel, round 1 (docs/audits/phase2-intel-codex-20261021-round1.md, 3462ce2).
One reproduction per finding; each failed before its fix."""
import asyncio
from itertools import count

import pytest

from jinx.adapters.pumpapi.feed import PumpApiFeed
from jinx.core.lifecycle import Evidence, Kind, State, TokenLifecycle
from jinx.intel import early_momentum as em_mod
from jinx.intel.early_momentum import EarlyMomentum, Status
from jinx.intel.migration import MigrationTracker
from jinx.intel.readings import Clock
from tests.pumpapi_msgs import T0_MS, amm_trade, buy, create, events, migrate, one, sell

MIG_T = T0_MS + 60_000
POOL = "AMM1"


def feed(target, msgs, **kw):
    out = []
    for m in msgs:
        for ev in events(m, **kw):
            out += target.on_event(ev)
    return out


def detected():
    em = EarlyMomentum()
    feed(em, [create(initial_buy=0.0, v_quote=30.0), buy("A", 1, v_quote=31.0), buy("B", 2, v_quote=32.0),
              buy("C", 3, v_quote=35.0)])
    return em


def peak(evidence):
    return next(p for p in evidence.predicates if p.name == em_mod.PEAK_GATE)


# ---- F1 (HIGH): a trade-feed gap makes the history behind it incomplete ---------------------------------------
def test_f1_a_feed_gap_blocks_every_history_that_spans_it():
    em = detected()
    assert em.reserve_check("MintApump", True)["eligible"] is True
    em.mark_feed_gap((T0_MS + 4_000) * 1_000_000, "pumpapi reconnected")
    check = em.reserve_check("MintApump", True)                       # the feed is live again: still not eligible
    assert check["eligible"] is False and "feed gap" in check["reason"]
    assert peak(em.evidence("MintApump")).status is Status.UNAVAILABLE
    # a token whose whole life comes after the gap is unaffected
    feed(em, [create(mint="LaterMint", initial_buy=0.0, v_quote=30.0, t_ms=T0_MS + 5_000),
              *(buy(p, 6 + i, v_quote=31.0 + i, mint="LaterMint") for i, p in enumerate("DEF"))])
    assert em.reserve_check("LaterMint", True)["eligible"] is True


def test_f1_the_pumpapi_feed_reports_every_connection_to_its_owner():
    connects = []

    class Ws:
        def __init__(self):
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionError("dropped")

        async def close(self):
            self.closed = True

    feed_ = PumpApiFeed(bus=None, connect=lambda url: Ws(), on_connect=connects.append, reconnect_initial_s=0.001,
                        reconnect_max_s=0.001, clock_ns=iter(range(10, 10_000)).__next__)

    async def main():
        task = asyncio.create_task(feed_.run())
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(main())
    assert len(connects) >= 2 and connects == sorted(connects)


# ---- F2 (HIGH): background lookups are bounded -----------------------------------------------------------------
def test_f2_background_lookups_are_bounded_and_deduplicated(tmp_path):
    from jinx import config
    from jinx.app import JinxApp

    class Stuck:
        running = True

        async def call(self, *a):
            await asyncio.Event().wait()                               # GMGN never answers

    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    app.gmgn = Stuck()
    app.MAX_BACKGROUND_TASKS = 5

    async def main():
        for i in range(50):
            app._request_holders(f"M{i}")
            app._request_holders(f"M{i}")                              # the same lookup twice: one task
        await asyncio.sleep(0)
        n = len(app._background)
        for t in list(app._background):
            t.cancel()
        return n
    assert asyncio.run(main()) == 5
    assert app.background_stats["shed"] == 90 and app.background_stats["deduplicated"] == 5
    app.store.close()


# ---- F3 (HIGH): per-token lifecycle lists are bounded ----------------------------------------------------------
def test_f3_lifecycle_history_conflicts_and_failures_are_bounded():
    lc = TokenLifecycle("M")
    lc.observe(Evidence(Kind.MIGRATION_CONFIRMED, "pumpapi.stream", 10, 10, "provider",
                        {"post_pool": "P", "exchange": "pump-amm"}))
    for i in range(2_000):
        lc.observe(Evidence(Kind.ON_CURVE, "pumpapi.stream", 20 + i, 20 + i, "provider", {}))
        lc.record_failure("gmgn", 20 + i, "timeout")
    assert len(lc.conflicts) <= 64 and lc.conflicts_total == 2_000
    assert len(lc.failures) <= 64 and lc.failures_total == 2_000
    assert len(lc.history) <= 64 and lc.state in (State.CONFLICTING, State.FAILED)


# ---- F4 (HIGH): verification needs complete, ordered evidence and is revoked by contrary evidence ---------------
def tracked():
    m = MigrationTracker()
    feed(m, [create(dev="DEV", slot=100), buy("W1", 1, v_quote=114.0, slot=480)])
    return m


def pool_trade(dt_ms, slot, **kw):
    return amm_trade("buy", payer=kw.pop("payer", "S1"), t_ms=MIG_T + dt_ms, slot=slot, pool_id=POOL, **kw)


def test_f4_a_pool_trade_before_the_migration_time_never_verifies_even_without_slots():
    m = tracked()
    m.record_report("MintApump", "gmgn.getTokenInfo", MIG_T * 1_000_000, Clock.PROVIDER,
                    (MIG_T + 1) * 1_000_000, pool=POOL)                          # no slot from GMGN
    early = pool_trade(-1_000, slot=600)
    assert feed(m, [early]) == [] and m.evidence("MintApump").verified is False
    [confirmed] = feed(m, [pool_trade(500, slot=601)])
    assert confirmed.verified and confirmed.first_verified_liquidity["ms_after_migration"] == pytest.approx(500)


def test_f4_a_trade_without_reserves_is_not_verified_liquidity():
    m = tracked()
    feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    bare = pool_trade(300, slot=500)
    bare["quoteInPool"] = bare["tokensInPool"] = None
    assert feed(m, [bare]) == [] and m.evidence("MintApump").verified is False
    assert "reserves" in m.evidence("MintApump").verification_status


def test_f4_a_migration_time_on_jinxs_receipt_clock_cannot_verify():
    m = tracked()
    # the PumpAPI adapter now refuses a migrate without its timestamp (round 2, R2-2); the tracker must still
    # handle a pool-naming report that has only JINX's receipt time
    from jinx.core.events import EventKind, MarketEvent
    m.on_event(MarketEvent(kind=EventKind.MIGRATION, source="pumpapi.stream", mint="MintApump", signature="M0",
                           slot=499, recv_ns=(MIG_T + 50) * 1_000_000, seq=999_999,
                           raw={"pool": "pump-amm", "poolId": POOL, "leg": f"pool:{POOL}"}))
    feed(m, [pool_trade(300, slot=500)])
    ev = m.evidence("MintApump")
    assert ev.verified is False and m.lifecycle("MintApump").state is State.MIGRATION_OBSERVED
    assert "provider" in ev.verification_status


def test_f4_a_later_slot_revokes_a_verification_and_the_next_valid_trade_restores_it():
    m = tracked()
    m.record_report("MintApump", "gmgn.getTokenInfo", (MIG_T - 2_000) * 1_000_000, Clock.PROVIDER,
                    (MIG_T + 1) * 1_000_000, pool=POOL)                          # whole-second time, no slot
    [first] = feed(m, [pool_trade(-500, slot=498)])
    assert first.verified is True
    revoked = feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])           # the transaction itself: slot 499
    assert revoked and revoked[-1].verified is False and m.evidence("MintApump").verified is False
    [restored] = feed(m, [pool_trade(400, slot=500, payer="S2")])
    assert restored.verified and restored.first_verified_liquidity["fee_payer"] == "S2"


# ---- F5 (MEDIUM): shared state is the tracker's current evidence ----------------------------------------------
def test_f5_the_confirmation_is_published_after_the_market_transition():
    heard = []
    m = MigrationTracker(notify=heard.append)
    feed(m, [create(dev="DEV", slot=100), buy("W1", 1, v_quote=114.0, slot=480),
             migrate(pool_id=POOL, t_ms=MIG_T, slot=499), pool_trade(300, slot=500)])
    assert heard[-1].verified and heard[-1].state == "POST_MIGRATION_MARKET"


def test_f5_the_engine_reads_the_trackers_current_evidence(tmp_path):
    from jinx import config
    from jinx.app import JinxApp
    from tests.test_intel_migration import HOLDERS
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    later, earlier = pool_trade(900, slot=502, payer="B"), pool_trade(300, slot=500, payer="A")
    for msg in [create(dev="DEV", slot=100), buy("W1", 1, v_quote=114.0, slot=480),
                migrate(pool_id=POOL, t_ms=MIG_T, slot=499), later, earlier]:
        for ev in events(msg):
            app.migration.on_event(ev)
    app.migration.record_holders("MintApump", HOLDERS, "gmgn.getTokenTopHolders", (MIG_T + 5_000) * 1_000_000)
    view = app.entry_check("MintApump")["migration"]
    assert view["state"] == "POST_MIGRATION_MARKET" and view["first_verified_liquidity"]["fee_payer"] == "A"
    assert view["holder_concentration"]["verified"]["source"] == "gmgn.getTokenTopHolders"
    latest = app.intel_health()["migration"]["latest"][-1]
    assert latest["mint"] == "MintApump" and latest["verified"] is True
    app.store.close()


# ---- F6 (MEDIUM): conflicting pools are adjudicated, not decided by arrival order -------------------------------
@pytest.mark.parametrize("order", ["gmgn_first", "pumpapi_first"])
def test_f6_two_reports_naming_different_pools_never_verify_either(order):
    m = tracked()
    gmgn = lambda: m.record_report("MintApump", "gmgn.getTokenInfo", MIG_T * 1_000_000, Clock.PROVIDER,
                                   (MIG_T + 1) * 1_000_000, pool="OTHERPOOL")
    stream = lambda: feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    (gmgn, stream)[order == "pumpapi_first"]()
    (stream, gmgn)[order == "pumpapi_first"]()
    feed(m, [pool_trade(300, slot=500)])
    ev = m.evidence("MintApump")
    assert ev.verified is False and "conflicting pools" in ev.verification_status
    assert set(ev.migration.pools_named) == {POOL, "OTHERPOOL"}


def test_f6_a_conflicting_pool_after_confirmation_makes_the_lifecycle_conflicting():
    m = tracked()
    feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499), pool_trade(300, slot=500)])
    m.record_report("MintApump", "gmgn.getTokenInfo", MIG_T * 1_000_000, Clock.PROVIDER, (MIG_T + 9) * 1_000_000,
                    pool="OTHERPOOL")
    assert m.lifecycle("MintApump").state is State.CONFLICTING
    assert m.evidence("MintApump").verified is False


# ---- F7 (MEDIUM): duplicates never inflate, reordering never changes the evidence --------------------------------
def test_f7_duplicate_deliveries_do_not_inflate_migration_evidence():
    m = MigrationTracker()
    msgs = [create(dev="DEV", slot=100), buy("W1", 1, v_quote=114.0, slot=480),
            migrate(pool_id=POOL, t_ms=MIG_T, slot=499), pool_trade(300, slot=500),
            amm_trade("sell", payer="DEV", t_ms=MIG_T + 800, slot=501, pool_id=POOL, sol=1.0)]
    seq = count(1)
    for msg in msgs:
        evs = events(msg)
        for ev in evs + evs:                                            # every event delivered twice
            m.on_event(ev)
    ev = m.evidence("MintApump")
    assert ev.developer_selling["after"]["sells"] == 1
    assert len(ev.competing_early_transactions) == 2


def test_f7_a_creators_trade_from_before_the_migration_delivered_after_it_counts_as_before():
    """F7's rule (late delivery converges on the same evidence), applied to what the storage repair still tallies per
    wallet: the creator. Other wallets' pre-migration holdings come from GMGN's verified balances (2026-10-21)."""
    m = MigrationTracker()
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499),
             sell("DEV", 1, v_quote=60.0, slot=110, sol=0.7)])                   # a historical sell, delivered late
    dev = m.evidence("MintApump").developer_selling
    assert dev["before"]["sells"] == 1 and dev["after"]["sells"] == 0


# ---- F8 (MEDIUM): the largest sells survive the retention caps ------------------------------------------------
def test_f8_a_sell_after_the_pool_trade_cap_is_still_ranked():
    m = MigrationTracker(max_pool_trades=2)
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499),
             pool_trade(100, slot=500), pool_trade(200, slot=500, payer="S2"),
             amm_trade("sell", payer="DEV", t_ms=MIG_T + 300, slot=501, pool_id=POOL, sol=2.0)])
    sells = m.evidence("MintApump").sells_around_graduation
    assert [s["fee_payer"] for s in sells] == ["DEV"]


def test_f7_graduation_counts_a_duplicated_reading_once():
    from jinx.intel.graduation import GraduationTracker
    g = GraduationTracker()
    ev = one(buy("A", 1, v_quote=40.0))
    g.on_event(ev)
    assert g.on_event(ev) == [] and g.evidence("MintApump").readings == 1 and g.stats["duplicates"] == 1
