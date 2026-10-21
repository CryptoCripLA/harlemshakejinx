"""Per-mint state with a hard cap and an idle time-out (owner storage rule 2: every cache bounded, TTL + cap).

Idle time is measured on the receipt clock of the events themselves (the highest receipt time seen), so a replay
evicts exactly as the live run did. The least recently active mint goes first.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Generic, Iterator, TypeVar

T = TypeVar("T")


class MintTable(Generic[T]):
    def __init__(self, factory: Callable[[str], T], max_mints: int, idle_ttl_ns: int,
                 on_evict: Callable[[str, T], None] | None = None) -> None:
        self.factory, self.max_mints, self.idle_ttl_ns, self.on_evict = factory, max_mints, idle_ttl_ns, on_evict
        self._rows: OrderedDict[str, tuple[int, T]] = OrderedDict()
        self.now_ns = 0
        self.evicted = 0

    def touch(self, mint: str, recv_ns: int) -> T:
        """The mint's state (created if new), marked active at `recv_ns`; evicts over the cap and idle mints."""
        self.now_ns = max(self.now_ns, recv_ns)
        row = self._rows.get(mint)
        value = row[1] if row is not None else self.factory(mint)
        self._rows[mint] = (self.now_ns, value)
        self._rows.move_to_end(mint)
        while len(self._rows) > self.max_mints:
            self._drop(*self._rows.popitem(last=False))
        while self._rows:
            oldest, (seen, _) = next(iter(self._rows.items()))
            if self.now_ns - seen <= self.idle_ttl_ns:
                break
            self._drop(oldest, self._rows.pop(oldest))
        return value

    def _drop(self, mint: str, row: tuple[int, T]) -> None:
        self.evicted += 1
        if self.on_evict is not None:
            self.on_evict(mint, row[1])

    def get(self, mint: str) -> T | None:
        row = self._rows.get(mint)
        return row[1] if row is not None else None

    def items(self) -> Iterator[tuple[str, T]]:
        return ((m, row[1]) for m, row in self._rows.items())

    def __len__(self) -> int:
        return len(self._rows)
