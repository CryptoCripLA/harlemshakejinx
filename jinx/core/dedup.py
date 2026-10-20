"""Bounded in-memory duplicate filter.

This is the fast first line. The durable unique index on the events table
(jinx/store.py) is the backstop for keys evicted from memory or seen before a
restart.
"""
from __future__ import annotations

from collections import OrderedDict


class Deduplicator:
    def __init__(self, capacity: int = 200_000) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._keys: OrderedDict[str, None] = OrderedDict()
        self.duplicates = 0

    def first_time(self, key: str) -> bool:
        if key in self._keys:
            self._keys.move_to_end(key)
            self.duplicates += 1
            return False
        self._keys[key] = None
        if len(self._keys) > self.capacity:
            self._keys.popitem(last=False)
        return True

    def forget(self, key: str) -> None:
        """Undo first_time() for a key whose event could not be stored, so a resend is accepted."""
        self._keys.pop(key, None)

    def __len__(self) -> int:
        return len(self._keys)
