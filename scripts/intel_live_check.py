"""Live check of the Phase 2 event intel on the free PumpAPI stream: Early Momentum, graduation and migration.

Runs the real PumpApiFeed (one connection, as PumpAPI allows) for N seconds. Every published event goes, in RAM, to
EarlyMomentum, GraduationTracker and MigrationTracker; nothing raw is kept beyond a bounded per-mint buffer. Writes:
- a summary JSON (counts and p50/p95/p99 timings; no wallet addresses): detection delay is PumpAPI's timestamp to
  evidence, i.e. receipt lag plus local processing. It is not order execution;
- optionally a migration fixture for replay tests: for each migration in the window, the mint's last
  `--curve-before` curve records, the migrate record and its first `--pool-after` PumpSwap records (compact records,
  as tests/fixtures/pumpapi-live-20261021.jsonl.gz).

    nice .venv/bin/python scripts/intel_live_check.py --seconds 600 \\
        --out docs/evidence/phase2_event_intel_live_20261021.json \\
        --fixture tests/fixtures/pumpapi-migrations-live-20261021.jsonl.gz
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import socket
import subprocess
import sys
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jinx.adapters.pumpapi.feed import PumpApiFeed  # noqa: E402
from jinx.adapters.pumpapi.normalize import compact  # noqa: E402
from jinx.core.events import EventKind  # noqa: E402
from jinx.intel.early_momentum import EarlyMomentum  # noqa: E402
from jinx.intel.graduation import GraduationTracker  # noqa: E402
from jinx.intel.migration import MigrationTracker  # noqa: E402
from jinx.intel.replay import percentiles  # noqa: E402


class IntelBus:
    """What the feed publishes goes straight to the three modules; dedup bounded like the real bus."""

    def __init__(self, curve_before: int, pool_after: int) -> None:
        self.em, self.grad, self.mig = EarlyMomentum(), GraduationTracker(), MigrationTracker()
        self.modules = {"early_momentum": self.em.on_event, "graduation": self.grad.on_event,
                        "migration": self.mig.on_event}
        self.processing = {name: deque(maxlen=500_000) for name in self.modules}
        self.momentum, self.migrations = [], []
        self._keys: OrderedDict[str, None] = OrderedDict()
        self._seq = 0
        self.curve_before, self.pool_after = curve_before, pool_after
        self._recent: OrderedDict[str, deque] = OrderedDict()     # mint -> last curve records (bounded)
        self.fixture: dict[str, list] = {}                         # migrated mint -> records
        self._last = None

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def publish(self, ev) -> bool:
        key = ev.idempotency_key
        if key in self._keys:
            return False
        self._keys[key] = None
        while len(self._keys) > 200_000:
            self._keys.popitem(last=False)
        self._keep(ev)
        for name, on_event in self.modules.items():
            t0 = time.perf_counter_ns()
            out = on_event(ev)
            self.processing[name].append(time.perf_counter_ns() - t0)
            if name == "early_momentum":
                self.momentum += out
            elif name == "migration":
                self.migrations += out
        return True

    def _keep(self, ev) -> None:
        rec = (ev.recv_ns, ev.signature, ev.mint)
        if rec == self._last:            # a create publishes two events from one message
            return
        self._last = rec
        row = {"recv_ns": ev.recv_ns, "raw": compact(dict(ev.raw))}
        if ev.kind is EventKind.MIGRATION:
            self.fixture.setdefault(ev.mint, list(self._recent.get(ev.mint, ()))).append(row)
            return
        if ev.mint in self.fixture:
            pool_rows = sum(r["raw"].get("pool") == "pump-amm" for r in self.fixture[ev.mint])
            if ev.raw.get("pool") == "pump-amm" and pool_rows < self.pool_after:
                self.fixture[ev.mint].append(row)
            return
        if ev.v_sol_lamports is not None:
            q = self._recent.setdefault(ev.mint, deque(maxlen=self.curve_before))
            q.append(row)
            self._recent.move_to_end(ev.mint)
            while len(self._recent) > 5_000:
                self._recent.popitem(last=False)


def code_identity() -> dict:
    """The code the run loads, taken at start: HEAD and whether any file under jinx/ or scripts/ differs from it."""
    git = (lambda *a: subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip())
    return {"commit": git("rev-parse", "--short", "HEAD"),
            "code_changes_since_commit": bool(git("status", "--porcelain", "--", "jinx", "scripts"))}


def summary(bus: IntelBus, feed: PumpApiFeed, seconds: float, started: str, code: dict) -> dict:
    em = bus.momentum
    first = {}
    for e in bus.migrations:
        first.setdefault(e.mint, e)
    finals = {m: bus.mig.evidence(m) for m in first}
    verified = [e for e in finals.values() if e.verified]
    lag = [e.feed_delay_ns / 1e6 for e in em]
    return {
        "started_utc": started, "seconds": seconds, "host": socket.gethostname(), "code_at_start": code,
        "feed": {k: feed.stats[k] for k in ("received", "published", "duplicates", "amm_not_followed", "malformed",
                                             "not_quoted_in_sol", "connections", "disconnects", "stalls")},
        "feed_receipt_lag_ms": feed.health()["receipt_lag_ms"], "connections": list(feed.connection_log),
        "per_event_processing_us": {n: percentiles([x / 1e3 for x in v]) for n, v in bus.processing.items()},
        "early_momentum": {
            "detections": len(em), "trade_permitted": sum(e.trade_permitted for e in em),
            "feed_delay_ms": percentiles(lag),
            "processing_us": percentiles([e.processing_ns / 1e3 for e in em]),
            "detection_delay_ms": percentiles([(e.feed_delay_ns + e.processing_ns) / 1e6 for e in em]),
            "creator_in_window": sum(bool(e.creator_in_window) for e in em),
            "not_standard_curve_at_trigger": sum(
                next(p for p in e.predicates if p.name == "curve_gate_0_5_to_10_sol").detail[
                    "standard_curve_at_trigger"] is False for e in em),
            "observed_since_creation": sum(bool(e.reserves["observed_since_creation"]) for e in em),
        },
        "graduation": {"readings": bus.grad.stats["readings"], "late_readings": bus.grad.stats["late_readings"],
                       "mints": bus.grad.tracked_mints()},
        "migration": {
            "migrations": len(first), "verified": len(verified),
            "signal_delay_ms": percentiles([(e.observed_ns - e.event_ns + e.processing_ns) / 1e6
                                            for e in first.values()]),
            "migration_to_first_verified_trade_ms": percentiles(
                [e.first_verified_liquidity["ms_after_migration"] for e in verified]),
            "slots_to_first_verified_trade": percentiles(
                [e.first_verified_liquidity["slots_after_migration"] for e in verified
                 if e.first_verified_liquidity["slots_after_migration"] is not None]),
            "pre_migration_real_sol": percentiles([e.pre_migration_liquidity["real_sol"] for e in finals.values()
                                                   if e.pre_migration_liquidity
                                                   and e.pre_migration_liquidity["real_sol"] is not None]),
            "first_pool_quote_sol": percentiles([e.first_verified_liquidity["quote_reserve_lamports"] / 1e9
                                                 for e in verified
                                                 if e.first_verified_liquidity["quote_reserve_lamports"]]),
            "unverified_waiting": sum(not e.verified for e in finals.values()),
            "foreign_pool_trades": sum(e.foreign_pool_trades for e in finals.values()),
            "states": sorted({e.state for e in finals.values()}),
        },
    }


async def main(args) -> dict:
    code = code_identity()
    bus = IntelBus(args.curve_before, args.pool_after)
    feed = PumpApiFeed(bus)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(args.seconds)
    await asyncio.sleep(args.tail)                 # let the last migrations get their first pool trades
    task.cancel()
    report = summary(bus, feed, args.seconds, started, code)
    if args.fixture:
        rows = sorted((r for rows in bus.fixture.values() for r in rows), key=lambda r: r["recv_ns"])
        with gzip.open(args.fixture, "wt") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        report["fixture"] = {"path": args.fixture, "records": len(rows), "mints": len(bus.fixture)}
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=600)
    p.add_argument("--tail", type=float, default=0)
    p.add_argument("--out")
    p.add_argument("--fixture")
    p.add_argument("--curve-before", type=int, default=40)
    p.add_argument("--pool-after", type=int, default=40)
    a = p.parse_args()
    text = json.dumps(asyncio.run(main(a)), indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
