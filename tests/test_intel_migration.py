"""Migration intelligence (M1, M2, M3; owner directives 2026-10-21 §3) feeding jinx/core/lifecycle.py.

M1: the actual migration transaction and the first verified post-migration liquidity. M2: pre- and post-migration
liquidity, large sells around graduation, developer and concentrated-holder selling, holder-distribution changes,
competing early transactions. M3: a configurable confirmation delay for paper experiments only, off by default.
Owner mandate 2026-10-20: never infer graduation from a price rise or a token missing from a query; migration is
neither a buy signal nor a reason to abandon a position."""
import json

import pytest

from jinx.core.events import EventKind, MarketEvent
from jinx.core.lifecycle import State
from jinx.intel.migration import MigrationTracker
from tests.pumpapi_msgs import T0_MS, amm_trade, buy, create, events, migrate, one, sell

MIG_T = T0_MS + 60_000        # migration one minute after creation, slot 499


def feed(m, msgs, **kw):
    out = []
    for msg in msgs:
        for ev in events(msg, **kw):
            out += m.on_event(ev)
    return out


def launched():
    return [create(dev="DEV", slot=100), buy("W1", 1, v_quote=60.0, slot=110, sol=20.0, tokens=300_000_000.0),
            buy("W2", 2, v_quote=70.0, slot=120, sol=5.0, tokens=20_000_000.0),
            sell("DEV", 3, v_quote=68.0, slot=130, sol=0.6, tokens=30_000_000.0),
            buy("W3", 50, v_quote=114.0, slot=480, sol=40.0, tokens=100_000_000.0)]


def pool(action, payer, dt_s, slot, pool_id="AMM1", sol=0.5, tokens=1_000_000.0, quote=84.0):
    return amm_trade(action, payer=payer, t_ms=MIG_T + int(dt_s * 1000), slot=slot, pool_id=pool_id, sol=sol,
                     tokens=tokens, quote_in_pool=quote)


def test_the_migrate_event_is_the_migration_transaction_observed_but_not_yet_verified():
    m = MigrationTracker()
    assert feed(m, launched()) == []
    [ev] = feed(m, [migrate(pool_id="AMM1", t_ms=MIG_T, slot=499, sig="MIGTX")])
    assert ev.migration.signature == "MIGTX" and ev.migration.pool_id == "AMM1" and ev.migration.slot == 499
    assert ev.migration.clock == "provider" and ev.verified is False
    assert m.lifecycle("MintApump").state is State.MIGRATION_OBSERVED


def test_the_first_trade_on_the_announced_pool_confirms_and_is_the_first_verified_liquidity():
    m = MigrationTracker()
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499)])
    [ev] = feed(m, [pool("buy", "S1", 0.4, slot=500, quote=85.2)])
    assert ev.verified is True
    first = ev.first_verified_liquidity
    assert first["quote_reserve_lamports"] == 85_200_000_000 and first["slots_after_migration"] == 1
    assert first["ms_after_migration"] == pytest.approx(400)
    # the reserve is the one after that trade, so the trade itself is part of the record (live: 139.6 SOL buys)
    assert (first["side"], first["sol_lamports"], first["fee_payer"]) == ("buy", 500_000_000, "S1")
    life = m.lifecycle("MintApump")
    assert life.state is State.POST_MIGRATION_MARKET and life.post_pool == "AMM1"
    assert feed(m, [pool("buy", "S2", 0.9, slot=501)]) == []          # later trades update, emit nothing new


def test_the_first_verified_liquidity_is_the_earliest_pool_trade_whatever_the_arrival_order():
    m = MigrationTracker()
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499)])
    late, early = pool("buy", "S2", 0.9, slot=501, quote=86.0), pool("buy", "S1", 0.4, slot=500, quote=85.2)
    [confirmed] = feed(m, [late])
    assert confirmed.first_verified_liquidity["signature"] == late["signature"]
    assert feed(m, [early]) == []                                     # confirmed once; the record is corrected
    first = m.evidence("MintApump").first_verified_liquidity
    assert first["signature"] == early["signature"] and first["quote_reserve_lamports"] == 85_200_000_000
    assert m.evidence("MintApump").competing_early_transactions[0]["fee_payer"] == "S1"


def test_a_trade_on_another_pool_never_confirms_and_never_contradicts():
    m = MigrationTracker()
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499)])
    assert feed(m, [pool("buy", "X1", 0.5, slot=500, pool_id="OTHERPOOL")]) == []
    ev = m.evidence("MintApump")
    assert ev.verified is False and ev.foreign_pool_trades == 1
    assert m.lifecycle("MintApump").state is State.MIGRATION_OBSERVED


def test_a_pool_trade_delivered_before_the_migrate_event_is_held_and_then_confirms():
    """Feed delay: the first pool trade arrives before the migration that created the pool."""
    m = MigrationTracker()
    feed(m, launched())
    assert feed(m, [pool("buy", "S1", 0.3, slot=500)]) == []
    [observed, confirmed] = feed(m, [migrate(t_ms=MIG_T, slot=499)])
    assert observed.verified is False and confirmed.verified is True
    assert m.lifecycle("MintApump").state is State.POST_MIGRATION_MARKET


def test_a_trade_on_the_announced_pool_before_the_migration_slot_does_not_confirm():
    m = MigrationTracker()
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499)])
    assert feed(m, [pool("buy", "S0", -1, slot=498)]) == []
    assert m.evidence("MintApump").verified is False and m.stats["pool_trades_before_migration"] == 1


def test_pumpportals_migration_names_only_the_venue_so_it_cannot_verify_a_pool():
    m = MigrationTracker()
    feed(m, launched())
    pp = MarketEvent(kind=EventKind.MIGRATION, source="mako.pumpportal", mint="MintApump", signature="PPMIG",
                     recv_ns=(MIG_T + 90) * 1_000_000, seq=10_000, raw={"pool": "pump-amm", "txType": "migrate"})
    [ev] = m.on_event(pp)
    assert ev.migration.pool_id is None and ev.migration.venue == "pump-amm" and ev.migration.clock == "receipt"
    assert feed(m, [pool("buy", "S1", 0.5, slot=500)]) == []
    ev = m.evidence("MintApump")
    assert ev.verified is False and ev.unverified_pool_trades == 1
    assert "pool address" in ev.verification_status


def test_pre_migration_liquidity_is_the_last_curve_reading_before_the_migration():
    m = MigrationTracker()
    [ev] = feed(m, launched() + [migrate(t_ms=MIG_T, slot=499)])
    pre = ev.pre_migration_liquidity
    assert pre["virtual_sol"] == pytest.approx(114.0) and pre["real_sol"] == pytest.approx(84.0)
    assert pre["standard_curve"] is True and pre["peak_virtual_sol"] == pytest.approx(114.0)


def test_sells_around_graduation_are_listed_by_size_without_inventing_a_large_sell_rule():
    m = MigrationTracker()
    msgs = launched() + [sell("W2", 55, v_quote=110.0, slot=490, sol=4.0), migrate(t_ms=MIG_T, slot=499),
                         pool("buy", "S1", 0.4, slot=500), pool("sell", "W1", 1.0, slot=502, sol=9.0)]
    feed(m, msgs)
    ev = m.evidence("MintApump")
    sells = ev.sells_around_graduation
    assert [(s["fee_payer"], s["phase"]) for s in sells[:3]] == [("W1", "after"), ("W2", "before"), ("DEV", "before")]
    assert sells[0]["sol_lamports"] == 9_000_000_000 and sells[0]["quote_reserve_after_lamports"] == 84_000_000_000
    assert "B-03" in ev.large_sell_rule


def test_developer_selling_before_and_after_the_migration():
    m = MigrationTracker()
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499), pool("buy", "S1", 0.4, slot=500),
                          pool("sell", "DEV", 2, slot=505, sol=1.5, tokens=4_000_000.0)])
    dev = m.evidence("MintApump").developer_selling
    assert dev["creator"] == "DEV" and dev["status"] == "observed"
    assert dev["before"] == {"sells": 1, "sol_lamports": 600_000_000, "tokens": 30_000_000_000_000}
    assert dev["after"] == {"sells": 1, "sol_lamports": 1_500_000_000, "tokens": 4_000_000_000_000}


def test_developer_selling_is_unavailable_when_the_creation_was_not_seen():
    m = MigrationTracker()
    feed(m, launched()[1:] + [migrate(t_ms=MIG_T, slot=499)])
    assert m.evidence("MintApump").developer_selling["status"] == "UNAVAILABLE"


def test_concentrated_holder_selling_and_distribution_from_observed_trade_flow():
    m = MigrationTracker()
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499), pool("buy", "S1", 0.4, slot=500, tokens=10_000_000.0),
                          pool("sell", "W1", 1.0, slot=502, sol=9.0, tokens=150_000_000.0)])
    ev = m.evidence("MintApump")
    holders = ev.holder_selling
    assert holders["basis"].startswith("observed trades by fee payer")
    assert holders["complete"] is True
    top = holders["top_at_migration"][0]
    assert top["fee_payer"] == "W1" and top["after"]["sells"] == 1 and top["after"]["tokens"] == 150_000_000_000_000
    dist = ev.holder_distribution
    assert dist["at_migration"]["top10_share"] == pytest.approx(1.0)
    assert dist["latest"]["holders"] == dist["at_migration"]["holders"] + 1


def test_competing_early_transactions_are_the_first_pool_trades_with_their_offsets():
    m = MigrationTracker()
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499)]
         + [pool("buy", f"S{i}", 0.1 * (i + 1), slot=500 + i // 2) for i in range(30)])
    early = m.evidence("MintApump").competing_early_transactions
    assert len(early) == 20 and early[0]["fee_payer"] == "S0" and early[0]["slots_after_migration"] == 1
    assert early[5]["ms_after_migration"] == pytest.approx(600)


def test_the_m3_confirmation_delay_is_off_by_default_and_a_paper_reference_when_set():
    msgs = launched() + [migrate(t_ms=MIG_T, slot=499)] + [pool("buy", f"S{i}", i * 0.7, slot=500 + i) for i in range(6)]
    off = MigrationTracker()
    feed(off, msgs)
    assert off.evidence("MintApump").research is None
    on = MigrationTracker(confirmation_delay_ms=2_000)
    feed(on, msgs)
    r = on.evidence("MintApump").research
    assert r["variant"] == "M3 confirmation delay" and r["live_policy"] is False and r["delay_ms"] == 2_000
    assert r["reference_trade"]["fee_payer"] == "S3"        # first pool trade at or after migration + 2 s (2.1 s)


def test_the_lifecycle_keeps_creation_curve_and_migration_facts_with_provenance():
    m = MigrationTracker()
    feed(m, launched() + [migrate(pool_id="AMM1", t_ms=MIG_T, slot=499), pool("buy", "S1", 0.4, slot=500)])
    life = m.lifecycle("MintApump")
    assert [t.to_state for t in life.history] == [State.NEWLY_DETECTED, State.ON_BONDING_CURVE,
                                                  State.MIGRATION_OBSERVED, State.MIGRATION_CONFIRMED,
                                                  State.POST_MIGRATION_MARKET]
    assert life.fields["pre_pool"].value == "CURVE-MintApump" and life.fields["exchange"].value == "pump-amm"
    assert life.fields["created"].clock == "provider" and life.peak_v_sol_lamports == 114_000_000_000


def test_migration_is_never_inferred_from_price_or_a_complete_curve():
    m = MigrationTracker()
    done = buy("W9", 55, v_quote=115.0, slot=490, sol=1.0)
    done["tokensInPool"] = 0.0
    assert feed(m, launched() + [done]) == []
    assert m.evidence("MintApump") is None
    assert m.lifecycle("MintApump").state is State.ON_BONDING_CURVE


def test_a_repeated_migrate_delivery_emits_once():
    m = MigrationTracker()
    feed(m, launched())
    mig = one(migrate(t_ms=MIG_T, slot=499))
    assert len(m.on_event(mig)) == 1 and m.on_event(mig) == []


def test_listeners_hear_the_signal_and_the_confirmation_and_their_failures_are_contained():
    heard = []
    m = MigrationTracker(notify=heard.append)
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499), pool("buy", "S1", 0.4, slot=500)])
    assert [e.verified for e in heard] == [False, True]

    def boom(_):
        raise RuntimeError("down")
    m2 = MigrationTracker(notify=boom)
    feed(m2, launched() + [migrate(t_ms=MIG_T, slot=499)])
    assert m2.stats["listener_errors"] == 1 and m2.evidence("MintApump") is not None


def test_state_is_bounded():
    m = MigrationTracker(max_mints=3, idle_ttl_s=60, max_curve_trades=4, max_pool_trades=5, max_wallets=3)
    for i in range(10):
        feed(m, [buy("A", i * 0.01, mint=f"M{i}")])
    assert m.tracked_mints() == 3
    feed(m, [buy(f"B{i}", 3600 + i * 0.01, mint="LATE") for i in range(10)])
    assert m.tracked_mints() == 1 and m.curve_trades_held("LATE") == 4
    assert m.stats["wallets_untracked"] >= 7


def test_evidence_carries_the_confluence_fields_and_serializes():
    m = MigrationTracker()
    feed(m, launched() + [migrate(t_ms=MIG_T, slot=499), pool("buy", "S1", 0.4, slot=500)])
    d = json.loads(json.dumps(m.evidence("MintApump").to_dict()))
    assert {"mint", "signal_type", "sources", "observed_ns", "event_ns", "clock", "freshness", "payload", "state",
            "verified"} <= set(d)
    assert d["signal_type"] == "migration" and d["state"] == "POST_MIGRATION_MARKET"

