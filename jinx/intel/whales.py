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

Data (owner storage repair 2026-10-21: "Remove the massive general-purpose per-token buyer archive ... Query GMGN for
historical wallet analysis instead of hoarding unrelated buyer histories"):
- the stream tells JINX which tokens graduated (MIGRATION events) and when; nothing about their other buyers is kept;
- at each discovery pass GMGN `getTokenTopTraders` names each graduated token's traders with `start_holding_at`; a
  trader who started holding before the migration time is a pre-graduation buyer and becomes a candidate. A trader
  without that time cannot be shown to have bought before graduation and is not a candidate;
- candidates' statistics come from GMGN `getWalletStats` (formats pinned by
  tests/fixtures/gmgn_wallet_stats_sample_20261021.json).
RAM holds only: tracked wallets (pinned and promoted), Layer-1 alerts, short-lived co-buy evidence of tracked wallets,
the graduated tokens awaiting the next pass and the candidate queue, each with a cap and, where it ages, an expiry.
Promoted wallets are also written to the research store.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from jinx.core.events import EventKind, MarketEvent, Side
from jinx.intel.readings import event_time

DISCOVERY_INTERVAL_S = 4 * 3600          # CHAD: "runs every 4 hours"
PROMOTION_WIN_RATE = 0.40                # CHAD: "promotes those with 40%+ win rate"
RECENCY_HALF_LIFE_S = 14 * 86_400        # CHAD: "Score halves every 14 days of inactivity"
MAX_PINNED = 15                          # CHAD: "Add your own wallets — up to 15"
CLUSTER_MIN_WALLETS = 3                  # CHAD: "3 or more tracked profitable wallets"
B03_VOLUME = "unset: CHAD does not say how observed buys map to the 25 volume points (B-03)"
B03_CLUSTER = "unset: CHAD's cluster 'short window' is not quantified (B-03)"
STATS_BATCH = 20
TOP_TRADERS_LIMIT = 100                  # GMGN `token_top_traders` page asked per graduated token (weight 5)
# RAM bounds (owner storage rule 2), not trading rules
CO_BUY_TTL_S = 1_800                     # co-buy evidence of tracked wallets is short-lived (receipt clock)
MAX_CO_BUYS_PER_MINT = 64
MAX_GRADUATED = 2_000                    # graduated tokens awaiting the next discovery pass
NO_START_TIME = "no start_holding_at: cannot be shown to have bought before graduation"


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
    """Tracked wallets, Layer-1 alerts from the live stream, co-buy evidence, and graduated tokens awaiting discovery."""

    def __init__(self, max_tokens: int = 2_000, max_candidates: int = 5_000, max_alerts: int = 500,
                 max_tracked: int = 2_000, co_buy_ttl_s: float = CO_BUY_TTL_S,
                 max_graduated: int = MAX_GRADUATED) -> None:
        self.max_tokens, self.max_candidates, self.max_graduated = max_tokens, max_candidates, max_graduated
        self.max_tracked = max_tracked
        self.co_buy_ttl_ns = int(co_buy_ttl_s * 1_000_000_000)
        self.tracked: OrderedDict[str, dict] = OrderedDict()
        self.alerts: deque[dict] = deque(maxlen=max_alerts)
        self._candidates: OrderedDict[str, None] = OrderedDict()
        self._graduated: OrderedDict[str, tuple[int, str]] = OrderedDict()   # mint -> (migration time ns, clock)
        # mint -> (last receipt ns, [(wallet, t_s)], incomplete reason or None): tracked wallets' buys only
        self._tracked_buys: OrderedDict[str, list] = OrderedDict()
        self.now_ns = 0
        self.stats = {"co_buys_expired": 0, "dropped_events": 0, "graduated_queued": 0, "graduated_dropped": 0}

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
        self.now_ns = max(self.now_ns, ev.recv_ns)
        self._expire()
        if ev.kind is EventKind.MIGRATION:
            if ev.mint not in self._graduated:
                t, clock = event_time(ev)
                self._graduated[ev.mint] = (t, str(clock))
                self.stats["graduated_queued"] += 1
                while len(self._graduated) > self.max_graduated:
                    self._graduated.popitem(last=False)
                    self.stats["graduated_dropped"] += 1
            return
        if ev.kind is not EventKind.TRADE or ev.side is not Side.BUY or not ev.trader:
            return
        if ev.trader in self.tracked:                                      # Layer 1: zero API cost
            t_s = ev.recv_ns // 1_000_000_000
            self.alerts.append({"wallet": ev.trader, "mint": ev.mint, "signature": ev.signature, "ts": t_s,
                                "source": self.tracked[ev.trader]["source"]})
            row = self._tracked_buys.get(ev.mint)
            if row is None:
                row = self._tracked_buys[ev.mint] = [ev.recv_ns, [], None]
            row[0] = max(row[0], ev.recv_ns)
            self._tracked_buys.move_to_end(ev.mint)
            if len(row[1]) < MAX_CO_BUYS_PER_MINT:
                row[1].append((ev.trader, t_s))
            else:
                row[2] = row[2] or f"more than {MAX_CO_BUYS_PER_MINT} tracked buys: later ones not kept"
            while len(self._tracked_buys) > self.max_tokens:
                self._tracked_buys.popitem(last=False)

    def on_dropped(self, ev: MarketEvent) -> None:
        """The bus dropped this event before it reached the book (background lane full). A dropped trade of a tracked
        wallet makes that token's co-buy facts incomplete; a dropped migration still queues the token."""
        self.stats["dropped_events"] += 1
        if ev.kind is EventKind.MIGRATION:
            self.on_event(ev)
        elif ev.kind is EventKind.TRADE and ev.trader in self.tracked:
            row = self._tracked_buys.setdefault(ev.mint, [ev.recv_ns, [], None])
            row[2] = "the bus dropped a tracked wallet's trade of this token (background lane full)"

    def _expire(self) -> None:
        cutoff = self.now_ns - self.co_buy_ttl_ns
        while self._tracked_buys:
            mint, row = next(iter(self._tracked_buys.items()))
            if row[0] >= cutoff:
                break
            del self._tracked_buys[mint]
            self.stats["co_buys_expired"] += 1

    def sweep(self, now_ns: int, held: frozenset = frozenset(), idle_ns: int | None = None) -> dict:
        self.now_ns = max(self.now_ns, now_ns)
        kept = {m: self._tracked_buys.pop(m) for m in held if m in self._tracked_buys}
        self._expire()
        for m, row in kept.items():                 # a held token's co-buy evidence is never expired
            self._tracked_buys[m] = row
        return {"tracked": len(self.tracked), "co_buy_tokens": len(self._tracked_buys),
                "graduated_queued": len(self._graduated), "candidates": len(self._candidates)}

    def discovery_candidates(self) -> set[str]:
        return set(self._candidates)

    def graduated(self) -> dict[str, tuple[int, str]]:
        return dict(self._graduated)

    def add_candidates(self, wallets) -> None:
        for w in wallets:
            self._candidates[w] = None
            self._candidates.move_to_end(w)
        while len(self._candidates) > self.max_candidates:
            self._candidates.popitem(last=False)

    def cluster_facts(self, mint: str) -> dict:
        row = self._tracked_buys.get(mint)
        buys = row[1] if row else []
        wallets = {w for w, _ in buys}
        times = [t for _, t in buys]
        return {"tracked_buyers": len(wallets), "span_s": (max(times) - min(times)) if times else None,
                "min_wallets": CLUSTER_MIN_WALLETS, "cluster": None, "cluster_status": B03_CLUSTER,
                "complete": row is None or row[2] is None, "incomplete_reason": row[2] if row else None}


def pre_graduation_buyers(traders, migration_ns: int) -> tuple[list[str], int]:
    """Wallets among GMGN's top traders of a graduated token that started holding before its migration, and how many
    traders had no start time (not candidates: unprovable)."""
    out, unproven = [], 0
    for t in traders if isinstance(traders, list) else []:
        if not isinstance(t, dict) or not isinstance(t.get("address"), str) or not t["address"]:
            continue
        start = t.get("start_holding_at")
        if isinstance(start, bool) or not isinstance(start, (int, float)) or start <= 0:
            unproven += 1
            continue
        if start * 1_000_000_000 < migration_ns:
            out.append(t["address"])
    return out, unproven


async def discovery_pass(gmgn, book: WhaleBook, now_ts: float | None = None, period: str = "7d",
                         research=None) -> dict:
    """One CHAD discovery cycle over the pre-graduation buyers collected since the last pass. Candidates whose
    statistics could not be fetched stay queued for the next pass."""
    now_ts = now_ts or time.time()
    report = {"graduated": 0, "graduated_errors": 0, "unproven_traders": 0, "candidates": 0, "evaluated": 0,
              "promoted": [], "errors": [], "period": period}
    # 1. Pre-graduation buyers of each graduated token, from GMGN (not from a local archive of every buyer).
    for mint, (migration_ns, _clock) in book.graduated().items():
        try:
            res = await gmgn.call("getTokenTopTraders", "sol", mint, {"limit": TOP_TRADERS_LIMIT})
        except Exception as e:
            report["graduated_errors"] += 1
            report["errors"].append(type(e).__name__)
            continue                                  # stays queued for the next pass
        rows = res.get("list") if isinstance(res, dict) else res
        wallets, unproven = pre_graduation_buyers(rows, migration_ns)
        report["graduated"] += 1
        report["unproven_traders"] += unproven
        book.add_candidates(wallets)
        book._graduated.pop(mint, None)
    candidates = list(book.discovery_candidates())
    report["candidates"] = len(candidates)
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
