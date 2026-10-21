"""Durable JINX storage: one SQLite database in WAL mode.

Holds the event log (with a unique key per on-chain fact, so dedup survives
restarts), settings, registries, the position ledger and the paper wallet.
WAL with synchronous=NORMAL survives a process crash; it can lose the last
commits on power loss, which the restart reconciliation must tolerate.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from jinx.core.events import EventKind, MarketEvent, Side

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    idempotency_key TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    recv_ns INTEGER NOT NULL,
    event_time_ns INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    mint TEXT NOT NULL,
    signature TEXT NOT NULL,
    trader TEXT,
    side TEXT,
    sol_lamports INTEGER,
    token_amount INTEGER,
    v_sol_lamports INTEGER,
    v_tokens INTEGER,
    slot INTEGER,
    block_time INTEGER,
    raw_json TEXT NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_unapplied ON events(applied, event_time_ns, seq);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_ns INTEGER NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_ns INTEGER NOT NULL,
    changed_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    mint TEXT PRIMARY KEY,
    creator TEXT,
    buys INTEGER NOT NULL DEFAULT 0,
    sells INTEGER NOT NULL DEFAULT 0,
    unique_traders INTEGER NOT NULL DEFAULT 0,
    v_sol_lamports INTEGER,
    v_tokens INTEGER,
    peak_v_sol_lamports INTEGER,
    latest_time_ns INTEGER,
    latest_seq INTEGER,
    first_time_ns INTEGER,
    first_seq INTEGER
);
CREATE TABLE IF NOT EXISTS token_traders (
    mint TEXT NOT NULL,
    trader TEXT NOT NULL,
    PRIMARY KEY (mint, trader)
);
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    buys INTEGER NOT NULL DEFAULT 0,
    sells INTEGER NOT NULL DEFAULT 0,
    buy_lamports INTEGER NOT NULL DEFAULT 0,
    sell_lamports INTEGER NOT NULL DEFAULT 0,
    first_time_ns INTEGER,
    last_time_ns INTEGER
);

CREATE TABLE IF NOT EXISTS positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_intent_id TEXT NOT NULL UNIQUE,
    entry_budget_lamports INTEGER CHECK (entry_budget_lamports IS NULL OR entry_budget_lamports > 0),
    exit_intent_id TEXT,
    mint TEXT NOT NULL,
    status TEXT NOT NULL,
    token_amount INTEGER NOT NULL DEFAULT 0 CHECK (token_amount >= 0),
    cost_lamports INTEGER NOT NULL DEFAULT 0 CHECK (cost_lamports >= 0),
    proceeds_lamports INTEGER NOT NULL DEFAULT 0 CHECK (proceeds_lamports >= 0),
    protocol_fees_lamports INTEGER NOT NULL DEFAULT 0 CHECK (protocol_fees_lamports >= 0),
    network_fees_lamports INTEGER NOT NULL DEFAULT 0 CHECK (network_fees_lamports >= 0),
    realized_pnl_lamports INTEGER,
    simulated INTEGER NOT NULL,
    opened_ns INTEGER NOT NULL,
    closed_ns INTEGER,
    note TEXT
);
CREATE INDEX IF NOT EXISTS positions_active ON positions(status);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions(position_id),
    side TEXT NOT NULL,
    token_amount INTEGER NOT NULL CHECK (token_amount > 0),
    sol_lamports INTEGER NOT NULL CHECK (sol_lamports >= 0),
    protocol_fee_lamports INTEGER NOT NULL CHECK (protocol_fee_lamports >= 0),
    network_fee_lamports INTEGER NOT NULL CHECK (network_fee_lamports >= 0),
    simulated INTEGER NOT NULL,
    ts_ns INTEGER NOT NULL,
    exit_intent_id TEXT  -- provenance: the exit intent that produced this sell (NULL before schema v5)
);
CREATE TABLE IF NOT EXISTS exit_intents (
    intent_id TEXT PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions(position_id),
    token_amount INTEGER CHECK (token_amount IS NULL OR token_amount > 0),
    created_ns INTEGER NOT NULL,
    legacy_fill_id TEXT  -- set for exits recorded before schema v2 (their only identity is the old fill id)
);

CREATE TABLE IF NOT EXISTS historical_exits (
    fill_id TEXT PRIMARY KEY,            -- a sell fill written without provenance (before schema v5)
    intent_id TEXT NOT NULL,             -- the exit intent id that fill implies: its id minus ":sell"
    position_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS historical_exits_intent ON historical_exits(intent_id);
CREATE TABLE IF NOT EXISTS unclassified_exits (
    position_id INTEGER NOT NULL,        -- a position with a provenance-free sell whose fill id is not text
    fill_ref TEXT NOT NULL,              -- "<sqlite type>:<hex of the stored value>"; never decoded or merged
    PRIMARY KEY (position_id, fill_ref)
);

CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tbl TEXT NOT NULL,                  -- the trading-record table that changed
    pk TEXT NOT NULL,                   -- that record's primary key; at most one pending row per record
    op TEXT NOT NULL,                   -- 'upsert' (row after the change) or 'delete' (its key)
    row_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outbox_record ON outbox(tbl, pk);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL,            -- the entry or exit intent this stage belongs to
    position_id INTEGER,
    side TEXT NOT NULL,                 -- 'buy' or 'sell'
    stage TEXT NOT NULL,                -- DECIDED, QUOTED, SUBMITTED, FILLED, FAILED, REFUSED
    ts_ns INTEGER NOT NULL,
    simulated INTEGER NOT NULL,
    cost_lamports INTEGER NOT NULL DEFAULT 0 CHECK (cost_lamports >= 0),  -- e.g. a failed transaction's fee
    detail TEXT
);
CREATE INDEX IF NOT EXISTS order_events_intent ON order_events(intent_id);

CREATE TABLE IF NOT EXISTS paper_wallet (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    starting_lamports INTEGER NOT NULL,
    balance_lamports INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_wallet_entries (
    entry_id TEXT PRIMARY KEY,
    delta_lamports INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ts_ns INTEGER NOT NULL
);
"""


SCHEMA_VERSION = 10
# v1 (up to commit 1ad0d65): no entry budget, no exit_intents, no amount CHECK constraints.
# v2: positions.entry_budget_lamports (NULL = unknown for positions created by v1), exit_intents, CHECKs.
# v3: exit_intents.legacy_fill_id; v1 sell fills ("<intent>:sell") become historical exit bindings, so a
#     replayed pre-v2 exit is recognized instead of selling again.
# v4: legacy sell fills are identified through durable exit bindings, not an id prefix (the v3 rule missed
#     legacy intents whose id began with "p<position>:"); the backfill re-runs on every open.
# v5: fills.exit_intent_id records provenance; sell fills without it are historical (the v4 rule trusted a
#     binding, which a rejected attempt can leave behind).
# v6: historical sell evidence lives in historical_exits, keyed by fill id, and is checked before any exit
#     binding; a binding under the same intent id can no longer suppress it.
# v7: the historical-evidence scan runs in Python (NUL-safe) and also preserves sells it cannot classify.
# v8: a provenance-free sell whose fill id SQLite stores as BLOB or NULL is recorded in unclassified_exits
#     (it is a distinct key from any text id, so it is never decoded into text evidence); its position refuses
#     new exit claims until reconciled. Before v8 one such row made every open fail.
# v9: outbox. Triggers write every change of a trading record (OUTBOX_TABLES) into `outbox` in the same
#     transaction, for asynchronous replication to PostgreSQL (jinx/persistence/postgres.py; owner order
#     2026-10-21). The triggers are rebuilt from the live columns on every open, so they follow schema changes.
#     Bounded (owner storage rule 7): a change to a record replaces that record's pending row, so the outbox never
#     holds more than one row per trading record, and the replicator deletes rows once PostgreSQL has them.
# v10 (Phase 3): positions record their owning strategy, their paper exit policy (with its settings), the persisted
#     price peak, the entry score/threshold/filter and the sizing decision; exit_intents record the owner's ExitIntent
#     fields; order_events records each order's lifecycle stages (decided, quoted, submitted, filled, failed) with
#     times and failure costs. Columns are added in place (ALTER TABLE), so every existing row is kept.

# trading-record tables replicated to PostgreSQL, with their primary keys
OUTBOX_TABLES = {"positions": ("position_id",), "fills": ("fill_id",), "exit_intents": ("intent_id",),
                 "paper_wallet": ("id",), "paper_wallet_entries": ("entry_id",), "settings": ("key",),
                 "settings_audit": ("id",), "order_events": ("id",)}

# Phase 3 columns (schema v10), added in place to existing tables. Types are INTEGER or TEXT only (the PostgreSQL
# replica maps exactly those); decimal values (scores, fractions) are stored as exact decimal text.
PHASE3_COLUMNS = {
    "positions": (("strategy", "TEXT"), ("exit_policy", "TEXT"), ("exit_policy_json", "TEXT"),
                  ("peak_value_lamports", "INTEGER"), ("peak_ns", "INTEGER"), ("entry_score", "TEXT"),
                  ("entry_threshold", "TEXT"), ("entry_filter", "TEXT"), ("sizing_json", "TEXT"),
                  ("signal_ns", "INTEGER"), ("last_mark_lamports", "INTEGER"), ("last_mark_ns", "INTEGER")),
    "exit_intents": (("source_strategy", "TEXT"), ("exit_policy", "TEXT"), ("reason_code", "TEXT"),
                     ("exit_urgency", "TEXT"), ("required_sell_pct", "TEXT"), ("event_ns", "INTEGER"),
                     ("triggering_evidence", "TEXT"), ("previously_executed_fraction", "TEXT"),
                     ("remaining_confirmed_balance", "INTEGER"), ("venue", "TEXT"), ("quote_status", "TEXT"),
                     ("quote_lamports", "INTEGER")),
    "fills": (("quote_lamports", "INTEGER"),),   # the proceeds/cost quoted at the decision, for slippage
}

V2_POSITIONS = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS positions ("):SCHEMA.index("CREATE INDEX IF NOT EXISTS positions_active")]
V2_FILLS = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS fills ("):SCHEMA.index("CREATE TABLE IF NOT EXISTS exit_intents")]
# Only a fill's own provenance proves which exit produced it: from schema v5 every sell fill records its
# exit_intent_id in the same transaction. Every sell fill without provenance (written before v5) is
# recorded as historical evidence in its own table, keyed by the fill id, so no exit binding can displace
# it. The scan runs in Python, not SQL: SQLite's text functions (LIKE, length, substr) stop at an embedded
# NUL, which once let an old intent id escape. A fill id ending in ":sell" yields its intent id; any other
# provenance-free sell is still recorded, under its own full fill id. Idempotent; runs on every open.
SELL_SUFFIX = ":sell"


class SchemaTooNew(RuntimeError):
    pass


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # must precede the first write of a new database (journal_mode below writes its header)
        self.conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        # Storage hygiene (owner storage rules 1-2): a new database releases freed pages on request
        # (`maintain()`, auto_vacuum above), and the WAL file is truncated to at most 16 MB after each checkpoint.
        self.conn.execute("PRAGMA journal_size_limit=16777216")
        self._lock = threading.RLock()
        self._depth = 0
        self._migrate()

    # ---- schema versions ----------------------------------------------
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _migrate(self) -> None:
        version = self.schema_version()
        if version > SCHEMA_VERSION:
            raise SchemaTooNew(f"database schema v{version} is newer than this build (v{SCHEMA_VERSION})")
        if version < 2 and "positions" in self._tables() and "entry_budget_lamports" not in self._columns("positions"):
            self._migrate_v1_to_v2()
        if "outbox" in self._tables() and "pk" not in self._columns("outbox"):
            self.conn.execute("DROP TABLE outbox")   # pre-release layout; the replicator's full resync covers it
        self.conn.executescript(SCHEMA)
        self._bind_legacy_exits()
        self._phase3_columns()
        self._outbox_triggers()
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _bind_legacy_exits(self) -> None:
        with self.transaction() as c:
            if "legacy_fill_id" not in self._columns("exit_intents"):
                c.execute("ALTER TABLE exit_intents ADD COLUMN legacy_fill_id TEXT")
            if "exit_intent_id" not in self._columns("fills"):
                c.execute("ALTER TABLE fills ADD COLUMN exit_intent_id TEXT")
            rows = c.execute("SELECT fill_id, typeof(fill_id), position_id FROM fills"
                             " WHERE side = 'sell' AND exit_intent_id IS NULL").fetchall()
            for fill_id, kind, position_id in rows:
                if kind != "text":
                    raw = fill_id if isinstance(fill_id, bytes) else b"" if fill_id is None else str(fill_id).encode()
                    c.execute("INSERT OR IGNORE INTO unclassified_exits (position_id, fill_ref) VALUES (?,?)",
                              (position_id, f"{kind}:{raw.hex()}"))
                    continue
                intent_id = fill_id[:-len(SELL_SUFFIX)] if fill_id.endswith(SELL_SUFFIX) else fill_id
                c.execute("INSERT OR IGNORE INTO historical_exits (fill_id, intent_id, position_id) VALUES (?,?,?)",
                          (fill_id, intent_id, position_id))

    def _phase3_columns(self) -> None:
        with self.transaction() as c:
            for tbl, cols in PHASE3_COLUMNS.items():
                have = self._columns(tbl)
                for name, kind in cols:
                    if name not in have:
                        c.execute(f"ALTER TABLE {tbl} ADD COLUMN {name} {kind}")

    def _outbox_triggers(self) -> None:
        def value(row: str, col: str) -> str:   # a binary value cannot be JSON: tag it instead of failing the write
            return f"CASE WHEN typeof({row}.{col}) = 'blob' THEN 'blob:' || hex({row}.{col}) ELSE {row}.{col} END"
        with self.transaction() as c:
            for tbl, key in OUTBOX_TABLES.items():
                cols = [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]
                new = ", ".join(f"'{col}', {value('NEW', col)}" for col in cols)
                old = ", ".join(f"'{col}', {value('OLD', col)}" for col in key)
                for event in ("insert", "update", "delete"):
                    c.execute(f"DROP TRIGGER IF EXISTS outbox_{tbl}_{event}")
                for event, row, op, body in (("INSERT", "NEW", "upsert", new), ("UPDATE", "NEW", "upsert", new),
                                             ("DELETE", "OLD", "delete", old)):
                    k = f"{row}.{key[0]}"   # every replicated table has a one-column key; a legacy NULL or BLOB
                    pk = (f"CASE WHEN {k} IS NULL THEN 'null:' || {row}.rowid "   # key (round 8) must not fail
                          f"WHEN typeof({k}) = 'blob' THEN 'blob:' || hex({k}) ELSE CAST({k} AS TEXT) END")
                    c.execute(f"CREATE TRIGGER outbox_{tbl}_{event.lower()} AFTER {event} ON {tbl} BEGIN "
                              f"DELETE FROM outbox WHERE tbl = '{tbl}' AND pk = {pk}; "
                              f"INSERT INTO outbox (tbl, pk, op, row_json) VALUES ('{tbl}', {pk}, '{op}', "
                              f"json_object({body})); END")

    def _tables(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def _migrate_v1_to_v2(self) -> None:
        """Rebuild positions and fills with the v2 definitions, keeping every row. A v1 position's entry budget
        is reconstructed from its paper buy fills (the paper buy fill is the whole budget); a position with no
        buy fill keeps NULL, meaning unknown, rather than an invented value."""
        self.conn.execute("PRAGMA foreign_keys=OFF")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                c = self.conn
                c.execute("ALTER TABLE positions RENAME TO positions_v1")
                c.execute("ALTER TABLE fills RENAME TO fills_v1")
                for stmt in (V2_POSITIONS, V2_FILLS):
                    c.execute(stmt)
                c.execute("""INSERT INTO positions (position_id, entry_intent_id, entry_budget_lamports, exit_intent_id,
                             mint, status, token_amount, cost_lamports, proceeds_lamports, protocol_fees_lamports,
                             network_fees_lamports, realized_pnl_lamports, simulated, opened_ns, closed_ns, note)
                             SELECT p.position_id, p.entry_intent_id,
                                    (SELECT SUM(f.sol_lamports) FROM fills_v1 f
                                     WHERE f.position_id = p.position_id AND f.side = 'buy'),
                                    p.exit_intent_id, p.mint, p.status, p.token_amount, p.cost_lamports,
                                    p.proceeds_lamports, p.protocol_fees_lamports, p.network_fees_lamports,
                                    p.realized_pnl_lamports, p.simulated, p.opened_ns, p.closed_ns, p.note
                             FROM positions_v1 p""")
                c.execute("INSERT INTO fills (fill_id, position_id, side, token_amount, sol_lamports,"
                          " protocol_fee_lamports, network_fee_lamports, simulated, ts_ns)"
                          " SELECT fill_id, position_id, side, token_amount, sol_lamports, protocol_fee_lamports,"
                          " network_fee_lamports, simulated, ts_ns FROM fills_v1")
                c.execute("DROP TABLE fills_v1")
                c.execute("DROP TABLE positions_v1")
                if c.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("foreign key check failed after migration")
                c.execute("PRAGMA user_version = 2")
                c.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        finally:
            self.conn.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Atomic unit of work. Nested calls join the outermost transaction."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                try:
                    self.conn.execute("COMMIT")
                except BaseException:
                    # a failed COMMIT can leave SQLite inside the transaction; roll back so the next write starts clean
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    raise

    # ---- events -------------------------------------------------------
    def record_event(self, ev: MarketEvent) -> bool:
        """Store an event. Returns False when that on-chain fact was already stored."""
        with self.transaction() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO events (idempotency_key, seq, recv_ns, event_time_ns, kind, source, mint,"
                " signature, trader, side, sol_lamports, token_amount, v_sol_lamports, v_tokens, slot, block_time,"
                " raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev.idempotency_key, ev.seq, ev.recv_ns, ev.event_time_ns, str(ev.kind), ev.source, ev.mint,
                 ev.signature, ev.trader, str(ev.side) if ev.side else None, ev.sol_lamports, ev.token_amount,
                 ev.v_sol_lamports, ev.v_tokens, ev.slot, ev.block_time, json.dumps(dict(ev.raw), default=str)),
            )
            return cur.rowcount == 1

    def is_applied(self, idempotency_key: str) -> bool:
        row = self.conn.execute("SELECT applied FROM events WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return bool(row and row[0])

    def mark_applied(self, idempotency_key: str) -> None:
        # the raw payload is only needed until the event is applied; release it (owner storage rule 2)
        self.conn.execute("UPDATE events SET applied = 1, raw_json = '{}' WHERE idempotency_key = ?",
                          (idempotency_key,))

    def unapplied_events(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events WHERE applied = 0 ORDER BY event_time_ns, seq").fetchall()

    @staticmethod
    def row_to_event(row: sqlite3.Row) -> MarketEvent:
        return MarketEvent(kind=EventKind(row["kind"]), source=row["source"], mint=row["mint"],
                           signature=row["signature"], recv_ns=row["recv_ns"], seq=row["seq"], trader=row["trader"],
                           side=Side(row["side"]) if row["side"] else None, sol_lamports=row["sol_lamports"],
                           token_amount=row["token_amount"], v_sol_lamports=row["v_sol_lamports"],
                           v_tokens=row["v_tokens"], slot=row["slot"], block_time=row["block_time"],
                           raw=json.loads(row["raw_json"]))

    def quick_check(self) -> bool:
        return self.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def max_seq(self) -> int:
        return self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]

    def journal_mode(self) -> str:
        return self.conn.execute("PRAGMA journal_mode").fetchone()[0]

    # ---- storage hygiene ------------------------------------------------
    def size_bytes(self) -> int:
        """Bytes the database occupies: pages in use (RAM for ":memory:") plus the WAL file on disk."""
        pages = self.conn.execute("PRAGMA page_count").fetchone()[0] * self.conn.execute("PRAGMA page_size").fetchone()[0]
        wal = Path(f"{self.path}-wal")
        return pages + (wal.stat().st_size if str(self.path) != ":memory:" and wal.exists() else 0)

    def outbox_backlog(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def clear_outbox(self) -> int:
        """Drop pending replication rows (used when no PostgreSQL is configured; the replicator's full resync on
        start makes them redundant)."""
        with self.transaction() as c:
            return c.execute("DELETE FROM outbox").rowcount

    def maintain(self) -> dict:
        """Release space: checkpoint and truncate the WAL, return freed pages, refresh query statistics."""
        before = self.size_bytes()
        freelist = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
        with self._lock:
            self.conn.execute("PRAGMA incremental_vacuum").fetchall()
            self.conn.execute("PRAGMA optimize")
            if str(self.path) != ":memory:":    # last: the steps above write to the WAL too
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        return {"bytes_before": before, "bytes_after": self.size_bytes(), "free_pages_released": freelist}

    # ---- settings -----------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str, by: str = "system", now_ns: int = 0) -> None:
        with self.transaction() as c:
            old = self.get_setting(key)
            c.execute("INSERT INTO settings (key, value, updated_ns, updated_by) VALUES (?,?,?,?)"
                      " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ns=excluded.updated_ns,"
                      " updated_by=excluded.updated_by", (key, value, now_ns, by))
            c.execute("INSERT INTO settings_audit (key, old_value, new_value, changed_ns, changed_by)"
                      " VALUES (?,?,?,?,?)", (key, old, value, now_ns, by))

    def close(self) -> None:
        self.conn.close()
