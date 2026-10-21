"""CHAD Bot 5, Graduation Signals (C05): bonding-curve reserve and graduation-progress observations.

CHAD (docs/CHAD_BLUEPRINT.md, Bot 5): "Monitors every pump.fun bonding curve in real time via Helius gRPC. Scores
each token's progress toward graduation (0–20 pts) and feeds it into the Confluence board. Tokens near the threshold
get elevated signal weight." The 0–20 range is CHAD's; the progress-to-points mapping, "near the threshold" and the
"elevated signal weight" are not (B-03), so no points are produced unless an owner mapping is supplied."""
import json

import pytest

from jinx.core.events import EventKind, MarketEvent, Side
from jinx.intel.graduation import (INITIAL_REAL_TOKENS, INITIAL_VIRTUAL_SOL, INITIAL_VIRTUAL_TOKENS,
                                   GraduationTracker)
from jinx.sim import capture_events, load_capture
from tests.pumpapi_msgs import K, amm_trade, buy, create, events, migrate, one, sell
from tests.test_intel_replay import FIXTURE

RECORDS = load_capture(FIXTURE)


def feed(g, msgs):
    out = []
    for m in msgs:
        for ev in events(m):
            out += g.on_event(ev)
    return out


def test_d3ads_token_offset_matches_every_live_curve_reading():
    """PumpAPI reports real reserves beside the virtual ones. On the live capture the token difference is D3AD's
    recorded initial offset (pumpfun.rs:539-540) on every reading, which the progress reading relies on."""
    curve = [r["raw"] for r in RECORDS if r["raw"]["pool"] == "pump"]
    assert len(curve) >= 1000
    offset = (INITIAL_VIRTUAL_TOKENS - INITIAL_REAL_TOKENS) / 1e6
    for raw in curve:
        assert raw["vTokensInBondingCurve"] - raw["tokensInPool"] == pytest.approx(offset, abs=1e-3)


def test_curves_that_are_not_standard_30_sol_curves_are_flagged_not_reinterpreted():
    """Live finding: 26 of 118 curve mints in the capture report virtual SOL far below the 30 SOL a standard curve
    starts at, with a virtual-minus-real SOL gap that changes trade by trade. Their readings are kept as reported and
    flagged; real SOL is never derived from virtual SOL."""
    by_mint = {}
    for r in RECORDS:
        raw = r["raw"]
        if raw["pool"] == "pump":
            gap = raw["vQuoteInBondingCurve"] - raw["quoteInPool"]
            by_mint.setdefault(raw["mint"], set()).add(abs(gap - INITIAL_VIRTUAL_SOL / 1e9) < 1e-6)
    standard = {m for m, flags in by_mint.items() if flags == {True}}
    assert len(by_mint) == 118 and len(by_mint) - len(standard) == 26
    g = GraduationTracker()
    for ev in (e for evs in capture_events(RECORDS, source="pumpapi") if evs for e in evs):
        g.on_event(ev)
    for mint in by_mint:
        latest = g.evidence(mint).latest
        assert latest.standard_curve is (mint in standard)
        # progress is defined for a standard curve only: 5 of the others hold more than 793.1M sellable tokens
        assert (latest.progress is not None) is (mint in standard)
        assert (latest.progress_sol_mako is not None) is (mint in standard)


def test_progress_is_the_share_of_the_curves_sellable_tokens_already_bought():
    g = GraduationTracker()
    [ev] = feed(g, [buy("A", 1, v_quote=40.0)])
    sold = 1_073_000_000 - K / 40.0
    assert ev.latest.progress == pytest.approx(sold / 793_100_000, rel=1e-9)
    assert ev.latest.real_sol_lamports == 10_000_000_000 and ev.latest.reserves_basis == "provider"
    assert ev.latest.complete is False and ev.latest.standard_curve is True


def test_makos_reading_is_real_sol_over_85_capped_at_one():
    g = GraduationTracker()
    [a] = feed(g, [buy("A", 1, v_quote=72.5)])
    assert a.latest.progress_sol_mako == pytest.approx(42.5 / 85)
    [b] = feed(g, [buy("B", 2, v_quote=120.0)])
    assert b.latest.progress_sol_mako == 1.0


def test_a_curve_is_complete_when_its_real_token_reserve_is_exhausted():
    g = GraduationTracker()
    msg = buy("A", 1, v_quote=114.9)
    msg["tokensInPool"] = 0.0
    [ev] = feed(g, [msg])
    assert ev.latest.complete is True and ev.latest.progress == 1.0 and ev.complete_observed


def test_no_score_is_invented():
    g = GraduationTracker()
    [ev] = feed(g, [buy("A", 1, v_quote=60.0)])
    assert ev.score_points is None and "B-03" in ev.score_status
    assert ev.near_threshold is None and "no provider status yet" in ev.near_threshold_status
    assert "graduation_score" in ev.blocking


def test_an_owner_mapping_is_applied_only_inside_chads_0_to_20_range():
    g = GraduationTracker(score_mapping=lambda progress: 20 * progress)
    [ev] = feed(g, [buy("A", 1, v_quote=40.0)])
    assert ev.score_points == pytest.approx(20 * ev.latest.progress) and "graduation_score" not in ev.blocking
    bad = GraduationTracker(score_mapping=lambda progress: 25.0)
    [ev] = feed(bad, [buy("A", 1, v_quote=40.0)])
    assert ev.score_points is None and "outside 0–20" in ev.score_status and bad.stats["score_errors"] == 1


def test_a_late_reading_counts_toward_the_peak_but_never_replaces_the_latest():
    g = GraduationTracker()
    feed(g, [create(), buy("A", 10, v_quote=35.0)])
    [ev] = feed(g, [buy("B", 5, v_quote=50.0)])          # happened earlier, arrives later
    assert ev.latest.v_sol_lamports == 35_000_000_000 and ev.late_readings == 1
    assert ev.peak_v_sol_lamports == 50_000_000_000 and ev.peak_progress > ev.latest.progress


def test_sells_move_the_reading_down_and_the_peak_stays():
    g = GraduationTracker()
    feed(g, [create(), buy("A", 1, v_quote=60.0)])
    [ev] = feed(g, [sell("A", 2, v_quote=45.0)])
    assert ev.latest.v_sol_lamports == 45_000_000_000 and ev.peak_v_sol_lamports == 60_000_000_000
    assert ev.observed_since_creation is True


def test_pumpportal_curve_events_carry_virtual_reserves_only_and_no_progress():
    g = GraduationTracker()
    ev = MarketEvent(kind=EventKind.TRADE, source="mako.pumpportal", mint="MintApump", signature="PP1", trader="DEV",
                     side=Side.BUY, sol_lamports=1_000_000_000, token_amount=34_000_000_000_000,
                     v_sol_lamports=31_000_000_000, v_tokens=1_038_387_096_774_194, recv_ns=10, seq=1)
    [out] = g.on_event(ev)
    assert out.latest.reserves_basis == "virtual only" and out.latest.v_sol_lamports == 31_000_000_000
    assert out.latest.real_sol_lamports is None and out.latest.progress is None and out.latest.standard_curve is None
    assert out.latest.clock == "receipt"

def test_post_migration_trades_and_migrations_are_not_curve_readings():
    g = GraduationTracker()
    assert feed(g, [amm_trade("buy"), migrate()]) == []
    assert g.evidence("MintApump") is None


def test_state_is_bounded():
    g = GraduationTracker(max_mints=3, idle_ttl_s=60)
    for i in range(10):
        feed(g, [buy("A", i * 0.01, mint=f"M{i}")])
    assert g.tracked_mints() == 3
    feed(g, [buy("A", 3600, mint="LATE")])
    assert g.tracked_mints() == 1


def test_evidence_carries_the_confluence_fields_and_serializes():
    g = GraduationTracker()
    [ev] = feed(g, [buy("A", 1, v_quote=40.0)])
    d = json.loads(json.dumps(ev.to_dict()))
    assert {"mint", "signal_type", "sources", "observed_ns", "event_ns", "clock", "freshness", "payload",
            "blocking", "score_points", "score_status"} <= set(d)
    assert d["signal_type"] == "graduation" and d["payload"]["latest"]["progress"] == pytest.approx(ev.latest.progress)


def test_near_graduation_is_the_providers_verified_status_not_a_jinx_number():
    """Owner order 2026-10-21 item 6: verified provider statuses (GMGN Trenches category) and source-derived progress;
    no invented threshold, no 0-20 formula."""
    g = GraduationTracker()
    feed(g, [buy("A", 1, v_quote=100.0)])
    assert g.evidence("MintApump").near_threshold is None
    g.record_provider_status("MintApump", "near_completion", "gmgn.trenches", 123, progress=0.98)
    ev = g.evidence("MintApump")
    assert ev.near_threshold is True and "gmgn.trenches" in ev.near_threshold_status
    assert ev.provider_status == {"category": "near_completion", "progress": 0.98, "source": "gmgn.trenches",
                                  "observed_ns": 123}
    assert ev.score_points is None and "graduation_score" in ev.blocking         # 0-20 still not invented
    g.record_provider_status("MintApump", "completed", "gmgn.trenches", 456, progress=1.0)
    assert g.evidence("MintApump").near_threshold is False
    g.record_provider_status("NeverSeen", "near_completion", "gmgn.trenches", 1, progress=0.9)
    assert g.evidence("NeverSeen") is None and g.tracked_mints() == 1
