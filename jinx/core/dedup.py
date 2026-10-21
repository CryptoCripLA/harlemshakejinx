"""Expiring, bounded duplicate filter for market events (owner order 2026-10-21, storage repair §1).

A key is remembered for `ttl_s` (120 s) from its first sighting and never more than `capacity` (16,000) keys at
once. Both values are the owner's initial test values, checked against the measured duplicate delivery: the same fact
from PumpAPI and PumpPortal arrives within seconds. `stats()` reports the delays of the duplicates refused and every
key that capacity forced out before its 120 s were up, so the bound can be adjusted on evidence. Time is the
receipt clock of the events themselves, never running backwards, so a replay expires keys exactly as the live run
did.

Keys are held as 64-bit hashes of the event identity, never as the identity strings. Over 16,000 keys, a hash
collision has a chance of about 1e-11.

This filter only stops repeated market chatter. It is not what prevents a duplicate order: order intents and fills
are idempotent in the durable trading ledger (jinx/ledger.py: unique entry intent, fill and exit-intent ids), which
never expires.
"""
from __future__ import annotations

from collections import OrderedDict

from jinx.core.latency import LatencyHistogram

NS = 1_000_000_000
DEFAULT_TTL_S = 120          # owner order 2026-10-21: "Initially test a 120-second expiry"
DEFAULT_CAPACITY = 16_000    # ... "and a 16,000-key capacity"


class Deduplicator:
    def __init__(self, ttl_s: float = DEFAULT_TTL_S, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1 or ttl_s <= 0:
            raise ValueError("capacity must be >= 1 and ttl_s > 0")
        self.capacity, self.ttl_ns = capacity, int(ttl_s * NS)
        self._keys: OrderedDict[int, int] = OrderedDict()     # key hash -> first-seen receipt time, oldest first
        self.now_ns = 0
        self.duplicates = self.expired = self.evicted_before_expiry = 0
        self._dup_delay = LatencyHistogram()
        self._youngest_early_ns: int | None = None

    def first_time(self, key: str, now_ns: int) -> bool:
        """True the first time `key` is seen inside the window (the event is new); False for a duplicate."""
        self.now_ns = max(self.now_ns, now_ns)
        self._expire()
        h = hash(key)
        seen = self._keys.get(h)
        if seen is not None:
            self.duplicates += 1
            self._dup_delay.record(self.now_ns - seen)
            return False
        self._keys[h] = self.now_ns
        while len(self._keys) > self.capacity:
            _, t = self._keys.popitem(last=False)
            self.evicted_before_expiry += 1
            age = self.now_ns - t
            self._youngest_early_ns = age if self._youngest_early_ns is None else min(self._youngest_early_ns, age)
        return True

    def _expire(self) -> None:
        cutoff = self.now_ns - self.ttl_ns
        keys = self._keys
        while keys:
            h, t = next(iter(keys.items()))
            if t >= cutoff:
                break
            del keys[h]
            self.expired += 1

    def forget(self, key: str) -> None:
        """Undo first_time() for a key whose event could not be handled, so a resend is accepted."""
        self._keys.pop(hash(key), None)

    def __len__(self) -> int:
        return len(self._keys)

    def stats(self) -> dict:
        return {"keys": len(self._keys), "capacity": self.capacity, "ttl_s": self.ttl_ns / NS,
                "duplicates": self.duplicates, "expired": self.expired,
                "evicted_before_expiry": self.evicted_before_expiry,
                "youngest_early_eviction_ms": (None if self._youngest_early_ns is None
                                               else self._youngest_early_ns / 1e6),
                "duplicate_delay_ms": self._dup_delay.summary()}
