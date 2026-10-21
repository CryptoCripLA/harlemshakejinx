"""Per-mint state with a hard cap and an idle time-out (owner storage rule 2: every cache bounded, TTL + cap).

Idle time is measured on the receipt clock of the events themselves (the highest receipt time seen), so a replay
evicts exactly as the live run did. The least recently active mint goes first.
"""
from __future__ import annotations

from array import array
from collections import OrderedDict
from typing import Callable, Generic, Iterator, TypeVar

T = TypeVar("T")


class MintTable(Generic[T]):
    """Least recently active mint first. A mint JINX holds is never evicted to meet the cap or the idle time-out: an
    open position's evidence outlives any market-cache limit. The held set comes from `held_provider` (the ledger's
    open and pending positions), asked at the moment something is about to be evicted, so a position opened since
    the last maintenance pass is protected too; `expire` also refreshes it."""

    def __init__(self, factory: Callable[[str], T], max_mints: int, idle_ttl_ns: int,
                 on_evict: Callable[[str, T], None] | None = None) -> None:
        self.factory, self.max_mints, self.idle_ttl_ns, self.on_evict = factory, max_mints, idle_ttl_ns, on_evict
        self._rows: OrderedDict[str, tuple[int, T]] = OrderedDict()
        self.held: frozenset[str] = frozenset()
        self.held_provider: Callable[[], frozenset] | None = None
        self.now_ns = 0
        self.evicted = 0

    def touch(self, mint: str, recv_ns: int) -> T:
        """The mint's state (created if new), marked active at `recv_ns`; evicts over the cap and idle mints."""
        self.now_ns = max(self.now_ns, recv_ns)
        row = self._rows.get(mint)
        value = row[1] if row is not None else self.factory(mint)
        self._rows[mint] = (self.now_ns, value)
        self._rows.move_to_end(mint)
        self._evict()
        return value

    def set(self, mint: str, value: T) -> None:
        """Replace a tracked mint's state, keeping its activity time (a new mint is marked active now)."""
        row = self._rows.get(mint)
        self._rows[mint] = (row[0] if row is not None else self.now_ns, value)

    def expire(self, now_ns: int, held: frozenset = frozenset(), idle_ns: int | None = None) -> int:
        """Market maintenance: refresh the held set and evict what went idle with no event (`idle_ns` overrides the
        idle time-out for this pass only). Returns the count."""
        self.held = frozenset(held)
        self.now_ns = max(self.now_ns, now_ns)
        before = self.evicted
        self._evict(idle_ns)
        return self.evicted - before

    def _evict(self, idle_ns: int | None = None) -> None:
        cutoff = self.now_ns - (self.idle_ttl_ns if idle_ns is None else min(idle_ns, self.idle_ttl_ns))
        skipped, asked = 0, False
        while self._rows and skipped < len(self._rows):
            mint, (seen, _) = next(iter(self._rows.items()))
            if len(self._rows) <= self.max_mints and seen >= cutoff:
                break
            if not asked and self.held_provider is not None:     # the current positions, before any eviction
                self.held, asked = self.held | frozenset(self.held_provider()), True
            if mint in self.held:
                self._rows.move_to_end(mint)
                skipped += 1
                continue
            self._drop(mint, self._rows.pop(mint))

    def _drop(self, mint: str, row: tuple[int, T]) -> None:
        self.evicted += 1
        if self.on_evict is not None:
            self.on_evict(mint, row[1])

    def discard(self, mint: str) -> None:
        row = self._rows.pop(mint, None)
        if row is not None:
            self._drop(mint, row)

    def get(self, mint: str) -> T | None:
        row = self._rows.get(mint)
        return row[1] if row is not None else None

    def __contains__(self, mint: str) -> bool:
        return mint in self._rows

    def items(self) -> Iterator[tuple[str, T]]:
        return ((m, row[1]) for m, row in self._rows.items())

    def __len__(self) -> int:
        return len(self._rows)


class RecentIds:
    """The newest `cap` identities (64-bit ints, e.g. hashed event keys), for duplicate detection in a bounded RAM
    budget. The ring is a typed array (8 bytes per identity, no int objects); most tokens see a handful of trades, so
    a short window is scanned linearly and a set is built only for windows longer than SMALL. With `timed`, each
    identity carries a time, and `add` returns the time of the identity that left the window."""
    SMALL = 64
    __slots__ = ("cap", "_set", "_ring", "_times")

    def __init__(self, cap: int, timed: bool = False) -> None:
        self.cap = cap
        self._set: set[int] | None = None
        self._ring = array("q")
        self._times = array("q") if timed else None

    def __contains__(self, ident: int) -> bool:
        return ident in self._set if self._set is not None else ident in self._ring

    def __len__(self) -> int:
        return len(self._ring)

    def widen(self, cap: int) -> None:
        """Keep up to `cap` identities from now on (never narrows)."""
        self.cap = max(self.cap, cap)

    def add(self, ident: int, t: int = 0) -> int | None:
        self._ring.append(ident)
        if self._times is not None:
            self._times.append(t)
        gone = gone_t = None
        if len(self._ring) > self.cap:
            gone = self._ring.pop(0)
            gone_t = self._times.pop(0) if self._times is not None else None
        if self._set is not None:
            self._set.add(ident)
            if gone is not None:
                self._set.discard(gone)
        elif len(self._ring) > self.SMALL:
            self._set = set(self._ring)
        return gone_t
