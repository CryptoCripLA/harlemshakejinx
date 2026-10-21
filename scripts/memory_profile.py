"""Memory proof for the storage repair (owner order 2026-10-21 §4): replay real captured traffic through the real
JINX core and measure RSS and memory by subsystem over (event) time.

The capture is the two-feed recording of `scripts/memory_capture.py`: JSON lines of {"src", "recv_ns", "raw"}.
`--passes N` replays it N times back to back. Each later pass gets fresh identities: mint, signature, wallets and
pool get a suffix. It is also shifted in time (receipt time, PumpAPI timestamp and slot), so a 45-minute capture
becomes N x 45 minutes of distinct traffic with real per-token behaviour. Duplicates in the capture, the same
fact from both feeds and resends, are delivered as recorded.

What runs is the app's own core (`JinxApp._build_core`): bus, dedup, core state, Early Momentum, graduation,
migration, bundles, whales. Feeds, GMGN and RugCheck are not started, so nothing leaves the host. Once a minute of
event time the app's own market maintenance runs, as the status loop does live.

Output: one JSON document holding samples (RSS, deep size per subsystem, tracked counts, queue high-water, latency
percentiles) and a summary.

    nice -n 19 .venv/bin/python scripts/memory_profile.py --capture /tmp/x/live45.jsonl.gz --passes 3 --out /tmp/x/after.json
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import gzip
import json
import sys
import tempfile
import time
import types
from pathlib import Path

MB = 1024 * 1024
# callbacks are not owned data: a bound method would pull its whole object (the app) into one subsystem
SKIP = (type, types.ModuleType, types.FunctionType, types.MethodType, types.BuiltinFunctionType)
STR_FIELDS = ("signature", "mint", "txSigner", "traderPublicKey", "poolId", "bondingCurveKey", "user")


def records(path: Path):
    with gzip.open(path, "rt") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def capture_span(path: Path) -> tuple[int, int, int, int]:
    lo = hi = None
    slot_lo = slot_hi = None
    for r in records(path):
        t = r["recv_ns"]
        lo, hi = (t if lo is None else min(lo, t)), (t if hi is None else max(hi, t))
        raw = r.get("raw")
        if isinstance(raw, dict) and isinstance(raw.get("block"), int):
            b = raw["block"]
            slot_lo, slot_hi = (b if slot_lo is None else min(slot_lo, b)), (b if slot_hi is None else max(slot_hi, b))
    return lo, hi, slot_lo or 0, slot_hi or 0


def remap(rec: dict, k: int, shift_ns: int, shift_slots: int) -> dict:
    if k == 0:
        return rec
    raw = rec.get("raw")
    if isinstance(raw, dict):
        raw = dict(raw)
        for f in STR_FIELDS:
            if isinstance(raw.get(f), str) and raw[f]:
                raw[f] = f"{raw[f]}~{k}"
        if isinstance(raw.get("tradersInvolved"), list):
            raw["tradersInvolved"] = [f"{w}~{k}" for w in raw["tradersInvolved"]]
        if isinstance(raw.get("timestamp"), (int, float)):
            raw["timestamp"] = raw["timestamp"] + shift_ns // 1_000_000
        if isinstance(raw.get("block"), int):
            raw["block"] = raw["block"] + shift_slots
    return {"src": rec["src"], "recv_ns": rec["recv_ns"] + shift_ns, "raw": raw}


def deep_size(obj, seen: set) -> int:
    """Bytes reachable from `obj` that no earlier-measured component already owns (first owner wins)."""
    size, stack = 0, [obj]
    while stack:
        o = stack.pop()
        i = id(o)
        if i in seen or isinstance(o, SKIP):
            continue
        seen.add(i)
        try:
            size += sys.getsizeof(o)
        except TypeError:
            continue
        if isinstance(o, dict):
            stack.extend(o.keys())
            stack.extend(o.values())
        elif isinstance(o, (list, tuple, set, frozenset)) or type(o).__name__ == "deque":
            stack.extend(o)
        else:
            stack.extend(gc.get_referents(o))
    return size


def components(app) -> list[tuple[str, object]]:
    """The subsystems, in attribution order (an object shared by two is counted under the first)."""
    bus = app.bus
    out = [("dedup", getattr(bus, "_dedup", None)),
           ("bus_latency", getattr(bus, "_latency", None)),
           ("bus_queues", getattr(bus, "_queues", None)),
           ("token_registry", getattr(app.core, "tokens", None)),
           ("wallet_registry", getattr(app.core, "wallets", None)),
           ("core_latency", getattr(app.core, "_receive_latency", None)),
           ("handler_timing", getattr(app, "_handler_ns", None)),
           ("early_momentum", app.early), ("graduation", app.graduation), ("migration", app.migration),
           ("bundles", app.bundles), ("whales", app.whales),
           ("candidates_index", (app.candidates, app.migrations))]
    return [(n, o) for n, o in out if o is not None]


def sample(app, now_ns: int, t0_ns: int, events: int, high_water: dict, deep: bool) -> dict:
    from jinx.storage import process_rss_bytes
    gc.collect()
    row = {"event_time_min": round((now_ns - t0_ns) / 60e9, 1), "events": events,
           "rss_mb": round((process_rss_bytes() or 0) / MB, 1)}
    market = getattr(app, "market", None)
    if market is not None and hasattr(market, "size_bytes"):
        row["market_sqlite_mb"] = round(market.size_bytes() / MB, 1)
    if deep:
        seen: set = set()
        row["subsystem_mb"] = {n: round(deep_size(o, seen) / MB, 2) for n, o in components(app)}
    row["tracked"] = {"tokens": len(app.core.tokens),
                      "wallets": len(app.core.wallets) if getattr(app.core, "wallets", None) is not None else None,
                      "early_momentum": app.early.tracked_mints(), "graduation": app.graduation.tracked_mints(),
                      "migration": app.migration.tracked_mints(), "candidates": len(app.candidates),
                      "dedup_keys": len(app.bus._dedup)}
    row["queue_high_water"] = dict(high_water)
    return row


async def run(paths: list[Path], passes: int, sample_every_s: int, deep_every: int, db_dir: Path) -> dict:
    from jinx import config
    from jinx.adapters.mako.normalize import MalformedMessage, normalize as pp_normalize
    from jinx.adapters.pumpapi.normalize import MalformedEvent, NotQuotedInSol, normalize as api_normalize
    from jinx.app import JinxApp
    from jinx.core.bus import Lane
    from jinx.storage import process_rss_bytes

    normalizers = {"pumpapi": (api_normalize, (MalformedEvent, NotQuotedInSol)),
                   "pumpportal": (pp_normalize, (MalformedMessage,))}
    settings = config.load_settings(data_dir=str(db_dir))
    app = JinxApp(settings)
    app._build_core()
    rss0 = process_rss_bytes() or 0
    runner = asyncio.create_task(app.bus.run())
    spans = [capture_span(p) for p in paths]
    lo, hi = min(s[0] for s in spans), max(s[1] for s in spans)
    span_ns = hi - lo + 1_000_000_000
    slot_span = max(s[3] for s in spans) - min(s[2] for s in spans) + 1_000
    high = {"fast": 0, "background": 0}
    samples, events, malformed, published, next_sample, next_tick, n = [], 0, 0, 0, None, None, 0
    started = time.perf_counter()
    for k in range(passes):
        for path in paths:
            for rec in records(path):
                rec = remap(rec, k, k * span_ns, k * slot_span)
                fn, refused = normalizers[rec["src"]]
                try:
                    evs = fn(rec["raw"], rec["recv_ns"], app.bus.next_seq)
                except refused:
                    malformed += 1
                    continue
                now = rec["recv_ns"]
                if next_sample is None:
                    t0, next_sample, next_tick = now, now, now + 60_000_000_000
                for ev in evs:
                    events += 1
                    published += bool(await app.bus.publish(ev))
                n += 1
                for lane in ("fast", "background"):
                    high[lane] = max(high[lane], app.bus.queue_depth(Lane(lane)))
                if n % 2000 == 0:
                    await app.bus.drain()            # live traffic is drained continuously; keep the replay alike
                if now >= next_tick:
                    await app.bus.drain()
                    tick = getattr(app, "market_tick", None)
                    if tick is not None:
                        tick(now)
                    else:                             # the pre-repair app: the status loop's prune
                        app.memory.prune(now, held=set())
                    next_tick = now + 60_000_000_000
                if now >= next_sample:
                    await app.bus.drain()
                    deep = len(samples) % deep_every == 0
                    samples.append(sample(app, now, t0, events, high, deep))
                    print(json.dumps(samples[-1]), file=sys.stderr, flush=True)
                    next_sample = now + sample_every_s * 1_000_000_000
    await app.bus.drain()
    samples.append(sample(app, now, t0, events, high, True))
    elapsed = time.perf_counter() - started
    runner.cancel()
    lat = app.bus.stats
    try:
        from jinx.intel.replay import percentiles
        fast = (app.bus.latency(Lane.FAST).summary() if hasattr(app.bus, "latency")
                else percentiles([x / 1e6 for x in app.bus.latency_ns(Lane.FAST)]))
    except Exception as e:                            # report, never hide, a measurement failure
        fast = {"error": repr(e)}
    handler = {}
    if hasattr(app, "handler_summary"):
        handler = app.handler_summary()
    elif getattr(app, "_handler_ns", None):
        from jinx.intel.replay import percentiles
        handler = {k: percentiles([x / 1e3 for x in v]) for k, v in app._handler_ns.items()}
    rss = [s["rss_mb"] for s in samples]
    return {"passes": passes, "captures": [str(p) for p in paths], "event_minutes": samples[-1]["event_time_min"],
            "messages": n, "events": events, "published": published, "malformed": malformed,
            "replay_wall_s": round(elapsed, 1), "rss_start_mb": round(rss0 / MB, 1), "rss_end_mb": rss[-1],
            "rss_max_mb": max(rss), "bus": {k: v for k, v in lat.items() if k != "handler_errors"},
            "handler_errors": lat.get("handler_errors"), "fast_lane_ms": fast, "handler_us": handler,
            "samples": samples}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--capture", action="append", required=True)
    p.add_argument("--passes", type=int, default=1)
    p.add_argument("--sample-every-s", type=int, default=600, help="event-time seconds between samples")
    p.add_argument("--deep-every", type=int, default=1, help="deep-size every Nth sample (slow on big heaps)")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    with tempfile.TemporaryDirectory() as d:          # the ledger store of the run is deleted afterwards
        result = asyncio.run(run([Path(c) for c in a.capture], a.passes, a.sample_every_s, a.deep_every, Path(d)))
    Path(a.out).write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}))
