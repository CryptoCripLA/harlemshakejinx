"""Phase 4 V14/S7: median, p95 and worst latency per stage, from the market event to a paper order.

    .venv/bin/python scripts/phase4_latency.py tests/fixtures/pumpapi-live-20261021.jsonl.gz --speed 1 \\
        --out docs/evidence/PHASE4_LATENCY_20261021.json

The live capture is played into the REAL PumpAPI feed handler (`PumpApiFeed.handle`: byte prefilter, JSON parse,
normalize, durable publish) of a real `JinxApp` core, paced by the recorded receipt times (`--speed 1` = real time,
0 = as fast as possible). Stages, all measured on this host:

  provider_to_receipt  PumpAPI's event timestamp -> JINX's receipt, as recorded LIVE in the capture (network and
                       provider delay; measured on the host that made the capture)
  receive_normalize    handle() entry -> events built (prefilter, JSON parse, normalize)
  admission_to_state   bus.publish -> every fast-lane handler done (dedup, durable write, queue wait, handlers)
  handler_<name>       each fast-lane handler's own processing time
  detection            the triggering buy's arrival in Early Momentum -> evidence emitted
  entry_decision       JinxApp.entry_check() at the detection (the view the decision reads)
  paper_submission     ExecutionGateway.submit_entry on paper (ledger reservation + simulated fill, local SQLite)
  receipt_to_paper_fill  the triggering record's receipt -> its paper fill (sum of the local stages, wall clock)

Paper orders are a mechanical timing probe (enter and exit at once at every detection on the captured reserves); they
are not signals and their P&L means nothing. Every figure is LOCAL processing except provider_to_receipt; none of it
is order landing time on Solana, which needs a live GMGN order and is not measured (paper only).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jinx.adapters.pumpapi.feed as feed_mod  # noqa: E402
from jinx.adapters.pumpapi.feed import PumpApiFeed  # noqa: E402
from jinx.core.bus import Lane  # noqa: E402
from jinx.execution import EntryIntent, ExitIntent  # noqa: E402
from jinx.intel.replay import percentiles  # noqa: E402
from jinx.modes import Mode, set_mode  # noqa: E402
from jinx.sim import load_capture  # noqa: E402
from tests.phase4 import harness as h  # noqa: E402


class Live:
    def health(self):
        return {"connected": True, "ingest_stalled": False}


def stats_ms(values_ns) -> dict:
    p = percentiles([v / 1e6 for v in values_ns])
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in p.items()}


async def play(app, records, speed: float) -> dict:
    samples = {"receive_normalize": [], "detection": [], "entry_decision": [], "paper_submission": [],
               "receipt_to_paper_fill": []}
    t_receipt: dict[int, int] = {}                   # seq of each built event -> perf_counter_ns at its receipt
    real_normalize = feed_mod.normalize
    current = {"t0": 0}

    def timed_normalize(msg, recv_ns, next_seq):
        t = time.perf_counter_ns()
        events = real_normalize(msg, recv_ns, next_seq)
        samples["receive_normalize"].append(time.perf_counter_ns() - current["t0"])
        for ev in events:
            t_receipt[ev.seq] = current["t0"]
        return events
    feed_mod.normalize = timed_normalize
    original = app._on_early_momentum
    probes = {"n": 0}

    def on_detection(evidence):
        original(evidence)
        if probes["n"] and evidence.mint in app.candidates and app.candidates[evidence.mint].get("probed"):
            return
        samples["detection"].append(evidence.processing_ns)
        t = time.perf_counter_ns()
        app.entry_check(evidence.mint)
        samples["entry_decision"].append(time.perf_counter_ns() - t)
        t = time.perf_counter_ns()
        r = app.gateway.submit_entry(EntryIntent(f"probe-{probes['n']}", evidence.mint, 10_000_000,
                                                 origin="phase4-latency-probe"))
        done = time.perf_counter_ns()
        if r.ok:
            samples["paper_submission"].append(done - t)
            start = t_receipt.get(evidence.trigger.seq)
            if start:
                samples["receipt_to_paper_fill"].append(done - start)
            app.gateway.submit_exit(ExitIntent(f"probe-exit-{probes['n']}", r.position_id))
        app.candidates[evidence.mint]["probed"] = True
        probes["n"] += 1
    app.early.notify = on_detection

    feed = PumpApiFeed(app.bus)
    app.pumpapi = Live()
    runner = asyncio.create_task(app.bus.run())
    wall0, rec0 = time.perf_counter_ns(), records[0]["recv_ns"]
    try:
        for rec in records:
            if speed > 0:
                due = wall0 + (rec["recv_ns"] - rec0) / speed
                delay = (due - time.perf_counter_ns()) / 1e9
                if delay > 0:
                    await asyncio.sleep(delay)
            raw = json.dumps(rec["raw"], separators=(",", ":")).encode()
            current["t0"] = time.perf_counter_ns()
            await feed.handle(raw, rec["recv_ns"])
        await app.bus.drain()
    finally:
        runner.cancel()
        feed_mod.normalize = real_normalize
    out = {k: stats_ms(v) for k, v in samples.items()}
    # constant-memory histograms since the storage repair (2026-10-21): their summaries, not raw samples
    out["admission_to_state"] = app.bus.latency(Lane.FAST).summary()
    for name, v in app._handler_ns.items():
        out[f"handler_{name}"] = v.summary()
    out["provider_to_receipt"] = stats_ms([r["recv_ns"] - int(r["raw"]["timestamp"] * 1_000_000) for r in records
                                           if isinstance(r["raw"].get("timestamp"), (int, float))])
    out["_counts"] = {"records": len(records), "events_published": feed.stats["published"],
                      "detections": app.early.stats["detections"], "paper_probes": len(samples["paper_submission"]),
                      "background_dropped": app.bus.stats["background_dropped"],
                      "handler_errors": app.bus.stats["handler_errors"]}
    return out


def external_recorded() -> dict:
    """External stages JINX does not control, from evidence recorded earlier (each with its source file)."""
    ev = ROOT / "docs" / "evidence"
    out = {}

    def load(name):
        try:
            return json.loads((ev / name).read_text())
        except (OSError, ValueError):
            return None
    soak = load("pumpapi_soak_20261021.json")
    if soak:
        out["pumpapi_provider_to_receipt_10min_soak"] = {**soak["receipt_lag_ms_last_2000"],
                                                         "source": "docs/evidence/pumpapi_soak_20261021.json"}
    rc = load("rugcheck_check_20261021.json")
    if rc:
        out["rugcheck_http_jinx_nyc"] = {**rc["latency_ms"], "source": "docs/evidence/rugcheck_check_20261021.json"}
    sc = load("screening_contabo_20261021.json")
    if sc:
        out["rugcheck_http_contabo"] = {**sc["rugcheck"]["latency_ms"],
                                        "source": "docs/evidence/screening_contabo_20261021.json"}
        out["safety_screen_attempts_contabo"] = {"examples": sc["examples"],
                                                 "source": "docs/evidence/screening_contabo_20261021.json"}
    out["rugcheck_indexing_after_creation_s"] = {
        "min": 7.9, "max": 10.2, "source": "docs/BLOCKERS.md B-19 (measured live 2026-10-21)",
        "effect": "the safety verdict, and so any entry, waits until RugCheck has indexed the token"}
    return out


def bottlenecks(stages: dict) -> list[dict]:
    rows = [{"stage": k, "p50_ms": v.get("p50"), "p95_ms": v.get("p95"), "max_ms": v.get("max")}
            for k, v in stages.items() if not k.startswith("_") and not k.startswith("handler_") and v.get("n")]
    return sorted(rows, key=lambda r: -(r["p95_ms"] or 0))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time, 0 = as fast as possible")
    ap.add_argument("--out")
    args = ap.parse_args()
    records = load_capture(Path(args.capture))
    d = Path(tempfile.mkdtemp(prefix="jinx-p4-latency-"))
    try:
        app = h.build_app(d)
        set_mode(app.store, Mode.AUTO, by="phase4-latency-probe")
        stages = h.loop_of(app).run_until_complete(play(app, records, args.speed))
        h.close(app)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    report = {"what": "Phase 4 V14/S7 per-stage latency; LOCAL processing except provider_to_receipt; not order "
                      "landing time (paper only, no GMGN order placed)",
              "capture": Path(args.capture).name, "speed": args.speed,
              "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                       text=True).stdout.strip(),
              "host": platform.node(), "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "stages_ms": stages, "ranked_by_p95": bottlenecks(stages), "external_recorded": external_recorded()}
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
