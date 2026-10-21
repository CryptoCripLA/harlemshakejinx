"""Bundle evidence (owner completeness requirements 2026-10-21, R1: "bundle and common-funder evidence").

A launch bundle is visible on chain as buys that land in the same slot as the token's creation. PumpAPI delivers
creations and trades with their slot (`block`), so this module records, per created token, the buys in the creation
slot: how many, how many distinct fee payers, how much SOL, and whether the creator bought in that slot. Facts only;
no rejection threshold is set here (CHAD sets none). Evidence becomes `final` once a later slot is seen for the token.

State is RAM-only and bounded (owner storage rule 2): the newest `max_tokens` creations, and at most `max_pending`
buys whose creation has not been seen yet (a buy can arrive before its create).
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from jinx.core.events import EventKind, MarketEvent, Side


@dataclass
class BundleEvidence:
    mint: str
    creator: str | None
    create_slot: int
    create_signature: str
    same_slot_buys: int = 0
    same_slot_buy_lamports: int = 0
    buyers: set = field(default_factory=set)
    creator_bought_same_slot: bool = False
    final: bool = False
    _seen: set = field(default_factory=set, repr=False)

    @property
    def distinct_buyers(self) -> int:
        return len(self.buyers)

    def add_buy(self, ev: MarketEvent) -> None:
        key = (ev.signature, ev.raw.get("leg") if ev.raw else None)
        if key in self._seen:
            return
        self._seen.add(key)
        self.same_slot_buys += 1
        self.same_slot_buy_lamports += ev.sol_lamports or 0
        if ev.trader:
            self.buyers.add(ev.trader)
            if ev.trader == self.creator:
                self.creator_bought_same_slot = True


class BundleTracker:
    def __init__(self, max_tokens: int = 5_000, max_pending: int = 20_000) -> None:
        self.max_tokens, self.max_pending = max_tokens, max_pending
        self._tokens: OrderedDict[str, BundleEvidence] = OrderedDict()
        self._pending: OrderedDict[tuple[str, int], list[MarketEvent]] = OrderedDict()   # (mint, slot) -> buys

    def on_event(self, ev: MarketEvent) -> None:
        if ev.slot is None:
            return
        if ev.kind is EventKind.TOKEN_CREATED:
            b = BundleEvidence(ev.mint, ev.trader, ev.slot, ev.signature)
            self._tokens[ev.mint] = b
            self._tokens.move_to_end(ev.mint)
            for buy in self._pending.pop((ev.mint, ev.slot), []):
                b.add_buy(buy)
            while len(self._tokens) > self.max_tokens:
                self._tokens.popitem(last=False)
            return
        if ev.kind is not EventKind.TRADE:
            return
        b = self._tokens.get(ev.mint)
        if b is None:
            if ev.side is Side.BUY:
                self._pending.setdefault((ev.mint, ev.slot), []).append(ev)
                while len(self._pending) > self.max_pending:
                    self._pending.popitem(last=False)
            return
        if ev.slot > b.create_slot:
            b.final = True
        elif ev.slot == b.create_slot and ev.side is Side.BUY:
            b.add_buy(ev)

    def evidence(self, mint: str) -> BundleEvidence | None:
        return self._tokens.get(mint)
