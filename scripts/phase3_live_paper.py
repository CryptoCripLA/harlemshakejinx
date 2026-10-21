"""Phase 3 live paper run on jinx-nyc (owner order 2026-10-21 §4): STRATEGY=AUTO, EXECUTION=PAPER, LIVE_SIGNING=DISABLED.

The real engine (`JinxApp.start`): the live PumpAPI and PumpPortal streams, GMGN (security, developer, funders,
Trenches, the dollar rate) through the read-only bridge, RugCheck, Early Momentum, the fast-path entry with
quarter-Kelly sizing, the position monitor (CHAD protective exits, then BIGMAC_PAPER), the gateway, the paper
executor and the ledger. Sizing and BigMac numbers are labelled TEST_FIXTURE settings (B-03, B-30, B-31).

Isolation: a temporary data directory that is deleted at the end (owner storage rule 6); no PostgreSQL, Redis,
object storage or AI gateway (their variables are removed from this process); its own HTTP port; GMGN paced to half
the Plus budget so the Contabo engine sharing the key keeps the other half. Positions still open at the end stay
open: there is no forced or timed exit; they are reported unrealized.

    PYTHONPATH=. nice -n 19 python scripts/phase3_live_paper.py --seconds 1800 --out docs/evidence/PHASE3_LIVE_PAPER.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for var in ("JINX_DATABASE_URL", "DATABASE_URL", "JINX_RESEARCH_DATABASE_URL", "JINX_REDIS_URL", "REDIS_URL",
            "AWS_ENDPOINT_URL_S3", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "NEON_AI_GATEWAY_BASE_URL",
            "NEON_AI_GATEWAY_TOKEN", "PUMPPORTAL_API_KEY"):
    os.environ.pop(var, None)

from jinx import config  # noqa: E402
from jinx.adapters.gmgn.budget import Plan, RateBudget  # noqa: E402
from jinx.app import JinxApp  # noqa: E402
from jinx.exits.policy import BigMacPaperConfig, save_bigmac_config  # noqa: E402
from jinx.modes import Mode, get_execution_mode, set_mode  # noqa: E402
from jinx.operator_view import operator_view, render_text, trade_report  # noqa: E402
from jinx.sizing.kelly import KellyConfig, save_kelly_config  # noqa: E402
from jinx.storage import process_rss_bytes  # noqa: E402

BIGMAC_FIXTURE = BigMacPaperConfig(take_profit_pct=50, stop_loss_pct=20, trailing_stop_pct=12,
                                   trailing_stop_activation_pct=20, label="TEST_FIXTURE_phase3_live_paper",
                                   set_by="scripts/phase3_live_paper.py")
KELLY_FIXTURE = KellyConfig(label="TEST_FIXTURE_phase3_live_paper", bucket_floors=(Decimal(0),),
                            priors={"0": (Decimal("0.5"), Decimal("2"))}, cap_fraction=Decimal("0.05"),
                            entry_filter="early_momentum", unscored_bucket=Decimal(0))
FILL_DELAY_MS = 300   # labelled paper setting: GMGN's own stated end-to-end latency ("under 0.3 seconds"), unmeasured


async def main_async(seconds: float, port: int) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="jinx-p3-live-"))
    try:
        app = JinxApp(config.load_settings(str(tmp), port=port))
        await app.start()
        app.gmgn.budget = RateBudget(Plan("plus-half-for-shared-key", 10, 10))
        app.gateway.fill_delay_ns = FILL_DELAY_MS * 1_000_000
        set_mode(app.store, Mode.AUTO, by="scripts/phase3_live_paper.py")
        save_kelly_config(app.store, KELLY_FIXTURE, by="scripts/phase3_live_paper.py")
        save_bigmac_config(app.store, BIGMAC_FIXTURE)
        assert get_execution_mode(app.store).value == "PAPER" and app.gateway.live is None
        started, samples = time.time(), []
        while time.time() - started < seconds:
            await asyncio.sleep(min(60, max(1.0, seconds - (time.time() - started))))
            v = operator_view(app)
            samples.append({"t_s": round(time.time() - started), "open": len(v["open_positions"]),
                            "entries": dict(app.entries.stats), "exits": app.exit_monitor.health()["exits_filled"],
                            "balance_usd": v["balance"]["usd"], "realized_usd": v["pnl"]["realized_usd"],
                            "unrealized_usd": v["pnl"]["unrealized_usd"]})
            samples[:] = samples[-120:]
        view = operator_view(app)
        health = await app.health()
        out = {"label": "SIMULATED paper trading on live market data (jinx-nyc); not live trading, not profit",
               "seconds": seconds, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
               "settings": {"mode": "AUTO", "execution": "PAPER", "live_signing": view["live_signing"],
                            "fill_delay_ms": FILL_DELAY_MS, "bigmac": BIGMAC_FIXTURE.to_dict(),
                            "kelly": KELLY_FIXTURE.to_dict(), "gmgn_budget": "10 weight/s (half of Plus)"},
               "detections": app.early.stats.get("detections"), "screening": dict(app.screen_stats),
               "entries": dict(app.entries.stats), "entry_decisions": list(app.entries.decisions),
               "exit_monitor": app.exit_monitor.health(), "executioner": dict(app.executioner.stats),
               "report": trade_report(app.ledger, app.wallet, app.sol_usd), "operator_text": render_text(view),
               "samples": samples, "feeds": view["feeds"], "bus": {k: v for k, v in health["bus"].items()
                                                                   if isinstance(v, (int, float, dict))},
               "rss_mb": round(process_rss_bytes() / 2 ** 20, 1)}
        await app.stop()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=1800)
    p.add_argument("--port", type=int, default=18093)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)
    out = asyncio.run(main_async(a.seconds, a.port))
    a.out.write_text(json.dumps(out, indent=1, default=str))
    print(out["operator_text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
