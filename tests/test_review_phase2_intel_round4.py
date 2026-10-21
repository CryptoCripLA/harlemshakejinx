"""Codex review of the Phase 2 event intel, round 4 (docs/audits/phase2-intel-codex-20261021-round4.md, b978908).
One reproduction per finding; each failed before its fix."""
from itertools import count

from jinx.core.events import EventKind, MarketEvent, Side
from jinx.intel.early_momentum import EarlyMomentum
from jinx.intel.migration import MAX_REPORTS, MigrationTracker
from jinx.intel.readings import Clock
from tests.pumpapi_msgs import K, T0_MS, VIRTUAL_TOKEN_OFFSET, amm_trade, buy, create, events, migrate

MIG_T = T0_MS + 60_000
POOL = "AMM1"
GAP_NS = (T0_MS + 2_500) * 1_000_000
_seq = count(7_000_000)


def feed(target, msgs, **kw):
    out = []
    for m in msgs:
        for ev in events(m, **kw):
            out += target.on_event(ev)
    return out


def pumpportal_creation(recv_ns):
    """PumpPortal's creation: no provider time, no real reserves, no creator buy."""
    return MarketEvent(kind=EventKind.TOKEN_CREATED, source="mako.pumpportal", mint="MintApump", signature="CPP",
                       trader="DEV", v_sol_lamports=30_000_000_000, v_tokens=1_073_000_000_000_000,
                       recv_ns=recv_ns, seq=next(_seq), raw={"pool": "pump"})


def consistent_buys(levels, start_s=4):
    """Buys whose token amounts are exactly what the curve reserves say left the curve: nothing unseen."""
    msgs, prev = [], 30.0
    for i, (payer, v_quote) in enumerate(zip("ABC", levels)):
        tokens = (K / prev - VIRTUAL_TOKEN_OFFSET) - (K / v_quote - VIRTUAL_TOKEN_OFFSET)
        msgs.append(buy(payer, start_s + i, v_quote=v_quote, tokens=round(tokens, 6)))
        prev = v_quote
    return msgs


# ---- R4-1 (HIGH): a receipt-only creation after a gap needs reserve continuity --------------------------------
def test_r4_1_a_late_receipt_only_creation_with_unseen_trades_is_blocked():
    em = EarlyMomentum()
    em.mark_feed_gap(GAP_NS, "pumpapi reconnected")
    em.on_event(pumpportal_creation((T0_MS + 3_000) * 1_000_000))     # happened at T0? JINX cannot tell
    # buys whose reserves show far more tokens left the curve than the observed buys took: trades were missed
    feed(em, [buy("A", 4, v_quote=31.0), buy("B", 5, v_quote=32.0), buy("C", 6, v_quote=35.0)])
    check = em.reserve_check("MintApump", True)
    assert check["eligible"] is False and "receipt time" in check["reason"]


def test_r4_1_superseded_by_r5_1_even_fully_accounted_reserves_do_not_prove_the_history():
    """Round 5 (R5-1): equal net reserves cannot exclude an unseen buy-and-sell round trip (a hidden peak), so a
    receipt-only creation after a gap stays unprovable however consistent its reserves look."""
    em = EarlyMomentum()
    em.mark_feed_gap(GAP_NS, "pumpapi reconnected")
    em.on_event(pumpportal_creation((T0_MS + 3_000) * 1_000_000))
    feed(em, consistent_buys([31.0, 32.0, 35.0]))
    assert em.reserve_check("MintApump", True)["eligible"] is False


def test_r4_1_the_migration_flow_of_such_a_token_is_not_complete():
    m = MigrationTracker()
    m.mark_feed_gap("reconnected", gap_ns=GAP_NS)
    m.on_event(pumpportal_creation((T0_MS + 3_000) * 1_000_000))
    feed(m, [buy("A", 4, v_quote=31.0, slot=110), migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    assert m.evidence("MintApump").holder_distribution["observed_trade_flow"]["complete"] is False


# ---- R4-2 (MEDIUM): the M3 reference is derived from the current record ------------------------------------
def pool_trade(t_ms, slot, payer):
    return amm_trade("buy", payer=payer, t_ms=t_ms, slot=slot, pool_id=POOL)


def test_r4_2_an_earlier_migration_time_moves_the_reference_earlier():
    m = MigrationTracker(confirmation_delay_ms=300)
    feed(m, [create(dev="DEV", slot=100)])
    m.record_report("MintApump", "gmgn.getTokenInfo", (MIG_T + 1_000) * 1_000_000, Clock.PROVIDER,
                    (MIG_T + 1_100) * 1_000_000, pool=POOL)
    feed(m, [pool_trade(MIG_T + 800, 600, "EARLY"), pool_trade(MIG_T + 1_400, 601, "LATE")])
    feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])                     # PumpAPI's exact time: T
    ev = m.evidence("MintApump")
    assert ev.first_verified_liquidity["fee_payer"] == "EARLY"
    assert ev.research["reference_trade"]["fee_payer"] == "EARLY"


def test_r4_2_a_later_migration_time_finds_the_next_reference_beyond_the_trade_cap():
    m = MigrationTracker(max_pool_trades=2, confirmation_delay_ms=300)
    feed(m, [create(dev="DEV", slot=100)])
    m.record_report("MintApump", "gmgn.trenches", MIG_T * 1_000_000, Clock.PROVIDER, (MIG_T + 50) * 1_000_000,
                    pool=POOL)
    feed(m, [pool_trade(MIG_T + 100, 600, "P1"), pool_trade(MIG_T + 200, 601, "P2"), pool_trade(MIG_T + 400, 602, "P3")])
    m.record_report("MintApump", "gmgn.getTokenInfo", (MIG_T + 500) * 1_000_000, Clock.PROVIDER,
                    (MIG_T + 600) * 1_000_000, pool=POOL, slot=599)             # with a slot: now the record's time
    feed(m, [pool_trade(MIG_T + 900, 603, "NEXT")])
    assert m.evidence("MintApump").research["reference_trade"]["fee_payer"] == "NEXT"


# ---- R4-3 (MEDIUM): making room never erases a source or the first observation -------------------------------
def test_r4_3_the_report_cap_keeps_every_source_and_the_first_observation():
    from jinx.adapters.mako.normalize import normalize as pumpportal_normalize
    m = MigrationTracker()
    feed(m, [create(dev="DEV", slot=100)])
    [pp] = pumpportal_normalize({"signature": "MIGTX", "mint": "MintApump", "txType": "migrate", "pool": "pump-amm"},
                                (MIG_T + 90) * 1_000_000, count(9_000_000).__next__)
    m.on_event(pp)
    feed(m, [migrate(pool_id=POOL, t_ms=MIG_T, slot=499, sig="MIGTX")], recv_delay_ms=100)
    for i in range(MAX_REPORTS + 2):
        m.record_report("MintApump", "gmgn.trenches", (MIG_T // 1000 - i) * 10**9, Clock.PROVIDER,
                        (MIG_T + 1_000 + i) * 1_000_000)
    record = m.evidence("MintApump").migration
    assert set(record.sources) == {"mako.pumpportal", "pumpapi.stream", "gmgn.trenches"}
    assert record.observed_ns == (MIG_T + 90) * 1_000_000
    assert m.summary("MintApump")["observed_ns"] == (MIG_T + 90) * 1_000_000
    assert record.reports_seen == 2 + MAX_REPORTS + 2                          # every report is still counted
