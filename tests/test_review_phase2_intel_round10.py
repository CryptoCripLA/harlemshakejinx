"""Codex review of the Phase 2 event intel, round 10 (docs/audits/phase2-intel-codex-20261021-round10.md, 27b9ec9).
Each reproduction failed before its fix."""
from jinx.intel.migration import MigrationTracker
from tests.pumpapi_msgs import T0_MS, amm_trade, buy, create, events, migrate, sell

MIG_T = T0_MS + 60_000


def feed(m, msgs):
    for msg in msgs:
        for ev in events(msg):
            m.on_event(ev)


def migrated(pool_id="AMM1"):
    m = MigrationTracker()
    feed(m, [create(initial_buy=0.0, v_quote=30.0), migrate(pool_id=pool_id, t_ms=MIG_T, slot=499)])
    return m


# ---- R10-1 (MEDIUM): a wallet the tracker could not tally is never shown as zero sells -------------------------
def test_r10_1_a_holder_beyond_the_wallet_cap_has_an_unavailable_tally_not_zero():
    m = migrated()
    feed(m, [amm_trade("buy", payer=f"W{i}", t_ms=MIG_T + 100 + i, slot=500 + i, pool_id="AMM1")
             for i in range(1_026)])
    feed(m, [amm_trade("sell", payer="HOLDER", t_ms=MIG_T + 5_000, slot=3_000, pool_id="AMM1", sol=2.0)])
    m.record_holders("MintApump", [{"address": "HOLDER", "balance": 1.0, "amount_percentage": 0.05}],
                     "gmgn.getTokenTopHolders", (MIG_T + 6_000) * 1_000_000)
    [holder] = m.evidence("MintApump").holder_selling["verified"]["holders"]
    assert holder["tally"] == "UNAVAILABLE" and holder["after"] is None
    assert any(s["fee_payer"] == "HOLDER" for s in m.evidence("MintApump").sells_around_graduation)


def test_r10_1_a_holder_that_never_traded_has_a_true_zero_while_the_flow_is_complete():
    m = migrated()
    m.record_holders("MintApump", [{"address": "QUIET", "balance": 1.0, "amount_percentage": 0.05}],
                     "gmgn.getTokenTopHolders", (MIG_T + 6_000) * 1_000_000)
    [holder] = m.evidence("MintApump").holder_selling["verified"]["holders"]
    assert holder["tally"] == "observed" and holder["after"]["sells"] == 0


# ---- R10-2 (LOW): a resend behind the dedup horizon never inflates a trade count ------------------------------
def test_r10_2_a_resend_behind_the_horizon_does_not_inflate_the_pool_counts():
    m = migrated("AMM1")
    other = [amm_trade("buy", payer=f"P{i}", t_ms=MIG_T + 100 + i, slot=500 + i, pool_id="OTHER")
             for i in range(1_026)]
    feed(m, other)
    assert m.evidence("MintApump").foreign_pool_trades == 1_026
    feed(m, [other[512]])                                                    # resent after its identity left
    ev = m.evidence("MintApump")
    assert ev.foreign_pool_trades == 1_026 and ev.pool_trades_uncounted == 1


def test_r10_1_the_creators_tally_is_unavailable_when_its_trades_went_untallied():
    m = MigrationTracker(max_wallets=1)
    feed(m, [create(dev="DEV", slot=100, initial_buy=0.0), buy("FIRST", 1, v_quote=31.0, slot=101),
             sell("DEV", 2, v_quote=30.5, slot=102, sol=0.5),             # the creator is beyond the 1-wallet cap
             migrate(pool_id="AMM1", t_ms=MIG_T, slot=499)])
    dev = m.evidence("MintApump").developer_selling
    assert (dev["status"], dev["tally"], dev["after"], dev["complete"]) == ("UNAVAILABLE", "UNAVAILABLE", None, False)
