"""Migration intelligence (M1, M2, M3; docs/OWNER_DIRECTIVES_20261021.md §3) and the lifecycle feed (owner mandate
2026-10-20 §3, jinx/core/lifecycle.py), from the PumpAPI and PumpPortal streams.

M1, the actual migration transaction and the first verified post-migration liquidity:
- PumpAPI's `migrate` event is the migration transaction: signature, slot, PumpAPI time, and the new pool (`poolId`).
  It makes the token MIGRATION OBSERVED.
- The first PumpSwap trade on that announced pool, at or after the migration slot, verifies it: MIGRATION CONFIRMED
  (destination pool, venue "pump-amm", the migration's own PumpAPI time) and then POST-MIGRATION MARKET with the pool's
  reported reserves. Live (docs/PUMPAPI_EVALUATION.md): the first such trade came 0–0.7 s after the migrate.
- A pool trade delivered before its migrate is held (bounded) and checked when the migrate arrives. A trade on any
  other pool never confirms and never contradicts: anyone can open a second PumpSwap pool for a mint.
- PumpPortal's migration names the venue but not the pool, and has no time. It is a signal only; with it alone no
  pool can be verified. (Both feeds report the same transaction, so the bus keeps whichever arrives first; PumpAPI
  usually does, ~100 ms earlier.)
- Never inferred: not from a price rise, not from a complete curve, not from a missing token.

M2, decision-time evidence (no migration-buy threshold is invented): pre-migration liquidity (the last curve reading
before the migration, its peak, and whether the curve is a standard one); first and latest post-migration
liquidity; the largest sells around graduation, listed by size with the reserve after each (what counts as "large"
is B-03); the creator's sells before and after the migration; the wallets with the largest observed net buys at the
migration and their sells after it; the observed holder distribution at the migration and now; the first pool
trades after the migration (competing early transactions) with their slot and time offsets. Holder figures come from
observed trades by fee payer since JINX saw the creation: transfers are invisible, so they are not balances, and
`complete` says whether the observation covers the token's whole life.

M3, a research variant, off by default: with `confirmation_delay_ms` set, the evidence names the first pool trade at
or after migration + delay as the paper reference. Never live policy.

Migration is neither a buy signal nor a reason to abandon a position: this module places nothing and permits
nothing. State is RAM only and bounded (mints, curve and pool trades, held pool trades, wallets per mint).
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from jinx.core.events import LAMPORTS_PER_SOL, EventKind, MarketEvent, Side
from jinx.core.lifecycle import Evidence as LifeEvidence, Kind, MissingEvidence, TokenLifecycle
from jinx.intel.bounded import MintTable
from jinx.intel.readings import (PUMPAPI, PUMPSWAP_VENUE, Clock, event_time, is_curve, pool_id, real_reserves,
                                 standard_curve, venue)

log = logging.getLogger(__name__)

SIGNAL_TYPE = "migration"
LARGE_SELL_RULE = "not classified: CHAD and the owner directive name large sells around graduation but no size (B-03)"
TOP_SELLS = 10
TOP_HOLDERS = 10            # the same top-10 cut as GMGN's `top_10_holder_rate`
COMPETING_LISTED = 20
# RAM bounds (owner storage rule 2), not trading rules
MAX_MINTS = 20_000
IDLE_TTL_S = 2 * 3600
MAX_CURVE_TRADES = 256
MAX_POOL_TRADES = 256
MAX_HELD_POOL_TRADES = 64
MAX_WALLETS = 5_000


@dataclass(frozen=True)
class Trade:
    signature: str
    fee_payer: str
    side: Side
    sol_lamports: int
    token_amount: int
    slot: int | None
    event_ns: int
    clock: Clock
    recv_ns: int
    seq: int
    venue: str | None
    pool_id: str | None
    quote_reserve_lamports: int | None    # real SOL in the curve or pool after the trade
    token_reserve: int | None
    v_sol_lamports: int | None

    @property
    def key(self) -> tuple[int, int]:
        return (self.event_ns, self.seq)


@dataclass(frozen=True)
class MigrationTx:
    signature: str
    source: str
    slot: int | None
    event_ns: int
    clock: Clock
    observed_ns: int
    pool_id: str | None
    venue: str | None

    def to_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Clock) else v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class MigrationEvidence:
    mint: str
    state: str
    migration: MigrationTx | None
    verified: bool
    verification_status: str
    first_verified_liquidity: dict | None
    latest_post_liquidity: dict | None
    pre_migration_liquidity: dict | None
    sells_around_graduation: list
    developer_selling: dict
    holder_selling: dict
    holder_distribution: dict
    competing_early_transactions: list
    foreign_pool_trades: int
    unverified_pool_trades: int
    research: dict | None
    observed_ns: int
    processing_ns: int
    large_sell_rule: str = LARGE_SELL_RULE
    signal_type: str = SIGNAL_TYPE

    @property
    def event_ns(self) -> int:
        return self.migration.event_ns

    @property
    def clock(self) -> Clock:
        return self.migration.clock

    def to_dict(self) -> dict:
        return {"mint": self.mint, "signal_type": self.signal_type, "sources": [self.migration.source],
                "observed_ns": self.observed_ns, "event_ns": self.event_ns, "clock": str(self.clock),
                "freshness": {"feed_delay_ms": (self.observed_ns - self.event_ns) / 1e6,
                              "processing_us": self.processing_ns / 1e3},
                "state": self.state, "verified": self.verified, "verification_status": self.verification_status,
                "payload": {"migration": self.migration.to_dict(),
                            "first_verified_liquidity": self.first_verified_liquidity,
                            "latest_post_liquidity": self.latest_post_liquidity,
                            "pre_migration_liquidity": self.pre_migration_liquidity,
                            "sells_around_graduation": self.sells_around_graduation,
                            "large_sell_rule": self.large_sell_rule, "developer_selling": self.developer_selling,
                            "holder_selling": self.holder_selling, "holder_distribution": self.holder_distribution,
                            "competing_early_transactions": self.competing_early_transactions,
                            "foreign_pool_trades": self.foreign_pool_trades,
                            "unverified_pool_trades": self.unverified_pool_trades, "research": self.research}}


def _sol(lamports: int | None) -> float | None:
    return None if lamports is None else lamports / LAMPORTS_PER_SOL


def _life_clock(clock: Clock) -> str:
    return "receipt" if clock is Clock.RECEIPT else "provider"


class _Flow:
    __slots__ = ("bought", "sold", "pre", "post")

    def __init__(self) -> None:
        self.bought = self.sold = 0
        self.pre = [0, 0, 0]    # sells, SOL, tokens before the migration
        self.post = [0, 0, 0]   # after it

    @property
    def net(self) -> int:
        return self.bought - self.sold


class _Token:
    def __init__(self, mint: str, max_curve: int) -> None:
        self.life = TokenLifecycle(mint)
        self.creator: str | None = None
        self.since_creation = False
        self.curve: deque[Trade] = deque(maxlen=max_curve)
        self.latest_curve: Trade | None = None
        self.peak_v = 0
        self.migration: MigrationTx | None = None
        self.held: list[Trade] = []
        self.pool: list[Trade] = []
        self.latest_pool: Trade | None = None
        self.first: Trade | None = None
        self.foreign = self.unverified = 0
        self.flow: dict[str, _Flow] = {}
        self.flow_complete = True
        self.snapshot: dict | None = None          # holders at the migration
        self.detection: tuple[int, int] | None = None   # (observed_ns, processing_ns) of the migration evidence


class MigrationTracker:
    def __init__(self, notify: Callable[[MigrationEvidence], Any] | None = None,
                 confirmation_delay_ms: int | None = None, max_mints: int = MAX_MINTS,
                 idle_ttl_s: float = IDLE_TTL_S, max_curve_trades: int = MAX_CURVE_TRADES,
                 max_pool_trades: int = MAX_POOL_TRADES, max_wallets: int = MAX_WALLETS) -> None:
        self.notify, self.confirmation_delay_ms = notify, confirmation_delay_ms
        self.max_curve, self.max_pool, self.max_wallets = max_curve_trades, max_pool_trades, max_wallets
        self._tokens: MintTable[_Token] = MintTable(lambda mint: _Token(mint, self.max_curve), max_mints,
                                                    int(idle_ttl_s * 1_000_000_000))
        self.stats = {"events": 0, "migrations": 0, "confirmed": 0, "pool_trades_before_migration": 0,
                      "wallets_untracked": 0, "lifecycle_refused": 0, "listener_errors": 0}

    # ---- API -----------------------------------------------------------------------------------------
    def on_event(self, ev: MarketEvent) -> list[MigrationEvidence]:
        started = time.perf_counter_ns()
        self.stats["events"] += 1
        tok = self._tokens.touch(ev.mint, ev.recv_ns)
        if ev.kind is EventKind.TOKEN_CREATED:
            tok.creator, tok.since_creation = ev.trader, True
            self._observe(tok, Kind.CREATED, ev, {"pre_pool": pool_id(ev)})
            return []
        if ev.kind is EventKind.MIGRATION:
            return self._migration(tok, ev, started)
        if ev.kind is not EventKind.TRADE:
            return []
        trade = self._trade(ev)
        if is_curve(ev):
            self._curve_trade(tok, trade, ev)
            return []
        if venue(ev) == PUMPSWAP_VENUE:
            return self._pool_trade(tok, trade)
        return []

    def evidence(self, mint: str) -> MigrationEvidence | None:
        tok = self._tokens.get(mint)
        return self._build(mint, tok) if tok is not None and tok.migration is not None else None

    def lifecycle(self, mint: str) -> TokenLifecycle | None:
        tok = self._tokens.get(mint)
        return tok.life if tok is not None else None

    def tracked_mints(self) -> int:
        return len(self._tokens)

    def curve_trades_held(self, mint: str) -> int:
        tok = self._tokens.get(mint)
        return len(tok.curve) if tok else 0

    # ---- feeding -------------------------------------------------------------------------------------
    @staticmethod
    def _trade(ev: MarketEvent) -> Trade:
        t, clock = event_time(ev)
        quote, tokens = real_reserves(ev)
        return Trade(ev.signature, ev.trader, ev.side, ev.sol_lamports, ev.token_amount, ev.slot, t, clock, ev.recv_ns,
                     ev.seq, venue(ev), pool_id(ev), quote, tokens, ev.v_sol_lamports)

    def _observe(self, tok: _Token, kind: Kind, ev: MarketEvent, payload: dict) -> None:
        t, clock = event_time(ev)
        self._life(tok, kind, ev.source, ev.recv_ns, t, clock, payload)

    def _life(self, tok: _Token, kind: Kind, source: str, observed_ns: int, event_ns: int, clock: Clock,
              payload: dict) -> None:
        try:
            tok.life.observe(LifeEvidence(kind, source, observed_ns, event_ns, _life_clock(clock),
                                          {k: v for k, v in payload.items() if v is not None}))
        except MissingEvidence:
            self.stats["lifecycle_refused"] += 1

    def _curve_trade(self, tok: _Token, trade: Trade, ev: MarketEvent) -> None:
        tok.curve.append(trade)
        tok.peak_v = max(tok.peak_v, trade.v_sol_lamports)
        if tok.latest_curve is None or trade.key > tok.latest_curve.key:
            tok.latest_curve = trade
        self._flow(tok, trade)
        self._observe(tok, Kind.ON_CURVE, ev, {"v_sol_lamports": ev.v_sol_lamports, "pre_pool": trade.pool_id,
                                               "liquidity": trade.quote_reserve_lamports})

    def _flow(self, tok: _Token, trade: Trade) -> None:
        f = tok.flow.get(trade.fee_payer)
        if f is None:
            if len(tok.flow) >= self.max_wallets:
                tok.flow_complete = False
                self.stats["wallets_untracked"] += 1
                return
            f = tok.flow[trade.fee_payer] = _Flow()
        if trade.side is Side.BUY:
            f.bought += trade.token_amount
            return
        f.sold += trade.token_amount
        after = tok.migration is not None and self._after_migration(tok, trade)
        bucket = f.post if after else f.pre
        bucket[0] += 1
        bucket[1] += trade.sol_lamports
        bucket[2] += trade.token_amount

    @staticmethod
    def _after_migration(tok: _Token, trade: Trade) -> bool:
        m = tok.migration
        if trade.venue == PUMPSWAP_VENUE:
            return True
        return trade.slot is not None and m.slot is not None and trade.slot > m.slot

    def _migration(self, tok: _Token, ev: MarketEvent, started: int) -> list[MigrationEvidence]:
        t, clock = event_time(ev)
        new = MigrationTx(ev.signature, ev.source, ev.slot, t, clock, ev.recv_ns, pool_id(ev), venue(ev))
        if tok.migration is not None and (tok.migration.pool_id is not None or new.pool_id is None):
            return []   # a repeated report, or a second one without a pool, adds nothing
        first_report = tok.migration is None
        tok.migration = new
        if first_report:
            self.stats["migrations"] += 1
            tok.snapshot = self._holders(tok)
        self._observe(tok, Kind.MIGRATION_SIGNAL, ev, {"signature": ev.signature, "exchange": new.venue,
                                                       "announced_pool": new.pool_id})
        tok.detection = (ev.recv_ns, time.perf_counter_ns() - started)
        out = [self._emit(tok)]
        held, tok.held = tok.held, []
        for trade in sorted(held, key=lambda x: x.key):
            out += self._pool_trade(tok, trade)
        return out

    def _pool_trade(self, tok: _Token, trade: Trade) -> list[MigrationEvidence]:
        m = tok.migration
        if m is None:
            if len(tok.held) < MAX_HELD_POOL_TRADES:
                tok.held.append(trade)
            return []
        self._flow(tok, trade)
        if m.pool_id is None:
            tok.unverified += 1
            return []
        if trade.pool_id != m.pool_id:
            tok.foreign += 1
            return []
        if trade.slot is not None and m.slot is not None and trade.slot < m.slot:
            self.stats["pool_trades_before_migration"] += 1
            return []
        if len(tok.pool) < self.max_pool:
            tok.pool.append(trade)
        if tok.latest_pool is None or trade.key > tok.latest_pool.key:
            tok.latest_pool = trade
        out = []
        if tok.first is None:
            tok.first = trade
            self.stats["confirmed"] += 1
            # confirmed on the migration's own time (the provider's), verified by the first trade on its pool
            self._life(tok, Kind.MIGRATION_CONFIRMED, m.source, trade.recv_ns, m.event_ns, m.clock,
                       {"post_pool": m.pool_id, "exchange": m.venue, "migration_signature": m.signature,
                        "verified_by": trade.signature})
            out.append(self._emit(tok))
        elif trade.key < tok.first.key:
            tok.first = trade   # an earlier trade delivered late is the first verified liquidity
        self._life(tok, Kind.POST_MIGRATION_MARKET, PUMPAPI, trade.recv_ns, trade.event_ns, trade.clock,
                   {"pool": m.pool_id, "liquidity": trade.quote_reserve_lamports, "signature": trade.signature})
        return out

    def _emit(self, tok: _Token) -> MigrationEvidence:
        evidence = self._build(tok.life.mint, tok)
        if self.notify is not None:
            try:
                self.notify(evidence)
            except Exception:
                self.stats["listener_errors"] += 1
                log.exception("migration listener failed")
        return evidence

    # ---- evidence ------------------------------------------------------------------------------------
    def _holders(self, tok: _Token) -> dict:
        positive = sorted(((w, f.net) for w, f in tok.flow.items() if f.net > 0), key=lambda x: (-x[1], x[0]))
        total = sum(n for _, n in positive)
        top = positive[:TOP_HOLDERS]
        return {"holders": len(positive), "observed_net_tokens": total,
                "top10_share": (sum(n for _, n in top) / total) if total else None,
                "top": [w for w, _ in top]}

    def _liquidity(self, trade: Trade | None, m: MigrationTx) -> dict | None:
        if trade is None:
            return None
        slots = trade.slot - m.slot if trade.slot is not None and m.slot is not None else None
        return {"signature": trade.signature, "slot": trade.slot, "event_ns": trade.event_ns,
                "clock": str(trade.clock), "pool_id": trade.pool_id, "quote_reserve_lamports": trade.quote_reserve_lamports,
                "token_reserve": trade.token_reserve, "slots_after_migration": slots,
                "ms_after_migration": (trade.event_ns - m.event_ns) / 1e6}

    def _sell_row(self, trade: Trade, tok: _Token) -> dict:
        return {"signature": trade.signature, "fee_payer": trade.fee_payer, "venue": trade.venue,
                "phase": "after" if self._after_migration(tok, trade) else "before", "slot": trade.slot,
                "event_ns": trade.event_ns, "sol_lamports": trade.sol_lamports, "tokens": trade.token_amount,
                "quote_reserve_after_lamports": trade.quote_reserve_lamports}

    def _build(self, mint: str, tok: _Token) -> MigrationEvidence:
        m = tok.migration
        pre = tok.latest_curve
        pre_liq = None if pre is None else {
            "signature": pre.signature, "slot": pre.slot, "event_ns": pre.event_ns,
            "virtual_sol": _sol(pre.v_sol_lamports), "real_sol": _sol(pre.quote_reserve_lamports),
            "real_tokens": pre.token_reserve, "standard_curve": standard_curve(pre.v_sol_lamports,
                                                                               pre.quote_reserve_lamports),
            "peak_virtual_sol": _sol(tok.peak_v)}
        sells = [t for t in (*tok.curve, *tok.pool) if t.side is Side.SELL]
        sells.sort(key=lambda t: (-t.sol_lamports, t.key))
        if m.pool_id is None:
            status = "unverifiable: the migration report names the venue but no pool address (PumpPortal)"
        elif tok.first is None:
            status = "waiting: no trade yet on the announced pool"
        else:
            status = "verified: first trade on the announced pool"
        research = None
        if self.confirmation_delay_ms is not None:
            due = m.event_ns + self.confirmation_delay_ms * 1_000_000
            ref = next((t for t in sorted(tok.pool, key=lambda x: x.key) if t.event_ns >= due), None)
            research = {"variant": "M3 confirmation delay", "live_policy": False,
                        "delay_ms": self.confirmation_delay_ms, "due_event_ns": due,
                        "reference_trade": None if ref is None else {**self._liquidity(ref, m),
                                                                     "fee_payer": ref.fee_payer}}
        observed_ns, processing_ns = tok.detection
        return MigrationEvidence(
            mint=mint, state=tok.life.state.name, migration=m, verified=tok.first is not None,
            verification_status=status, first_verified_liquidity=self._liquidity(tok.first, m),
            latest_post_liquidity=self._liquidity(tok.latest_pool, m), pre_migration_liquidity=pre_liq,
            sells_around_graduation=[self._sell_row(t, tok) for t in sells[:TOP_SELLS]],
            developer_selling=self._developer(tok), holder_selling=self._holder_selling(tok),
            holder_distribution={"basis": self._basis(), "complete": self._complete(tok),
                                 "at_migration": tok.snapshot, "latest": self._holders(tok)},
            competing_early_transactions=self._competing(tok, m), foreign_pool_trades=tok.foreign,
            unverified_pool_trades=tok.unverified, research=research, observed_ns=observed_ns,
            processing_ns=processing_ns)

    @staticmethod
    def _basis() -> str:
        return ("observed trades by fee payer since JINX saw the creation; transfers are invisible, so these are not "
                "balances")

    def _complete(self, tok: _Token) -> bool:
        return tok.since_creation and tok.flow_complete

    def _developer(self, tok: _Token) -> dict:
        if not tok.since_creation or tok.creator is None:
            return {"status": "UNAVAILABLE", "reason": "the creation was not observed", "creator": tok.creator}
        f = tok.flow.get(tok.creator)
        pre, post = (f.pre, f.post) if f is not None else ([0, 0, 0], [0, 0, 0])
        as_dict = (lambda b: {"sells": b[0], "sol_lamports": b[1], "tokens": b[2]})
        return {"status": "observed", "creator": tok.creator, "bought_tokens": f.bought if f else 0,
                "before": as_dict(pre), "after": as_dict(post)}

    def _holder_selling(self, tok: _Token) -> dict:
        top = (tok.snapshot or {}).get("top", [])
        rows = []
        for w in top:
            f = tok.flow.get(w)
            if f is None:
                continue
            rows.append({"fee_payer": w, "net_tokens_now": f.net,
                         "before": {"sells": f.pre[0], "sol_lamports": f.pre[1], "tokens": f.pre[2]},
                         "after": {"sells": f.post[0], "sol_lamports": f.post[1], "tokens": f.post[2]}})
        return {"basis": self._basis(), "complete": self._complete(tok), "top_at_migration": rows}

    @staticmethod
    def _competing(tok: _Token, m: MigrationTx) -> list:
        rows = []
        for t in sorted(tok.pool, key=lambda x: x.key)[:COMPETING_LISTED]:
            rows.append({"signature": t.signature, "fee_payer": t.fee_payer, "side": str(t.side),
                         "sol_lamports": t.sol_lamports, "slot": t.slot,
                         "slots_after_migration": t.slot - m.slot if t.slot is not None and m.slot is not None else None,
                         "ms_after_migration": (t.event_ns - m.event_ns) / 1e6})
        return rows
