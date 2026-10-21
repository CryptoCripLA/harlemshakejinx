"""Research store on Railway PostgreSQL: wallet intelligence and performance data (owner order 2026-10-21:
"Railway PostgreSQL: Wallet intelligence, research, analytics, and performance data").

Separate from the trading ledger (Neon), and never on the trading path: it is written from the background status
loop in batches.

- `wallet_stats`: per-wallet totals (buys, sells, SOL in and out, first and last seen) accumulated across the
  whole stream. RAM keeps a wallet only while it is active (jinx/market_memory.py); each pass sends the *increase*
  since the previous pass, so totals keep adding up after a wallet is evicted from RAM and comes back.
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

from jinx.registry import WalletState

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
CREATE TABLE IF NOT EXISTS jinx_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL,
                                      updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""

UPSERT = """
INSERT INTO wallet_stats (address, buys, sells, buy_lamports, sell_lamports, first_time_ns, last_time_ns)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (address) DO UPDATE SET
    buys = wallet_stats.buys + EXCLUDED.buys,
    sells = wallet_stats.sells + EXCLUDED.sells,
    buy_lamports = wallet_stats.buy_lamports + EXCLUDED.buy_lamports,
    sell_lamports = wallet_stats.sell_lamports + EXCLUDED.sell_lamports,
    first_time_ns = LEAST(wallet_stats.first_time_ns, EXCLUDED.first_time_ns),
    last_time_ns = GREATEST(wallet_stats.last_time_ns, EXCLUDED.last_time_ns),
    updated_at = now()
"""


class ResearchStore:
    def __init__(self, dsn: str, wallet_retention_days: int = 30, perf_retention_days: int = 30) -> None:
        self.dsn = dsn
        self.wallet_retention_days, self.perf_retention_days = wallet_retention_days, perf_retention_days
        self._sent: dict[str, tuple[int, int, int, int]] = {}   # per wallet in RAM: totals already sent
        self._ready = False
        self.stats = {"wallet_rows": 0, "perf_rows": 0, "failures": 0, "last_ok_ns": None, "last_error": None,
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

    def wallet_deltas(self, wallets: list[WalletState]) -> list[tuple]:
        """Increase of each wallet's totals since the last pass. A wallet evicted from RAM and seen again starts from
        zero in RAM, so its whole RAM total is new."""
        rows, current = [], set()
        for w in wallets:
            current.add(w.address)
            now = (w.buys, w.sells, w.buy_lamports, w.sell_lamports)
            prev = self._sent.get(w.address, (0, 0, 0, 0))
            if any(n < p for n, p in zip(now, prev)):          # RAM restarted this wallet: everything is new
                prev = (0, 0, 0, 0)
            delta = tuple(n - p for n, p in zip(now, prev))
            if any(delta):
                rows.append((w.address, *delta, w.first_time_ns, w.last_time_ns))
        return rows

    def _commit_sent(self, wallets: list[WalletState]) -> None:
        self._sent = {w.address: (w.buys, w.sells, w.buy_lamports, w.sell_lamports) for w in wallets}

    async def flush(self, wallets: list[WalletState], sample: dict | None = None) -> int:
        rows = self.wallet_deltas(wallets)
        try:
            if not self._ready:
                await self.init_schema()
            async with await self._connect() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        if rows:
                            await cur.executemany(UPSERT, rows)
                        if sample is not None:
                            await cur.execute("INSERT INTO perf_samples (ts, sample) VALUES (now(), %s)"
                                              " ON CONFLICT (ts) DO NOTHING", (json.dumps(sample, default=str),))
        except Exception as e:
            self.stats["failures"] += 1
            self.stats["last_error"] = type(e).__name__
            log.warning("research store write failed (%s); the increases are sent next pass", type(e).__name__)
            return 0
        self._commit_sent(wallets)
        self.stats["wallet_rows"] += len(rows)
        self.stats["perf_rows"] += sample is not None
        self.stats["last_ok_ns"], self.stats["last_error"] = time.time_ns(), None
        return len(rows)

    async def prune(self, now_ns: int | None = None) -> dict:
        now_ns = now_ns or time.time_ns()
        cutoff = now_ns - self.wallet_retention_days * 86_400 * NS
        async with await self._connect() as conn:
            async with conn.transaction():
                w = (await conn.execute("DELETE FROM wallet_stats WHERE last_time_ns < %s", (cutoff,))).rowcount
                p = (await conn.execute("DELETE FROM perf_samples WHERE ts < now() - make_interval(days => %s)",
                                        (self.perf_retention_days,))).rowcount
            cur = await conn.execute("SELECT pg_database_size(current_database())")
            self.stats["db_bytes"] = (await cur.fetchone())[0]
        self.stats["pruned"] = {"wallets": w, "perf_samples": p}
        return self.stats["pruned"]

    def health(self) -> dict:
        return {"component": "research_postgres", "tracked_wallets": len(self._sent), **self.stats}
