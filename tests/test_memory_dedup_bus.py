"""Storage repair (owner order 2026-10-21 §1): the duplicate filter expires and is bounded, the bus keeps no latency
sample arrays, every queue is finite with a defined overflow, and a dropped background event marks its consumer's
evidence incomplete instead of passing for clean data."""
from __future__ import annotations

import asyncio
import random

import pytest

from jinx.core.bus import EventBus, Lane
from jinx.core.dedup import Deduplicator
from jinx.core.events import EventKind, MarketEvent, Side
from jinx.core.latency import LatencyHistogram, RollingLatency

S = 1_000_000_000


def ev(bus, sig="S", mint="M", recv_ns=1_000 * S):
    return MarketEvent(kind=EventKind.TRADE, source="test", mint=mint, signature=sig, recv_ns=recv_ns,
                       seq=bus.next_seq(), trader="W", side=Side.BUY, sol_lamports=1, token_amount=1)


# ---- latency histogram: constant memory, honest percentiles -----------------------------------------------------
def test_histogram_memory_does_not_grow_with_samples():
    h = LatencyHistogram()
    before = h.footprint_bytes()
    for v in range(0, 5_000_000, 7):
        h.record(v)
    assert h.footprint_bytes() == before
    assert h.count == len(range(0, 5_000_000, 7))


def test_histogram_percentiles_are_within_the_bucket_error():
    rng = random.Random(7)
    values = [int(rng.lognormvariate(13, 1.5)) for _ in range(50_000)]   # ~0.4 ms median, long tail
    h = LatencyHistogram()
    for v in values:
        h.record(v)
    values.sort()
    for q in (0.50, 0.95, 0.99):
        exact = values[int(q * (len(values) - 1))]
        assert abs(h.quantile(q) - exact) <= exact * LatencyHistogram.MAX_RELATIVE_ERROR + 1
    assert h.max_ns == values[-1] and h.min_ns == values[0]


def test_histogram_clamps_negative_and_huge_values_and_counts_them():
    h = LatencyHistogram()
    h.record(-5)
    h.record(10 ** 15)
    assert h.count == 2 and h.clamped == 2
    assert h.max_ns == 10 ** 15                      # the exact maximum is still reported


def test_rolling_latency_forgets_old_windows_but_keeps_totals():
    t = [0.0]
    r = RollingLatency(window_s=60, slots=6, clock=lambda: t[0])
    for _ in range(100):
        r.record(1_000_000)                          # 1 ms
    t[0] = 30.0
    r.record(9_000_000)
    assert r.summary()["n"] == 101
    t[0] = 200.0                                     # past the window
    r.record(2_000_000)
    s = r.summary()
    assert s["n"] == 1 and s["total_n"] == 102
    assert s["p50"] == pytest.approx(2.0, rel=LatencyHistogram.MAX_RELATIVE_ERROR)


# ---- duplicate filter: 120 s expiry, 16,000 keys -----------------------------------------------------------------
def test_dedup_defaults_are_the_owner_ordered_120_s_and_16000_keys():
    d = Deduplicator()
    assert d.ttl_ns == 120 * S and d.capacity == 16_000


def test_dedup_refuses_a_duplicate_inside_the_window_and_measures_its_delay():
    d = Deduplicator(ttl_s=120, capacity=100)
    assert d.first_time("k", 1_000 * S) is True
    assert d.first_time("k", 1_001_500_000_000) is False      # 1.5 s later: the other feed's copy
    assert d.duplicates == 1
    assert d.stats()["duplicate_delay_ms"]["max"] == pytest.approx(1_500, rel=0.04)


def test_dedup_key_expires_after_the_window():
    d = Deduplicator(ttl_s=120, capacity=100)
    d.first_time("k", 1_000 * S)
    d.first_time("other", 1_121 * S)                 # time moves on (receipt clock of the stream)
    assert len(d) == 1                               # "k" expired
    assert d.first_time("k", 1_121 * S) is True
    assert d.stats()["expired"] == 1


def test_dedup_capacity_is_a_hard_bound_and_early_evictions_are_counted():
    d = Deduplicator(ttl_s=120, capacity=3)
    for i in range(10):
        d.first_time(f"k{i}", 1_000 * S + i)
    assert len(d) == 3
    st = d.stats()
    assert st["evicted_before_expiry"] == 7          # capacity, not the 120 s window, decided these: reported
    assert st["youngest_early_eviction_ms"] is not None


def test_dedup_holds_no_key_strings():
    d = Deduplicator(ttl_s=120, capacity=10)
    key = "x" * 500
    d.first_time(key, S)
    assert all(not isinstance(k, str) for k in d._keys)


def test_dedup_clock_never_runs_backwards():
    d = Deduplicator(ttl_s=10, capacity=10)
    d.first_time("a", 100 * S)
    d.first_time("b", 50 * S)                        # an event received earlier but processed later
    assert d.first_time("a", 60 * S) is False        # still inside the window measured from the newest time


def test_dedup_forget_lets_a_resend_in():
    d = Deduplicator()
    d.first_time("k", S)
    d.forget("k")
    assert d.first_time("k", S) is True


# ---- bus: finite queues, constant-memory diagnostics, no silent loss --------------------------------------------
def test_bus_has_no_latency_sample_arrays():
    bus = EventBus()
    assert not any(type(v).__name__ == "deque" for v in vars(bus).values())
    assert isinstance(bus.latency(Lane.FAST), RollingLatency)


def test_bus_default_queues_are_finite_and_smaller_than_before():
    bus = EventBus()
    assert 0 < bus.capacity(Lane.FAST) <= 4_096 and 0 < bus.capacity(Lane.BACKGROUND) <= 4_096


def test_bus_dedups_on_the_events_receipt_clock():
    async def main():
        bus = EventBus()
        handled = []
        bus.subscribe(Lane.FAST, lambda e: handled.append(e.recv_ns))
        task = asyncio.create_task(bus.run())
        assert await bus.publish(ev(bus, "A", recv_ns=1_000 * S)) is True
        assert await bus.publish(ev(bus, "A", recv_ns=1_100 * S)) is False   # inside 120 s
        assert await bus.publish(ev(bus, "B", recv_ns=1_200 * S)) is True    # moves the clock
        assert await bus.publish(ev(bus, "A", recv_ns=1_200 * S)) is True    # A expired: admitted again
        await bus.drain()
        task.cancel()
        return handled
    assert len(asyncio.run(main())) == 3


def test_bus_reports_queue_high_water_and_backpressure_waits():
    async def main():
        bus = EventBus(fast_capacity=2, background_capacity=8)
        prod = asyncio.ensure_future(asyncio.gather(*(bus.publish(ev(bus, f"S{i}")) for i in range(5))))
        await asyncio.sleep(0.05)
        bus.subscribe(Lane.FAST, lambda e: None)
        task = asyncio.create_task(bus.run())
        await prod
        await bus.drain()
        task.cancel()
        return bus.stats
    stats = asyncio.run(main())
    assert stats["queue_high_water"]["fast"] == 2
    assert stats["fast_backpressure_waits"] >= 3
    assert stats["fast_dropped"] == 0


def test_dropped_background_event_marks_the_consumer_incomplete():
    async def main():
        bus = EventBus(fast_capacity=10, background_capacity=1)
        dropped = []
        bus.subscribe(Lane.BACKGROUND, lambda e: None, name="bundles", on_drop=lambda e: dropped.append(e.mint))
        for i in range(3):
            await bus.publish(ev(bus, f"S{i}", mint=f"M{i}"))
        task = asyncio.create_task(bus.run())
        await bus.drain()
        task.cancel()
        return dropped, bus.stats
    dropped, stats = asyncio.run(main())
    assert dropped == ["M1", "M2"]
    assert stats["background_dropped"] == 2


def test_latency_is_sampled_into_the_histogram_only_for_successful_dispatch():
    async def main():
        bus = EventBus()
        bus.subscribe(Lane.FAST, lambda e: (_ for _ in ()).throw(RuntimeError("x")) if e.signature == "BAD" else None)
        task = asyncio.create_task(bus.run())
        await bus.publish(ev(bus, "OK"))
        await bus.publish(ev(bus, "BAD"))
        await bus.drain()
        task.cancel()
        return bus
    bus = asyncio.run(main())
    assert bus.latency(Lane.FAST).summary()["n"] == 1
    assert bus.stats["handler_errors"]
