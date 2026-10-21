"""B-02 owner decision (2026-10-21): the Early Momentum reserve policy.

- Current reserve: 0.5 <= verified real SOL in the pump.fun bonding curve <= 10.0. Real SOL is PumpAPI's
  `quoteInPool` on a pre-migration curve (`pool: pump`) quoted in SOL/WSOL; never derived from virtual SOL.
- Historical peak: the highest verified real SOL reserve since creation must be <= 10.0 (the same ceiling). A token
  that went above 10 and came back is rejected. Missing or incomplete history blocks.
- CHAD's 35 SOL statement is retired as an entry condition and kept as a superseded specification.
- Non-standard curves get the same rule when their real reserve is a verified SOL amount; unverified or non-SOL
  curves are ineligible. Migrated pools are outside this pre-graduation path.
- At the entry decision the reserves are checked again on the current state; a stale snapshot never qualifies.
- A reserve PASS never overrides safety, wallet-event verification or any other requirement.
"""
import json

import pytest

from jinx.adapters.mako.normalize import normalize as pumpportal_normalize
from jinx.adapters.pumpapi.normalize import NotQuotedInSol, normalize as pumpapi_normalize
from jinx.intel import readings
from jinx.intel.early_momentum import EarlyMomentum, Status
from tests.pumpapi_msgs import buy, create, events, migrate, sell

CURRENT = "current_reserve_0_5_to_10_real_sol"
PEAK = "peak_reserve_max_10_real_sol"


def feed(em, msgs):
    out = []
    for m in msgs:
        for ev in events(m):
            out += em.on_event(ev)
    return out


def gate(evidence, name):
    [p] = [p for p in evidence.predicates if p.name == name]
    return p


def detect_at(real_sols, mint="MintApump"):
    """A fresh creation (0 real SOL) and three buys by distinct wallets ending at the given real SOL levels."""
    em = EarlyMomentum()
    msgs = [create(mint=mint, initial_buy=0.0, v_quote=30.0)]
    msgs += [buy(p, i, v_quote=30.0 + r, mint=mint) for i, (p, r) in enumerate(zip("ABC", real_sols), start=1)]
    out = feed(em, msgs)
    return em, out[-1]


@pytest.mark.parametrize("real,status", [(0.49, Status.FAIL), (0.50, Status.PASS), (5.00, Status.PASS),
                                         (10.00, Status.PASS), (10.01, Status.FAIL)])
def test_current_reserve_boundaries(real, status):
    em, ev = detect_at([min(real, 0.3), min(real, 0.4), real])
    assert gate(ev, CURRENT).status is status
    if status is Status.PASS:
        assert gate(ev, PEAK).status is Status.PASS


@pytest.mark.parametrize("peak_real,status", [(10.0, Status.PASS), (10.000000001, Status.FAIL)])
def test_the_peak_ceiling_is_exactly_10_real_sol_to_the_lamport(peak_real, status):
    """Owner B-02: the peak "must never have exceeded 10 REAL SOL": exactly 10 passes, one lamport more fails, even
    after the reserve falls back into range."""
    em = EarlyMomentum()
    feed(em, [create(initial_buy=0.0, v_quote=30.0), buy("A", 1, v_quote=30.0 + peak_real),
              sell("A", 2, v_quote=34.0), buy("B", 3, v_quote=34.5)])
    [ev] = feed(em, [buy("C", 4, v_quote=35.0)])
    assert gate(ev, CURRENT).status is Status.PASS
    assert gate(ev, PEAK).status is status


def test_a_token_that_peaked_at_16_and_fell_to_7_is_rejected():
    em = EarlyMomentum()
    feed(em, [create(initial_buy=0.0, v_quote=30.0), buy("A", 1, v_quote=46.0), sell("A", 2, v_quote=40.0),
              buy("B", 3, v_quote=38.0), sell("B", 4, v_quote=36.0)])
    [ev] = feed(em, [buy("C", 5, v_quote=37.0)])
    assert gate(ev, CURRENT).status is Status.PASS              # 7 real SOL looks early on a snapshot
    assert gate(ev, PEAK).status is Status.FAIL                 # it already ran to 16
    assert gate(ev, PEAK).detail["peak_real_sol"] == pytest.approx(16.0)


def test_a_token_that_peaked_at_60_and_fell_to_5_is_rejected():
    em = EarlyMomentum()
    feed(em, [create(initial_buy=0.0, v_quote=30.0), buy("A", 1, v_quote=90.0), sell("A", 2, v_quote=35.0),
              buy("B", 3, v_quote=35.0)])
    [ev] = feed(em, [buy("C", 4, v_quote=35.0)])
    assert gate(ev, CURRENT).status is Status.PASS and gate(ev, PEAK).status is Status.FAIL


def test_a_token_already_at_58_is_rejected():
    em, ev = detect_at([50.0, 55.0, 58.0])
    assert gate(ev, CURRENT).status is Status.FAIL and gate(ev, PEAK).status is Status.FAIL


def test_the_peak_is_never_lowered_by_a_later_lower_reading():
    em = EarlyMomentum()
    feed(em, [create(initial_buy=0.0, v_quote=30.0), buy("A", 1, v_quote=42.0), sell("A", 2, v_quote=31.0)])
    [ev] = feed(em, [buy("B", 3, v_quote=31.5), buy("C", 4, v_quote=32.0)])
    assert ev.reserves["peak_real_sol"] == pytest.approx(12.0) and ev.reserves["peak_virtual_sol"] == pytest.approx(42.0)
    assert gate(ev, PEAK).status is Status.FAIL


def test_missing_history_blocks_entry():
    em = EarlyMomentum()
    [ev] = feed(em, [buy("A", 1, v_quote=31.0), buy("B", 2, v_quote=32.0), buy("C", 3, v_quote=33.0)])
    assert gate(ev, CURRENT).status is Status.PASS
    assert gate(ev, PEAK).status is Status.UNAVAILABLE and not ev.trade_permitted


def test_an_unverified_reading_after_creation_makes_the_history_incomplete():
    em = EarlyMomentum()
    hole = buy("A", 1, v_quote=31.0)
    del hole["quoteInPool"]                                  # PumpAPI reading without a real reserve
    [ev] = feed(em, [create(initial_buy=0.0, v_quote=30.0), hole, buy("B", 2, v_quote=32.0),
                     buy("C", 3, v_quote=33.0)])
    assert gate(ev, PEAK).status is Status.UNAVAILABLE
    assert "unverified" in json.dumps(gate(ev, PEAK).detail)


def test_a_verified_sol_non_standard_curve_gets_the_same_policy():
    def odd(msgs, reals):
        for m, r in zip(msgs, reals):
            m["vQuoteInBondingCurve"], m["quoteInPool"] = 0.13 + r, r      # virtual - real is not 30 SOL
        return msgs
    em = EarlyMomentum()
    c = odd([create(initial_buy=0.0, v_quote=30.0)], [0.0])
    [ev] = feed(em, c + odd([buy(p, i, v_quote=31.0) for i, p in enumerate("ABC", start=1)], [1.0, 2.0, 3.0]))
    assert gate(ev, CURRENT).status is Status.PASS and gate(ev, PEAK).status is Status.PASS
    assert gate(ev, CURRENT).detail["standard_curve"] is False
    em2 = EarlyMomentum()
    [ev2] = feed(em2, odd([create(initial_buy=0.0, v_quote=30.0)], [0.0]) +
                 odd([buy(p, i, v_quote=31.0) for i, p in enumerate("ABC", start=1)], [4.0, 8.0, 12.0]))
    assert gate(ev2, CURRENT).status is Status.FAIL and gate(ev2, PEAK).status is Status.FAIL


def test_non_sol_quotes_are_refused_and_unverified_curves_are_ineligible():
    usdc = buy("A", 1, v_quote=31.0)
    usdc["quoteMint"] = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    with pytest.raises(NotQuotedInSol):
        pumpapi_normalize(usdc, 1, iter(range(1, 9)).__next__)
    [ev] = events(buy("A", 1, v_quote=31.0))
    assert readings.verified_real_sol(ev) == 1_000_000_000
    no_quote_mint = buy("A", 1, v_quote=31.0)
    del no_quote_mint["quoteMint"]                           # quote asset not stated: not verified as SOL
    [ev2] = events(no_quote_mint)
    assert readings.verified_real_sol(ev2) is None


def test_a_stale_snapshot_never_qualifies_at_the_entry_decision():
    em, ev = detect_at([1.0, 3.0, 5.0])
    assert gate(ev, CURRENT).status is Status.PASS
    assert em.reserve_check("MintApump", feed_live=True)["eligible"] is True
    feed(em, [buy("D", 6, v_quote=42.0)])                    # the token runs past 10 before the decision
    later = em.reserve_check("MintApump", feed_live=True)
    assert later["eligible"] is False and later["current"]["status"] == "FAIL" and later["peak"]["status"] == "FAIL"
    em2, _ = detect_at([1.0, 3.0, 5.0], mint="MintBpump")
    stale = em2.reserve_check("MintBpump", feed_live=False)  # the trade feed is down: reserves are not current
    assert stale["eligible"] is False and "stale" in stale["reason"]


def test_a_migrated_token_is_outside_the_early_momentum_reserve_path():
    em, ev = detect_at([1.0, 3.0, 5.0])
    feed(em, [migrate()])
    check = em.reserve_check("MintApump", feed_live=True)
    assert check["eligible"] is False and "migrat" in check["reason"]


def test_the_35_sol_statement_is_superseded_not_an_active_gate():
    em, ev = detect_at([1.0, 3.0, 5.0])
    names = {p.name for p in ev.predicates}
    assert "curve_gate_below_35_sol" not in names and {CURRENT, PEAK} <= names
    sup = ev.to_dict()["superseded"]
    assert "35 SOL" in sup["curve_gate_below_35_sol"]["chad"] and "B-02" in sup["curve_gate_below_35_sol"]["status"]


def test_a_reserve_pass_does_not_override_other_requirements():
    em, ev = detect_at([1.0, 3.0, 5.0])
    assert gate(ev, CURRENT).status is Status.PASS and gate(ev, PEAK).status is Status.PASS
    assert gate(ev, "sybil_funder").status is Status.UNAVAILABLE      # funders not known yet
    assert ev.trade_permitted is False


def test_a_creation_seen_first_on_pumpportal_is_bounded_by_the_creators_own_buy():
    """Both feeds report a creation; the bus keeps whichever copy arrives first. PumpPortal's copy has no real
    reserve, but the curve starts empty, so the real SOL right after creation cannot exceed the creator's buy."""
    def pp_create(sol):
        msg = {"signature": "PPC1", "mint": "MintApump", "traderPublicKey": "DEV", "txType": "create",
               "initialBuy": 30_000_000.0 if sol else 0.0, "solAmount": sol, "bondingCurveKey": "BC1",
               "vTokensInBondingCurve": 1_000_000_000.0, "vSolInBondingCurve": 30.0 + sol, "marketCapSol": 28.0,
               "name": "A", "symbol": "A", "uri": "https://x", "pool": "pump"}
        return pumpportal_normalize(msg, 1, iter(range(1, 9)).__next__)

    for dev_buy, expected in ((2.0, Status.PASS), (12.0, Status.UNAVAILABLE)):
        em = EarlyMomentum()
        for ev in pp_create(dev_buy):
            em.on_event(ev)
        reals = [dev_buy + 0.5, dev_buy + 1.0, dev_buy + 1.5] if dev_buy < 5 else [3.0, 4.0, 5.0]
        [ev] = feed(em, [buy(p, i, v_quote=30.0 + r) for i, (p, r) in enumerate(zip("ABC", reals), start=1)])
        assert gate(ev, PEAK).status is expected, dev_buy
