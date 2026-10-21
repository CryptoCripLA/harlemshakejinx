"""Bundle evidence (owner completeness requirements 2026-10-21, R1: "bundle and common-funder evidence").

A launch bundle is visible on chain as buys that land in the same slot as the token's creation. PumpAPI delivers
creations and trades with their slot (`block`), so this module records, per created token, the buys in the creation
slot: how many, how many distinct fee payers, how much SOL, and whether the creator bought in that slot. Facts only;
no rejection threshold is set here (CHAD sets none). Evidence becomes `final` once a later slot is seen for the token.

RAM only and bounded (owner storage repair 2026-10-21: "Retain only bounded, short-lived event identifiers, wallet
identities, slot information and bundle counters needed to establish the same-block relationship. Evict expired
pending groups"):
- per created token, counters plus at most `max_buyers` distinct fee payers and hashed trade identities; the newest
  `max_tokens` creations, each evicted after `idle_ttl_s` without an event, except tokens JINX holds;
- a buy that arrives before its creation waits as a compact (identity hash, fee payer, lamports) entry, never as a
  MarketEvent, in a group keyed by (mint, slot). A group expires `pending_ttl_s` after it was opened (the two feeds
  deliver the same slot within seconds), and at most `max_pending` groups wait.
- when the bus had to drop one of a token's events (background lane full), the evidence says `complete = False`
  with the reason, so a partial count never passes for a clean one.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from jinx.core.events import EventKind, MarketEvent, Side
from jinx.intel.bounded import MintTable, RecentIds

NS = 1_000_000_000
MAX_TOKENS = 5_000
IDLE_TTL_S = 600
MAX_PENDING = 2_000
PENDING_TTL_S = 30
MAX_PENDING_BUYS = 64
MAX_BUYERS = 64
MAX_DROP_MARKS = 2_000
DROPPED = "the bus dropped an event of this token (background lane full)"


def _ident(ev: MarketEvent) -> int:
    return hash((ev.signature, ev.raw.get("leg") if ev.raw else None))


@dataclass(slots=True)
class BundleEvidence:
    mint: str
    creator: str | None
    create_slot: int
    create_signature: str
    same_slot_buys: int = 0
    same_slot_buy_lamports: int = 0
    buyers: set = field(default_factory=set)
    buyers_capped: bool = False
    creator_bought_same_slot: bool = False
    final: bool = False
    complete: bool = True
    incomplete_reason: str | None = None
    _seen: RecentIds = field(default_factory=lambda: RecentIds(MAX_PENDING_BUYS), repr=False)

    @property
    def distinct_buyers(self) -> int:
        """Distinct fee payers held; a lower bound when `buyers_capped`."""
        return len(self.buyers)

    def add_buy(self, ident: int, trader: str | None, lamports: int) -> None:
        if ident in self._seen:
            return
        self._seen.add(ident)
        self.same_slot_buys += 1
        self.same_slot_buy_lamports += lamports
        if trader:
            if trader == self.creator:
                self.creator_bought_same_slot = True
            if trader not in self.buyers:
                if len(self.buyers) < MAX_BUYERS:
                    self.buyers.add(trader)
                else:
                    self.buyers_capped = True

    def mark_incomplete(self, reason: str) -> None:
        self.complete, self.incomplete_reason = False, reason


class BundleTracker:
    def __init__(self, max_tokens: int = MAX_TOKENS, max_pending: int = MAX_PENDING, idle_ttl_s: float = IDLE_TTL_S,
                 pending_ttl_s: float = PENDING_TTL_S) -> None:
        self.max_tokens, self.max_pending, self.max_buyers = max_tokens, max_pending, MAX_BUYERS
        self.idle_ttl_ns, self.pending_ttl_ns = int(idle_ttl_s * NS), int(pending_ttl_s * NS)
        self._tokens: MintTable[BundleEvidence | None] = MintTable(lambda _m: None, max_tokens, self.idle_ttl_ns)
        # (mint, slot) -> (opened at, [(identity hash, fee payer, lamports)]), oldest first
        self._pending: OrderedDict[tuple[str, int], tuple[int, list[tuple[int, str | None, int]]]] = OrderedDict()
        self._dropped: OrderedDict[str, int] = OrderedDict()   # mints with a dropped event, before their creation
        self.now_ns = 0
        self.stats = {"pending_expired": 0, "pending_buys_capped": 0, "dropped_events": 0}

    def on_event(self, ev: MarketEvent) -> None:
        self.now_ns = max(self.now_ns, ev.recv_ns)
        self._expire_pending()
        if ev.slot is None:
            return
        if ev.kind is EventKind.TOKEN_CREATED:
            b = BundleEvidence(ev.mint, ev.trader, ev.slot, ev.signature)
            self._tokens.touch(ev.mint, ev.recv_ns)
            self._tokens.set(ev.mint, b)
            if self._dropped.pop(ev.mint, None) is not None:
                b.mark_incomplete(DROPPED)
            group = self._pending.pop((ev.mint, ev.slot), None)
            for ident, trader, lamports in (group[1] if group else ()):
                b.add_buy(ident, trader, lamports)
            return
        if ev.kind is not EventKind.TRADE:
            return
        b = self._tokens.get(ev.mint)
        if b is None:
            if ev.side is Side.BUY:
                key = (ev.mint, ev.slot)
                group = self._pending.get(key)
                if group is None:
                    group = self._pending[key] = (self.now_ns, [])
                    while len(self._pending) > self.max_pending:
                        self._pending.popitem(last=False)
                if len(group[1]) < MAX_PENDING_BUYS:
                    group[1].append((_ident(ev), ev.trader, ev.sol_lamports or 0))
                else:
                    self.stats["pending_buys_capped"] += 1
            return
        self._tokens.touch(ev.mint, ev.recv_ns)
        if ev.slot > b.create_slot:
            b.final = True
        elif ev.slot == b.create_slot and ev.side is Side.BUY:
            b.add_buy(_ident(ev), ev.trader, ev.sol_lamports or 0)

    def on_dropped(self, ev: MarketEvent) -> None:
        """The bus dropped this event before it reached the tracker: the token's evidence is incomplete."""
        self.stats["dropped_events"] += 1
        b = self._tokens.get(ev.mint)
        if b is not None:
            b.mark_incomplete(DROPPED)
            return
        self._dropped[ev.mint] = ev.recv_ns
        self._dropped.move_to_end(ev.mint)
        while len(self._dropped) > MAX_DROP_MARKS:
            self._dropped.popitem(last=False)

    def _expire_pending(self) -> None:
        cutoff = self.now_ns - self.pending_ttl_ns
        while self._pending:
            key, (opened, _) = next(iter(self._pending.items()))
            if opened >= cutoff:
                break
            del self._pending[key]
            self.stats["pending_expired"] += 1
        cutoff = self.now_ns - self.idle_ttl_ns
        while self._dropped and next(iter(self._dropped.values())) < cutoff:
            self._dropped.popitem(last=False)

    def set_held_provider(self, provider) -> None:
        """`provider()` returns the mints JINX holds; they are never evicted (asked right before any eviction)."""
        self._tokens.held_provider = provider

    def sweep(self, now_ns: int, held: frozenset = frozenset(), idle_ns: int | None = None) -> dict:
        self.now_ns = max(self.now_ns, now_ns)
        self._expire_pending()
        evicted = self._tokens.expire(self.now_ns, held, idle_ns)
        return {"tokens": len(self._tokens), "pending_groups": len(self._pending), "evicted": evicted}

    def evidence(self, mint: str) -> BundleEvidence | None:
        return self._tokens.get(mint)

    def tracked(self) -> int:
        return len(self._tokens)
