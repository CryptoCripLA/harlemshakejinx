"""Token and wallet registries: what JINX knows about each mint and each trader.

Both are derived from the event stream and written through to the store, so
they reload after a restart. Arrival order does not change the final state:
current reserves always come from the newest event by order key, the reserve
peak is a max, and counts are sums. A late event still counts but never
overwrites newer reserves.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace

from jinx.core.events import EventKind, MarketEvent, Side
from jinx.store import Store


@dataclass
class TokenState:
    mint: str
    creator: str | None = None
    buys: int = 0
    sells: int = 0
    unique_traders: int = 0
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


_TOKEN_COLS = [f for f in TokenState.__dataclass_fields__]


class TokenRegistry:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.late_events = 0
        self.reload()

    def reload(self) -> None:
        """Rebuild the in-memory view from committed state (after a restart or a rolled-back apply)."""
        self._tokens = {row["mint"]: TokenState(**dict(row))
                        for row in self.store.conn.execute(f"SELECT {', '.join(_TOKEN_COLS)} FROM tokens")}

    def get(self, mint: str) -> TokenState | None:
        return self._tokens.get(mint)

    def all(self) -> list[TokenState]:
        return list(self._tokens.values())

    def __len__(self) -> int:
        return len(self._tokens)

    def apply(self, ev: MarketEvent) -> None:
        if ev.kind not in (EventKind.TOKEN_CREATED, EventKind.TRADE):
            return   # e.g. MIGRATION: kept in the event store for the Phase 2 lifecycle; not a token statistic
        known = self._tokens.get(ev.mint)
        # work on a copy so a rolled-back transaction leaves the cache untouched
        t = replace(known) if known else TokenState(mint=ev.mint)
        with self.store.transaction() as c:
            if ev.kind is EventKind.TOKEN_CREATED:
                t.creator = ev.trader
            elif ev.kind is EventKind.TRADE:
                if ev.side is Side.BUY:
                    t.buys += 1
                else:
                    t.sells += 1
                new_trader = c.execute("INSERT OR IGNORE INTO token_traders (mint, trader) VALUES (?, ?)",
                                       (ev.mint, ev.trader)).rowcount == 1
                t.unique_traders += int(new_trader)
            if t.first_key is None or ev.order_key < t.first_key:
                t.first_time_ns, t.first_seq = ev.order_key
            if ev.v_sol_lamports is not None:
                t.peak_v_sol_lamports = max(t.peak_v_sol_lamports or 0, ev.v_sol_lamports)
                if t.latest_key is None or ev.order_key > t.latest_key:
                    t.v_sol_lamports, t.v_tokens = ev.v_sol_lamports, ev.v_tokens
                    t.latest_time_ns, t.latest_seq = ev.order_key
                else:
                    self.late_events += 1
            values = [getattr(t, f) for f in _TOKEN_COLS]
            c.execute(f"INSERT OR REPLACE INTO tokens ({', '.join(_TOKEN_COLS)}) VALUES"
                      f" ({', '.join('?' * len(_TOKEN_COLS))})", values)
        self._tokens[ev.mint] = t

    def digest(self) -> str:
        """Order-independent fingerprint of all token state (used by replay tests)."""
        blob = json.dumps([asdict(self._tokens[m]) for m in sorted(self._tokens)], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class WalletState:
    address: str
    buys: int = 0
    sells: int = 0
    buy_lamports: int = 0
    sell_lamports: int = 0
    first_time_ns: int | None = None
    last_time_ns: int | None = None


_WALLET_COLS = [f for f in WalletState.__dataclass_fields__]


class WalletRegistry:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.reload()

    def reload(self) -> None:
        self._wallets = {row["address"]: WalletState(**dict(row))
                         for row in self.store.conn.execute(f"SELECT {', '.join(_WALLET_COLS)} FROM wallets")}

    def get(self, address: str) -> WalletState | None:
        return self._wallets.get(address)

    def all(self) -> list[WalletState]:
        return list(self._wallets.values())

    def __len__(self) -> int:
        return len(self._wallets)

    def apply(self, ev: MarketEvent) -> None:
        if ev.kind is not EventKind.TRADE:
            return
        known = self._wallets.get(ev.trader)
        w = replace(known) if known else WalletState(address=ev.trader)
        if ev.side is Side.BUY:
            w.buys += 1
            w.buy_lamports += ev.sol_lamports
        else:
            w.sells += 1
            w.sell_lamports += ev.sol_lamports
        t = ev.event_time_ns
        w.first_time_ns = t if w.first_time_ns is None else min(w.first_time_ns, t)
        w.last_time_ns = t if w.last_time_ns is None else max(w.last_time_ns, t)
        with self.store.transaction() as c:
            c.execute(f"INSERT OR REPLACE INTO wallets ({', '.join(_WALLET_COLS)}) VALUES"
                      f" ({', '.join('?' * len(_WALLET_COLS))})", [getattr(w, f) for f in _WALLET_COLS])
        self._wallets[ev.trader] = w
