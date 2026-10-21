"""Codex review of the Phase 2 event intel, round 3 (docs/audits/phase2-intel-codex-20261021-round3.md, ae93665).
One reproduction per finding; each failed before its fix."""
from itertools import count

from jinx.core.lifecycle import State
from jinx.intel.early_momentum import EarlyMomentum
from jinx.intel.migration import MAX_REPORTS, MigrationTracker
from jinx.intel.readings import Clock
from jinx.intel.replay import replay_events
from tests.pumpapi_msgs import T0_MS, amm_trade, buy, create, events, migrate

MIG_T = T0_MS + 60_000
POOL = "AMM1"


def feed(target, msgs, **kw):
    out = []
    for m in msgs:
        for ev in events(m, **kw):
            out += target.on_event(ev)
    return out


def pool_trade(dt_ms, slot, pool_id=POOL, **kw):
    return amm_trade("buy", payer=kw.pop("payer", "S1"), t_ms=MIG_T + dt_ms, slot=slot, pool_id=pool_id, **kw)


# ---- R3-1 (HIGH): a migration delivered before the creation still takes the token out of this path -------------
def test_r3_1_a_migration_seen_first_keeps_the_token_out_of_early_momentum():
    em = EarlyMomentum()
    feed(em, [migrate(pool_id=POOL, t_ms=T0_MS + 60_000, slot=499)])
    feed(em, [create(initial_buy=0.0, v_quote=30.0), buy("A", 1, v_quote=31.0), buy("B", 2, v_quote=32.0),
              buy("C", 3, v_quote=35.0)])
    check = em.reserve_check("MintApump", True)
    assert check["eligible"] is False and "migrated" in check["reason"]


def test_r3_1_entry_check_never_passes_a_token_the_lifecycle_has_seen_migrate(tmp_path):
    from jinx import config
    from jinx.app import JinxApp
    from jinx.intel.early_momentum import Funding
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    app._trade_feed_live = lambda: True
    for w, f in (("A", "F1"), ("B", "F2"), ("C", "F3")):
        app.early.record_funding(Funding(w, f, "test", 1))
    for msg in [create(initial_buy=0.0, v_quote=30.0), buy("A", 1, v_quote=31.0), buy("B", 2, v_quote=32.0),
                buy("C", 3, v_quote=35.0)]:
        for ev in events(msg):
            app.early.on_event(ev)
            app.migration.on_event(ev)
    app.candidates["MintApump"] = {"safety": {"eligible": True}}
    assert app.entry_check("MintApump")["all_checks_pass"] is True           # the control: every check passes
    for ev in events(migrate(pool_id=POOL, t_ms=MIG_T, slot=499)):
        app.migration.on_event(ev)                                           # only the lifecycle hears it
    check = app.entry_check("MintApump")
    assert check["all_checks_pass"] is False and check["lifecycle_state"] == "MIGRATION_OBSERVED"
    app.store.close()


# ---- R3-2 (HIGH): an event that happened before the gap marks its token, whenever it is received ----------------
def test_r3_2_a_creation_from_before_the_gap_received_after_it_is_behind_the_gap():
    em = EarlyMomentum()
    em.mark_feed_gap((T0_MS + 2_500) * 1_000_000, "pumpapi reconnected")
    feed(em, [create(initial_buy=0.0, v_quote=30.0)], recv_delay_ms=3_000)   # happened at T0, received at T0 + 3 s
    feed(em, [buy("A", 4, v_quote=31.0), buy("B", 5, v_quote=32.0), buy("C", 6, v_quote=35.0)])
    check = em.reserve_check("MintApump", True)
    assert check["eligible"] is False and "feed gap" in check["reason"]


def test_r3_2_the_same_for_the_migration_flow():
    m = MigrationTracker()
    m.mark_feed_gap("reconnected", gap_ns=(T0_MS + 2_500) * 1_000_000)
    feed(m, [create(dev="DEV", slot=100)], recv_delay_ms=3_000)
    feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    assert m.evidence("MintApump").holder_distribution["observed_trade_flow"]["complete"] is False


# ---- R3-3 (MEDIUM): the report cap never hides new facts ----------------------------------------------------
def test_r3_3_a_conflicting_pool_after_the_report_cap_still_counts():
    m = MigrationTracker()
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499), pool_trade(300, slot=500)])
    for i in range(MAX_REPORTS):
        m.record_report("MintApump", "gmgn.trenches", (MIG_T // 1000 - i) * 10**9, Clock.PROVIDER,
                        (MIG_T + 1_000 + i) * 1_000_000)
    m.record_report("MintApump", "gmgn.getTokenInfo", MIG_T * 1_000_000, Clock.PROVIDER, (MIG_T + 9_000) * 1_000_000,
                    pool="OTHERPOOL")
    ev = m.evidence("MintApump")
    assert "OTHERPOOL" in ev.migration.pools_named and ev.verified is False
    assert m.lifecycle("MintApump").state is State.CONFLICTING


def test_r3_3_the_pool_report_after_the_cap_still_confirms():
    m = MigrationTracker()
    feed(m, [create(dev="DEV", slot=100)])
    for i in range(MAX_REPORTS):
        m.record_report("MintApump", "gmgn.trenches", (MIG_T // 1000 - i) * 10**9, Clock.PROVIDER,
                        (MIG_T + 1_000 + i) * 1_000_000)
    feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499), pool_trade(300, slot=500)])
    assert m.evidence("MintApump").verified is True


# ---- R3-4 (MEDIUM): the M3 paper reference survives the trade retention cap -----------------------------------
def test_r3_4_the_confirmation_delay_reference_after_the_cap_is_kept():
    m = MigrationTracker(max_pool_trades=2, confirmation_delay_ms=300)
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499),
             pool_trade(100, slot=500, payer="P1"), pool_trade(200, slot=500, payer="P2"),
             pool_trade(400, slot=501, payer="REF")])
    assert m.evidence("MintApump").research["reference_trade"]["fee_payer"] == "REF"


# ---- R3-5, R3-6 (MEDIUM): the replay report survives eviction and measures on the final record --------------------
def test_r3_5_the_replay_report_survives_an_evicted_migration():
    msgs = [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499),
            create(mint="Later", dev="DEV2", slot=900, t_ms=MIG_T + 3 * 3_600_000)]
    report = replay_events([events(m) for m in msgs]).report()
    assert report["migration"]["migrations"] == 1 and report["migration"]["evicted"] == 1


def test_r3_6_the_replay_signal_delay_is_measured_on_the_final_record():
    from jinx.adapters.mako.normalize import normalize as pumpportal_normalize
    seq = count(5_000_000)
    pp = pumpportal_normalize({"signature": "MIGTX", "mint": "MintApump", "txType": "migrate", "pool": "pump-amm"},
                              (MIG_T + 90) * 1_000_000, seq.__next__)
    deliveries = [events(create(dev="DEV", slot=100)), pp,
                  events(migrate(pool_id=POOL, t_ms=MIG_T, slot=499, sig="MIGTX"), recv_delay_ms=100),
                  events(pool_trade(300, slot=500))]
    delay = replay_events(deliveries).report()["migration"]["signal_delay_ms"]
    assert delay["n"] == 1 and 90 <= delay["p50"] < 95                     # provider time T to first receipt T + 90
