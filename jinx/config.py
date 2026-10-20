"""Runtime settings with their sources. Nothing here is a strategy value.

Strategy thresholds (entry score, curve gates, Kelly cap, stop and trailing
values) are not set in Phase 1; they come from CHAD or from owner decisions
in later phases.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from jinx.paper import CurveFees

ROOT = Path(__file__).resolve().parent.parent

# CHAD.txt, FREE tier: "2 SOL persistent paper wallet"
PAPER_START_LAMPORTS = 2_000_000_000

# pump.fun fee rates as recorded in D3AD's pinned source
# (shred-sniper/sniper/src/pumpfun.rs:535-544, "Live mainnet values at the time of writing").
# Phase 3 reads the live values from the pump.fun global account instead.
PUMP_CURVE_FEES = CurveFees(fee_bps=95, creator_fee_bps=5)

# Solana base fee: 5,000 lamports per signature (protocol constant).
# Priority fees and tips are added in Phase 3 with the execution path.
NETWORK_FEE_LAMPORTS = 5_000


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    upstream_dir: Path
    gmgn_build_dir: Path
    http_port: int
    max_watched_tokens: int
    feed_stale_after_s: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jinx.db"


def load_settings(data_dir: str | None = None, port: int | None = None) -> Settings:
    env = os.environ
    return Settings(
        data_dir=Path(data_dir or env.get("JINX_DATA_DIR") or ROOT / "data"),
        upstream_dir=Path(env.get("JINX_UPSTREAM_DIR") or ROOT / "upstream"),
        gmgn_build_dir=Path(env.get("JINX_GMGN_BUILD_DIR") or ROOT / "build" / "gmgn"),
        http_port=int(port or env.get("PORT") or 8080),  # Railway provides PORT
        # Engineering bounds (not strategy): how many new mints' trade streams to hold open at once,
        # and how long with no feed message before health reports the feed stale.
        max_watched_tokens=int(env.get("JINX_MAX_WATCHED_TOKENS") or 300),
        feed_stale_after_s=int(env.get("JINX_FEED_STALE_AFTER_S") or 120),
    )
