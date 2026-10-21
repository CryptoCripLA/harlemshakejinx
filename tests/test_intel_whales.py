"""CHAD Whale Intelligence (Phase 2: C04, C06 facts, W1-W3, W6 facts).

CHAD: discovery "runs every 4 hours", "identifies wallets that bought tokens before graduation, promotes those with
40%+ win rate"; confidence "Win rate (50 pts) + recency decay (25 pts) + trade volume (25 pts)", win rate "based on
actual copy-trade outcomes", recency "Score halves every 14 days of inactivity", "Add your own wallets — up to 15".
Unquantified parts (volume points, cluster and co-buy windows, the stats look-back) stay unset (B-03)."""
import asyncio
import json
from pathlib import Path

import pytest

from jinx.core.events import EventKind, MarketEvent, Side
from jinx.intel.whales import (DISCOVERY_INTERVAL_S, PROMOTION_WIN_RATE, WhaleBook, confidence, discovery_pass,
                               wallet_evidence)

STATS = json.loads((Path(__file__).parent / "fixtures" / "gmgn_wallet_stats_sample_20261021.json").read_text())["stats"]
DAY = 86_400


def trade(mint, trader, t, side=Side.BUY, kind=EventKind.TRADE, pool="pump"):
    return MarketEvent(kind=kind, source="pumpapi", mint=mint, signature=f"{mint}{trader}{t}{side}", recv_ns=t * 10**9,
                       seq=t, trader=trader, side=side if kind is EventKind.TRADE else None, sol_lamports=1_000,
                       token_amount=1 if kind is EventKind.TRADE else None, raw={"pool": pool})


def test_chad_constants_are_quoted_exactly():
    assert DISCOVERY_INTERVAL_S == 4 * 3600 and PROMOTION_WIN_RATE == 0.40


def test_wallet_evidence_reads_gmgn_stats_with_fees_and_multiples():
    ev = wallet_evidence(STATS, period="7d")
    f = ev.facts
    assert f["win_rate"] == pytest.approx(0.3529, abs=1e-4) and f["tokens_traded"] == 362
    assert f["realized_profit_usd"] == pytest.approx(1790.0) and f["fees_usd"] == pytest.approx(954.81, abs=0.01)
    assert f["multiples"] == {"below_-50%": 6, "-50%_to_0": 223, "0_to_2x": 127, "2x_to_5x": 6, "above_5x": 0}
    assert f["avg_holding_s"] == pytest.approx(20885.8, abs=0.1)
    assert f["winning_vs_losing_hold_s"] is None          # W2 split needs per-trade pairing: not reported by GMGN
    assert f["funded_by"].startswith("DGRL")
    assert ev.promotable is False                         # 35% < CHAD's 40%


def test_promotion_requires_chads_forty_percent():
    assert wallet_evidence(dict(STATS, pnl_stat=dict(STATS["pnl_stat"], winrate=0.40)), "7d").promotable is True
    assert wallet_evidence(dict(STATS, pnl_stat=dict(STATS["pnl_stat"], winrate=None)), "7d").promotable is False


def test_confidence_uses_only_what_chad_defines():
    c = confidence(last_trade_ts=1_000 * DAY, now_ts=1_014 * DAY, copy_trade_win_rate=None, observed_buys=12)
    assert c["recency_points"] == pytest.approx(12.5)              # one 14-day half-life
    assert c["win_rate_points"] is None                            # needs JINX copy-trade outcomes
    assert c["volume_points"] is None and "B-03" in c["volume_status"]
    assert c["total"] is None                                      # never a partial total presented as the score
    c2 = confidence(last_trade_ts=1_000 * DAY, now_ts=1_000 * DAY, copy_trade_win_rate=0.6, observed_buys=1)
    assert c2["win_rate_points"] == pytest.approx(30.0) and c2["recency_points"] == pytest.approx(25.0)


def test_curve_buyers_of_a_graduated_token_become_discovery_candidates():
    book = WhaleBook()
    for i, w in enumerate(["A", "B", "C", "A"]):
        book.on_event(trade("M", w, 100 + i))
    book.on_event(trade("M", "LATE", 200, pool="pump-amm"))          # after the curve: not a pre-graduation buyer
    book.on_event(trade("M", None, 300, kind=EventKind.MIGRATION))
    book.on_event(trade("N", "Z", 100))                               # never graduated
    assert book.discovery_candidates() == {"A", "B", "C"}


def test_tracked_wallet_buys_raise_zero_cost_alerts_and_cluster_facts():
    book = WhaleBook()
    book.track("A", source="promoted"); book.track("B", source="promoted"); book.track("C", source="pinned")
    for w, t in (("A", 10), ("B", 20), ("C", 40), ("X", 41)):
        book.on_event(trade("M", w, t))
    assert [a["wallet"] for a in book.alerts] == ["A", "B", "C"]
    facts = book.cluster_facts("M")
    assert facts["tracked_buyers"] == 3 and facts["span_s"] == 30
    assert facts["cluster"] is None and "B-03" in facts["cluster_status"]   # "short window" unspecified


def test_pinned_wallets_are_capped_at_fifteen():
    book = WhaleBook()
    for i in range(15):
        book.pin(f"P{i}")
    with pytest.raises(ValueError):
        book.pin("P15")


def test_discovery_pass_promotes_from_gmgn_stats_and_survives_api_failure():
    class Gmgn:
        def __init__(self, fail=False):
            self.fail, self.calls = fail, []

        async def call(self, method, chain, wallets, period):
            self.calls.append((method, tuple(wallets), period))
            if self.fail:
                raise TimeoutError
            w = wallets[0]                      # like the live API: only the first address is answered
            return dict(STATS, wallet_address=w, pnl_stat=dict(STATS["pnl_stat"], winrate=0.5 if w == "GOOD" else 0.2))

    book = WhaleBook()
    for w in ("GOOD", "BAD"):
        book.on_event(trade("M", w, 1))
    book.on_event(trade("M", None, 2, kind=EventKind.MIGRATION))
    report = asyncio.run(discovery_pass(Gmgn(), book, now_ts=10))
    assert report["promoted"] == ["GOOD"] and book.tracked["GOOD"]["source"] == "promoted" and "BAD" not in book.tracked
    assert book.discovery_candidates() == set()                       # consumed
    book.on_event(trade("M2", "Q", 3)); book.on_event(trade("M2", None, 4, kind=EventKind.MIGRATION))
    failed = asyncio.run(discovery_pass(Gmgn(fail=True), book, now_ts=20))
    assert failed["errors"] and book.discovery_candidates() == {"Q"}  # kept for the next pass


def test_whale_state_is_bounded():
    book = WhaleBook(max_tokens=10, max_buyers_per_token=5, max_candidates=7, max_alerts=3)
    for i in range(50):
        for j in range(20):
            book.on_event(trade(f"M{i}", f"W{i}-{j}", i * 100 + j))
        book.on_event(trade(f"M{i}", None, i * 100 + 99, kind=EventKind.MIGRATION))
    assert len(book._curve_buyers) <= 10 and len(book._candidates) <= 7
    assert all(len(v) <= 5 for v in book._curve_buyers.values())
