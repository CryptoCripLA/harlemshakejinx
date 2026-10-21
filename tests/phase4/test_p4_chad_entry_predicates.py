"""V04: every documented CHAD entry predicate, verified through the running engine's decision view.

Each test starts from one scenario in which every predicate passes (`baseline`) and changes exactly one fact; the
entry decision view (`JinxApp.entry_check`, the view the Phase 3 decision reads) must then refuse the entry and name
the predicate. Line numbers are `docs/CHAD_BLUEPRINT.md` (verbatim CHAD.txt). Owner decisions that refine CHAD are
cited by blocker id (docs/BLOCKERS.md). Events are PumpAPI messages in the live field layout (tests/pumpapi_msgs.py)
normalized by the real adapter; GMGN and RugCheck are fakes answering with recorded live shapes. Nothing here places
an order.

CHAD entry predicates with no implementation to verify (no data source or no CHAD number) are listed in
`docs/PHASE4_VERIFICATION_MATRIX.md` as BLOCKED with their blocker: X Scout RUG_FILTER (L106, B-05), TG Scout (L114,
B-06), the cluster "short window" (L16/L41, B-03), the global confluence threshold (B-03). The cluster test below
checks that the unset window stays fail-closed (no cluster is ever reported as firing)."""
from pathlib import Path

import pytest

from jinx.intel.early_momentum import SUPERSEDED
from tests.phase4 import harness as h
from tests.pumpapi_msgs import buy, create, events, migrate, sell

M = "P4Mintpump"


class LiveFeed:
    def __init__(self, live=True):
        self.live = live

    def health(self):
        return {"connected": self.live, "ingest_stalled": False}


def funders(mapping: dict) -> dict:
    return {w: [{"wallet_address": w, "common": {"fund_from_address": f, "fund_from_ts": 1_792_539_000}}]
            for w, f in mapping.items()}


DISTINCT = funders({w: f"F-{w}" for w in ("DEV", "A", "B", "C", "D")})


def baseline_msgs():
    return [create(mint=M, dev="DEV", v_quote=31.0), buy("A", 1, v_quote=31.5, mint=M),
            buy("B", 2, v_quote=32.0, mint=M), buy("C", 3, v_quote=32.5, mint=M)]


def decide(tmp_path: Path, msgs, gmgn=None, rugcheck=None, feed_live=True):
    gmgn = gmgn or h.FakeGmgn(wallet_stats=DISTINCT)
    app = h.build_app(tmp_path, gmgn=gmgn, rugcheck=rugcheck or h.FakeRugCheck())
    app.pumpapi = LiveFeed(feed_live)
    stats = h.run(app, [events(m) for m in msgs])
    assert stats.handler_errors == {}
    out = app.entry_check(M), app.early.evidence(M), app
    return out


@pytest.fixture
def close_apps():
    apps = []
    yield apps
    for a in apps:
        h.close(a)


def run_case(tmp_path, close_apps, msgs, **kw):
    check, evidence, app = decide(tmp_path, msgs, **kw)
    close_apps.append(app)
    return check, evidence, app


def test_baseline_every_predicate_passes_and_the_entry_is_eligible(tmp_path, close_apps):
    check, ev, _ = run_case(tmp_path, close_apps, baseline_msgs())
    assert check["all_checks_pass"] is True and check["reserve_eligible"] and check["safety_eligible"]
    assert {p.name: str(p.status) for p in ev.predicates} == {
        "three_buys_three_fee_payers_30s": "PASS", "sol_reserve_read": "PASS",
        "current_reserve_0_5_to_10_real_sol": "PASS", "peak_reserve_max_10_real_sol": "PASS", "sybil_funder": "PASS"}


# ---- CHAD L65 "3 buys · 3 unique wallets · 30-second window"; L78 "3 distinct fee payers required — not 3 txs from one
# wallet" -----------------------------------------------------------------------------------------------------------

def test_L78_three_buys_from_one_wallet_never_count_as_three_wallets(tmp_path, close_apps):
    """The creator's buy (one fee payer, owner 2026-10-21 item 4) plus four buys by wallet A: five buys, two wallets."""
    msgs = [create(mint=M, dev="DEV", v_quote=31.0)] + [buy("A", t, v_quote=31.0 + 0.5 * t, mint=M) for t in (1, 2, 3, 4)]
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert ev is None and check["all_checks_pass"] is False and check["early_momentum_blocking"] == ["no detection"]


def test_L65_three_wallets_spread_over_more_than_30_seconds_do_not_trigger(tmp_path, close_apps):
    msgs = [create(mint=M, dev="DEV", v_quote=31.0), buy("A", 15, v_quote=31.5, mint=M),
            buy("B", 30.001, v_quote=32.0, mint=M)]
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert ev is None and check["all_checks_pass"] is False


def test_L65_three_wallets_exactly_30_seconds_apart_trigger(tmp_path, close_apps):
    msgs = [create(mint=M, dev="DEV", v_quote=31.0), buy("A", 15, v_quote=31.5, mint=M),
            buy("B", 30, v_quote=32.0, mint=M)]
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert ev is not None and check["all_checks_pass"] is True


def test_L65_sells_never_count_as_buyers(tmp_path, close_apps):
    msgs = [create(mint=M, dev="DEV", v_quote=31.0), buy("A", 1, v_quote=31.5, mint=M),
            sell("B", 2, v_quote=31.4, mint=M), sell("C", 3, v_quote=31.3, mint=M)]
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert ev is None and check["all_checks_pass"] is False


# ---- CHAD L68-69 "0.5–10 SOL bonding curve gate ... (0.5 SOL floor) ... (10 SOL ceiling)"; owner B-02: real SOL -----

def test_L68_below_the_half_sol_floor_is_refused(tmp_path, close_apps):
    msgs = [create(mint=M, dev="DEV", v_quote=30.1, quote=0.1), buy("A", 1, v_quote=30.2, mint=M, sol=0.1),
            buy("B", 2, v_quote=30.3, mint=M, sol=0.1), buy("C", 3, v_quote=30.45, mint=M, sol=0.15)]
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert check["reserve_eligible"] is False and check["all_checks_pass"] is False
    assert "current_reserve_0_5_to_10_real_sol" in ev.blocking


def test_L68_exactly_half_a_sol_is_inside_the_gate(tmp_path, close_apps):
    msgs = [create(mint=M, dev="DEV", v_quote=30.2), buy("A", 1, v_quote=30.3, mint=M),
            buy("B", 2, v_quote=30.4, mint=M), buy("C", 3, v_quote=30.5, mint=M)]
    check, _, _ = run_case(tmp_path, close_apps, msgs)
    assert check["reserve_eligible"] is True


def test_L69_exactly_ten_sol_is_inside_the_gate(tmp_path, close_apps):
    msgs = baseline_msgs() + [buy("D", 4, v_quote=40.0, mint=M, sol=7.5)]
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert ev.reserves["current_real_sol"] == pytest.approx(10.0) and check["reserve_eligible"] is True


def test_L69_above_the_ten_sol_ceiling_is_refused(tmp_path, close_apps):
    msgs = baseline_msgs() + [buy("D", 4, v_quote=40.5, mint=M, sol=8.0)]
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert check["reserve_eligible"] is False and check["all_checks_pass"] is False
    assert ev.reserves["current_real_sol"] == pytest.approx(10.5)


def test_L61_the_35_sol_statement_is_kept_only_as_a_superseded_specification(tmp_path, close_apps):
    assert "curve_gate_below_35_sol" in SUPERSEDED and "superseded" in SUPERSEDED["curve_gate_below_35_sol"]["status"]
    msgs = baseline_msgs() + [buy("D", 4, v_quote=50.0, mint=M, sol=17.0)]       # 20 real SOL: under 35, over 10
    check, _, _ = run_case(tmp_path, close_apps, msgs)
    assert check["all_checks_pass"] is False


# ---- CHAD L61 "The peak SOL gate catches pump-and-dump retraces"; L227-228 "tracks all-time high
# virtualSolReserves ... ran to 60 SOL and dumped to 25 SOL looks early on a snapshot"; owner B-02 peak <= 10 real SOL --

def test_L227_a_token_that_ran_past_the_ceiling_and_came_back_is_refused(tmp_path, close_apps):
    msgs = [create(mint=M, dev="DEV", v_quote=31.0), buy("X", 0.5, v_quote=42.0, mint=M, sol=11.0),
            sell("X", 0.8, v_quote=33.0, mint=M, sol=9.0), buy("A", 1, v_quote=33.5, mint=M),
            buy("B", 2, v_quote=34.0, mint=M)]
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert ev is not None and ev.reserves["current_real_sol"] == pytest.approx(4.0)       # looks early on a snapshot
    assert "peak_reserve_max_10_real_sol" in ev.blocking and check["all_checks_pass"] is False


def test_B02_a_token_whose_creation_was_not_observed_has_no_provable_peak(tmp_path, close_apps):
    msgs = baseline_msgs()[1:]                                    # joined after the launch
    msgs.insert(0, buy("DEV", 0, v_quote=31.0, mint=M))
    check, ev, _ = run_case(tmp_path, close_apps, msgs)
    assert "peak_reserve_max_10_real_sol" in ev.blocking and check["all_checks_pass"] is False


def test_B02_a_detection_that_left_the_range_before_the_decision_is_refused_at_the_decision(tmp_path, close_apps):
    check, _, app = run_case(tmp_path, close_apps, baseline_msgs())
    assert check["all_checks_pass"] is True
    h.run(app, [events(buy("D", 5, v_quote=41.0, mint=M, sol=9.0))])
    assert app.entry_check(M)["all_checks_pass"] is False


def test_B02_a_migrated_token_is_outside_this_entry_path(tmp_path, close_apps):
    msgs = baseline_msgs() + [migrate(mint=M, t_ms=1_792_540_010_000)]
    check, _, _ = run_case(tmp_path, close_apps, msgs)
    assert check["migrated"] is True and check["all_checks_pass"] is False


# ---- CHAD L79-80 "SOL reserve check: Live bonding curve read — confirms real money in" ---------------------------------

def test_L79_without_a_live_trade_feed_the_reserve_cannot_be_confirmed(tmp_path, close_apps):
    check, _, _ = run_case(tmp_path, close_apps, baseline_msgs(), feed_live=False)
    assert check["reserve_eligible"] is False and check["reserve_reason"].startswith("stale")
    assert check["all_checks_pass"] is False


# ---- CHAD L81-82 "Sybil funder check: If 3+ wallets share the same SOL funder → emergency exit fires instantly";
# owner 2026-10-21 item 5: missing funder data is never a clean result ---------------------------------------------------

def test_L81_three_buyers_sharing_one_funder_block_and_raise_the_emergency_exit(tmp_path, close_apps):
    shared = funders({"DEV": "F-DEV", "A": "SAME", "B": "SAME", "C": "SAME"})
    msgs = [create(mint=M, dev="DEV", v_quote=31.0, initial_buy=0.0, quote=0.0)] + [
        buy(w, t, v_quote=31.0 + 0.5 * t, mint=M) for w, t in (("A", 1), ("B", 2), ("C", 3))]
    check, ev, _ = run_case(tmp_path, close_apps, msgs, gmgn=h.FakeGmgn(wallet_stats=shared))
    assert "sybil_funder" in ev.blocking and ev.emergency_exit is True and check["all_checks_pass"] is False


def test_L81_an_unknown_funder_is_unavailable_never_clean(tmp_path, close_apps):
    partial = funders({"DEV": "F-DEV", "A": "F-A"})                     # B's funder is unknown
    check, ev, _ = run_case(tmp_path, close_apps, baseline_msgs(), gmgn=h.FakeGmgn(wallet_stats=partial))
    sybil = next(p for p in ev.predicates if p.name == "sybil_funder")
    assert str(sybil.status) == "UNAVAILABLE" and check["all_checks_pass"] is False


# ---- CHAD L61 "Fires immediately the moment P1 safety checks pass"; L74-75 "Full safety check still fires ... in
# parallel"; L16 "Runs RugCheck, honeypot detection, and mint/freeze authority analysis on every candidate" ----------

def test_L74_an_entry_waits_for_a_passing_safety_verdict(tmp_path, close_apps):
    check, _, _ = run_case(tmp_path, close_apps, baseline_msgs(), gmgn=h.FakeGmgn(mode="down", wallet_stats=DISTINCT))
    assert check["safety_eligible"] is False and check["all_checks_pass"] is False


def test_L16_without_a_rugcheck_answer_the_safety_check_is_not_a_pass(tmp_path, close_apps):
    check, _, app = run_case(tmp_path, close_apps, baseline_msgs(), rugcheck=h.FakeRugCheck(mode="garbage"))
    assert app.candidates[M]["safety"]["verdict"] == "UNKNOWN" and check["all_checks_pass"] is False


# ---- CHAD L16/L41 cluster: "3 or more tracked profitable wallets buy the same token within a short window" ----------

def test_L41_the_unquantified_cluster_window_never_reports_a_firing_cluster(tmp_path, close_apps):
    _, _, app = run_case(tmp_path, close_apps, baseline_msgs())
    for w in ("A", "B", "C"):
        app.whales.track(w, "test", {"why": "labelled test fixture: tracked wallet"})
    facts = app.whales.cluster_facts(M)
    assert facts["cluster"] is None and "B-03" in facts["cluster_status"]
