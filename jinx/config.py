"""Runtime settings with their sources. Nothing here is a strategy value.

Strategy thresholds (entry score, curve gates, Kelly cap, stop and trailing
values) are not set in Phase 1; they come from CHAD or from owner decisions
in later phases.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from jinx.paper import CurveFees

ROOT = Path(__file__).resolve().parent.parent

# CHAD.txt, FREE tier: "2 SOL persistent paper wallet"
PAPER_START_LAMPORTS = 2_000_000_000

# pump.fun fee rates as recorded in D3AD's pinned source
# (shred-sniper/sniper/src/pumpfun.rs:535-544, "Live mainnet values at the time of writing").
# Phase 3 reads the live values from the pump.fun global account instead.
PUMP_CURVE_FEES = CurveFees(fee_bps=95, creator_fee_bps=5)

# Owner decision 2026-10-20 ("its ok for paper we need real data"): the runtime GMGN bridge loads the
# request-signing key so GMGN's signed reads (followed-wallet trades, wallet holdings, order status) work in
# paper mode. Trading methods stay refused by the bridge's read-only allowlist whatever this says.
GMGN_ALLOW_SIGNING_KEY = True

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
    market_db: str = ":memory:"        # raw market events: RAM only (owner order 2026-10-21)
    market_event_ttl_s: int = 300      # applied raw events kept this long (cross-feed dedup window)
    market_idle_ttl_s: int = 7200      # tokens and wallets with no activity this long are evicted from RAM
    pumpapi_enabled: bool = True       # PumpAPI.io free stream (one connection per IP)
    rugcheck_enabled: bool = True      # official public RugCheck API, 1 request/s unauthenticated (B-19)
    # RAM hard caps on top of the TTLs (owner storage rule 2: no unbounded caches). Engineering bounds, not strategy.
    market_max_events: int = 60_000    # ~2 KB each in the in-memory store (measured 2026-10-21) -> ~120 MB worst case
    market_max_tokens: int = 20_000
    market_max_wallets: int = 200_000
    # Services. Connection strings carry passwords: repr=False keeps them out of logs and tracebacks.
    database_url: str | None = field(default=None, repr=False)   # PostgreSQL (Neon): durable trading records
    research_database_url: str | None = field(default=None, repr=False)  # PostgreSQL (Railway): intelligence, perf
    research_every_s: int = 300        # wallet-intelligence and performance write interval
    research_retention_days: int = 30  # wallets unseen and performance samples older than this are deleted
    redis_url: str | None = field(default=None, repr=False)      # Redis: transient state, every key expires
    redis_max_tokens: int = 2_000      # token snapshots written per pass (most recently active first)
    s3_endpoint: str | None = None     # Neon Object Storage (S3 API): daily ledger backup, JINX-enforced retention
    s3_region: str | None = None
    s3_bucket: str = "jinx-backups"
    backup_keep: int = 7               # daily ledger backups kept; older ones are deleted by JINX
    ai_gateway_url: str | None = None  # Neon AI Gateway: asynchronous context only, never on the trading path
    # Storage budgets watched by jinx/storage.py (warn at 80%, critical at 95%, or when growth would hit the budget)
    ram_budget_mb: int = 1_024
    disk_budget_mb: int = 1_024
    pg_budget_mb: int = 400            # Neon free plan: 0.5 GB per project
    research_pg_budget_mb: int = 4_000 # Railway volume: 5 GB
    redis_budget_mb: int = 25          # Redis Cloud free: 30 MB
    s3_budget_mb: int = 1_024          # Neon free plan object storage: 5 GB per project
    capture_max_bytes: int = 50 * 1024 * 1024   # an opt-in engineering capture stops writing at this size

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
        # Engineering bounds on RAM, not strategy (jinx/market_memory.py).
        market_db=env.get("JINX_MARKET_DB") or ":memory:",
        market_event_ttl_s=int(env.get("JINX_MARKET_EVENT_TTL_S") or 300),
        market_idle_ttl_s=int(env.get("JINX_MARKET_IDLE_TTL_S") or 7200),
        pumpapi_enabled=(env.get("JINX_PUMPAPI", "1") != "0"),
        rugcheck_enabled=(env.get("JINX_RUGCHECK", "1") != "0"),
        market_max_events=int(env.get("JINX_MARKET_MAX_EVENTS") or 60_000),
        market_max_tokens=int(env.get("JINX_MARKET_MAX_TOKENS") or 20_000),
        market_max_wallets=int(env.get("JINX_MARKET_MAX_WALLETS") or 200_000),
        database_url=env.get("JINX_DATABASE_URL") or env.get("DATABASE_URL") or None,
        research_database_url=env.get("JINX_RESEARCH_DATABASE_URL") or None,
        research_every_s=int(env.get("JINX_RESEARCH_EVERY_S") or 300),
        research_retention_days=int(env.get("JINX_RESEARCH_RETENTION_DAYS") or 30),
        redis_url=env.get("JINX_REDIS_URL") or env.get("REDIS_URL") or None,
        redis_max_tokens=int(env.get("JINX_REDIS_MAX_TOKENS") or 2_000),
        s3_endpoint=env.get("AWS_ENDPOINT_URL_S3") or None,
        s3_region=env.get("AWS_REGION") or None,
        s3_bucket=env.get("JINX_S3_BUCKET") or "jinx-backups",
        backup_keep=int(env.get("JINX_BACKUP_KEEP") or 7),
        ai_gateway_url=env.get("NEON_AI_GATEWAY_BASE_URL") or None,
        ram_budget_mb=int(env.get("JINX_RAM_BUDGET_MB") or 1_024),
        disk_budget_mb=int(env.get("JINX_DISK_BUDGET_MB") or 1_024),
        pg_budget_mb=int(env.get("JINX_PG_BUDGET_MB") or 400),
        research_pg_budget_mb=int(env.get("JINX_RESEARCH_PG_BUDGET_MB") or 4_000),
        redis_budget_mb=int(env.get("JINX_REDIS_BUDGET_MB") or 25),
        s3_budget_mb=int(env.get("JINX_S3_BUDGET_MB") or 1_024),
        capture_max_bytes=int(env.get("JINX_CAPTURE_MAX_MB") or 50) * 1024 * 1024,
    )
