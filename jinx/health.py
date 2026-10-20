"""Integration health: one JSON document describing whether JINX is fit to run.

failed   - the store is unhealthy or the paper wallet does not reconcile. Do not trade.
degraded - running, but the live feed is down, stale or stalled on writes, stored events are waiting
           to be applied, or the slot setting is unreadable.
ok       - everything checked is healthy.
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


def build_health(store: Store, ledger: PositionLedger, wallet: PaperWallet, adapters: list[dict],
                 bus_stats: dict, now_ns: int, latency_ns: list[int] | None = None,
                 feed_stale_after_ns: int | None = None, receive_latency_ns: list[int] | None = None) -> dict:
    rec = wallet.reconcile()
    store_ok = store.journal_mode() == "wal" and store.quick_check()
    feeds = [a for a in adapters if a.get("adapter") == "mako"]
    feed_ok = bool(feeds) and all(f.get("connected") for f in feeds)
    if feed_ok and feed_stale_after_ns is not None:
        feed_ok = all(f.get("last_message_ns") and now_ns - f["last_message_ns"] <= feed_stale_after_ns for f in feeds)
    if any(f.get("ingest_stalled") for f in feeds):
        feed_ok = False
    unapplied = len(store.unapplied_events())
    max_slots = ledger.max_slots()
    if not (store_ok and rec.ok):
        status = "failed"
    elif feed_ok and max_slots is not None and unapplied == 0:
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
        "store": {"ok": store_ok, "events": store.event_count(), "unapplied": unapplied},
        "adapters": adapters,
        "bus": bus_stats,
        # local processing inside JINX, not order-execution latency
        "admission_to_state_ms": percentiles_ms(latency_ns or []),
        "receive_to_state_ms": percentiles_ms(receive_latency_ns or []),
    }
