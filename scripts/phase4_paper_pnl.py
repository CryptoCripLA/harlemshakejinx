"""Phase 4 V15/V16: paper P&L after all modeled costs, adverse moves and failed exits, on real captured markets.

    nice -n 19 .venv/bin/python scripts/phase4_paper_pnl.py <capture.jsonl.gz> --delay-ms 0 400 1000 2000 \\
        --fail-sell-every 0 3 --rate 107.76 --rate-source "GMGN getGasPrice native_token_usd_price, <utc>" \\
        --out docs/evidence/PHASE4_PAPER_PNL_<date>.json

The whole engine runs on the capture, one record at a time in capture order, through `JinxApp`'s own bus: Early
Momentum, the safety screen, the fast-path entry with quarter-Kelly sizing, the position monitor (CHAD protective
exits first, then BIGMAC_PAPER), the one gateway, the paper executor and the ledger (STRATEGY=AUTO, EXECUTION=PAPER,
no live signing). The capture may mix both feeds (records carrying "src": "pumpapi" | "pumpportal").

Modeled, per run:
- execution delay: every paper buy and sell is priced on the first curve reading at least `delay` after the decision
  (event time; Phase 3's labelled `fill_delay_ns`), never at the decision's own price;
- costs: pump.fun curve fees both ways (95 + 5 bps, D3AD's recorded mainnet values) and the 5,000-lamport network fee
  per transaction both ways; GMGN priority fees and tips are NOT modeled (no owner value, B-39) and are listed;
- the clock: The Executioner's sweep and due fills run every 10 s of EVENT time, as the live engine does on wall time;
- failed exits: with `--fail-sell-every N`, every N-th paper sell is refused as if no quote came back; the position
  keeps its exit owner and is retried on the next price;
- no hindsight: positions still open when the capture ends are reported as UNREALIZED at their last mark, never sold
  at a later or better price.

Not real, and labelled in the output (`stand_ins`), exactly as Phase 3's replay: the safety verdict (PASS for every
screened candidate: GMGN/RugCheck answers were not captured), the buyers' funders (distinct), and the sizing and BigMac
numbers (TEST_FIXTURE settings; the owner has set none: B-30, B-31, B-32). So this is the machinery on real prices
and timing; it is not evidence that any strategy is profitable, and it is SIMULATED, never realized profit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from jinx.operator_view import SolUsd, trade_report  # noqa: E402
from jinx.paper import ExecResult  # noqa: E402
from jinx.sim import NORMALIZERS, load_capture  # noqa: E402
import phase3_paper_replay as p3  # noqa: E402

SWEEP_NS = 10 * 1_000_000_000          # CHAD: "every 10 seconds"
NOT_MODELED = ["GMGN priority fees and tips (B-39: no owner value)", "MEV and competing buys inside one block",
               "partial fills (a paper fill is all or nothing)", "on-chain landing failures other than the injected ones"]


def mixed_deliveries(records: list[dict], default_src: str = "pumpapi"):
    """Each record through its own feed's real normalizer, sequence numbers from its capture position."""
    for idx, rec in enumerate(records):
        normalize_fn, refused = NORMALIZERS[rec.get("src", default_src)]
        seqs = iter((2 * idx + 1, 2 * idx + 2))
        try:
            yield normalize_fn(rec["raw"], rec["recv_ns"], seqs.__next__)
        except refused:
            yield None


def install_sell_failures(app, every: int) -> Counter:
    counts = Counter()
    if not every:
        return counts
    real = app.paper.sell

    def sell(position_id, intent_id, token_amount, **kw):
        counts["attempts"] += 1
        if counts["attempts"] % every == 0:
            counts["injected_failures"] += 1
            app.ledger.abort_exit(position_id, "NO_QUOTE (phase4 injected failed exit)")
            return ExecResult(False, position_id, "NO_QUOTE")
        return real(position_id, intent_id, token_amount, **kw)
    app.paper.sell = sell
    return counts


async def drive(app, deliveries) -> dict:
    runner = asyncio.create_task(app.bus.run())
    next_sweep, last_event = None, None
    published = 0
    for evs in deliveries:
        for ev in evs or ():
            if await app.bus.publish(ev):
                published += 1
            t = ev.event_time_ns
            last_event = t if last_event is None else max(last_event, t)
        if last_event is not None:
            if next_sweep is None:
                next_sweep = last_event + SWEEP_NS
            while last_event >= next_sweep:                      # the engine's 10 s sweep, on event time
                await app.bus.drain()
                app.gateway.process_due(next_sweep)
                app.exit_monitor.sweep(next_sweep)
                next_sweep += SWEEP_NS
        await asyncio.sleep(0)
    await app.bus.drain()
    for _ in range(500):
        if not app._background:
            break
        await asyncio.sleep(0.01)
    if last_event is not None:
        app.gateway.process_due(last_event)
        app.exit_monitor.sweep(last_event)
    runner.cancel()
    return {"events_published": published}


def run(capture: Path, records: list[dict], delay_ms: int, fail_every: int, sol_usd: SolUsd | None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="jinx-p4-pnl-"))
    try:
        app = p3.build(tmp, delay_ms)
        failures = install_sell_failures(app, fail_every)
        t0 = time.perf_counter()
        drove = asyncio.run(drive(app, mixed_deliveries(records)))
        rep = trade_report(app.ledger, app.wallet, sol_usd)
        reasons = Counter(e["reason"] for t in rep["trades"] for e in t["exits"])
        losses = sorted((t["realized_pnl_lamports"] for t in rep["trades"] if t["realized_pnl_lamports"] is not None))
        out = {"delay_ms": delay_ms, "fail_sell_every": fail_every, "wall_s": round(time.perf_counter() - t0, 1),
               **drove, "detections": app.early.stats["detections"], "entry_decisions": dict(app.entries.stats),
               "exit_reasons": dict(reasons), "injected": dict(failures),
               "exit_monitor": {k: v for k, v in app.exit_monitor.health().items() if k != "latest"},
               "pending_fills_left": len(app.gateway.pending), "handler_errors": dict(app.bus.stats["handler_errors"]),
               "totals": rep["totals"], "usd": rep["usd"],
               "realized_round_trips": {"n": len(losses), "losing": sum(1 for x in losses if x < 0),
                                        "winning": sum(1 for x in losses if x > 0),
                                        "worst_lamports": losses[0] if losses else None,
                                        "best_lamports": losses[-1] if losses else None},
               "trades": [{k: t[k] for k in ("position_id", "status", "cost_lamports", "proceeds_lamports",
                                             "protocol_fees_lamports", "network_fees_lamports", "failed_attempts",
                                             "realized_pnl_lamports", "unrealized_pnl_lamports", "peak_lamports")}
                          | {"exits": [e["reason"] for e in t["exits"]]} for t in rep["trades"]]}
        app.store.close()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--delay-ms", type=int, nargs="+", default=[0, 400, 1000, 2000])
    ap.add_argument("--fail-sell-every", type=int, nargs="+", default=[0])
    ap.add_argument("--rate")
    ap.add_argument("--rate-source", default="stated")
    ap.add_argument("--limit", type=int, help="first N records only (smoke runs)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    rate = SolUsd(Decimal(a.rate), a.rate_source, time.time_ns()) if a.rate else None
    records = load_capture(a.capture)
    if a.limit:
        records = records[:a.limit]
    span_s = (records[-1]["recv_ns"] - records[0]["recv_ns"]) / 1e9 if records else 0
    runs = [run(a.capture, records, d, f, rate) for d in a.delay_ms for f in a.fail_sell_every]
    report = {"what": "Phase 4 V15/V16 SIMULATED paper P&L on real captured markets; not live trading, not profit",
              "capture": a.capture.name, "records": len(records), "capture_span_s": round(span_s, 1),
              "sources": dict(Counter(r.get("src", "pumpapi") for r in records)),
              "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                       text=True).stdout.strip(),
              "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "stand_ins": p3.STAND_INS, "not_modeled": NOT_MODELED, "runs": runs}
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
