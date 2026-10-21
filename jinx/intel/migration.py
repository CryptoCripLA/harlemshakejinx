"""Migration intelligence (M1, M2, M3; docs/OWNER_DIRECTIVES_20261021.md §3) and the lifecycle feed (owner mandate
2026-10-20 §3, jinx/core/lifecycle.py), from the PumpAPI and PumpPortal streams and GMGN.

One migration, one record. Every report of a token's migration is kept and merged into one authoritative record
(owner order 2026-10-21: "deduplicate effects, not useful information"):
- PumpAPI's `migrate` is the migration transaction: signature, slot, PumpAPI millisecond time and the new pool
  (`poolId`). PumpPortal's report of the same transaction names only the venue and has no time. GMGN reports a
  completed curve (Trenches `completed`, whole-second `complete_timestamp`) and, through token info, the
  `migrated_pool` (docs/GMGN_ADAPTER_CONTRACT.md §5). GMGN reports are taken for tokens JINX already tracks.
- The record's time comes from the report on the most authoritative clock: chain time, then the provider's own time
  (PumpAPI's transaction report, which has the slot, before GMGN's whole second), then JINX's receipt time. A later
  report never replaces a provider time with a receipt time. Reports naming different pools are a conflict: no pool
  is chosen by arrival order, nothing is verified, and a conflict after a confirmation makes the lifecycle
  CONFLICTING. Without a pool, `request_pool(mint)` asks the app for GMGN's `migrated_pool`.
- Effects happen once: the first report (MIGRATION OBSERVED), a report that adds the pool, and every change of the
  verification result are pushed to `notify`; a corroborating report adds provenance only.

M1, the first verified post-migration liquidity, is derived (never sticky) from the current record and the PumpSwap
trades seen: the earliest trade on the record's single pool that has both reserves and is provably at or after the
migration (slot against slot when both are known, otherwise provider time against provider time). The migration time
must be on a provider clock. Verification makes the token MIGRATION CONFIRMED (on the migration's own time) and then
POST-MIGRATION MARKET; later evidence that invalidates the verifying trade revokes it (recorded as a lifecycle
failure) until another trade qualifies. A trade on any other pool never confirms. Never inferred from a price rise, a
complete curve or a missing token.

M2, decision-time evidence (no migration-buy threshold is invented): pre-migration liquidity (the last curve reading,
its peak, whether the curve is a standard one); first and latest post-migration liquidity; the largest sells of the
token's observed life with seller, size, phase and the reserve after each (no "large" label: no approved rule),
ranked independently of the retention caps; the creator's sells before and after; holder concentration from
verified balances (GMGN top holders, requested once per migration through `request_holders`) where available, and
separately the observed trade flow by fee payer, labelled as not proof of ownership (its at-migration view is computed
from trades before the migration, so late delivery converges); the first pool trades (competing early transactions).

M3, a research variant, off by default: with `confirmation_delay_ms` set, the evidence names the first qualifying
pool trade at or after migration + delay as the paper reference. Never live policy.

Migration is neither a buy signal nor a reason to abandon a position: this module places nothing and permits
nothing. Duplicate deliveries of a trade are counted once. State is RAM only and bounded (mints, reports, trades,
seen trade identities, wallets, ranked sells, holders).
"""
from __future__ import annotations

import bisect
import logging
import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from jinx.core.events import LAMPORTS_PER_SOL, EventKind, MarketEvent, Side
from jinx.core.lifecycle import Evidence as LifeEvidence, Kind, MissingEvidence, State, TokenLifecycle
from jinx.intel.bounded import MintTable
from jinx.intel.readings import (PUMPAPI, PUMPSWAP_VENUE, Clock, event_time, is_curve, pool_id, real_reserves,
                                 reserves_continuous, standard_curve, venue)

log = logging.getLogger(__name__)

SIGNAL_TYPE = "migration"
LARGE_SELL_RULE = ("not classified: no approved rule says what makes a sell large; sizes and sellers are recorded "
                   "(owner order 2026-10-21, item 7)")
OBSERVED_FLOW_BASIS = ("observed trades by fee payer since JINX saw the creation; transfers are invisible, so this is "
                       "not proof of current ownership")
TOP_SELLS = 10
TOP_HOLDERS = 10            # the same top-10 cut as GMGN's `top_10_holder_rate`
COMPETING_LISTED = 20
CLOCK_RANK = {Clock.CHAIN: 0, Clock.PROVIDER: 1, Clock.RECEIPT: 2}
# RAM bounds (owner storage rule 2), not trading rules
MAX_MINTS = 20_000
IDLE_TTL_S = 2 * 3600
MAX_CURVE_TRADES = 256
MAX_POOL_TRADES = 256
MAX_WALLETS = 5_000
MAX_REPORTS = 8
MAX_POOLS = 8
M3_RETENTION = 4_096        # pool trades kept when the M3 paper variant is on, so its reference is derived, not cached
MAX_HOLDERS_KEPT = 100


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
class MigrationReport:
    """One source's report of a token's migration, as it arrived."""
    source: str
    signature: str | None
    slot: int | None
    event_ns: int
    clock: Clock
    observed_ns: int
    pool_id: str | None
    venue: str | None

    @property
    def rank(self) -> tuple[int, int, int]:
        return (CLOCK_RANK[self.clock], 0 if self.slot is not None else 1, self.event_ns)

    def to_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Clock) else v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class MigrationTx:
    """The one authoritative migration record, merged from every report (arrival order kept in `reports`)."""
    reports: tuple[MigrationReport, ...]
    reports_seen: int = 0       # every distinct report received, including those folded away at the cap

    @property
    def _best(self) -> MigrationReport:
        return min(self.reports, key=lambda r: r.rank)

    @property
    def event_ns(self) -> int:
        return self._best.event_ns

    @property
    def clock(self) -> Clock:
        return self._best.clock

    @property
    def source(self) -> str:
        return self._best.source

    @property
    def signature(self) -> str | None:
        best = self._best
        return best.signature or next((r.signature for r in self.reports if r.signature), None)

    @property
    def slot(self) -> int | None:
        return next((r.slot for r in self.reports if r.slot is not None), None)

    @property
    def pools_named(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.pool_id for r in self.reports if r.pool_id))

    @property
    def pool_id(self) -> str | None:
        """The pool, only when every report that names one names the same one."""
        pools = self.pools_named
        return pools[0] if len(pools) == 1 else None

    @property
    def venue(self) -> str | None:
        return next((r.venue for r in self.reports if r.venue), None)

    @property
    def observed_ns(self) -> int:
        return min(r.observed_ns for r in self.reports)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.source for r in self.reports))

    def to_dict(self) -> dict:
        return {"signature": self.signature, "slot": self.slot, "event_ns": self.event_ns, "clock": str(self.clock),
                "time_source": self.source, "observed_ns": self.observed_ns, "pool_id": self.pool_id,
                "pools_named": list(self.pools_named), "venue": self.venue, "sources": list(self.sources),
                "reports_seen": self.reports_seen, "reports": [r.to_dict() for r in self.reports]}


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
        return {"mint": self.mint, "signal_type": self.signal_type, "sources": list(self.migration.sources),
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


def _fraction(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
        return None
    return float(v)


class _Flow:
    __slots__ = ("bought_pre", "bought_post", "pre", "post")

    def __init__(self) -> None:
        self.bought_pre = self.bought_post = 0
        self.pre = [0, 0, 0]    # sells, SOL, tokens before the migration
        self.post = [0, 0, 0]   # after it

    @property
    def net_pre(self) -> int:
        return self.bought_pre - self.pre[2]

    @property
    def net(self) -> int:
        return self.bought_pre + self.bought_post - self.pre[2] - self.post[2]


class _Pool:
    """PumpSwap trades on one pool, kept as the earliest by event time (not by arrival), so a late-delivered earlier
    trade still counts; trades with both reserves are kept apart so verification evidence is never crowded out."""
    __slots__ = ("all", "full", "latest", "count", "cap")

    def __init__(self, cap: int) -> None:
        self.all: list[Trade] = []
        self.full: list[Trade] = []
        self.latest: Trade | None = None
        self.count = 0
        self.cap = cap

    def add(self, trade: Trade) -> None:
        self.count += 1
        lists = (self.all, self.full) if (trade.quote_reserve_lamports is not None
                                          and trade.token_reserve is not None) else (self.all,)
        for kept in lists:
            bisect.insort(kept, trade, key=lambda t: t.key)
            if len(kept) > self.cap:
                kept.pop()
        if self.latest is None or trade.key > self.latest.key:
            self.latest = trade


class _Token:
    def __init__(self, mint: str, max_curve: int, max_seen: int) -> None:
        self.life = TokenLifecycle(mint)
        self.creator: str | None = None
        self.since_creation = False
        self.seen: OrderedDict[str, int] = OrderedDict()     # trade identity -> event time (duplicates count once)
        self.max_seen = max_seen
        self.seen_floor: int | None = None         # newest event time whose identity has left `seen`
        self.curve: deque[Trade] = deque(maxlen=max_curve)
        self.curve_pool: str | None = None
        self.latest_curve: Trade | None = None
        self.peak_v = 0
        self.migration: MigrationTx | None = None
        self.pools: dict[str, _Pool] = {}          # PumpSwap trades per pool (at most MAX_POOLS pools)
        self.other_pool_trades = 0                 # trades on pools beyond MAX_POOLS
        self.verified_by: Trade | None = None
        self.status = ""
        self.confirmed = False                     # the lifecycle accepted a confirmation for the current pool
        self.flow: dict[str, _Flow] = {}
        self.flow_complete = True
        self.gaps: list[str] = []
        self.sells: list[Trade] = []               # largest sells, ranked at arrival, independent of trade caps
        self.verified_holders: dict | None = None  # GMGN balances
        self.net_tokens = self.curve_trades = 0    # curve trades seen since creation (for reserve continuity)
        self.receipt_creation_after_gap = False    # creation timed only by JINX's receipt, after a gap
        self.detection: tuple[int, int] | None = None   # (observed_ns, processing_ns) of the first report


class MigrationTracker:
    def __init__(self, notify: Callable[[MigrationEvidence], Any] | None = None,
                 request_pool: Callable[[str], Any] | None = None,
                 request_holders: Callable[[str], Any] | None = None,
                 confirmation_delay_ms: int | None = None, max_mints: int = MAX_MINTS,
                 idle_ttl_s: float = IDLE_TTL_S, max_curve_trades: int = MAX_CURVE_TRADES,
                 max_pool_trades: int = MAX_POOL_TRADES, max_wallets: int = MAX_WALLETS) -> None:
        self.notify, self.request_pool, self.request_holders = notify, request_pool, request_holders
        self.confirmation_delay_ms = confirmation_delay_ms
        self.max_curve, self.max_pool, self.max_wallets = max_curve_trades, max_pool_trades, max_wallets
        if confirmation_delay_ms is not None:      # the paper reference needs trades past the delay (round 4, R4-2)
            self.max_pool = max(max_pool_trades, M3_RETENTION)
        max_seen = 2 * (max_curve_trades + max_pool_trades)
        self._tokens: MintTable[_Token] = MintTable(lambda mint: _Token(mint, self.max_curve, max_seen), max_mints,
                                                    int(idle_ttl_s * 1_000_000_000))
        self.stats = {"events": 0, "migrations": 0, "reports": 0, "enriched": 0, "confirmed": 0, "revoked": 0,
                      "pool_conflicts": 0, "duplicates": 0, "beyond_dedup_horizon": 0, "reports_dropped": 0,
                      "wallets_untracked": 0,
                      "lifecycle_refused": 0, "listener_errors": 0, "provider_reports_untracked": 0}
        self._gap: tuple[int, str] | None = None   # (receipt time of the latest trade-feed gap, reason)

    # ---- API -----------------------------------------------------------------------------------------
    def on_event(self, ev: MarketEvent) -> list[MigrationEvidence]:
        started = time.perf_counter_ns()
        self.stats["events"] += 1
        tok = self._tokens.touch(ev.mint, ev.recv_ns)
        if self._gap is not None and min(ev.recv_ns, event_time(ev)[0]) < self._gap[0]:
            self._incomplete(tok, self._gap[1])     # received or happened before the gap: the flow spans it
        if ev.kind is EventKind.TOKEN_CREATED:
            tok.creator, tok.since_creation = ev.trader, True
            tok.curve_pool = tok.curve_pool or pool_id(ev)
            if event_time(ev)[1] is Clock.RECEIPT and self._gap is not None and ev.recv_ns >= self._gap[0]:
                tok.receipt_creation_after_gap = True    # complete only if the reserves prove it (R4-1)
            self._observe(tok, Kind.CREATED, ev, {"pre_pool": pool_id(ev)})
            return []
        if ev.kind is EventKind.MIGRATION:
            t, clock = event_time(ev)
            report = MigrationReport(ev.source, ev.signature, ev.slot, t, clock, ev.recv_ns, pool_id(ev), venue(ev))
            return self._report(tok, report, started)
        if ev.kind is not EventKind.TRADE:
            return []
        trade = self._trade(ev)
        if ev.idempotency_key in tok.seen:
            self.stats["duplicates"] += 1
            return []
        if tok.seen_floor is not None and trade.event_ns <= tok.seen_floor:
            # older than the duplicate-detection window: it may be a resend, so it is not counted, and the token's
            # flow is marked incomplete instead of possibly counting a trade twice
            self.stats["beyond_dedup_horizon"] += 1
            self._incomplete(tok, "a trade older than the duplicate-detection window arrived and was not counted")
            return []
        tok.seen[ev.idempotency_key] = trade.event_ns
        while len(tok.seen) > tok.max_seen:
            _, old_ns = tok.seen.popitem(last=False)
            tok.seen_floor = max(tok.seen_floor or old_ns, old_ns)
        if is_curve(ev):
            self._curve_trade(tok, trade, ev)
            return []
        if venue(ev) == PUMPSWAP_VENUE:
            return self._pool_trade(tok, trade)
        return []

    def record_report(self, mint: str, source: str, event_ns: int, clock: Clock, observed_ns: int,
                      pool: str | None = None, venue_name: str | None = None, signature: str | None = None,
                      slot: int | None = None) -> list[MigrationEvidence]:
        """A migration report that does not arrive as a stream event (GMGN Trenches `completed`, GMGN token info
        `migrated_pool`). Taken only for tokens JINX already tracks, so a provider listing never creates state."""
        started = time.perf_counter_ns()
        tok = self._tokens.get(mint)
        if tok is None:
            self.stats["provider_reports_untracked"] += 1
            return []
        self._tokens.touch(mint, observed_ns)
        report = MigrationReport(source, signature, slot, event_ns, clock, observed_ns, pool or None, venue_name)
        return self._report(tok, report, started)

    def record_provider_status(self, mint: str, category: str, source: str, observed_ns: int,
                               progress: float | None = None) -> None:
        """GMGN Trenches category for a tracked token. `near_completion` is the provider's own statement that the
        curve is approaching graduation: lifecycle APPROACHING GRADUATION, on GMGN's read time (contract §5). JINX
        sets no number of its own; a token missing from a listing is not evidence."""
        tok = self._tokens.get(mint)
        if tok is None or category != "near_completion":
            return
        self._life(tok, Kind.NEAR_COMPLETION, source, observed_ns, observed_ns, Clock.RECEIPT,
                   {"category": category, "progress": _fraction(progress)})

    def record_holders(self, mint: str, holders: Iterable[dict], source: str, observed_ns: int) -> None:
        """Verified balances (GMGN top holders): each holder's address, balance and share of supply as reported.
        Accounts that are the token's curve or migration pool are marked, not mixed with wallets."""
        tok = self._tokens.get(mint)
        if tok is None:
            return
        pools = {p for p in (tok.curve_pool, *(tok.migration.pools_named if tok.migration else ())) if p}
        rows = []
        for h in holders:
            if not isinstance(h, dict) or not isinstance(h.get("address"), str) or not h["address"]:
                continue
            share = _fraction(h.get("amount_percentage"))
            balance = h.get("balance")
            rows.append({"address": h["address"], "share_of_supply": share,
                         "balance": balance if isinstance(balance, (int, float)) and not isinstance(balance, bool)
                         else None,
                         "pool_account": h["address"] in pools or bool(h.get("exchange")),
                         "provider_tag": h.get("wallet_tag_v2")})
            if len(rows) >= MAX_HOLDERS_KEPT:
                break
        wallets = [r for r in rows if not r["pool_account"]]
        shares = [r["share_of_supply"] for r in wallets[:TOP_HOLDERS]]
        tok.verified_holders = {
            "source": source, "observed_ns": observed_ns,
            "relative_to_migration": (None if tok.migration is None
                                      else "after" if observed_ns >= tok.migration.observed_ns else "before"),
            "top_wallets": wallets[:TOP_HOLDERS],
            "top10_wallet_share": sum(shares) if shares and None not in shares else None,
            "pool_accounts": [r for r in rows if r["pool_account"]], "holders_listed": len(rows)}

    def mark_feed_gap(self, reason: str, gap_ns: int | None = None) -> None:
        """The trade feed reconnected: trades may have been missed for every token tracked so far, so their observed
        trade flow is no longer complete. With `gap_ns`, an event received before it but processed later marks its
        token too."""
        if gap_ns is not None and (self._gap is None or gap_ns >= self._gap[0]):
            self._gap = (gap_ns, reason)
        for _, tok in self._tokens.items():
            self._incomplete(tok, reason)

    @staticmethod
    def _incomplete(tok: _Token, reason: str) -> None:
        tok.flow_complete = False
        if reason not in tok.gaps:
            tok.gaps = (tok.gaps + [reason])[-4:]

    def evidence(self, mint: str) -> MigrationEvidence | None:
        tok = self._tokens.get(mint)
        return self._build(mint, tok) if tok is not None and tok.migration is not None else None

    def summary(self, mint: str) -> dict | None:
        """A cheap view of a token's migration for shared state (no full evidence build)."""
        tok = self._tokens.get(mint)
        if tok is None or tok.migration is None:
            return None
        m = tok.migration
        return {"state": tok.life.state.name, "verified": tok.verified_by is not None, "status": tok.status,
                "pool": m.pool_id, "pools_named": list(m.pools_named), "sources": list(m.sources),
                "event_ns": m.event_ns, "clock": str(m.clock), "observed_ns": m.observed_ns}

    def lifecycle(self, mint: str) -> TokenLifecycle | None:
        tok = self._tokens.get(mint)
        return tok.life if tok is not None else None

    def tracks(self, mint: str) -> bool:
        return self._tokens.get(mint) is not None

    def tracked_mints(self) -> int:
        return len(self._tokens)

    def verified_mints(self) -> int:
        return sum(1 for _, tok in self._tokens.items() if tok.verified_by is not None)

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
              payload: dict) -> bool:
        try:
            tok.life.observe(LifeEvidence(kind, source, observed_ns, event_ns, _life_clock(clock),
                                          {k: v for k, v in payload.items() if v is not None}))
            return True
        except MissingEvidence:
            self.stats["lifecycle_refused"] += 1
            return False

    def _curve_trade(self, tok: _Token, trade: Trade, ev: MarketEvent) -> None:
        tok.net_tokens += trade.token_amount if trade.side is Side.BUY else -trade.token_amount
        tok.curve_trades += 1
        tok.curve.append(trade)
        tok.curve_pool = tok.curve_pool or trade.pool_id
        tok.peak_v = max(tok.peak_v, trade.v_sol_lamports)
        if tok.latest_curve is None or trade.key > tok.latest_curve.key:
            tok.latest_curve = trade
        self._flow(tok, trade)
        self._observe(tok, Kind.ON_CURVE, ev, {"v_sol_lamports": ev.v_sol_lamports, "pre_pool": trade.pool_id,
                                               "liquidity": trade.quote_reserve_lamports})

    def _after_migration(self, tok: _Token, trade: Trade) -> bool:
        """A PumpSwap trade comes after the migration; a curve trade does only when its slot is later (which a
        consistent feed never shows)."""
        if trade.venue == PUMPSWAP_VENUE:
            return True
        m = tok.migration
        return m is not None and trade.slot is not None and m.slot is not None and trade.slot > m.slot

    def _flow(self, tok: _Token, trade: Trade) -> None:
        after = self._after_migration(tok, trade)
        if trade.side is Side.SELL:
            tok.sells.append(trade)
            tok.sells.sort(key=lambda t: (-t.sol_lamports, t.key))
            del tok.sells[2 * TOP_SELLS:]
        f = tok.flow.get(trade.fee_payer)
        if f is None:
            if len(tok.flow) >= self.max_wallets:
                tok.flow_complete = False
                self.stats["wallets_untracked"] += 1
                return
            f = tok.flow[trade.fee_payer] = _Flow()
        if trade.side is Side.BUY:
            if after:
                f.bought_post += trade.token_amount
            else:
                f.bought_pre += trade.token_amount
            return
        bucket = f.post if after else f.pre
        bucket[0] += 1
        bucket[1] += trade.sol_lamports
        bucket[2] += trade.token_amount

    def _report(self, tok: _Token, report: MigrationReport, started: int) -> list[MigrationEvidence]:
        old = tok.migration
        same = (lambda r: (r.source, r.signature, r.slot, r.event_ns, r.clock, r.pool_id, r.venue))
        if old is not None and any(same(r) == same(report) for r in old.reports):
            return []          # the same report again: no effect at all (a better report from a source is kept)
        reports = list(old.reports) if old else []
        seen = (old.reports_seen if old else 0) + 1
        if len(reports) >= MAX_REPORTS and not self._make_room(reports):
            self.stats["reports_dropped"] += 1       # every kept report is informative; the record already conflicts
            tok.migration = MigrationTx(old.reports, seen)
            return []
        self.stats["reports"] += 1
        tok.migration = MigrationTx(tuple(reports) + (report,), seen)
        self._life(tok, Kind.MIGRATION_SIGNAL, report.source, report.observed_ns, report.event_ns, report.clock,
                   {"signature": report.signature, "exchange": report.venue, "announced_pool": report.pool_id})
        out = []
        if old is None:
            self.stats["migrations"] += 1
            tok.detection = (report.observed_ns, time.perf_counter_ns() - started)
            self._call(self.request_holders, tok.life.mint)
            out.append(self._emit(tok))
        elif old.pool_id is None and not old.pools_named and tok.migration.pool_id is not None:
            self.stats["enriched"] += 1
            out.append(self._emit(tok))
        if len(tok.migration.pools_named) > len(old.pools_named if old else ()) > 0:
            self.stats["pool_conflicts"] += 1
            if tok.confirmed:     # contradicts a confirmed destination: the lifecycle's own conflict rule applies
                self._life(tok, Kind.MIGRATION_CONFIRMED, report.source, report.observed_ns, report.event_ns,
                           report.clock, {"post_pool": report.pool_id, "exchange": report.venue or PUMPSWAP_VENUE})
        out += self._reverify(tok)
        if not tok.migration.pools_named:
            self._call(self.request_pool, tok.life.mint)
        return out

    @staticmethod
    def _make_room(reports: list[MigrationReport]) -> bool:
        """At the report cap, drop the oldest report that adds nothing: neither the best-ranked one (the record's
        time) nor the first to name its pool. New facts (a pool, a contradiction, a better time) are never refused
        because corroborations filled the record (Codex round 3, R3-3)."""
        best = min(reports, key=lambda r: r.rank)
        first_seen = min(reports, key=lambda r: r.observed_ns)
        namers, per_source = {}, {}
        for r in reports:
            if r.pool_id and r.pool_id not in namers:
                namers[r.pool_id] = r
            per_source.setdefault(r.source, r)
        # never folded: the record's time, the first observation, the first namer of each pool, each source's first
        # report (provenance), so sources, timing and pools are unchanged by the cap (round 4, R4-3)
        keep = {id(best), id(first_seen), *(id(r) for r in namers.values()), *(id(r) for r in per_source.values())}
        victim = next((r for r in reports if id(r) not in keep), None)
        if victim is None:
            return False
        reports.remove(victim)
        return True

    def _research_reference(self, tok: _Token, m: MigrationTx) -> Trade | None:
        """M3: the earliest qualifying pool trade at or after migration + delay, derived from the current record and
        the kept trades every time (round 4, R4-2); with M3 on, M3_RETENTION trades per pool are kept."""
        due = m.event_ns + self.confirmation_delay_ms * 1_000_000
        pool = tok.pools.get(m.pool_id) if m.pool_id else None
        return next((t for t in pool.full if t.event_ns >= due and self._ordered(t, m)), None) if pool else None

    def _pool_trade(self, tok: _Token, trade: Trade) -> list[MigrationEvidence]:
        self._flow(tok, trade)                                   # a PumpSwap trade is after the migration
        pool = tok.pools.get(trade.pool_id)
        if pool is None and trade.pool_id is not None and len(tok.pools) < MAX_POOLS:
            pool = tok.pools[trade.pool_id] = _Pool(self.max_pool)
        if pool is None:
            tok.other_pool_trades += 1
        else:
            pool.add(trade)
        if tok.migration is None:
            return []
        out = self._reverify(tok)                                # a new verification posts its trades itself
        if not out and tok.verified_by is not None and tok.confirmed and trade.pool_id == tok.migration.pool_id \
                and self._qualifies(trade, tok.migration):
            self._life(tok, Kind.POST_MIGRATION_MARKET, PUMPAPI, trade.recv_ns, trade.event_ns, trade.clock,
                       {"pool": trade.pool_id, "liquidity": trade.quote_reserve_lamports, "signature": trade.signature})
        return out

    # ---- verification (derived from the current record and the trades seen) -------------------------
    @staticmethod
    def _ordered(trade: Trade, m: MigrationTx) -> bool:
        if trade.slot is not None and m.slot is not None:
            return trade.slot >= m.slot
        if trade.clock is not Clock.RECEIPT and m.clock is not Clock.RECEIPT:
            return trade.event_ns >= m.event_ns
        return False                                             # no comparable order: not provably after

    def _qualifies(self, trade: Trade, m: MigrationTx) -> bool:
        return (trade.quote_reserve_lamports is not None and trade.token_reserve is not None
                and self._ordered(trade, m))

    def _verify(self, tok: _Token) -> tuple[Trade | None, str]:
        m = tok.migration
        pools = m.pools_named
        if len(pools) > 1:
            return None, f"conflicting pools named: {', '.join(pools)} (no pool chosen; nothing verified)"
        if not pools:
            return None, ("waiting for the pool: no report has named it yet (PumpPortal and GMGN Trenches name "
                          "only the venue); GMGN's migrated_pool is requested")
        if m.clock is Clock.RECEIPT:
            return None, "the migration time is only JINX's receipt time, not on a provider clock: not verifiable"
        pool = tok.pools.get(pools[0])
        valid = next((t for t in pool.full if self._ordered(t, m)), None) if pool else None
        if valid is not None:
            return valid, "verified: first trade with reserves on the pool after the migration"
        if pool is not None and any(self._ordered(t, m) for t in pool.all):
            return None, "waiting: trades on the pool carry no reserves"
        return None, "waiting: no trade on the pool provably at or after the migration"

    def _reverify(self, tok: _Token) -> list[MigrationEvidence]:
        before = tok.verified_by
        tok.verified_by, tok.status = self._verify(tok)
        m, now = tok.migration, tok.verified_by
        if now is not None and (before is None or not tok.confirmed):
            # confirmed on the migration's own time, verified by the first qualifying trade on its pool
            tok.confirmed = self._life(tok, Kind.MIGRATION_CONFIRMED, m.source, now.recv_ns, m.event_ns, m.clock,
                                       {"post_pool": m.pool_id, "exchange": m.venue or now.venue,
                                        "migration_signature": m.signature, "verified_by": now.signature})
            if not tok.confirmed:
                tok.verified_by, tok.status = None, "the lifecycle refused the confirmation"
                return [] if before is None else [self._emit(tok)]
            for t in (t for t in tok.pools[m.pool_id].full if self._ordered(t, m)):
                self._life(tok, Kind.POST_MIGRATION_MARKET, PUMPAPI, t.recv_ns, t.event_ns, t.clock,
                           {"pool": t.pool_id, "liquidity": t.quote_reserve_lamports, "signature": t.signature})
            if before is None:
                self.stats["confirmed"] += 1
                return [self._emit(tok)]
            return []
        if now is None and before is not None:
            self.stats["revoked"] += 1
            if tok.confirmed and tok.life.state is not State.CONFLICTING:   # a conflict already says it
                tok.life.record_failure("migration", time.time_ns(), f"verification revoked: {tok.status}")
            tok.confirmed = False
            return [self._emit(tok)]
        return []

    def _call(self, fn, *args) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            self.stats["listener_errors"] += 1
            log.exception("migration listener failed")

    def _emit(self, tok: _Token) -> MigrationEvidence:
        evidence = self._build(tok.life.mint, tok)
        self._call(self.notify, evidence)
        return evidence

    # ---- evidence ------------------------------------------------------------------------------------
    @staticmethod
    def _observed_holders(tok: _Token, at_migration: bool) -> dict:
        nets = ((w, f.net_pre if at_migration else f.net) for w, f in tok.flow.items())
        positive = sorted(((w, n) for w, n in nets if n > 0), key=lambda x: (-x[1], x[0]))
        total = sum(n for _, n in positive)
        top = positive[:TOP_HOLDERS]
        return {"holders": len(positive), "observed_net_tokens": total,
                "top10_share": (sum(n for _, n in top) / total) if total else None,
                "top": [w for w, _ in top]}

    @staticmethod
    def _liquidity(trade: Trade | None, m: MigrationTx) -> dict | None:
        if trade is None:
            return None
        slots = trade.slot - m.slot if trade.slot is not None and m.slot is not None else None
        # the reserves are those after this trade, so the trade itself belongs to the record
        return {"signature": trade.signature, "slot": trade.slot, "event_ns": trade.event_ns,
                "clock": str(trade.clock), "pool_id": trade.pool_id, "side": str(trade.side),
                "sol_lamports": trade.sol_lamports, "fee_payer": trade.fee_payer,
                "quote_reserve_lamports": trade.quote_reserve_lamports, "token_reserve": trade.token_reserve,
                "slots_after_migration": slots, "ms_after_migration": (trade.event_ns - m.event_ns) / 1e6}

    def _sell_row(self, trade: Trade, tok: _Token) -> dict:
        """Phase and creator flag are read now, so they follow facts that arrived after the sell."""
        after = self._after_migration(tok, trade)
        return {"signature": trade.signature, "fee_payer": trade.fee_payer, "venue": trade.venue,
                "pool_id": trade.pool_id, "phase": "after" if after else "before", "slot": trade.slot,
                "event_ns": trade.event_ns, "sol_lamports": trade.sol_lamports, "tokens": trade.token_amount,
                "quote_reserve_after_lamports": trade.quote_reserve_lamports,
                "seller_is_creator": trade.fee_payer == tok.creator if tok.creator else None}

    def _build(self, mint: str, tok: _Token) -> MigrationEvidence:
        m = tok.migration
        pre = tok.latest_curve
        pre_liq = None if pre is None else {
            "signature": pre.signature, "slot": pre.slot, "event_ns": pre.event_ns,
            "virtual_sol": _sol(pre.v_sol_lamports), "real_sol": _sol(pre.quote_reserve_lamports),
            "real_tokens": pre.token_reserve, "standard_curve": standard_curve(pre.v_sol_lamports,
                                                                               pre.quote_reserve_lamports),
            "peak_virtual_sol": _sol(tok.peak_v)}
        pool = tok.pools.get(m.pool_id) if m.pool_id else None
        on_pool = [t for t in pool.all if self._ordered(t, m)] if pool else []
        research = None
        if self.confirmation_delay_ms is not None:
            due = m.event_ns + self.confirmation_delay_ms * 1_000_000
            ref = self._research_reference(tok, m)
            research = {"variant": "M3 confirmation delay", "live_policy": False,
                        "delay_ms": self.confirmation_delay_ms, "due_event_ns": due,
                        "reference_trade": None if ref is None else {**self._liquidity(ref, m),
                                                                     "fee_payer": ref.fee_payer}}
        latest = pool.latest if pool else None
        named = set(m.pools_named)
        observed_ns, processing_ns = tok.detection
        return MigrationEvidence(
            mint=mint, state=tok.life.state.name, migration=m, verified=tok.verified_by is not None,
            verification_status=tok.status, first_verified_liquidity=self._liquidity(tok.verified_by, m),
            latest_post_liquidity=self._liquidity(latest, m), pre_migration_liquidity=pre_liq,
            sells_around_graduation=[self._sell_row(t, tok) for t in tok.sells[:TOP_SELLS]],
            developer_selling=self._developer(tok), holder_selling=self._holder_selling(tok),
            holder_distribution=self._holder_distribution(tok),
            competing_early_transactions=self._competing(on_pool, m),
            foreign_pool_trades=(sum(p.count for pid, p in tok.pools.items() if pid not in named)
                                 + tok.other_pool_trades) if named else 0,
            unverified_pool_trades=(sum(p.count for p in tok.pools.values()) + tok.other_pool_trades)
            if not named else 0, research=research,
            observed_ns=observed_ns, processing_ns=processing_ns)

    def _complete(self, tok: _Token) -> bool:
        if not (tok.since_creation and tok.flow_complete):
            return False
        if tok.receipt_creation_after_gap:     # proven only by the curve's own reserve accounting (round 4, R4-1)
            c = tok.latest_curve
            return bool(c) and reserves_continuous(tok.net_tokens, tok.curve_trades, c.v_sol_lamports,
                                                    c.quote_reserve_lamports, c.token_reserve) is True
        return True

    def _developer(self, tok: _Token) -> dict:
        if not tok.since_creation or tok.creator is None:
            return {"status": "UNAVAILABLE", "reason": "the creation was not observed", "creator": tok.creator}
        f = tok.flow.get(tok.creator)
        pre, post = (f.pre, f.post) if f is not None else ([0, 0, 0], [0, 0, 0])
        as_dict = (lambda b: {"sells": b[0], "sol_lamports": b[1], "tokens": b[2]})
        return {"status": "observed", "creator": tok.creator,
                "bought_tokens": (f.bought_pre + f.bought_post) if f else 0, "complete": self._complete(tok),
                "before": as_dict(pre), "after": as_dict(post)}

    def _sells_of(self, tok: _Token, wallet: str) -> dict:
        f = tok.flow.get(wallet)
        pre, post = (f.pre, f.post) if f is not None else ([0, 0, 0], [0, 0, 0])
        return {"before": {"sells": pre[0], "sol_lamports": pre[1], "tokens": pre[2]},
                "after": {"sells": post[0], "sol_lamports": post[1], "tokens": post[2]}}

    def _holder_selling(self, tok: _Token) -> dict:
        verified = tok.verified_holders
        if verified is None:
            v_part = {"status": "UNAVAILABLE", "reason": "no verified balances received for this token"}
        else:
            v_part = {"status": "verified balances", "source": verified["source"],
                      "observed_ns": verified["observed_ns"],
                      "holders": [{"address": h["address"], "share_of_supply": h["share_of_supply"],
                                   **self._sells_of(tok, h["address"])} for h in verified["top_wallets"]]}
        observed = [{"fee_payer": w, "net_tokens_now": tok.flow[w].net, **self._sells_of(tok, w)}
                    for w in self._observed_holders(tok, True)["top"]]
        return {"verified": v_part,
                "observed_trade_flow": {"basis": OBSERVED_FLOW_BASIS, "complete": self._complete(tok),
                                        "feed_gaps": list(tok.gaps), "top_at_migration": observed}}

    def _holder_distribution(self, tok: _Token) -> dict:
        verified = tok.verified_holders or {"status": "UNAVAILABLE",
                                            "reason": "no verified balances received for this token"}
        return {"verified": verified,
                "observed_trade_flow": {"basis": OBSERVED_FLOW_BASIS, "complete": self._complete(tok),
                                        "feed_gaps": list(tok.gaps),
                                        "at_migration": self._observed_holders(tok, True),
                                        "latest": self._observed_holders(tok, False)}}

    @staticmethod
    def _competing(on_pool: list[Trade], m: MigrationTx) -> list:
        rows = []
        for t in on_pool[:COMPETING_LISTED]:
            rows.append({"signature": t.signature, "fee_payer": t.fee_payer, "side": str(t.side),
                         "sol_lamports": t.sol_lamports, "slot": t.slot,
                         "slots_after_migration": t.slot - m.slot if t.slot is not None and m.slot is not None else None,
                         "ms_after_migration": (t.event_ns - m.event_ns) / 1e6})
        return rows
