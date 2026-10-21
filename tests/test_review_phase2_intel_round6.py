"""Codex review of the Phase 2 event intel, round 6 (docs/audits/phase2-intel-codex-20261021-round6.md, 872be0d).
The reproduction failed before its fix."""
from jinx.intel.migration import MigrationTracker
from jinx.intel.readings import Clock
from tests.pumpapi_msgs import T0_MS, amm_trade, create, events, migrate

T = T0_MS + 60_000
POOL = "AMM1"


def feed(target, msgs):
    for m in msgs:
        for ev in events(m):
            target.on_event(ev)


def test_r6_1_an_undeterminable_reference_is_never_replaced_by_a_later_guess():
    m = MigrationTracker(confirmation_delay_ms=5_000)
    feed(m, [create()])
    m.record_report("MintApump", "gmgn.trenches", T * 1_000_000, Clock.PROVIDER, (T + 50) * 1_000_000, pool=POOL)
    for i in range(4_096):
        feed(m, [amm_trade("buy", payer=f"P{i}", t_ms=T + i, slot=500 + i, pool_id=POOL)])
    feed(m, [amm_trade("buy", payer="OLDREF", t_ms=T + 6_000, slot=5_000, pool_id=POOL),
             amm_trade("buy", payer="TRUEFIRST", t_ms=T + 7_000, slot=5_001, pool_id=POOL)])
    feed(m, [migrate(pool_id=POOL, t_ms=T + 1_500, slot=499)])      # due moves to T + 6,500: TRUEFIRST was not kept
    assert m.evidence("MintApump").research["reference_status"].startswith("not determinable")
    feed(m, [amm_trade("buy", payer="LATER", t_ms=T + 8_000, slot=5_002, pool_id=POOL)])
    research = m.evidence("MintApump").research
    assert research["reference_status"].startswith("not determinable") and research["reference_trade"] is None


def test_r6_1_without_an_evidence_build_in_between_the_answer_is_the_same():
    m = MigrationTracker(confirmation_delay_ms=5_000)
    feed(m, [create()])
    m.record_report("MintApump", "gmgn.trenches", T * 1_000_000, Clock.PROVIDER, (T + 50) * 1_000_000, pool=POOL)
    for i in range(4_096):
        feed(m, [amm_trade("buy", payer=f"P{i}", t_ms=T + i, slot=500 + i, pool_id=POOL)])
    feed(m, [amm_trade("buy", payer="TRUEFIRST", t_ms=T + 7_000, slot=5_001, pool_id=POOL),
             migrate(pool_id=POOL, t_ms=T + 1_500, slot=499),
             amm_trade("buy", payer="LATER", t_ms=T + 8_000, slot=5_002, pool_id=POOL)])
    assert m.evidence("MintApump").research["reference_trade"] is None
