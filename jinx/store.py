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

from jinx.core.events import MarketEvent

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
    exit_intent_id TEXT,
    mint TEXT NOT NULL,
    status TEXT NOT NULL,
    token_amount INTEGER NOT NULL DEFAULT 0,
    cost_lamports INTEGER NOT NULL DEFAULT 0,
    proceeds_lamports INTEGER NOT NULL DEFAULT 0,
    protocol_fees_lamports INTEGER NOT NULL DEFAULT 0,
    network_fees_lamports INTEGER NOT NULL DEFAULT 0,
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
    token_amount INTEGER NOT NULL,
    sol_lamports INTEGER NOT NULL,
    protocol_fee_lamports INTEGER NOT NULL,
    network_fee_lamports INTEGER NOT NULL,
    simulated INTEGER NOT NULL,
    ts_ns INTEGER NOT NULL
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
        self.conn.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._depth = 0

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
                self.conn.execute("COMMIT")

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

    def mark_applied(self, idempotency_key: str) -> None:
        self.conn.execute("UPDATE events SET applied = 1 WHERE idempotency_key = ?", (idempotency_key,))

    def unapplied_events(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events WHERE applied = 0 ORDER BY event_time_ns, seq").fetchall()

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
