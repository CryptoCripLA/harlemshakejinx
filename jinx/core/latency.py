"""Constant-memory latency distributions (owner order 2026-10-21: "Remove the two 100,000-sample latency arrays. Use
constant-memory rolling counters/histograms for p50, p95, p99 and queue diagnostics").

`LatencyHistogram` keeps counts in log-linear buckets: exact below 32 ns, then 16 buckets per power of two up to
2^42 ns (~73 min), so a percentile is off by at most 1/32 of its value (`MAX_RELATIVE_ERROR`). Count, sum, the exact
minimum and maximum are kept too. Memory is one fixed array (~5 KB) whatever the number of samples.

`RollingLatency` is the window JINX reports: `slots` histograms each covering `window_s / slots` seconds of the
monotonic clock, so the summary covers roughly the last `window_s` seconds; a total count since start is kept as
well. Local processing time only, never order-execution latency.
"""
from __future__ import annotations

import time
from array import array
from typing import Callable

SUB_BITS = 4
SUB = 1 << SUB_BITS                        # 16 buckets per power of two
EXACT = 2 * SUB                            # values below 32 ns have their own bucket
MAX_EXP = 42                               # 2^42 ns ~ 73 minutes; larger values land in the top bucket
NBUCKETS = SUB * (MAX_EXP - SUB_BITS) + EXACT


def _index(v: int) -> int:
    if v < EXACT:
        return v
    e = v.bit_length() - (SUB_BITS + 1)
    return min(SUB * (e + 1) + ((v >> e) - SUB), NBUCKETS - 1)


def _bounds(i: int) -> tuple[int, int]:
    """[low, high) of bucket i."""
    if i < EXACT:
        return i, i + 1
    e = i // SUB - 1
    m = i % SUB + SUB
    return m << e, (m + 1) << e


class LatencyHistogram:
    MAX_RELATIVE_ERROR = 1 / EXACT
    __slots__ = ("_counts", "count", "sum_ns", "min_ns", "max_ns", "clamped")

    def __init__(self) -> None:
        self._counts = array("q", bytes(8 * NBUCKETS))
        self.count = self.sum_ns = self.clamped = 0
        self.min_ns: int | None = None
        self.max_ns: int | None = None

    def record(self, ns: int) -> None:
        v = int(ns)
        exact = v
        if v < 0:
            v = 0
            self.clamped += 1
        elif v >= 1 << MAX_EXP:
            self.clamped += 1
        self._counts[_index(v)] += 1
        self.count += 1
        self.sum_ns += max(exact, 0)
        self.min_ns = exact if self.min_ns is None else min(self.min_ns, exact)
        self.max_ns = exact if self.max_ns is None else max(self.max_ns, exact)

    def merge(self, other: "LatencyHistogram") -> None:
        for i, c in enumerate(other._counts):
            if c:
                self._counts[i] += c
        self.count += other.count
        self.sum_ns += other.sum_ns
        self.clamped += other.clamped
        for name, pick in (("min_ns", min), ("max_ns", max)):
            o = getattr(other, name)
            if o is not None:
                mine = getattr(self, name)
                setattr(self, name, o if mine is None else pick(mine, o))

    def quantile(self, q: float) -> float | None:
        """The value at quantile q (0..1): the midpoint of the bucket holding it, clamped to the exact min/max."""
        if not self.count:
            return None
        rank = q * (self.count - 1)
        seen = 0
        for i, c in enumerate(self._counts):
            if not c:
                continue
            if seen + c > rank:
                lo, hi = _bounds(i)
                mid = lo if hi - lo == 1 else (lo + hi) / 2
                return float(min(max(mid, self.min_ns), self.max_ns))
            seen += c
        return float(self.max_ns)

    def summary(self, scale: float = 1e6) -> dict:
        """{"n", "p50", "p95", "p99", "max", "mean"} in units of `scale` ns (default milliseconds)."""
        if not self.count:
            return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None, "mean": None}
        return {"n": self.count, "p50": self.quantile(0.50) / scale, "p95": self.quantile(0.95) / scale,
                "p99": self.quantile(0.99) / scale, "max": self.max_ns / scale,
                "mean": self.sum_ns / self.count / scale}

    def footprint_bytes(self) -> int:
        return self._counts.buffer_info()[1] * self._counts.itemsize + 64


class RollingLatency:
    __slots__ = ("window_s", "slot_s", "_clock", "_slots", "_starts", "total_n", "total_max_ns")

    def __init__(self, window_s: float = 300.0, slots: int = 5, clock: Callable[[], float] = time.monotonic) -> None:
        if window_s <= 0 or slots < 1:
            raise ValueError("window_s must be > 0 and slots >= 1")
        self.window_s, self.slot_s, self._clock = window_s, window_s / slots, clock
        self._slots = [LatencyHistogram() for _ in range(slots)]
        self._starts = [None] * slots
        self.total_n = 0
        self.total_max_ns: int | None = None

    def _current(self) -> LatencyHistogram:
        now = self._clock()
        n = int(now // self.slot_s)
        i = n % len(self._slots)
        if self._starts[i] != n:
            self._slots[i] = LatencyHistogram()
            self._starts[i] = n
        return self._slots[i]

    def record(self, ns: int) -> None:
        self._current().record(ns)
        self.total_n += 1
        self.total_max_ns = ns if self.total_max_ns is None else max(self.total_max_ns, ns)

    def window(self) -> LatencyHistogram:
        n_now = int(self._clock() // self.slot_s)
        merged = LatencyHistogram()
        for h, start in zip(self._slots, self._starts):
            if start is not None and n_now - start < len(self._slots):
                merged.merge(h)
        return merged

    def summary(self, scale: float = 1e6) -> dict:
        return {**self.window().summary(scale), "window_s": self.window_s, "total_n": self.total_n,
                "total_max": None if self.total_max_ns is None else self.total_max_ns / scale}
