"""Integration health: one JSON document describing whether JINX is fit to run.

failed   - the ledger store is unhealthy or the paper wallet does not reconcile. Do not trade.
degraded - running, but a live feed (PumpPortal through Mako, or the PumpAPI trade stream Early Momentum depends
           on) is down, stale or stalled on writes, PumpPortal has unsent unsubscribes or unconfirmed wanted
           subscriptions, or the slot setting is unreadable.
ok       - everything checked is healthy.

Latency figures are rolling-window summaries from constant-memory histograms (jinx/core/latency.py): local
processing inside JINX, never order-execution latency.
"""
from __future__ import annotations

import statistics

from jinx.ledger import PositionLedger
from jinx.modes import get_execution_mode, get_mode
from jinx.paper import PaperWallet
from jinx.store import Store


def percentiles_ms(samples_ns: list[int]) -> dict | None:
    if not samples_ns:
        return None
    s = sorted(samples_ns)
    pick = lambda q: s[min(len(s) - 1, int(q * len(s)))] / 1e6
    return {"n": len(s), "p50": pick(0.50), "p95": pick(0.95), "p99": pick(0.99), "max": s[-1] / 1e6,
            "mean": statistics.fmean(s) / 1e6}


FEED_ADAPTERS = ("mako", "pumpapi.stream")   # the live market feeds whose health decides "ok"


def build_health(store: Store, ledger: PositionLedger, wallet: PaperWallet, adapters: list[dict],
                 bus_stats: dict, now_ns: int, latency: dict | None = None,
                 feed_stale_after_ns: int | None = None, receive_latency: dict | None = None) -> dict:
    """`store` is the durable ledger store. Market data is RAM only (owner order 2026-10-21 §1), so event counts
    come from the bus. `latency` and `receive_latency` are histogram summaries (jinx/core/latency.py)."""
    rec = wallet.reconcile()
    store_ok = store.journal_mode() == "wal" and store.quick_check()
    feeds = [a for a in adapters if a.get("adapter") in FEED_ADAPTERS]
    feed_ok = bool(feeds) and all(f.get("connected") for f in feeds)
    if feed_ok and feed_stale_after_ns is not None:
        feed_ok = all(f.get("last_message_ns") and now_ns - f["last_message_ns"] <= feed_stale_after_ns for f in feeds)
    if any(f.get("ingest_stalled") or f.get("pending_unsubscribes") or f.get("unconfirmed_subscriptions")
           for f in feeds):
        feed_ok = False
    max_slots = ledger.max_slots()
    if not (store_ok and rec.ok):
        status = "failed"
    elif feed_ok and max_slots is not None:
        status = "ok"
    else:
        status = "degraded"
    return {
        "status": status,
        "mode": str(get_mode(store)),
        "execution_mode": str(get_execution_mode(store)),
        "slots": {"max": max_slots, "active": ledger.active_slots()},
        "paper_wallet": {"reconciled": rec.ok, "starting_lamports": rec.starting_lamports,
                         "balance_lamports": rec.balance_lamports},
        "store": {"ok": store_ok, "events": bus_stats.get("published", 0),
                  "market_store": "none: market state is RAM only", "legacy_market_rows": store.legacy_market_rows()},
        "adapters": adapters,
        "bus": bus_stats,
        # local processing inside JINX, not order-execution latency
        "admission_to_state_ms": latency,
        "receive_to_state_ms": receive_latency,
    }
