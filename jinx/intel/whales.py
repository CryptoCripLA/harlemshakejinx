"""CHAD Whale Intelligence (Phase 2: C04; C06 cluster facts; owner requirements W1-W3, W6 facts).

CHAD (docs/CHAD_BLUEPRINT.md, "Whale Intelligence — Self-Growing Smart Money Network"):
- "Self-growing discovery — runs every 4 hours. Scans pump.fun on-chain activity, identifies wallets that bought
  tokens before graduation, promotes those with 40%+ win rate."
- "0–100 confidence score per wallet: Win rate (50 pts) + recency decay (25 pts) + trade volume (25 pts)". Win rate
  is "based on actual copy-trade outcomes over time"; recency: "Score halves every 14 days of inactivity"; volume:
  "More confirmed observed buys = more reliable signal".
- "Layer 1: passive real-time alerts when a tracked high-conviction wallet buys — zero API cost."
- "Add your own wallets — up to 15."
- Cluster: "3 or more tracked profitable wallets buy the same token within a short window".

Unspecified, so left unset (B-03): how observed buys map to the 25 volume points, the cluster "short window", the
co-buy window for second-degree discovery, and the look-back of the win rate used for promotion (GMGN's `period`).
The win-rate points need JINX's own copy-trade outcomes, which do not exist before Phase 3, so they are None.
A total is never shown while a component is missing.

Data: pre-graduation buyers come from the PumpAPI/PumpPortal stream (curve trades of a token that later migrates);
wallet statistics from GMGN `getWalletStats` (formats pinned by tests/fixtures/gmgn_wallet_stats_sample_20261021.json).
All state is in RAM and bounded (owner storage rule 2); promoted wallets are also written to the research store.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from jinx.core.events import EventKind, MarketEvent, Side

DISCOVERY_INTERVAL_S = 4 * 3600          # CHAD: "runs every 4 hours"
PROMOTION_WIN_RATE = 0.40                # CHAD: "promotes those with 40%+ win rate"
RECENCY_HALF_LIFE_S = 14 * 86_400        # CHAD: "Score halves every 14 days of inactivity"
MAX_PINNED = 15                          # CHAD: "Add your own wallets — up to 15"
CLUSTER_MIN_WALLETS = 3                  # CHAD: "3 or more tracked profitable wallets"
B03_VOLUME = "unset: CHAD does not say how observed buys map to the 25 volume points (B-03)"
B03_CLUSTER = "unset: CHAD's cluster 'short window' is not quantified (B-03)"
STATS_BATCH = 20


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class WalletEvidence:
    address: str
    period: str
    facts: dict

    @property
    def promotable(self) -> bool:
        wr = self.facts.get("win_rate")
        return wr is not None and wr >= PROMOTION_WIN_RATE


def wallet_evidence(stats: dict, period: str) -> WalletEvidence:
    pnl = stats.get("pnl_stat") or {}
    common = stats.get("common") or {}
    fees = [_num(stats.get("bought_fee")), _num(stats.get("sold_fee"))]
    facts = {
        "win_rate": _num(pnl.get("winrate")),
        "tokens_traded": pnl.get("token_num"),
        "buys": stats.get("buy"), "sells": stats.get("sell"),
        "realized_profit_usd": _num(stats.get("realized_profit")),
        "realized_return": _num(stats.get("realized_profit_pnl")),
        "fees_usd": sum(f for f in fees if f is not None) if any(f is not None for f in fees) else None,
        "multiples": {"below_-50%": pnl.get("pnl_lt_nd5_num"), "-50%_to_0": pnl.get("pnl_nd5_0x_num"),
                      "0_to_2x": pnl.get("pnl_0x_2x_num"), "2x_to_5x": pnl.get("pnl_2x_5x_num"),
                      "above_5x": pnl.get("pnl_gt_5x_num")},
        "avg_holding_s": _num(pnl.get("avg_holding_period")),
        "winning_vs_losing_hold_s": None,      # W2: GMGN reports one average; the split needs per-trade pairing
        "last_trade_ts": stats.get("last_timestamp"),
        "funded_by": common.get("fund_from_address") or None,
        "tags": list(common.get("tags") or []),
        "tokens_created": common.get("created_token_count"),
        "source": f"gmgn.getWalletStats period={period}",
    }
    return WalletEvidence(stats.get("wallet_address", ""), period, facts)


def confidence(last_trade_ts: float | None, now_ts: float, copy_trade_win_rate: float | None,
               observed_buys: int | None) -> dict:
    recency = None
    if last_trade_ts is not None:
        recency = 25.0 * 0.5 ** (max(0.0, now_ts - last_trade_ts) / RECENCY_HALF_LIFE_S)
    win = None if copy_trade_win_rate is None else 50.0 * copy_trade_win_rate
    out = {"win_rate_points": win, "recency_points": recency, "volume_points": None, "volume_status": B03_VOLUME,
           "observed_buys": observed_buys}
    parts = (win, recency, None)
    out["total"] = sum(parts) if all(p is not None for p in parts) else None
    return out


class WhaleBook:
    """Tracked wallets, Layer-1 alerts from the live stream, and pre-graduation buyers awaiting discovery."""

    def __init__(self, max_tokens: int = 5_000, max_buyers_per_token: int = 300, max_candidates: int = 5_000,
                 max_alerts: int = 1_000, max_tracked: int = 2_000) -> None:
        self.max_tokens, self.max_buyers, self.max_candidates = max_tokens, max_buyers_per_token, max_candidates
        self.max_tracked = max_tracked
        self.tracked: OrderedDict[str, dict] = OrderedDict()
        self.alerts: deque[dict] = deque(maxlen=max_alerts)
        self._curve_buyers: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()
        self._candidates: OrderedDict[str, None] = OrderedDict()
        self._tracked_buys: OrderedDict[str, list[tuple[str, int]]] = OrderedDict()

    # ---- tracked set ---------------------------------------------------------------
    def track(self, wallet: str, source: str, evidence: dict | None = None) -> None:
        self.tracked[wallet] = {"source": source, "since_ts": time.time(), "evidence": evidence or {}}
        self.tracked.move_to_end(wallet)
        while len(self.tracked) > self.max_tracked:
            victim = next((w for w, v in self.tracked.items() if v["source"] != "pinned"), None)
            if victim is None:
                break
            del self.tracked[victim]

    def pin(self, wallet: str) -> None:
        pinned = [w for w, v in self.tracked.items() if v["source"] == "pinned"]
        if wallet not in pinned and len(pinned) >= MAX_PINNED:
            raise ValueError(f"CHAD allows up to {MAX_PINNED} pinned wallets")
        self.track(wallet, "pinned")

    # ---- stream ----------------------------------------------------------------------
    def on_event(self, ev: MarketEvent) -> None:
        if ev.kind is EventKind.MIGRATION:
            for w in self._curve_buyers.pop(ev.mint, {}):
                self._candidates[w] = None
                self._candidates.move_to_end(w)
            while len(self._candidates) > self.max_candidates:
                self._candidates.popitem(last=False)
            return
        if ev.kind is not EventKind.TRADE or ev.side is not Side.BUY or not ev.trader:
            return
        if (ev.raw or {}).get("pool", "pump") == "pump":                  # bonding curve, before graduation
            buyers = self._curve_buyers.setdefault(ev.mint, OrderedDict())
            self._curve_buyers.move_to_end(ev.mint)
            if ev.trader not in buyers and len(buyers) < self.max_buyers:
                buyers[ev.trader] = None
            while len(self._curve_buyers) > self.max_tokens:
                self._curve_buyers.popitem(last=False)
        if ev.trader in self.tracked:                                      # Layer 1: zero API cost
            t_s = ev.recv_ns // 1_000_000_000
            self.alerts.append({"wallet": ev.trader, "mint": ev.mint, "signature": ev.signature, "ts": t_s,
                                "source": self.tracked[ev.trader]["source"]})
            buys = self._tracked_buys.setdefault(ev.mint, [])
            self._tracked_buys.move_to_end(ev.mint)
            buys.append((ev.trader, t_s))
            while len(self._tracked_buys) > self.max_tokens:
                self._tracked_buys.popitem(last=False)

    def discovery_candidates(self) -> set[str]:
        return set(self._candidates)

    def cluster_facts(self, mint: str) -> dict:
        buys = self._tracked_buys.get(mint, [])
        wallets = {w for w, _ in buys}
        times = [t for _, t in buys]
        return {"tracked_buyers": len(wallets), "span_s": (max(times) - min(times)) if times else None,
                "min_wallets": CLUSTER_MIN_WALLETS, "cluster": None, "cluster_status": B03_CLUSTER}


async def discovery_pass(gmgn, book: WhaleBook, now_ts: float | None = None, period: str = "7d",
                         research=None) -> dict:
    """One CHAD discovery cycle over the pre-graduation buyers collected since the last pass. Candidates whose
    statistics could not be fetched stay queued for the next pass."""
    now_ts = now_ts or time.time()
    candidates = list(book.discovery_candidates())
    report = {"candidates": len(candidates), "evaluated": 0, "promoted": [], "errors": [], "period": period}
    promoted_rows = []
    # One wallet per request: given several addresses GMGN's wallet_stats answers only the first (live 2026-10-21).
    # Requests go out STATS_BATCH at a time; the GMGN adapter's rate budget paces them.
    for i in range(0, len(candidates), STATS_BATCH):
        batch = candidates[i:i + STATS_BATCH]
        results = await asyncio.gather(*(gmgn.call("getWalletStats", "sol", [w], period) for w in batch),
                                       return_exceptions=True)
        for w, res in zip(batch, results):
            if isinstance(res, Exception):
                report["errors"].append(type(res).__name__)
                continue                                  # stays queued for the next pass
            for stats in (res if isinstance(res, list) else [res] if isinstance(res, dict) else []):
                ev = wallet_evidence(stats, period)
                report["evaluated"] += 1
                if ev.promotable and ev.address:
                    book.track(ev.address, "promoted", ev.facts)
                    report["promoted"].append(ev.address)
                    promoted_rows.append(ev)
            book._candidates.pop(w, None)
    if research is not None and promoted_rows:
        await research.upsert_tracked_wallets(promoted_rows, now_ts)
    return report
