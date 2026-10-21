"""PumpPortal's free migration stream (owner request 2026-10-20: use pump.fun's free data on top of GMGN).

Measured 2026-10-20 ~23:45 UTC without an API key: `subscribeMigration` is accepted ("Subscribed to 'migration'
events.") and delivers {"signature", "mint", "txType": "migrate", "pool": "pump-amm"} when a coin graduates.
Mako's PumpPortalStream never subscribes to it, and its dispatcher routes only creations and trades, so the JINX
feed adapter subscribes once per connection and routes migrate messages itself; Mako's code stays unmodified.
A migrate message names the destination venue, not the new pool's address: in lifecycle terms it is a migration
signal on JINX's receipt clock, not a confirmation (docs/GMGN_ADAPTER_CONTRACT.md §5)."""
import asyncio
import json
from itertools import count
from pathlib import Path

import pytest

from jinx.adapters.mako.normalize import MalformedMessage, normalize
from jinx.core.events import EventKind, MarketEvent

ROOT = Path(__file__).resolve().parents[1]
MIGRATE = {"signature": "MS1", "mint": "MintMIGpump", "txType": "migrate", "pool": "pump-amm"}
CONFIRMED = {"message": "Subscribed to 'migration' events."}


def test_a_migrate_message_becomes_one_migration_event():
    [ev] = normalize(MIGRATE, 5, count(1).__next__)
    assert (ev.kind, ev.mint, ev.signature, ev.trader, ev.side) == (EventKind.MIGRATION, "MintMIGpump", "MS1", None, None)
    assert ev.raw["pool"] == "pump-amm" and ev.recv_ns == 5
    assert ev.idempotency_key == "MS1:migration:MintMIGpump:-:-"


@pytest.mark.parametrize("missing", ["signature", "mint", "pool"])
def test_an_incomplete_migrate_message_is_refused(missing):
    with pytest.raises(MalformedMessage):
        normalize({k: v for k, v in MIGRATE.items() if k != missing}, 5, count(1).__next__)


def test_a_migration_event_is_normalized_and_deduplicated_once():
    from jinx.core.dedup import Deduplicator
    [ev] = normalize(MIGRATE, 5, count(1).__next__)
    assert (ev.kind, ev.mint, ev.signature, ev.raw["pool"]) == (EventKind.MIGRATION, "MintMIGpump", "MS1", "pump-amm")
    d = Deduplicator()
    assert d.first_time(ev.idempotency_key, 5) is True and d.first_time(ev.idempotency_key, 6) is False


def test_the_token_state_ignores_a_migration_event_the_migration_module_owns():
    from jinx.registry import TokenRegistry
    [ev] = normalize(MIGRATE, 5, count(1).__next__)
    reg = TokenRegistry()
    reg.apply(ev)
    assert reg.get("MintMIGpump") is None


def test_the_feed_subscribes_to_migrations_once_per_connection_and_publishes_them():
    import websockets
    from jinx.adapters.mako.feed import MakoPumpPortalFeed
    from jinx.core.bus import EventBus, Lane

    async def main():
        per_conn = []
        async def server(ws):
            methods = []
            per_conn.append(methods)
            async for raw in ws:
                m = json.loads(raw)
                methods.append(m["method"])
                if m["method"] == "subscribeNewToken":
                    await ws.send(json.dumps({"message": "Successfully subscribed to token creation events."}))
                elif m["method"] == "subscribeMigration":
                    for out in (CONFIRMED, MIGRATE, MIGRATE):
                        await ws.send(json.dumps(out))
                    if len(per_conn) == 1:
                        await ws.close(code=1000)               # a clean close: Mako reconnects at once
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            bus = EventBus()
            got = []
            bus.subscribe(Lane.FAST, got.append)     # every event reaches both lanes; one is enough here
            feed = MakoPumpPortalFeed(bus, ROOT / "upstream" / "mako", ws_url=f"ws://127.0.0.1:{port}")
            runner = asyncio.create_task(bus.run())
            task = asyncio.create_task(feed.run())
            for _ in range(300):
                await asyncio.sleep(0.01)
                if len(per_conn) >= 2 and feed.stats["duplicates"] >= 3:
                    break
            await bus.drain()
            health = feed.health()
            task.cancel(); runner.cancel()
            return per_conn, got, feed.stats, health
    per_conn, got, stats, health = asyncio.run(main())
    assert len(per_conn) >= 2
    assert all(methods.count("subscribeMigration") == 1 for methods in per_conn)   # once per connection
    migrations = [e for e in got if e.kind is EventKind.MIGRATION]
    assert [(e.mint, e.signature) for e in migrations] == [("MintMIGpump", "MS1")]  # one fact, published once
    assert stats["duplicates"] >= 3 and stats["migrations"] == 1
    assert health["migration_stream"] == "on"


def test_migration_events_do_not_need_a_trader():
    ev = MarketEvent(kind=EventKind.MIGRATION, source="mako.pumpportal", mint="M", signature="S", recv_ns=1, seq=1)
    assert ev.trader is None
