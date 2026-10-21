"""PostgreSQL replication of JINX's trading records (owner order 2026-10-21: "PostgreSQL for durable new trading
records and intelligence"; "Keep DETECT -> SIGN -> FIRE fast and independent of remote persistence latency").

The local ledger store stays authoritative on the execution server: positions, fills and exits are reserved and
recorded there synchronously, as Phase 1 was reviewed. Every change is also written, in the same transaction, to
the store's outbox (SQLite triggers, `jinx.store.OUTBOX_TABLES`). This replicator runs in the background: it
upserts outbox rows into PostgreSQL in order, commits, and only then removes them locally. If PostgreSQL is down
the outbox simply waits; a crash between the remote commit and the local removal re-sends rows whose upserts are
idempotent. Nothing here is on the hot path.

The outbox is bounded (one pending row per record, removed once PostgreSQL has it). On start the replicator also
upserts every current trading record once (`resync`), so a cleared outbox (for example while no PostgreSQL was
configured) never loses a record. PostgreSQL receives trading records only: no market data, no raw events.

The PostgreSQL schema is created fresh from the current code: one table per replicated ledger table, with the same
columns (SQLite INTEGER -> BIGINT, TEXT -> TEXT) and primary key, plus `jinx_meta`. No data is migrated.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import psycopg
from psycopg import sql

from jinx.store import OUTBOX_TABLES, Store

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1
PG_TYPES = {"INTEGER": "BIGINT", "TEXT": "TEXT"}


class PostgresReplicator:
    def __init__(self, store: Store, dsn: str, batch: int = 500) -> None:
        self.store, self.dsn, self.batch = store, dsn, batch
        self._conn: psycopg.AsyncConnection | None = None
        self.stats = {"replicated": 0, "failures": 0, "last_ok_ns": None, "last_error": None, "resynced": None,
                      "db_bytes": None, "skipped_null_key": 0}

    def columns(self, tbl: str) -> list[tuple[str, str]]:
        return [(r[1], PG_TYPES.get(r[2].upper(), "TEXT")) for r in self.store.conn.execute(f"PRAGMA table_info({tbl})")]

    async def _connection(self) -> psycopg.AsyncConnection:
        if self._conn is None or self._conn.closed:
            self._conn = await psycopg.AsyncConnection.connect(self.dsn)
        return self._conn

    async def _reset(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
        self._conn = None

    async def init_schema(self) -> None:
        conn = await self._connection()
        async with conn.transaction():
            for tbl, key in OUTBOX_TABLES.items():
                cols = self.columns(tbl)
                defs = [sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t)) for c, t in cols]
                defs.append(sql.SQL("PRIMARY KEY ({})").format(sql.SQL(", ").join(map(sql.Identifier, key))))
                await conn.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                    sql.Identifier(tbl), sql.SQL(", ").join(defs)))
                for c, t in cols:   # a column added to the code later is added here too
                    await conn.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}").format(
                        sql.Identifier(tbl), sql.Identifier(c), sql.SQL(t)))
            await conn.execute("CREATE TABLE IF NOT EXISTS jinx_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                               "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            await conn.execute("INSERT INTO jinx_meta (key, value) VALUES ('schema_version', %s) ON CONFLICT (key) "
                               "DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (str(SCHEMA_VERSION),))
            await conn.execute("INSERT INTO jinx_meta (key, value) VALUES ('created_at', now()::text) "
                               "ON CONFLICT (key) DO NOTHING")

    async def apply_rows(self, rows: list[dict]) -> None:
        conn = await self._connection()
        async with conn.transaction():
            for row in rows:
                tbl, data = row["tbl"], json.loads(row["row_json"])
                key = OUTBOX_TABLES[tbl]
                if any(data.get(k) is None for k in key):    # a legacy row with a NULL key cannot be a PostgreSQL
                    self.stats["skipped_null_key"] += 1      # primary key; it stays in the local ledger
                    continue
                if row["op"] == "delete":
                    await conn.execute(sql.SQL("DELETE FROM {} WHERE {}").format(
                        sql.Identifier(tbl), sql.SQL(" AND ").join(
                            sql.SQL("{} = %s").format(sql.Identifier(k)) for k in key)), [data[k] for k in key])
                    continue
                cols = list(data)
                updates = [c for c in cols if c not in key]
                stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO {}").format(
                    sql.Identifier(tbl), sql.SQL(", ").join(map(sql.Identifier, cols)),
                    sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                    sql.SQL(", ").join(map(sql.Identifier, key)),
                    sql.SQL("UPDATE SET {}").format(sql.SQL(", ").join(
                        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in updates))
                    if updates else sql.SQL("NOTHING"))
                await conn.execute(stmt, [data[c] for c in cols])

    async def flush(self) -> int:
        rows = [dict(r) for r in self.store.conn.execute(
            "SELECT seq, tbl, op, row_json FROM outbox ORDER BY seq LIMIT ?", (self.batch,))]
        if not rows:
            return 0
        try:
            await self.apply_rows(rows)
        except Exception as e:
            self.stats["failures"] += 1
            self.stats["last_error"] = f"{type(e).__name__}"
            log.warning("PostgreSQL replication failed (%s); %d rows stay in the outbox", type(e).__name__, len(rows))
            await self._reset()
            return 0
        with self.store.transaction() as c:   # removed only after the remote commit
            c.execute("DELETE FROM outbox WHERE seq <= ?", (rows[-1]["seq"],))
        self.stats["replicated"] += len(rows)
        self.stats["last_ok_ns"] = time.time_ns()
        return len(rows)

    def backlog(self) -> int:
        return self.store.outbox_backlog()

    async def resync(self) -> int:
        """Upsert every current trading record, then drop outbox rows that were pending before the copy."""
        last = self.store.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM outbox").fetchone()[0]
        rows = []
        for tbl in OUTBOX_TABLES:
            cols = [c for c, _ in self.columns(tbl)]
            for r in self.store.conn.execute(f"SELECT {', '.join(cols)} FROM {tbl}"):
                rows.append({"tbl": tbl, "op": "upsert", "row_json": json.dumps(
                    {c: ("blob:" + v.hex().upper()) if isinstance(v, bytes) else v for c, v in zip(cols, r)})})
        for i in range(0, len(rows), self.batch):
            await self.apply_rows(rows[i:i + self.batch])
        with self.store.transaction() as c:
            c.execute("DELETE FROM outbox WHERE seq <= ?", (last,))
        self.stats["resynced"] = len(rows)
        return len(rows)

    async def database_bytes(self) -> int:
        """Size of the whole database, on its own short connection (the replication connection may be mid-batch)."""
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
            cur = await conn.execute("SELECT pg_database_size(current_database())")
            self.stats["db_bytes"] = (await cur.fetchone())[0]
        return self.stats["db_bytes"]

    async def start(self, retry_max_s: float = 30.0) -> None:
        """Create the schema and copy every current record, retrying with backoff until PostgreSQL answers."""
        delay = 1.0
        while True:
            try:
                await self.init_schema()
                await self.resync()
                return
            except Exception as e:
                self.stats["failures"] += 1
                self.stats["last_error"] = type(e).__name__
                log.warning("PostgreSQL start failed (%s); retrying in %.0fs", type(e).__name__, delay)
                await self._reset()
                await asyncio.sleep(delay)
                delay = min(delay * 2, retry_max_s)

    async def run(self, interval_s: float = 1.0, retry_max_s: float = 30.0, idle_close_s: float = 120.0,
                  size_every_s: float = 3600.0) -> None:
        await self.start(retry_max_s)
        try:
            await self.database_bytes()
        except Exception:
            pass
        delay, idle_since, sized = interval_s, time.monotonic(), time.monotonic()
        while True:
            sent = await self.flush()
            if sent:
                idle_since, delay = time.monotonic(), interval_s
                if time.monotonic() - sized >= size_every_s:      # size checks ride on work, never wake the compute
                    try:
                        await self.database_bytes()
                        sized = time.monotonic()
                    except Exception:
                        pass
                continue          # more may be waiting
            if self._conn is not None and not self.backlog() and time.monotonic() - idle_since >= idle_close_s:
                await self._reset()   # no open connection while nothing changes: Neon's compute can scale to zero
            delay = min(delay * 2, retry_max_s) if self.backlog() else interval_s
            await asyncio.sleep(delay)

    def health(self) -> dict:
        return {"component": "postgres", "backlog": self.backlog(), **self.stats}
