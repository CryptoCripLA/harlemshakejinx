"""V13 anti-lookahead: no decision reads data that arrived, or happened, after the decision.

Owner directives: "Use future market observations in historical decisions" is forbidden (CLAUDE.md zero-drift rules);
"Do not use historical hindsight to select an advantageous sell price" (2026-10-21 §3.6); DataNomad's no-lookahead
methodology is the reference (CLAUDE.md approved sources). Checks:

1. Prefix equivalence on the live capture: every Early Momentum detection, and both reserve gates as the entry
   decision reads them, are identical to what a fresh engine computes from only the records received up to that
   moment. So nothing received later can have shaped them.
2. Every buy in a detection window happened at or before the triggering buy, and was received at or before the
   detection.
3. Developer history counts only launches created strictly before the decision time.
4. A paper fill is priced on the curve as observed when the order was placed; later readings (including a later peak)
   never re-price it, and a sell is priced on the state at the sell, never the peak."""
import asyncio

import pytest

from jinx.execution import EntryIntent, ExitIntent
from jinx.intel.dev_reputation import dev_history
from jinx.modes import Mode, set_mode
from jinx.paper import sell_proceeds
from jinx.sim import load_capture
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import M, baseline_msgs
from tests.pumpapi_msgs import buy, events

LIVE = load_capture(h.FIXTURES / "pumpapi-live-20261021.jsonl.gz")


def streaming_decisions(tmp_path):
    """Replay in arrival order; record each detection's evidence and gates at the moment it is emitted, with the
    index of the record being processed."""
    app = h.build_app(tmp_path)
    seen, at = [], {"i": -1}
    original = app._on_early_momentum

    def capture(evidence):
        if evidence.mint not in {s[1] for s in seen}:          # first emission = the detection decision
            seen.append((at["i"], evidence.mint, h._canon(evidence.to_dict()),
                         h._canon(app.early.reserve_check(evidence.mint, feed_live=True))))
        return original(evidence)
    app.early.notify = capture

    async def go():
        runner = asyncio.create_task(app.bus.run())
        for i, evs in enumerate(h.deliveries(LIVE, "pumpapi")):
            at["i"] = i
            for ev in evs or ():
                await app.bus.publish(ev)
            await app.bus.drain()
        runner.cancel()
    h.loop_of(app).run_until_complete(go())
    h.close(app)
    return seen


@pytest.fixture(scope="module")
def decisions(tmp_path_factory):
    return streaming_decisions(tmp_path_factory.mktemp("stream"))


def test_the_live_capture_has_detections_to_check(decisions):
    assert len(decisions) == 38


def test_each_detection_equals_what_only_the_records_received_so_far_produce(decisions, tmp_path):
    for n, (i, mint, evidence, gates) in enumerate(decisions):
        app = h.build_app(tmp_path / f"prefix{n}")
        h.run(app, h.deliveries(LIVE[:i + 1], "pumpapi"), settle=False)
        ev = app.early.evidence(mint)
        assert ev is not None, mint
        assert h._canon(ev.to_dict()) == evidence, f"detection of {mint} at record {i} used later data"
        assert h._canon(app.early.reserve_check(mint, feed_live=True)) == gates, mint
        h.close(app)


def test_window_buys_happened_before_the_trigger_and_were_received_before_the_detection(decisions):
    for _, mint, evidence, _ in decisions:
        window = evidence["payload"]["window"]
        trigger = window[-1]
        assert all(b["event_ns"] <= trigger["event_ns"] for b in window), mint
        # observed_ns = receipt of the arrival that completed the detection: nothing received later is in it
        assert all(b["recv_ns"] <= evidence["observed_ns"] for b in window), mint


def test_developer_history_counts_only_launches_before_the_decision():
    decision = 1_792_543_000.0
    created = {"inner_count": 3, "tokens": [
        {"token_address": "old", "create_timestamp": decision - 3600, "is_open": False, "token_ath_mc": "1000"},
        {"token_address": "same-second", "create_timestamp": decision, "is_open": True, "token_ath_mc": "9e9"},
        {"token_address": "future", "create_timestamp": decision + 60, "is_open": True, "token_ath_mc": "9e9"}]}
    facts = dev_history("DEV", created, decision, 0).facts
    assert facts["launches_before_decision"] == 1 and facts["graduated_before_decision"] == 0
    assert facts["best_ath_mc_usd_before_decision"] == 1000.0
    assert facts["gmgn_lifetime_counts"]["decision_time_safe"] is False


def test_a_paper_fill_is_priced_at_the_order_and_never_re_priced_by_later_readings(tmp_path):
    app = h.build_app(tmp_path)
    h.run(app, [events(m) for m in baseline_msgs()])
    set_mode(app.store, Mode.AUTO, by="phase4-test")
    pos = app.gateway.submit_entry(EntryIntent("e1", M, 50_000_000)).position_id
    bought = app.ledger.get(pos)
    h.run(app, [events(buy("PEAK", 5, v_quote=39.0, mint=M, sol=6.0)),          # a later peak
                events(buy("LATE", 6, v_quote=33.0, mint=M, sol=0.1))])           # then the curve falls back
    assert app.ledger.get(pos).cost_lamports == bought.cost_lamports
    assert app.ledger.get(pos).token_amount == bought.token_amount
    tok = app.core.tokens.get(M)
    expected, _ = sell_proceeds(tok.v_sol_lamports, tok.v_tokens, bought.token_amount, app.paper.fees.total_bps)
    k = tok.v_sol_lamports * tok.v_tokens                                       # constant product of the curve
    peak_price, _ = sell_proceeds(tok.peak_v_sol_lamports, k // tok.peak_v_sol_lamports, bought.token_amount,
                                  app.paper.fees.total_bps)
    assert app.gateway.submit_exit(ExitIntent("x1", pos)).ok
    sold = app.ledger.get(pos)
    assert sold.proceeds_lamports == expected                                   # the state at the sell
    assert sold.proceeds_lamports < peak_price                                   # never the hindsight peak
    h.close(app)
