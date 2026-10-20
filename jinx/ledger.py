"""The one authoritative position ledger.

Every position JINX holds, paper or live, exists only here. Entries are
reserved atomically against the owner's global entry-slot limit (default one,
owner-ordered maximum two). Positions that are pending open, open, or
pending close all hold a slot. Exits are never blocked by mode or slots. A
fill is applied once, keyed by its fill id, so replays cannot double-count.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

from jinx.core.events import Side
from jinx.modes import entries_allowed, get_mode
from jinx.store import Store

SLOTS_KEY = "max_entry_slots"
DEFAULT_SLOTS = 1
ALLOWED_SLOTS = (1, 2)


class Status(StrEnum):
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


ACTIVE = (Status.PENDING_OPEN, Status.OPEN, Status.PENDING_CLOSE)


@dataclass(frozen=True)
class Fill:
    fill_id: str
    side: Side
    token_amount: int
    sol_lamports: int            # BUY: total SOL spent incl. protocol fee. SELL: SOL received net of protocol fee.
    protocol_fee_lamports: int
    network_fee_lamports: int
    simulated: bool
    ts_ns: int


@dataclass(frozen=True)
class Result:
    ok: bool
    position_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Position:
    position_id: int
    entry_intent_id: str
    exit_intent_id: str | None
    mint: str
    status: Status
    token_amount: int
    cost_lamports: int
    proceeds_lamports: int
    protocol_fees_lamports: int
    network_fees_lamports: int
    realized_pnl_lamports: int | None
    simulated: bool
    opened_ns: int
    closed_ns: int | None
    note: str | None


class PositionLedger:
    def __init__(self, store: Store, clock_ns=time.time_ns) -> None:
        self.store = store
        self.clock_ns = clock_ns

    # ---- slots --------------------------------------------------------
    def max_slots(self) -> int | None:
        """The owner's slot limit, or None if the stored value is invalid (fail closed)."""
        raw = self.store.get_setting(SLOTS_KEY)
        if raw is None:
            return DEFAULT_SLOTS
        try:
            n = int(raw)
        except ValueError:
            return None
        return n if n in ALLOWED_SLOTS else None

    def active_slots(self) -> int:
        q = f"SELECT COUNT(*) FROM positions WHERE status IN ({','.join('?' * len(ACTIVE))})"
        return self.store.conn.execute(q, [s.value for s in ACTIVE]).fetchone()[0]

    # ---- entry --------------------------------------------------------
    def reserve_entry(self, intent_id: str, mint: str, simulated: bool = True) -> Result:
        with self.store.transaction() as c:
            existing = c.execute("SELECT position_id FROM positions WHERE entry_intent_id = ?",
                                 (intent_id,)).fetchone()
            if existing:
                return Result(True, existing[0], "SAME_INTENT")
            if not entries_allowed(get_mode(self.store)):
                return Result(False, None, "MODE_OFF")
            limit = self.max_slots()
            if limit is None:
                return Result(False, None, "SLOT_STATE_INVALID")
            held = c.execute(f"SELECT 1 FROM positions WHERE mint = ? AND status IN"
                             f" ({','.join('?' * len(ACTIVE))})", [mint, *[s.value for s in ACTIVE]]).fetchone()
            if held:
                return Result(False, None, "DUPLICATE_POSITION")
            if self.active_slots() >= limit:
                return Result(False, None, "SLOT_LIMIT")
            cur = c.execute("INSERT INTO positions (entry_intent_id, mint, status, simulated, opened_ns)"
                            " VALUES (?,?,?,?,?)", (intent_id, mint, Status.PENDING_OPEN.value, int(simulated),
                                                    self.clock_ns()))
            return Result(True, cur.lastrowid)

    def record_buy_fill(self, position_id: int, fill: Fill) -> None:
        if fill.side is not Side.BUY:
            raise ValueError("record_buy_fill needs a BUY fill")
        with self.store.transaction() as c:
            pos = self._require(position_id, (Status.PENDING_OPEN, Status.OPEN))
            if not self._insert_fill(c, position_id, fill):
                return
            c.execute("UPDATE positions SET status=?, token_amount=token_amount+?, cost_lamports=cost_lamports+?,"
                      " protocol_fees_lamports=protocol_fees_lamports+?, network_fees_lamports=network_fees_lamports+?"
                      " WHERE position_id=?", (Status.OPEN.value, fill.token_amount, fill.sol_lamports,
                                               fill.protocol_fee_lamports, fill.network_fee_lamports, pos.position_id))

    def mark_open_failed(self, position_id: int, reason: str) -> None:
        with self.store.transaction() as c:
            self._require(position_id, (Status.PENDING_OPEN,))
            c.execute("UPDATE positions SET status=?, closed_ns=?, note=? WHERE position_id=?",
                      (Status.FAILED.value, self.clock_ns(), reason, position_id))

    # ---- exit ---------------------------------------------------------
    def begin_exit(self, position_id: int, intent_id: str) -> Result:
        """Take exit ownership. Never blocked by mode or slot limits."""
        with self.store.transaction() as c:
            pos = self.get(position_id)
            if pos is None:
                return Result(False, position_id, "UNKNOWN_POSITION")
            if pos.status is Status.PENDING_CLOSE:
                return Result(True, position_id, "ALREADY_EXITING")
            if pos.status is not Status.OPEN:
                return Result(False, position_id, f"NOT_OPEN:{pos.status}")
            c.execute("UPDATE positions SET status=?, exit_intent_id=? WHERE position_id=?",
                      (Status.PENDING_CLOSE.value, intent_id, position_id))
            return Result(True, position_id)

    def abort_exit(self, position_id: int, reason: str) -> None:
        """A failed sell keeps the position open (and still owned) for the next attempt."""
        with self.store.transaction() as c:
            self._require(position_id, (Status.PENDING_CLOSE,))
            c.execute("UPDATE positions SET status=?, note=? WHERE position_id=?",
                      (Status.OPEN.value, reason, position_id))

    def record_sell_fill(self, position_id: int, fill: Fill) -> None:
        if fill.side is not Side.SELL:
            raise ValueError("record_sell_fill needs a SELL fill")
        with self.store.transaction() as c:
            pos = self._require(position_id, (Status.PENDING_CLOSE,))
            if c.execute("SELECT 1 FROM fills WHERE fill_id = ?", (fill.fill_id,)).fetchone():
                return
            if fill.token_amount > pos.token_amount:
                raise ValueError(f"sell of {fill.token_amount} exceeds held {pos.token_amount}")
            self._insert_fill(c, position_id, fill)
            remaining = pos.token_amount - fill.token_amount
            proceeds = pos.proceeds_lamports + fill.sol_lamports
            network = pos.network_fees_lamports + fill.network_fee_lamports
            if remaining == 0:
                c.execute("UPDATE positions SET status=?, token_amount=0, proceeds_lamports=?,"
                          " protocol_fees_lamports=protocol_fees_lamports+?, network_fees_lamports=?,"
                          " realized_pnl_lamports=?, closed_ns=? WHERE position_id=?",
                          (Status.CLOSED.value, proceeds, fill.protocol_fee_lamports, network,
                           proceeds - pos.cost_lamports - network, self.clock_ns(), position_id))
            else:
                c.execute("UPDATE positions SET token_amount=?, proceeds_lamports=?,"
                          " protocol_fees_lamports=protocol_fees_lamports+?, network_fees_lamports=?"
                          " WHERE position_id=?",
                          (remaining, proceeds, fill.protocol_fee_lamports, network, position_id))

    # ---- reads --------------------------------------------------------
    def get(self, position_id: int) -> Position | None:
        row = self.store.conn.execute("SELECT * FROM positions WHERE position_id = ?", (position_id,)).fetchone()
        return _to_position(row) if row else None

    def active(self) -> list[Position]:
        q = f"SELECT * FROM positions WHERE status IN ({','.join('?' * len(ACTIVE))}) ORDER BY position_id"
        return [_to_position(r) for r in self.store.conn.execute(q, [s.value for s in ACTIVE])]

    def all(self) -> list[Position]:
        return [_to_position(r) for r in self.store.conn.execute("SELECT * FROM positions ORDER BY position_id")]

    # ---- internals ----------------------------------------------------
    def _require(self, position_id: int, allowed: tuple[Status, ...]) -> Position:
        pos = self.get(position_id)
        if pos is None:
            raise KeyError(f"unknown position {position_id}")
        if pos.status not in allowed:
            raise ValueError(f"position {position_id} is {pos.status}, expected one of {[s.value for s in allowed]}")
        return pos

    @staticmethod
    def _insert_fill(c, position_id: int, fill: Fill) -> bool:
        cur = c.execute("INSERT OR IGNORE INTO fills (fill_id, position_id, side, token_amount, sol_lamports,"
                        " protocol_fee_lamports, network_fee_lamports, simulated, ts_ns) VALUES (?,?,?,?,?,?,?,?,?)",
                        (fill.fill_id, position_id, fill.side.value, fill.token_amount, fill.sol_lamports,
                         fill.protocol_fee_lamports, fill.network_fee_lamports, int(fill.simulated), fill.ts_ns))
        return cur.rowcount == 1


def _to_position(row) -> Position:
    d = dict(row)
    d["status"] = Status(d["status"])
    d["simulated"] = bool(d["simulated"])
    return Position(**d)
