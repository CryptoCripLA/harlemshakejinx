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
    ts_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS exit_intents (
    intent_id TEXT PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions(position_id),
    token_amount INTEGER CHECK (token_amount IS NULL OR token_amount > 0),
    created_ns INTEGER NOT NULL
);

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


SCHEMA_VERSION = 2
# v1 (up to commit 1ad0d65): no entry budget, no exit_intents, no amount CHECK constraints.
# v2: positions.entry_budget_lamports (NULL = unknown for positions created by v1), exit_intents, CHECKs.

V2_POSITIONS = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS positions ("):SCHEMA.index("CREATE INDEX IF NOT EXISTS positions_active")]
V2_FILLS = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS fills ("):SCHEMA.index("CREATE TABLE IF NOT EXISTS exit_intents")]


class SchemaTooNew(RuntimeError):
    pass


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
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
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

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
                c.execute("INSERT INTO fills SELECT * FROM fills_v1")
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
        self.conn.execute("UPDATE events SET applied = 1 WHERE idempotency_key = ?", (idempotency_key,))

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
