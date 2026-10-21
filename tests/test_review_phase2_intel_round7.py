"""Codex review of the Phase 2 event intel, round 7 (docs/audits/phase2-intel-codex-20261021-round7.md, f4496a8).
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


def test_r7_1_a_full_kept_set_with_no_qualifying_trade_cannot_say_nothing_qualifies_yet():
    m = MigrationTracker(confirmation_delay_ms=1)
    feed(m, [create()])
    m.record_report("MintApump", "gmgn.trenches", T * 1_000_000, Clock.PROVIDER, (T + 50) * 1_000_000, pool=POOL)
    for i in range(4_096):
        feed(m, [amm_trade("buy", payer=f"P{i}", t_ms=T + i, slot=500 + i, pool_id=POOL)])
    feed(m, [amm_trade("buy", payer="TRUEFIRST", t_ms=T + 4_100, slot=5_000, pool_id=POOL),
             migrate(pool_id=POOL, t_ms=T + 4_094, slot=4_999)])         # kept trades now fail the slot order
    assert m.evidence("MintApump").research["reference_status"].startswith("not determinable")
    feed(m, [amm_trade("buy", payer="LATER", t_ms=T + 4_200, slot=5_001, pool_id=POOL)])
    research = m.evidence("MintApump").research
    assert research["reference_trade"] is None and research["reference_status"].startswith("not determinable")


def test_r7_1_with_room_left_the_answer_is_still_the_first_qualifying_trade():
    m = MigrationTracker(confirmation_delay_ms=1)
    feed(m, [create()])
    m.record_report("MintApump", "gmgn.trenches", T * 1_000_000, Clock.PROVIDER, (T + 50) * 1_000_000, pool=POOL)
    feed(m, [amm_trade("buy", payer=f"P{i}", t_ms=T + i, slot=500 + i, pool_id=POOL) for i in range(10)])
    feed(m, [migrate(pool_id=POOL, t_ms=T + 100, slot=4_999),
             amm_trade("buy", payer="FIRST", t_ms=T + 200, slot=5_000, pool_id=POOL)])
    assert m.evidence("MintApump").research["reference_trade"]["fee_payer"] == "FIRST"
