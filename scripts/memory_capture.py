"""Record a real two-feed capture for the storage-repair replay (owner order 2026-10-21 §4), streamed to disk.

Runs JINX's real PumpApiFeed and Mako PumpPortal feed (creations and migrations, free) for N seconds and writes one
JSON line per published message, as it arrives: {"src": "pumpapi" | "pumpportal", "recv_ns": ..., "raw": ...}.
Duplicates are kept on purpose (the same fact from both feeds, resends), so the replay measures real duplicate
delivery. Nothing is held in RAM: the file is written as the stream runs, and the capture stops at `--max-mb`
(owner storage rule 6). The output belongs in a temporary directory and is deleted after the proof run.

    nice -n 19 .venv/bin/python scripts/memory_capture.py --seconds 2700 --out /tmp/x/capture.jsonl.gz
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import time
from pathlib import Path

from jinx.adapters.mako.feed import MakoPumpPortalFeed
from jinx.adapters.pumpapi.feed import PumpApiFeed
from jinx.adapters.pumpapi.normalize import compact

ROOT = Path(__file__).resolve().parents[1]


class StreamingCaptureBus:
    """Accepts everything a feed publishes (so the feed never treats a resend as handled) and writes one line per
    message: a creation publishes two events from one message, and only the first is written."""

    def __init__(self, out, src: str, max_bytes: int) -> None:
        self.out, self.src, self.max_bytes = out, src, max_bytes
        self._seq, self._last = 0, None
        self.lines = 0
        self.full = False
        self.written = [0]

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def publish(self, ev) -> bool:
        rec = (ev.recv_ns, ev.signature, ev.mint)
        if rec == self._last:
            return True
        self._last = rec
        if self.written[0] >= self.max_bytes:
            self.full = True
            return True
        raw = compact(ev.raw) if self.src == "pumpapi" else dict(ev.raw)   # MarketEvent.raw is a read-only proxy
        line = json.dumps({"src": self.src, "recv_ns": ev.recv_ns, "raw": raw}, separators=(",", ":"),
                          default=str) + "\n"
        self.out.write(line)
        self.written[0] += len(line)
        self.lines += 1
        return True


async def main(seconds: float, out_path: str, max_mb: int) -> dict:
    with gzip.open(out_path, "wt") as out:
        api_bus = StreamingCaptureBus(out, "pumpapi", max_mb * 1024 * 1024)
        pp_bus = StreamingCaptureBus(out, "pumpportal", max_mb * 1024 * 1024)
        pp_bus.written = api_bus.written                     # one byte budget for the whole file
        api = PumpApiFeed(api_bus)
        pp = MakoPumpPortalFeed(pp_bus, ROOT / "upstream" / "mako")
        tasks = [asyncio.create_task(api.run()), asyncio.create_task(pp.run())]
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds and not (api_bus.full and pp_bus.full):
            await asyncio.sleep(1)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return {"seconds": round(time.monotonic() - t0, 1), "pumpapi_lines": api_bus.lines,
            "pumpportal_lines": pp_bus.lines, "bytes_uncompressed": api_bus.written[0],
            "bytes_gz": os.path.getsize(out_path), "stopped_at_cap": api_bus.full,
            "pumpapi": {k: api.stats.get(k) for k in ("received", "published", "connections", "malformed")},
            "pumpportal": {k: pp.stats.get(k) for k in ("received", "published", "malformed", "migrations")}}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--max-mb", type=int, default=400, help="uncompressed bytes written before the capture stops")
    a = p.parse_args()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps({"started_utc": started, **asyncio.run(main(a.seconds, a.out, a.max_mb))}))
