"""Central event bus: bounded fast and background lanes, dedup, fault isolation."""
import asyncio

from jinx.core.bus import EventBus, Lane
from jinx.core.events import EventKind, MarketEvent, Side


def ev(bus, sig="S", trader="W", **over):
    base = dict(kind=EventKind.TRADE, source="test", mint="M", signature=sig, trader=trader,
                side=Side.BUY, sol_lamports=1, token_amount=1, recv_ns=bus.clock_ns(), seq=bus.next_seq())
    base.update(over)
    return MarketEvent(**base)


def run(coro):
    return asyncio.run(coro)


def test_events_reach_subscribers_in_fifo_order_on_both_lanes():
    async def main():
        bus = EventBus()
        fast, bg = [], []
        bus.subscribe(Lane.FAST, lambda e: fast.append(e.signature))
        bus.subscribe(Lane.BACKGROUND, lambda e: bg.append(e.signature))
        task = asyncio.create_task(bus.run())
        for i in range(5):
            await bus.publish(ev(bus, sig=f"S{i}"))
        await bus.drain()
        task.cancel()
        return fast, bg
    fast, bg = run(main())
    assert fast == [f"S{i}" for i in range(5)]
    assert bg == fast


def test_duplicate_event_is_dispatched_once():
    async def main():
        bus = EventBus()
        seen = []
        bus.subscribe(Lane.FAST, lambda e: seen.append(e))
        task = asyncio.create_task(bus.run())
        first = ev(bus, sig="DUP")
        again = ev(bus, sig="DUP")  # same on-chain fact, later receive time and sequence
        assert await bus.publish(first) is True
        assert await bus.publish(again) is False
        await bus.drain()
        task.cancel()
        return seen, bus.stats
    seen, stats = run(main())
    assert len(seen) == 1
    assert stats["duplicates"] == 1


def test_durable_dedup_hook_blocks_events_seen_before_restart():
    async def main():
        persisted = {"OLD"}
        bus = EventBus(persist=lambda e: e.signature not in persisted)
        seen = []
        bus.subscribe(Lane.FAST, lambda e: seen.append(e.signature))
        task = asyncio.create_task(bus.run())
        await bus.publish(ev(bus, sig="OLD"))
        await bus.publish(ev(bus, sig="NEW"))
        await bus.drain()
        task.cancel()
        return seen
    assert run(main()) == ["NEW"]


def test_failing_handler_does_not_stop_other_handlers_or_later_events():
    async def main():
        bus = EventBus()
        good = []
        def bad(e):
            raise RuntimeError("boom")
        bus.subscribe(Lane.FAST, bad, name="bad")
        bus.subscribe(Lane.FAST, lambda e: good.append(e.signature), name="good")
        task = asyncio.create_task(bus.run())
        await bus.publish(ev(bus, sig="A"))
        await bus.publish(ev(bus, sig="B"))
        await bus.drain()
        task.cancel()
        return good, bus.stats
    good, stats = run(main())
    assert good == ["A", "B"]
    assert stats["handler_errors"] == {"bad": 2}


def test_async_handlers_are_awaited():
    async def main():
        bus = EventBus()
        seen = []
        async def handler(e):
            await asyncio.sleep(0)
            seen.append(e.signature)
        bus.subscribe(Lane.FAST, handler)
        task = asyncio.create_task(bus.run())
        await bus.publish(ev(bus, sig="X"))
        await bus.drain()
        task.cancel()
        return seen
    assert run(main()) == ["X"]


def test_fast_lane_is_bounded_and_applies_backpressure_instead_of_dropping():
    async def main():
        bus = EventBus(fast_capacity=2, background_capacity=2)
        published = 0
        async def producer():
            nonlocal published
            for i in range(5):
                await bus.publish(ev(bus, sig=f"S{i}"))
                published += 1
        prod = asyncio.create_task(producer())
        await asyncio.sleep(0.05)  # consumer not running: producer must block at capacity
        blocked_at = published
        handled = []
        bus.subscribe(Lane.FAST, lambda e: handled.append(e.signature))
        task = asyncio.create_task(bus.run())
        await prod
        await bus.drain()
        task.cancel()
        return blocked_at, handled, bus.stats
    blocked_at, handled, stats = run(main())
    assert blocked_at == 2
    assert handled == [f"S{i}" for i in range(5)]
    assert stats["fast_dropped"] == 0


def test_background_lane_drops_and_counts_when_full_but_fast_lane_keeps_everything():
    async def main():
        bus = EventBus(fast_capacity=10, background_capacity=1)
        bg = []
        bus.subscribe(Lane.BACKGROUND, lambda e: bg.append(e.signature))
        for i in range(3):
            await bus.publish(ev(bus, sig=f"S{i}"))
        task = asyncio.create_task(bus.run())
        await bus.drain()
        task.cancel()
        return bg, bus.stats
    bg, stats = run(main())
    assert stats["background_dropped"] == 2
    assert bg == ["S0"]
    assert stats["dispatched_fast"] == 3


def test_ingest_to_handled_latency_is_sampled_per_event():
    async def main():
        bus = EventBus()
        bus.subscribe(Lane.FAST, lambda e: None)
        task = asyncio.create_task(bus.run())
        for i in range(4):
            await bus.publish(ev(bus, sig=f"S{i}"))
        await bus.drain()
        task.cancel()
        return bus.latency(Lane.FAST).summary()
    summary = run(main())
    assert summary["n"] == 4 and summary["p50"] >= 0                  # a constant-memory histogram, not samples


def test_event_whose_durable_write_failed_is_accepted_when_resent():
    async def main():
        calls = {"n": 0}
        def flaky_persist(e):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
            return True
        bus = EventBus(persist=flaky_persist)
        seen = []
        bus.subscribe(Lane.FAST, lambda e: seen.append(e.signature))
        task = asyncio.create_task(bus.run())
        first = ev(bus, sig="RETRY")
        try:
            await bus.publish(first)
        except OSError:
            pass
        assert await bus.publish(ev(bus, sig="RETRY")) is True
        await bus.drain()
        task.cancel()
        return seen
    assert run(main()) == ["RETRY"]
