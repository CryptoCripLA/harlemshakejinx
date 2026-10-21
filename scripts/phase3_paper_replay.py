"""Phase 3 paper replay on REAL captured market events (CLAUDE.md Phase 3 "historical event replay as a separate
reproducible validation mode").

The capture's events go through JinxApp's own bus, in capture order, into the whole engine: Early Momentum, safety
screen, fast-path entry with quarter-Kelly sizing, the position monitor (CHAD protective exits, then BIGMAC_PAPER),
the gateway, the paper executor and the ledger. STRATEGY=AUTO, EXECUTION=PAPER, LIVE_SIGNING=DISABLED.

What is NOT real here, and is labelled in the output (`stand_ins`):
- the safety verdict: GMGN and RugCheck answers were not captured, so every screened candidate gets PASS;
- the buyers' funders: GMGN wallet stats were not captured, so each buyer is reported as funded by a distinct
  wallet (the Sybil check passes);
- sizing and BigMac numbers: TEST_FIXTURE settings, not strategy (B-03, B-30, B-31).
So this tests the machinery on real prices and timings; it is not evidence of profitability. Fills are priced on
the captured curve reserves, `--delay-ms` after the decision (0 = at the decision's reserves).

    PYTHONPATH=. python scripts/phase3_paper_replay.py tests/fixtures/pumpapi-live-20261021.jsonl.gz --delay-ms 0 400
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jinx import config  # noqa: E402
from jinx.app import JinxApp  # noqa: E402
from jinx.exits.policy import BigMacPaperConfig, save_bigmac_config  # noqa: E402
from jinx.intel.early_momentum import Funding  # noqa: E402
from jinx.modes import Mode, set_mode  # noqa: E402
from jinx.operator_view import SolUsd, trade_report  # noqa: E402
from jinx.sim import capture_events, load_capture  # noqa: E402
from jinx.sizing.kelly import KellyConfig, save_kelly_config  # noqa: E402

BIGMAC_FIXTURE = BigMacPaperConfig(take_profit_pct=50, stop_loss_pct=20, trailing_stop_pct=12,
                                   trailing_stop_activation_pct=20, label="TEST_FIXTURE_phase3_replay",
                                   set_by="scripts/phase3_paper_replay.py")
KELLY_FIXTURE = KellyConfig(label="TEST_FIXTURE_phase3_replay", bucket_floors=(Decimal(0),),
                            priors={"0": (Decimal("0.5"), Decimal("2"))}, cap_fraction=Decimal("0.05"),
                            entry_filter="early_momentum", unscored_bucket=Decimal(0))
STAND_INS = {"safety": "PASS for every screened candidate (GMGN/RugCheck answers not captured)",
             "funders": "each buyer funded by a distinct wallet (GMGN wallet stats not captured)",
             "bigmac": BIGMAC_FIXTURE.to_dict(), "kelly": KELLY_FIXTURE.to_dict()}
PASS = SimpleNamespace(safety=SimpleNamespace(verdict=SimpleNamespace(value="PASS"), eligible=True, veto_reasons=[],
                                              facts={}, checks=[]), latency_ms=0.0)


def build(data_dir: Path, delay_ms: int) -> JinxApp:
    app = JinxApp(config.load_settings(str(data_dir)))
    app._build_core()
    app.SCREEN_RETRY_S = 0.0
    app.gateway.fill_delay_ns = delay_ms * 1_000_000
    app._trade_feed_live = lambda: True

    async def safety_pass(mint, creator, priority=10):
        return PASS
    app.check_candidate = safety_pass
    app.safety = object()

    def funders(mint, wallets):
        now = time.time_ns()
        asyncio.get_running_loop().call_soon(lambda: [app.early.record_funding(
            Funding(w, f"STAND_IN_FUNDER_{w}", "replay stand-in: distinct funders", now)) for w in wallets])
    app.early.request_funders = funders
    set_mode(app.store, Mode.AUTO)
    save_kelly_config(app.store, KELLY_FIXTURE, by="scripts/phase3_paper_replay.py")
    save_bigmac_config(app.store, BIGMAC_FIXTURE)
    return app


async def _drive(app: JinxApp, deliveries) -> None:
    runner = asyncio.create_task(app.bus.run())
    for evs in deliveries:
        for ev in evs or ():
            await app.bus.publish(ev)
        await asyncio.sleep(0)                       # let call_soon callbacks (funders) and screening run
    await app.bus.drain()
    for _ in range(500):
        if not app._background:
            break
        await asyncio.sleep(0.01)
    app.exit_monitor.sweep()
    runner.cancel()


def run_replay(capture: Path, delay_ms: int, source: str = "pumpapi", sol_usd: SolUsd | None = None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="jinx-p3-replay-"))
    try:
        app = build(tmp, delay_ms)
        records = load_capture(capture)
        t0 = time.perf_counter()
        asyncio.run(_drive(app, capture_events(records, source=source)))
        wall_s = time.perf_counter() - t0
        rep = trade_report(app.ledger, app.wallet, sol_usd)
        out = {"capture": str(capture), "records": len(records), "delay_ms": delay_ms, "wall_s": round(wall_s, 2),
               "detections": app.early.stats["detections"], "entry_decisions": dict(app.entries.stats),
               "exit_monitor": app.exit_monitor.health(), "handler_errors": dict(app.bus.stats["handler_errors"]),
               "pending_fills_left": len(app.gateway.pending), "report": rep, "stand_ins": STAND_INS,
               "label": "SIMULATED paper replay on real captured events; not live trading, not profit"}
        app.store.close()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)      # owner storage rule 6: nothing left behind


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("capture", type=Path)
    p.add_argument("--delay-ms", type=int, nargs="+", default=[0])
    p.add_argument("--source", default="pumpapi")
    p.add_argument("--rate")
    p.add_argument("--rate-source", default="stated")
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    rate = SolUsd(Decimal(a.rate), a.rate_source, time.time_ns()) if a.rate else None
    runs = [run_replay(a.capture, d, a.source, rate) for d in a.delay_ms]
    text = json.dumps(runs, indent=1, default=str)
    if a.out:
        a.out.write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
