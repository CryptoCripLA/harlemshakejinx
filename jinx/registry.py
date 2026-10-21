"""The one authoritative current state of each token (owner order 2026-10-21 §2: "Maintain one authoritative current
token state. ... Use compact counters and records instead of growing transaction lists").

Derived from the event stream, in RAM only: market chatter is transient (owner order 2026-10-21 §1), so nothing here
is written to disk and a restart starts the market view empty. Positions, fills and the paper wallet live in the
durable ledger store, not here. Arrival order does not change the state: current reserves come from the newest event
by order key, the reserve peak is a max, and counts are sums. A late event still counts but never overwrites newer
reserves.

Bounded (owner storage rule 2, order §3): a token with no event for `idle_ttl_s` (receipt clock) is evicted, and at
most `max_tokens` are kept, least recently active first. A token JINX holds is never evicted to meet either bound:
the held set is asked from `held_provider` (the ledger) right before any eviction, and refreshed by `sweep`. The per-wallet registry and the per-token trader sets of earlier builds are gone:
wallet history comes from GMGN when needed, not from a local copy of every trade.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import asdict, dataclass

from jinx.core.events import EventKind, MarketEvent, Side

NS = 1_000_000_000
DEFAULT_MAX_TOKENS = 12_000      # live 2026-10-21: ~7,200 tokens active within 2 h on Contabo
DEFAULT_IDLE_TTL_S = 1_800


@dataclass(slots=True)
class TokenState:
    mint: str
    creator: str | None = None
    buys: int = 0
    sells: int = 0
    v_sol_lamports: int | None = None
    v_tokens: int | None = None
    peak_v_sol_lamports: int | None = None
    latest_time_ns: int | None = None
    latest_seq: int | None = None
    first_time_ns: int | None = None
    first_seq: int | None = None

    @property
    def latest_key(self) -> tuple[int, int] | None:
        return None if self.latest_time_ns is None else (self.latest_time_ns, self.latest_seq)

    @property
    def first_key(self) -> tuple[int, int] | None:
        return None if self.first_time_ns is None else (self.first_time_ns, self.first_seq)


class TokenRegistry:
    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS, idle_ttl_s: float = DEFAULT_IDLE_TTL_S) -> None:
        if max_tokens < 1 or idle_ttl_s <= 0:
            raise ValueError("max_tokens must be >= 1 and idle_ttl_s > 0")
        self.max_tokens, self.idle_ttl_ns = max_tokens, int(idle_ttl_s * NS)
        self._tokens: OrderedDict[str, TokenState] = OrderedDict()   # least recently active first
        self._last: dict[str, int] = {}                                  # mint -> receipt time of its newest event
        self.held: frozenset[str] = frozenset()
        self.held_provider = None          # () -> held mints, asked right before an eviction (see MintTable)
        self.now_ns = 0
        self.late_events = 0
        self.evicted = 0

    def get(self, mint: str) -> TokenState | None:
        return self._tokens.get(mint)

    def all(self) -> list[TokenState]:
        return list(self._tokens.values())

    def __len__(self) -> int:
        return len(self._tokens)

    def apply(self, ev: MarketEvent) -> None:
        if ev.kind not in (EventKind.TOKEN_CREATED, EventKind.TRADE):
            return   # e.g. MIGRATION: the migration module owns the lifecycle; not a token statistic
        t = self._tokens.get(ev.mint)
        if t is None:
            t = self._tokens[ev.mint] = TokenState(mint=ev.mint)
        else:
            self._tokens.move_to_end(ev.mint)
        self.now_ns = max(self.now_ns, ev.recv_ns)
        self._last[ev.mint] = max(self._last.get(ev.mint, 0), ev.recv_ns)
        if ev.kind is EventKind.TOKEN_CREATED:
            t.creator = ev.trader
        elif ev.side is Side.BUY:
            t.buys += 1
        else:
            t.sells += 1
        if t.first_key is None or ev.order_key < t.first_key:
            t.first_time_ns, t.first_seq = ev.order_key
        if ev.v_sol_lamports is not None:
            t.peak_v_sol_lamports = max(t.peak_v_sol_lamports or 0, ev.v_sol_lamports)
            if t.latest_key is None or ev.order_key > t.latest_key:
                t.v_sol_lamports, t.v_tokens = ev.v_sol_lamports, ev.v_tokens
                t.latest_time_ns, t.latest_seq = ev.order_key
            else:
                self.late_events += 1
        self._evict()

    def sweep(self, now_ns: int, held: set[str] | frozenset = frozenset(), idle_ns: int | None = None) -> dict:
        """Market maintenance (once a minute): refresh the held set and evict idle tokens (`idle_ns` overrides the
        idle time-out for this pass only)."""
        self.held = frozenset(held)
        self.now_ns = max(self.now_ns, now_ns)
        before = self.evicted
        self._evict(idle_ns)
        return {"tokens": len(self._tokens), "evicted": self.evicted - before}

    def _evict(self, idle_ns: int | None = None) -> None:
        cutoff = self.now_ns - (self.idle_ttl_ns if idle_ns is None else min(idle_ns, self.idle_ttl_ns))
        skipped, asked = 0, False
        while self._tokens and skipped < len(self._tokens):
            mint = next(iter(self._tokens))
            if len(self._tokens) <= self.max_tokens and self._last.get(mint, 0) >= cutoff:
                break
            if not asked and self.held_provider is not None:     # the current positions, before any eviction
                self.held, asked = self.held | frozenset(self.held_provider()), True
            if mint in self.held:                   # never evicted to meet a market-cache bound
                self._tokens.move_to_end(mint)
                skipped += 1
                continue
            del self._tokens[mint]
            self._last.pop(mint, None)
            self.evicted += 1

    def digest(self) -> str:
        """Order-independent fingerprint of all token state (used by replay tests)."""
        blob = json.dumps([asdict(self._tokens[m]) for m in sorted(self._tokens)], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()
