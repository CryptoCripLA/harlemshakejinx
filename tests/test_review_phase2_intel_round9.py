"""Codex review of the Phase 2 event intel, round 9 (docs/audits/phase2-intel-codex-20261021-round9.md, 449b917).
The reproduction failed before its fix."""
from jinx.intel.migration import MigrationTracker
from tests.pumpapi_msgs import T0_MS, amm_trade, create, events, migrate, sell

POOL = "AMM1"


def run(**kw):
    m = MigrationTracker(**kw)
    msgs = [create(initial_buy=0.0, v_quote=30.0), migrate(pool_id=POOL, t_ms=T0_MS + 60_000, slot=499)]
    msgs += [amm_trade("buy", payer=f"P{i}", t_ms=T0_MS + 61_000 + i, slot=501 + i, pool_id=POOL) for i in range(513)]
    msgs += [amm_trade("buy", payer="TRUE_FIRST", t_ms=T0_MS + 60_500, slot=500, pool_id=POOL)]
    for msg in msgs:
        for ev in events(msg):
            m.on_event(ev)
    return m


def test_r9_1_a_new_trade_behind_the_dedup_horizon_still_counts_as_ordering_evidence():
    ev = run().evidence("MintApump")
    assert ev.verified and ev.first_verified_liquidity["fee_payer"] == "TRUE_FIRST"
    assert ev.competing_early_transactions[0]["fee_payer"] == "TRUE_FIRST"
    assert ev.holder_distribution["observed_trade_flow"]["complete"] is False      # its flow is not counted


def test_r9_1_the_m3_reference_sees_it_too():
    research = run(confirmation_delay_ms=100).evidence("MintApump").research
    assert research["reference_status"] == "found" and research["reference_trade"]["fee_payer"] == "TRUE_FIRST"


def test_r9_1_a_resend_behind_the_horizon_is_never_listed_twice():
    m = run()
    old = amm_trade("sell", payer="SELLER", t_ms=T0_MS + 60_600, slot=500, pool_id=POOL, sol=5.0)
    for _ in range(2):
        for ev in events(old):
            m.on_event(ev)
    ev = m.evidence("MintApump")
    assert [s["fee_payer"] for s in ev.sells_around_graduation].count("SELLER") == 1
    assert [t["signature"] for t in ev.competing_early_transactions].count(old["signature"]) == 1
