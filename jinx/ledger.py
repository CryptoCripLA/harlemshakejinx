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


class FillConflict(Exception):
    """A fill id was reused with different contents: an id collision, not a replay."""


def _check_int(name: str, value, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")


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

    def __post_init__(self) -> None:
        if not self.fill_id:
            raise ValueError("fill needs an id")
        _check_int("token_amount", self.token_amount, 1)
        _check_int("sol_lamports", self.sol_lamports, 0)
        _check_int("protocol_fee_lamports", self.protocol_fee_lamports, 0)
        _check_int("network_fee_lamports", self.network_fee_lamports, 0)

    def row(self, position_id: int) -> tuple:
        return (position_id, self.side.value, self.token_amount, self.sol_lamports, self.protocol_fee_lamports,
                self.network_fee_lamports, int(self.simulated))


@dataclass(frozen=True)
class Result:
    ok: bool
    position_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Position:
    position_id: int
    entry_intent_id: str
    entry_budget_lamports: int | None  # None only for positions migrated from schema v1 without a buy fill
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
    def reserve_entry(self, intent_id: str, mint: str, budget_lamports: int, simulated: bool = True) -> Result:
        _check_int("budget_lamports", budget_lamports, 1)
        with self.store.transaction() as c:
            existing = c.execute("SELECT position_id, mint, entry_budget_lamports FROM positions"
                                 " WHERE entry_intent_id = ?", (intent_id,)).fetchone()
            if existing:
                stored_budget = existing["entry_budget_lamports"]
                # a NULL budget means the position predates schema v2; only the mint can be compared
                if existing["mint"] != mint or (stored_budget is not None and stored_budget != budget_lamports):
                    return Result(False, existing["position_id"], "INTENT_CONFLICT")
                return Result(True, existing["position_id"], "SAME_INTENT")
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
            cur = c.execute("INSERT INTO positions (entry_intent_id, entry_budget_lamports, mint, status, simulated,"
                            " opened_ns) VALUES (?,?,?,?,?,?)", (intent_id, budget_lamports, mint,
                                                                 Status.PENDING_OPEN.value, int(simulated),
                                                                 self.clock_ns()))
            return Result(True, cur.lastrowid)

    def record_buy_fill(self, position_id: int, fill: Fill) -> None:
        if fill.side is not Side.BUY:
            raise ValueError("record_buy_fill needs a BUY fill")
        with self.store.transaction() as c:
            if self._is_replay(c, position_id, fill):
                return
            pos = self._require(position_id, (Status.PENDING_OPEN, Status.OPEN))
            self._insert_fill(c, position_id, fill)
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
    def bind_exit_intent(self, intent_id: str, position_id: int, token_amount: int | None) -> str:
        """Bind an exit intent id to one position and amount: NEW, SAME (a replay) or CONFLICT."""
        with self.store.transaction() as c:
            row = c.execute("SELECT position_id, token_amount FROM exit_intents WHERE intent_id = ?",
                            (intent_id,)).fetchone()
            if row is None:
                c.execute("INSERT INTO exit_intents (intent_id, position_id, token_amount, created_ns)"
                          " VALUES (?,?,?,?)", (intent_id, position_id, token_amount, self.clock_ns()))
                return "NEW"
            return "SAME" if (row["position_id"], row["token_amount"]) == (position_id, token_amount) else "CONFLICT"

    def claim_exit(self, intent_id: str, position_id: int, token_amount: int | None, fill_id: str) -> Result:
        """Atomically: check the intent binding, validate the amount, and take exit ownership.
        Nothing is bound or locked unless every check passes."""
        with self.store.transaction() as c:
            pos = self.get(position_id)
            if pos is None:
                return Result(False, position_id, "UNKNOWN_POSITION")
            row = c.execute("SELECT position_id, token_amount, legacy_fill_id FROM exit_intents WHERE intent_id = ?",
                            (intent_id,)).fetchone()
            if row is not None and row["legacy_fill_id"]:
                return self._legacy_exit_replay(c, row, pos, token_amount)
            existing_fill = c.execute("SELECT exit_intent_id FROM fills WHERE fill_id = ?", (fill_id,)).fetchone()
            if row is not None:
                if (row["position_id"], row["token_amount"]) != (position_id, token_amount):
                    return Result(False, position_id, "INTENT_CONFLICT")
                if existing_fill is not None:
                    if existing_fill["exit_intent_id"] == intent_id:
                        return Result(True, position_id, "ALREADY_APPLIED")
                    # the fill under this id was not produced by this intent (or has no provenance): refuse
                    return Result(False, position_id, "AMBIGUOUS_HISTORICAL_FILL")
            elif existing_fill is not None:
                # a new intent whose fill id is already taken by another fill: refuse before binding anything
                return Result(False, position_id, "FILL_ID_COLLISION")
            if token_amount is not None and token_amount > pos.token_amount:
                return Result(False, position_id, "EXCEEDS_POSITION")
            if pos.status is Status.PENDING_CLOSE:
                if pos.exit_intent_id == intent_id:
                    return Result(True, position_id, "ALREADY_EXITING")
                return Result(False, position_id, "EXIT_IN_PROGRESS")
            if pos.status is not Status.OPEN:
                return Result(False, position_id, f"NOT_OPEN:{pos.status}")
            if row is None:
                c.execute("INSERT INTO exit_intents (intent_id, position_id, token_amount, created_ns)"
                          " VALUES (?,?,?,?)", (intent_id, position_id, token_amount, self.clock_ns()))
            c.execute("UPDATE positions SET status=?, exit_intent_id=? WHERE position_id=?",
                      (Status.PENDING_CLOSE.value, intent_id, position_id))
            return Result(True, position_id)

    @staticmethod
    def _legacy_exit_replay(c, row, pos: "Position", token_amount: int | None) -> Result:
        """An exit intent executed before schema v2. Its requested amount was never stored, so only an
        unambiguous replay is accepted: the same position and the same amount as its recorded fill, or a
        whole-position request when that fill is what closed the position. Anything else is refused."""
        if row["position_id"] != pos.position_id:
            return Result(False, pos.position_id, "INTENT_CONFLICT")
        fill = c.execute("SELECT token_amount, ts_ns FROM fills WHERE fill_id = ?", (row["legacy_fill_id"],)).fetchone()
        if token_amount is not None and fill is not None and token_amount == fill["token_amount"]:
            return Result(True, pos.position_id, "ALREADY_APPLIED")
        if token_amount is None and fill is not None and pos.status is Status.CLOSED:
            last = c.execute("SELECT MAX(ts_ns) FROM fills WHERE position_id = ? AND side = 'sell'",
                             (pos.position_id,)).fetchone()[0]
            if fill["ts_ns"] == last:
                return Result(True, pos.position_id, "ALREADY_APPLIED")
        return Result(False, pos.position_id, "INTENT_CONFLICT")

    def has_fill(self, fill_id: str) -> bool:
        return self.store.conn.execute("SELECT 1 FROM fills WHERE fill_id = ?", (fill_id,)).fetchone() is not None

    def begin_exit(self, position_id: int, intent_id: str) -> Result:
        """Take exit ownership. Never blocked by mode or slot limits; one exit owner at a time."""
        with self.store.transaction() as c:
            pos = self.get(position_id)
            if pos is None:
                return Result(False, position_id, "UNKNOWN_POSITION")
            if pos.status is Status.PENDING_CLOSE:
                if pos.exit_intent_id == intent_id:
                    return Result(True, position_id, "ALREADY_EXITING")
                return Result(False, position_id, "EXIT_IN_PROGRESS")
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
            if self._is_replay(c, position_id, fill):
                return
            pos = self._require(position_id, (Status.PENDING_CLOSE,))
            if fill.token_amount > pos.token_amount:
                raise ValueError(f"sell of {fill.token_amount} exceeds held {pos.token_amount}")
            self._insert_fill(c, position_id, fill, exit_intent_id=pos.exit_intent_id)
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
                # a partial sell completes this exit intent; the position is OPEN again for the next exit
                c.execute("UPDATE positions SET status=?, exit_intent_id=NULL, token_amount=?, proceeds_lamports=?,"
                          " protocol_fees_lamports=protocol_fees_lamports+?, network_fees_lamports=?"
                          " WHERE position_id=?",
                          (Status.OPEN.value, remaining, proceeds, fill.protocol_fee_lamports, network, position_id))

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
    def _is_replay(c, position_id: int, fill: Fill) -> bool:
        """True if this exact fill was already applied; FillConflict if the id was used for something else."""
        row = c.execute("SELECT position_id, side, token_amount, sol_lamports, protocol_fee_lamports,"
                        " network_fee_lamports, simulated FROM fills WHERE fill_id = ?", (fill.fill_id,)).fetchone()
        if row is None:
            return False
        if tuple(row) != fill.row(position_id):
            raise FillConflict(f"fill id {fill.fill_id} already used with different contents")
        return True

    @classmethod
    def _insert_fill(cls, c, position_id: int, fill: Fill, exit_intent_id: str | None = None) -> bool:
        if cls._is_replay(c, position_id, fill):
            return False
        cur = c.execute("INSERT OR IGNORE INTO fills (fill_id, position_id, side, token_amount, sol_lamports,"
                        " protocol_fee_lamports, network_fee_lamports, simulated, ts_ns, exit_intent_id)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (fill.fill_id, position_id, fill.side.value, fill.token_amount, fill.sol_lamports,
                         fill.protocol_fee_lamports, fill.network_fee_lamports, int(fill.simulated), fill.ts_ns,
                         exit_intent_id))
        return cur.rowcount == 1


def _to_position(row) -> Position:
    d = dict(row)
    d["status"] = Status(d["status"])
    d["simulated"] = bool(d["simulated"])
    return Position(**d)
