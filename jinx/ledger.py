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
    quote_lamports: int | None = None   # what the decision was quoted (slippage = quote - actual); not identity

    def __post_init__(self) -> None:
        if not isinstance(self.fill_id, str) or not self.fill_id:
            raise ValueError("fill needs a non-empty text id")
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
    # Phase 3 (schema v10); None for positions opened before it
    strategy: str | None = None
    exit_policy: str | None = None
    exit_policy_json: str | None = None
    peak_value_lamports: int | None = None
    peak_ns: int | None = None
    entry_score: str | None = None
    entry_threshold: str | None = None
    entry_filter: str | None = None
    sizing_json: str | None = None
    signal_ns: int | None = None
    last_mark_lamports: int | None = None
    last_mark_ns: int | None = None


POSITION_FIELDS = ("strategy", "exit_policy", "exit_policy_json", "entry_score", "entry_threshold", "entry_filter",
                   "sizing_json", "signal_ns")
EXIT_META = ("source_strategy", "exit_policy", "reason_code", "exit_urgency", "required_sell_pct", "event_ns",
             "triggering_evidence", "previously_executed_fraction", "remaining_confirmed_balance", "venue",
             "quote_status", "quote_lamports")
ORDER_STAGES = ("DECIDED", "QUOTED", "SUBMITTED", "FILLED", "FAILED", "REFUSED")


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
    def reserve_entry(self, intent_id: str, mint: str, budget_lamports: int, simulated: bool = True,
                      fields: dict | None = None) -> Result:
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
            extra = {k: v for k, v in (fields or {}).items() if k in POSITION_FIELDS}
            cols = ["entry_intent_id", "entry_budget_lamports", "mint", "status", "simulated", "opened_ns", *extra]
            cur = c.execute(f"INSERT INTO positions ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            (intent_id, budget_lamports, mint, Status.PENDING_OPEN.value, int(simulated),
                             self.clock_ns(), *extra.values()))
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

    def claim_exit(self, intent_id: str, position_id: int, token_amount: int | None, fill_id: str,
                   meta: dict | None = None) -> Result:
        """Atomically: check the intent binding, validate the amount, and take exit ownership.
        Nothing is bound or locked unless every check passes."""
        with self.store.transaction() as c:
            pos = self.get(position_id)
            if pos is None:
                return Result(False, position_id, "UNKNOWN_POSITION")
            if self._unclassified_history(c, position_id):
                return Result(False, position_id, "UNCLASSIFIED_HISTORY")
            historical = c.execute("SELECT fill_id, position_id FROM historical_exits WHERE intent_id = ?",
                                   (intent_id,)).fetchall()
            if historical:
                # historical evidence outranks any binding: this intent already sold before provenance existed
                return self._historical_exit_replay(c, historical, pos, token_amount)
            row = c.execute("SELECT position_id, token_amount, legacy_fill_id FROM exit_intents WHERE intent_id = ?",
                            (intent_id,)).fetchone()
            if row is not None and row["legacy_fill_id"]:
                return Result(False, position_id, "INTENT_CONFLICT")  # a legacy marker without evidence
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
                m = {k: v for k, v in (meta or {}).items() if k in EXIT_META}
                cols = ["intent_id", "position_id", "token_amount", "created_ns", *m]
                c.execute(f"INSERT INTO exit_intents ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          (intent_id, position_id, token_amount, self.clock_ns(), *m.values()))
            c.execute("UPDATE positions SET status=?, exit_intent_id=? WHERE position_id=?",
                      (Status.PENDING_CLOSE.value, intent_id, position_id))
            return Result(True, position_id)

    @staticmethod
    def _unclassified_history(c, position_id: int) -> bool:
        """The position has a sell recorded before provenance under a fill id that is not text (store v8). No
        intent can be matched to it, so any new exit could repeat it: refused until the owner reconciles."""
        return c.execute("SELECT 1 FROM unclassified_exits WHERE position_id = ?", (position_id,)).fetchone() is not None

    @staticmethod
    def _historical_exit_replay(c, historical, pos: "Position", token_amount: int | None) -> Result:
        """An exit intent whose sell happened before fills recorded provenance. Its requested amount was
        never stored, so only an unambiguous replay is accepted: the same position and the same amount as its
        recorded fill, or a whole-position request when that fill is what closed the position. Anything else
        is refused, and nothing is ever sold for it again."""
        mine = [h for h in historical if h["position_id"] == pos.position_id]
        if len(mine) != 1:
            return Result(False, pos.position_id, "INTENT_CONFLICT")
        fill = c.execute("SELECT token_amount, ts_ns FROM fills WHERE fill_id = ?", (mine[0]["fill_id"],)).fetchone()
        if fill is None:
            return Result(False, pos.position_id, "INTENT_CONFLICT")
        if token_amount is not None and token_amount == fill["token_amount"]:
            return Result(True, pos.position_id, "ALREADY_APPLIED")
        if token_amount is None and pos.status is Status.CLOSED:
            # the closing sell must be established uniquely: tied timestamps prove nothing (no ordering invented)
            closing = c.execute("SELECT COUNT(*) FROM fills WHERE position_id = ? AND side = 'sell' AND ts_ns ="
                                " (SELECT MAX(ts_ns) FROM fills WHERE position_id = ? AND side = 'sell')",
                                (pos.position_id, pos.position_id)).fetchone()[0]
            last = c.execute("SELECT MAX(ts_ns) FROM fills WHERE position_id = ? AND side = 'sell'",
                             (pos.position_id,)).fetchone()[0]
            if closing == 1 and fill["ts_ns"] == last:
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
            if self._unclassified_history(c, position_id):
                return Result(False, position_id, "UNCLASSIFIED_HISTORY")
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
                # a partial sell completes this exit intent; the position is OPEN again for the next exit. The peak
                # is the value of the tokens held, so it is rescaled to the tokens still held (same ratio to the
                # entry basis, which scales the same way).
                peak = None if pos.peak_value_lamports is None else pos.peak_value_lamports * remaining // pos.token_amount
                c.execute("UPDATE positions SET status=?, exit_intent_id=NULL, token_amount=?, proceeds_lamports=?,"
                          " protocol_fees_lamports=protocol_fees_lamports+?, network_fees_lamports=?,"
                          " peak_value_lamports=? WHERE position_id=?",
                          (Status.OPEN.value, remaining, proceeds, fill.protocol_fee_lamports, network, peak,
                           position_id))

    # ---- exit state (Phase 3) -------------------------------------------
    def raise_peak(self, position_id: int, value_lamports: int, ts_ns: int) -> int:
        """Persist the highest observed value of the held tokens; a peak is never lowered. Returns the peak."""
        _check_int("value_lamports", value_lamports, 0)
        with self.store.transaction() as c:
            row = c.execute("SELECT peak_value_lamports FROM positions WHERE position_id = ?",
                            (position_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown position {position_id}")
            if row[0] is None or value_lamports > row[0]:
                c.execute("UPDATE positions SET peak_value_lamports=?, peak_ns=? WHERE position_id=?",
                          (value_lamports, ts_ns, position_id))
                return value_lamports
            return row[0]

    def mark(self, position_id: int, value_lamports: int, ts_ns: int) -> None:
        """The latest executable value of the held tokens (unrealized P&L); never a fill."""
        self.store.conn.execute("UPDATE positions SET last_mark_lamports=?, last_mark_ns=? WHERE position_id=?",
                                (value_lamports, ts_ns, position_id))

    def bought_tokens(self, position_id: int) -> int:
        return self.store.conn.execute("SELECT COALESCE(SUM(token_amount), 0) FROM fills WHERE position_id = ?"
                                       " AND side = 'buy'", (position_id,)).fetchone()[0]

    def entry_basis(self, position_id: int) -> int | None:
        """What the tokens still held cost (protocol fee included, network fee not): the position's cost scaled
        to the tokens remaining. None when the position holds nothing or has no buy fill."""
        pos = self.get(position_id)
        bought = self.bought_tokens(position_id)
        if pos is None or bought == 0 or pos.token_amount == 0:
            return None
        return pos.cost_lamports * pos.token_amount // bought

    def record_order_event(self, intent_id: str, position_id: int | None, side: str, stage: str, ts_ns: int,
                           simulated: bool, cost_lamports: int = 0, detail: str | None = None) -> None:
        if stage not in ORDER_STAGES:
            raise ValueError(f"unknown order stage {stage}")
        _check_int("cost_lamports", cost_lamports, 0)
        with self.store.transaction() as c:
            c.execute("INSERT INTO order_events (intent_id, position_id, side, stage, ts_ns, simulated, cost_lamports,"
                      " detail) VALUES (?,?,?,?,?,?,?,?)", (intent_id, position_id, side, stage, ts_ns,
                                                           int(simulated), cost_lamports, detail))

    def order_events(self, intent_id: str) -> list[dict]:
        return [dict(r) for r in self.store.conn.execute("SELECT * FROM order_events WHERE intent_id = ? ORDER BY id",
                                                         (intent_id,))]

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
                        " protocol_fee_lamports, network_fee_lamports, simulated, ts_ns, exit_intent_id, quote_lamports)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (fill.fill_id, position_id, fill.side.value, fill.token_amount, fill.sol_lamports,
                         fill.protocol_fee_lamports, fill.network_fee_lamports, int(fill.simulated), fill.ts_ns,
                         exit_intent_id, fill.quote_lamports))
        return cur.rowcount == 1


def _to_position(row) -> Position:
    d = dict(row)
    d["status"] = Status(d["status"])
    d["simulated"] = bool(d["simulated"])
    return Position(**d)
