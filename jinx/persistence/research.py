"""Research store on Railway PostgreSQL: wallet intelligence and performance data (owner order 2026-10-21:
"Railway PostgreSQL: Wallet intelligence, research, analytics, and performance data").

Separate from the trading ledger (Neon), and never on the trading path: it is written from the background status
loop in batches.

- `wallet_stats`: no longer written (owner storage repair 2026-10-21: "Research history should be obtained from
  GMGN when needed, not copied into our local databases"). Rows written by earlier builds are left in place and
  removed by the retention pass below, never deleted wholesale.
- `tracked_wallets`: whales promoted by CHAD's discovery (jinx/intel/whales.py), reloaded on start; a whale with
  no trade for the retention window is removed.
- `perf_samples`: one compact row per pass: receive-to-state and admission-to-state latency percentiles, event
  throughput, RAM/registry sizes and the storage monitor's figures.

Retention (owner storage rule 3) runs in the end-of-day pass: wallets not seen for `wallet_retention_days` and
performance samples older than `perf_retention_days` are deleted. Nothing raw is stored here.
"""
from __future__ import annotations

import json
import logging
import time

import psycopg

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1
NS = 1_000_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_stats (
    address TEXT PRIMARY KEY,
    buys BIGINT NOT NULL DEFAULT 0,
    sells BIGINT NOT NULL DEFAULT 0,
    buy_lamports BIGINT NOT NULL DEFAULT 0,
    sell_lamports BIGINT NOT NULL DEFAULT 0,
    first_time_ns BIGINT,
    last_time_ns BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wallet_stats_last_seen ON wallet_stats (last_time_ns);
CREATE TABLE IF NOT EXISTS perf_samples (
    ts TIMESTAMPTZ PRIMARY KEY,
    sample JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS tracked_wallets (
    address TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    period TEXT,
    win_rate DOUBLE PRECISION,
    realized_profit_usd DOUBLE PRECISION,
    funded_by TEXT,
    last_trade_ts BIGINT,
    evidence JSONB NOT NULL,
    promoted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jinx_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL,
                                      updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""


class ResearchStore:
    def __init__(self, dsn: str, wallet_retention_days: int = 30, perf_retention_days: int = 30) -> None:
        self.dsn = dsn
        self.wallet_retention_days, self.perf_retention_days = wallet_retention_days, perf_retention_days
        self._ready = False
        self.stats = {"perf_rows": 0, "failures": 0, "last_ok_ns": None, "last_error": None,
                      "db_bytes": None, "pruned": None}

    async def _connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(self.dsn, connect_timeout=10)

    async def init_schema(self) -> None:
        async with await self._connect() as conn:
            async with conn.transaction():
                await conn.execute(SCHEMA)
                await conn.execute("INSERT INTO jinx_meta (key, value) VALUES ('schema_version', %s) ON CONFLICT (key)"
                                   " DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (str(SCHEMA_VERSION),))
                await conn.execute("INSERT INTO jinx_meta (key, value) VALUES ('role', 'research: wallet intelligence,"
                                   " performance') ON CONFLICT (key) DO NOTHING")
        self._ready = True

    async def flush(self, sample: dict | None = None) -> int:
        """Write one performance sample. Returns the rows written (0 or 1)."""
        if sample is None:
            return 0
        try:
            if not self._ready:
                await self.init_schema()
            async with await self._connect() as conn:
                async with conn.transaction():
                    await conn.execute("INSERT INTO perf_samples (ts, sample) VALUES (now(), %s)"
                                       " ON CONFLICT (ts) DO NOTHING", (json.dumps(sample, default=str),))
        except Exception as e:
            self.stats["failures"] += 1
            self.stats["last_error"] = type(e).__name__
            log.warning("research store write failed (%s); the next pass writes a new sample", type(e).__name__)
            return 0
        self.stats["perf_rows"] += 1
        self.stats["last_ok_ns"], self.stats["last_error"] = time.time_ns(), None
        return 1

    async def upsert_tracked_wallets(self, evidence: list, now_ts: float) -> int:
        rows = [(e.address, "promoted", e.period, e.facts.get("win_rate"), e.facts.get("realized_profit_usd"),
                 e.facts.get("funded_by"), e.facts.get("last_trade_ts"), json.dumps(e.facts, default=str))
                for e in evidence]
        if not self._ready:
            await self.init_schema()
        async with await self._connect() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO tracked_wallets (address, source, period, win_rate, realized_profit_usd, funded_by,"
                        " last_trade_ts, evidence) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (address) DO"
                        " UPDATE SET period = EXCLUDED.period, win_rate = EXCLUDED.win_rate, realized_profit_usd ="
                        " EXCLUDED.realized_profit_usd, funded_by = EXCLUDED.funded_by, last_trade_ts ="
                        " EXCLUDED.last_trade_ts, evidence = EXCLUDED.evidence, updated_at = now()", rows)
        return len(rows)

    async def load_tracked_wallets(self) -> list[tuple[str, str, dict]]:
        if not self._ready:
            await self.init_schema()
        async with await self._connect() as conn:
            rows = await (await conn.execute("SELECT address, source, evidence FROM tracked_wallets")).fetchall()
        return [(a, s, e) for a, s, e in rows]

    async def prune(self, now_ns: int | None = None) -> dict:
        now_ns = now_ns or time.time_ns()
        cutoff = now_ns - self.wallet_retention_days * 86_400 * NS
        async with await self._connect() as conn:
            async with conn.transaction():
                w = (await conn.execute("DELETE FROM wallet_stats WHERE last_time_ns < %s", (cutoff,))).rowcount
                tw = (await conn.execute("DELETE FROM tracked_wallets WHERE last_trade_ts < %s",
                                         (cutoff // NS,))).rowcount
                p = (await conn.execute("DELETE FROM perf_samples WHERE ts < now() - make_interval(days => %s)",
                                        (self.perf_retention_days,))).rowcount
            cur = await conn.execute("SELECT pg_database_size(current_database())")
            self.stats["db_bytes"] = (await cur.fetchone())[0]
        self.stats["pruned"] = {"wallets": w, "perf_samples": p, "tracked_wallets": tw}
        return self.stats["pruned"]

    def health(self) -> dict:
        return {"component": "research_postgres", **self.stats}
