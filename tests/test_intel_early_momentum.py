"""CHAD Bot 10, Early Momentum (C10): event-driven detection over the PumpAPI trade stream.

CHAD (docs/CHAD_BLUEPRINT.md, Bot 10): "3 buys · 3 unique wallets · 30-second window"; "3 distinct fee payers
required — not 3 txs from one wallet"; "0.5–10 SOL bonding curve gate" vs "all below the 35 SOL bonding curve
threshold" (B-02, decided by the owner 2026-10-21: 0.5–10 real SOL now and at the peak); "Peak SOL Pump-and-Dump Gate … tracks all-time high virtualSolReserves"; "If 3+
wallets share the same SOL funder → emergency exit fires instantly". Adverse cases from CLAUDE.md, PHASE 2,
"Required simulation"."""
import itertools
import json

import pytest

from jinx.intel.early_momentum import EarlyMomentum, Funding, Status
from tests.pumpapi_msgs import buy, create, events, one, sell, amm_trade, migrate


def fresh(**kw):
    """A creation without a creator buy, so only the named wallets buy."""
    return create(initial_buy=0.0, v_quote=30.0, **kw)


def feed(em, msgs, **kw):
    out = []
    for m in msgs:
        for ev in events(m, **kw):
            out += em.on_event(ev)
    return out


def predicate(evidence, name):
    [p] = [p for p in evidence.predicates if p.name == name]
    return p


def test_three_distinct_fee_payers_within_30_seconds_emit_one_evidence_immediately():
    got = []
    em = EarlyMomentum(notify=got.append)
    assert feed(em, [fresh(), buy("A", 1), buy("B", 10)]) == []
    [ev] = feed(em, [buy("C", 20)])
    assert got == [ev]                                   # fast-path notification, synchronously
    assert ev.signal_type == "early_momentum" and ev.mint == "MintApump"
    assert [b.fee_payer for b in ev.window] == ["A", "B", "C"]
    assert ev.trigger.fee_payer == "C" and ev.fee_payers == ("A", "B", "C")
    assert predicate(ev, "three_buys_three_fee_payers_30s").status is Status.PASS
    assert feed(em, [buy("D", 22), buy("E", 25)]) == []  # emitted once per mint
    assert len(got) == 1


@pytest.mark.parametrize("payers", [["A"] * 3, ["A"] * 12, ["A", "B", "A", "B", "A", "B"]])
def test_repeated_buys_from_one_or_two_wallets_never_trigger(payers):
    """Artificial volume and repeated wallet purchases: three transactions from one wallet are not three wallets."""
    em = EarlyMomentum()
    assert feed(em, [fresh()] + [buy(p, i) for i, p in enumerate(payers)]) == []
    assert em.evidence("MintApump") is None


def test_a_cluster_wider_than_30_seconds_does_not_trigger_but_a_later_tight_one_does():
    em = EarlyMomentum()
    assert feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 30.001)]) == []
    [ev] = feed(em, [buy("D", 30.5)])
    assert [b.fee_payer for b in ev.window] == ["B", "C", "D"]


def test_exactly_30_seconds_is_inside_the_window():
    em = EarlyMomentum()
    [ev] = feed(em, [fresh(), buy("A", 0), buy("B", 15), buy("C", 30.0)])
    assert ev.window[-1].event_ns - ev.window[0].event_ns == 30_000_000_000


def test_sells_and_post_migration_buys_are_not_curve_buys():
    em = EarlyMomentum()
    msgs = [fresh(), buy("A", 0), sell("B", 1), sell("C", 2),
            amm_trade("buy", payer="D", t_ms=3_000 + 1_792_540_000_000), migrate()]
    assert feed(em, msgs) == []


def test_the_window_uses_event_time_so_a_feed_backlog_does_not_compress_it():
    """Feed delay: three buys 0 s, 20 s and 45 s apart on PumpAPI's clock arrive together after an outage."""
    em = EarlyMomentum()
    burst = 1_792_540_100_000 * 1_000_000
    msgs = [buy("A", 0), buy("B", 20), buy("C", 45)]
    assert [x for m in msgs for x in em.on_event(one(m, recv_ns=burst))] == []


def test_a_delayed_signal_keeps_its_event_time_and_reports_the_delay():
    """A stale signal is reported as stale: event time from PumpAPI's clock, receipt 40 s later."""
    em = EarlyMomentum()
    [ev] = feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 2)], recv_delay_ms=40_000)
    assert ev.event_ns == (1_792_540_000_000 + 2_000) * 1_000_000 and ev.clock == "provider"
    assert ev.feed_delay_ns == 40_000 * 1_000_000
    assert ev.to_dict()["freshness"]["feed_delay_ms"] == pytest.approx(40_000)


def test_out_of_order_arrival_converges_to_the_same_evidence():
    msgs = [fresh(), buy("A", 0), buy("B", 10), buy("C", 20), buy("D", 25)]
    evs = [ev for m in msgs for ev in events(m)]
    reference = EarlyMomentum()
    for ev in evs:
        reference.on_event(ev)
    want = [b.signature for b in reference.evidence("MintApump").window]
    for order in itertools.permutations(evs[1:]):
        em = EarlyMomentum()
        emitted = [x for ev in (evs[0], *order) for x in em.on_event(ev)]
        assert len(emitted) == 1
        assert [b.signature for b in em.evidence("MintApump").window] == want


def test_a_late_buy_inside_the_window_joins_it_without_moving_the_trigger():
    em = EarlyMomentum()
    feed(em, [fresh(), buy("A", 0), buy("B", 10), buy("C", 20)])
    late = one(buy("A", 5))
    em.on_event(late)
    ev = em.evidence("MintApump")
    assert late.signature in [b.signature for b in ev.window] and ev.trigger.fee_payer == "C"


def test_a_duplicate_delivery_is_not_counted_twice():
    em = EarlyMomentum()
    a = one(buy("A", 0))
    for ev in (a, a, a):
        em.on_event(ev)
    assert feed(em, [buy("B", 1)]) == []


def test_the_owner_decided_reserve_gates_replace_the_contradiction():
    """B-02 decided 2026-10-21: 0.5-10 real SOL now and at the peak; the 35 SOL statement is superseded."""
    em = EarlyMomentum()
    [ev] = feed(em, [fresh(), buy("A", 1, v_quote=31.5), buy("B", 2, v_quote=32.0),
                     buy("C", 3, v_quote=33.0)])
    cur = predicate(ev, "current_reserve_0_5_to_10_real_sol")
    peak = predicate(ev, "peak_reserve_max_10_real_sol")
    assert cur.status is Status.PASS and peak.status is Status.PASS and cur.blocker == "B-02"
    assert "0.5–10 SOL bonding curve gate" in cur.chad
    assert cur.detail["current_real_sol"] == pytest.approx(3.0) and cur.detail["current_virtual_sol"] == pytest.approx(33.0)
    assert cur.detail["standard_curve"] is True
    assert "curve_gate_below_35_sol" not in {p.name for p in ev.predicates}
    assert ev.trade_permitted is False                   # funders still unknown: the sybil check blocks

def test_a_curve_that_is_not_a_standard_30_sol_curve_is_flagged_and_judged_on_its_verified_real_sol():
    """Live finding (tests/test_intel_graduation.py): some curves report virtual SOL far below 30."""
    em = EarlyMomentum()
    odd = [buy(p, i, v_quote=0.13) for i, p in enumerate("ABC")]
    for m in odd:
        m["quoteInPool"] = 0.03
    [ev] = feed(em, [fresh()] + odd)
    cur = predicate(ev, "current_reserve_0_5_to_10_real_sol")
    assert cur.detail["standard_curve"] is False and cur.status is Status.FAIL     # 0.03 real SOL is below 0.5

def test_the_peak_gate_sees_a_reserve_that_retraced_after_a_higher_peak():
    """A token that ran up and dumped looks early on a snapshot; the peak is kept and rejects it (B-02)."""
    em = EarlyMomentum()
    run_up = [buy(f"W{i}", 0.1 * i, v_quote=31 + i * 3) for i in range(1, 11)]       # to 61 SOL virtual, 31 real
    dump = [sell(f"W{i}", 40 + i, v_quote=61 - i * 3) for i in range(1, 9)]          # back to 37 virtual, 7 real
    ([first],) = [feed(em, [create()] + run_up)]
    late = feed(em, dump + [buy("X", 120, v_quote=37.5), buy("Y", 121, v_quote=38), buy("Z", 122, v_quote=38.5)])
    assert late == []                                   # already emitted for this mint at the run-up
    ev = em.evidence("MintApump")
    r = ev.reserves
    assert r["peak_virtual_sol"] == pytest.approx(61.0) and r["current_virtual_sol"] == pytest.approx(38.5)
    assert r["peak_real_sol"] == pytest.approx(31.0) and r["current_real_sol"] == pytest.approx(8.5)
    assert r["retrace_from_peak"] == pytest.approx((61 - 38.5) / 61)
    assert predicate(ev, "current_reserve_0_5_to_10_real_sol").status is Status.PASS
    peak = predicate(ev, "peak_reserve_max_10_real_sol")
    assert peak.status is Status.FAIL and peak.detail["peak_real_sol"] == pytest.approx(31.0)

def test_the_peak_is_not_all_time_when_the_creation_was_not_observed():
    em = EarlyMomentum()
    [ev] = feed(em, [buy("A", 0), buy("B", 1), buy("C", 2)])
    assert ev.reserves["observed_since_creation"] is False
    assert predicate(ev, "peak_reserve_max_10_real_sol").status is Status.UNAVAILABLE

def test_a_late_reserve_report_counts_toward_the_peak_but_not_the_current_reading():
    em = EarlyMomentum()
    feed(em, [create(), buy("A", 0, v_quote=31.5), buy("B", 5, v_quote=33.0), buy("C", 6, v_quote=34.0)])
    em.on_event(one(buy("D", 3, v_quote=40.0)))          # arrives last, happened earlier, higher reserve
    r = em.evidence("MintApump").reserves
    assert r["peak_virtual_sol"] == pytest.approx(40.0) and r["current_virtual_sol"] == pytest.approx(34.0)


def test_without_funding_data_the_sybil_check_is_unavailable_and_blocks():
    em = EarlyMomentum()
    [ev] = feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 2)])
    p = predicate(ev, "sybil_funder")
    assert p.status is Status.UNAVAILABLE and set(p.detail["unknown"]) == {"A", "B", "C"}
    assert "sybil_funder" in ev.blocking and ev.emergency_exit is False


def test_three_wallets_with_one_funder_fail_and_signal_emergency_exit():
    em = EarlyMomentum()
    for w in "ABC":
        em.record_funding(Funding(wallet=w, funder="F", source="test", observed_ns=1))
    [ev] = feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 2)])
    p = predicate(ev, "sybil_funder")
    assert p.status is Status.FAIL and p.detail["shared"] == {"F": ["A", "B", "C"]}
    assert ev.emergency_exit is True and ev.trade_permitted is False


def test_funding_learned_after_detection_is_pushed_immediately():
    got = []
    em = EarlyMomentum(notify=got.append)
    feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 2)])
    assert em.record_funding(Funding("A", "F", "test", 5)) == []
    assert em.record_funding(Funding("B", "F", "test", 6)) == []
    [update] = em.record_funding(Funding("C", "F", "test", 7))
    assert got[-1] is update and update.emergency_exit is True


def test_distinct_known_funders_pass_the_sybil_check():
    em = EarlyMomentum()
    for w, f in (("A", "F"), ("B", "F"), ("C", "G")):
        em.record_funding(Funding(w, f, "test", 1))
    [ev] = feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 2)])
    assert predicate(ev, "sybil_funder").status is Status.PASS


def test_the_funder_investigation_is_requested_at_detection():
    asked = []
    em = EarlyMomentum(request_funders=lambda mint, wallets: asked.append((mint, wallets)))
    feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 2)])
    assert asked == [("MintApump", ("A", "B", "C"))]


def test_a_wallet_that_joins_the_window_late_is_also_investigated():
    asked = []
    em = EarlyMomentum(request_funders=lambda mint, wallets: asked.append(wallets))
    feed(em, [fresh(), buy("A", 0), buy("B", 10), buy("C", 20)])
    em.on_event(one(buy("D", 5)))                         # happened earlier: the window becomes A, D, B
    assert [b.fee_payer for b in em.evidence("MintApump").window] == ["A", "D", "B"]
    assert asked == [("A", "B", "C"), ("D",)]


def test_the_creators_buy_counts_and_is_flagged():
    em = EarlyMomentum()
    [ev] = feed(em, [create(dev="DEV"), buy("A", 1), buy("B", 2)])
    assert ev.fee_payers == ("DEV", "A", "B") and ev.creator == "DEV" and ev.creator_in_window is True


def test_a_failing_listener_never_stops_detection():
    def boom(_):
        raise RuntimeError("listener down")
    em = EarlyMomentum(notify=boom, request_funders=lambda *a: boom(a))
    [ev] = feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 2)])
    assert ev.mint == "MintApump" and em.stats["listener_errors"] == 2


def test_evidence_carries_the_confluence_fields_and_serializes():
    em = EarlyMomentum()
    [ev] = feed(em, [fresh(), buy("A", 0), buy("B", 1), buy("C", 2)])
    d = json.loads(json.dumps(ev.to_dict()))
    assert {"mint", "signal_type", "sources", "observed_ns", "event_ns", "clock", "freshness", "payload",
            "blocking", "trade_permitted", "predicates"} <= set(d)
    assert d["sources"] == ["pumpapi.stream"] and d["trade_permitted"] is False


def test_state_is_bounded_by_mint_cap_idle_time_and_buys_per_mint():
    em = EarlyMomentum(max_mints=5, idle_ttl_s=60, max_buys_per_mint=8)
    for i in range(20):
        feed(em, [buy("A", i * 0.01, mint=f"M{i}")])
    assert em.tracked_mints() == 5
    feed(em, [buy("A", 3600, mint="LATE")])               # one hour later: every earlier mint is idle
    assert em.tracked_mints() == 1
    feed(em, [buy("SPAM", 3600 + i * 0.01, mint="LATE") for i in range(100)])
    assert em.buys_held("LATE") == 8
    assert em.stats["buys_dropped_over_cap"] >= 92


def test_a_buy_older_than_the_retention_horizon_is_counted_not_used():
    em = EarlyMomentum()
    feed(em, [buy("A", 100), buy("B", 101)])
    feed(em, [buy("C", 30)])                              # 70 s before the newest buy
    assert em.stats["late_beyond_retention"] == 1 and em.evidence("MintApump") is None
