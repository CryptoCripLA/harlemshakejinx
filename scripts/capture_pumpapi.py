"""Record a bounded PumpAPI capture for replay tests: what JINX's PumpApiFeed would actually ingest.

Runs the real PumpApiFeed for N seconds (one connection, as PumpAPI allows) and writes JSON lines of
{"recv_ns": <JINX receipt time>, "raw": <compact record>} for every message the feed published: every bonding-
curve event, every migration, and PumpSwap trades of mints first seen on the curve. Compact records normalize to
the same events as the live messages (tests/test_pumpapi_adapter.py).

    .venv/bin/python scripts/capture_pumpapi.py --seconds 60 --out tests/fixtures/pumpapi-live-20261021.jsonl.gz
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import time

from jinx.adapters.pumpapi.feed import PumpApiFeed
from jinx.adapters.pumpapi.normalize import compact


class CaptureBus:
    """Takes what the feed publishes; dedups like the real bus; keeps one record per published message."""

    def __init__(self):
        self.keys, self.records, self._seq, self._last = set(), [], 0, None

    def next_seq(self):
        self._seq += 1
        return self._seq

    async def publish(self, ev):
        if ev.idempotency_key in self.keys:
            return False
        self.keys.add(ev.idempotency_key)
        rec = (ev.recv_ns, ev.raw.get("signature"), ev.raw.get("action"), ev.mint)
        if rec != self._last:              # a create publishes two events from one message
            self.records.append({"recv_ns": ev.recv_ns, "raw": dict(ev.raw)})
            self._last = rec
        return True


async def main(seconds: float, out: str) -> dict:
    bus = CaptureBus()
    feed = PumpApiFeed(bus)
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(seconds)
    task.cancel()
    with gzip.open(out, "wt") as f:
        for r in bus.records:
            f.write(json.dumps({"recv_ns": r["recv_ns"], "raw": compact(r["raw"])}, separators=(",", ":")) + "\n")
    return {"records": len(bus.records), **{k: feed.stats[k] for k in ("received", "filtered", "published",
                                                                        "amm_not_followed", "malformed",
                                                                        "not_quoted_in_sol", "connections")},
            "receipt_lag_ms": feed.health()["receipt_lag_ms"]}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps({"started_utc": started, **asyncio.run(main(a.seconds, a.out))}))
