"""Codex review of the Phase 2 event intel, round 5 (docs/audits/phase2-intel-codex-20261021-round5.md, be5eff9).
One reproduction per finding; each failed before its fix."""
from jinx.intel.early_momentum import EarlyMomentum
from jinx.intel.migration import MigrationTracker
from tests.pumpapi_msgs import T0_MS, amm_trade, create, events, migrate
from tests.test_review_phase2_intel_round4 import GAP_NS, consistent_buys, pumpportal_creation

MIG_T = T0_MS + 60_000
POOL = "AMM1"


def feed(target, msgs, **kw):
    for m in msgs:
        for ev in events(m, **kw):
            target.on_event(ev)


# ---- R5-1 (HIGH): a receipt-only creation after a gap is unprovable, whatever its reserves show ------------------
def test_r5_1_a_hidden_round_trip_cannot_be_excluded_so_entry_is_blocked():
    em = EarlyMomentum()
    em.mark_feed_gap(GAP_NS, "pumpapi reconnected")
    em.on_event(pumpportal_creation((T0_MS + 3_000) * 1_000_000))
    feed(em, consistent_buys([31.0, 32.0, 35.0]))      # identical with or without an unseen 30 -> 50 -> 30 round trip
    check = em.reserve_check("MintApump", True)
    assert check["eligible"] is False and "receipt time" in check["reason"]


def test_r5_1_its_migration_flow_is_never_reported_complete():
    m = MigrationTracker()
    m.mark_feed_gap("reconnected", gap_ns=GAP_NS)
    m.on_event(pumpportal_creation((T0_MS + 3_000) * 1_000_000))
    feed(m, consistent_buys([31.0, 32.0, 35.0]) + [migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    assert m.evidence("MintApump").holder_distribution["observed_trade_flow"]["complete"] is False


# ---- R5-2 (MEDIUM): the M3 reference never disappears silently ---------------------------------------------
def test_r5_2_a_reference_past_thousands_of_earlier_trades_is_still_found():
    m = MigrationTracker(confirmation_delay_ms=5_000)
    feed(m, [create(dev="DEV", slot=100), migrate(pool_id=POOL, t_ms=MIG_T, slot=499)])
    for i in range(4_200):
        feed(m, [amm_trade("buy", payer=f"P{i}", t_ms=MIG_T + i, slot=500 + i, pool_id=POOL)])
    feed(m, [amm_trade("buy", payer="REF", t_ms=MIG_T + 6_000, slot=9_000, pool_id=POOL)])
    research = m.evidence("MintApump").research
    assert research["reference_trade"]["fee_payer"] == "REF" and research["reference_status"] == "found"
