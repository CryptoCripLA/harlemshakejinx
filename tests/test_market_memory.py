"""RAM-only market data (owner order 2026-10-21): raw feed events are processed in memory and discarded after the
useful information is extracted; nothing accumulates on the execution server's disk. The durable store keeps only
trading records (positions, fills, exits, paper wallet, settings)."""
import asyncio
from pathlib import Path

from jinx.core.bus import EventBus, Lane
from jinx.core.events import EventKind, MarketEvent, Side
from jinx.core_state import CoreState
from jinx.registry import TokenRegistry, WalletRegistry
from jinx.store import Store

S = 1_000_000_000


def trade(sig, mint, trader, t_s, side=Side.BUY):
    return MarketEvent(kind=EventKind.TRADE, source="test", mint=mint, signature=sig, recv_ns=t_s * S, seq=0,
                       trader=trader, side=side, sol_lamports=1_000, token_amount=10)


def memory(tmp_path):
    from jinx.market_memory import MarketMemory
    cwd_before = set(Path.cwd().iterdir())
    store = Store(":memory:")
    assert set(Path.cwd().iterdir()) == cwd_before            # nothing written to disk
    core = CoreState(store, TokenRegistry(store), WalletRegistry(store))
    return store, core, MarketMemory(store, core, event_ttl_s=300, idle_ttl_s=3600)


def ingest(store, core, events):
    async def main():
        bus = EventBus(persist=store.record_event)
        bus.subscribe(Lane.FAST, core.apply, name="core")
        runner = asyncio.create_task(bus.run())
        admitted = [await bus.publish(e) for e in events]
        await bus.drain()
        runner.cancel()
        return admitted
    return asyncio.run(main())


def test_the_market_store_lives_in_memory_and_counts_events(tmp_path):
    store, core, mem = memory(tmp_path)
    ingest(store, core, [trade("S1", "M", "A", 100), trade("S2", "M", "B", 101)])
    assert store.event_count() == 2 and core.tokens.get("M").buys == 2


def test_applied_raw_events_are_discarded_after_their_ttl_and_dedup_holds_within_it(tmp_path):
    store, core, mem = memory(tmp_path)
    ingest(store, core, [trade("OLD", "M", "A", 100), trade("NEW", "M", "B", 500)])
    report = mem.prune(now_ns=700 * S)
    assert report["events_discarded"] == 1 and store.event_count() == 1
    assert ingest(store, core, [trade("NEW", "M", "B", 500)]) == [False]   # still deduplicated inside the window
    assert core.tokens.get("M").buys == 2                                  # extracted state is kept


def test_unapplied_events_are_never_discarded(tmp_path):
    store, core, mem = memory(tmp_path)
    store.record_event(trade("PENDING", "M", "A", 100))                     # stored, not yet applied
    mem.prune(now_ns=10_000 * S)
    assert len(store.unapplied_events()) == 1


def test_idle_tokens_and_wallets_are_evicted_but_held_tokens_are_kept(tmp_path):
    store, core, mem = memory(tmp_path)
    ingest(store, core, [trade("S1", "IDLE", "A", 100), trade("S2", "HELD", "B", 100), trade("S3", "LIVE", "C", 5_000)])
    report = mem.prune(now_ns=6_000 * S, held={"HELD"})
    assert core.tokens.get("IDLE") is None and core.tokens.get("HELD") is not None and core.tokens.get("LIVE") is not None
    assert core.wallets.get("A") is None and core.wallets.get("C") is not None
    assert report["tokens_evicted"] == 1 and report["wallets_evicted"] >= 1
    assert store.conn.execute("SELECT COUNT(*) FROM token_traders WHERE mint = 'IDLE'").fetchone()[0] == 0


def test_a_replayed_live_capture_leaves_nothing_behind_after_pruning(tmp_path):
    from jinx.adapters.pumpapi.normalize import normalize
    from jinx.sim import load_capture
    store, core, mem = memory(tmp_path)
    records = load_capture(Path(__file__).parent / "fixtures" / "pumpapi-live-20261021.jsonl.gz")
    seq = iter(range(1, 10**6))
    events = [e for r in records for e in normalize(r["raw"], r["recv_ns"], seq.__next__)]
    ingest(store, core, events)
    assert store.event_count() > 1000 and len(core.tokens) > 0
    last = max(r["recv_ns"] for r in records)
    mem.prune(now_ns=last + 10 * 3600 * S)
    assert store.event_count() == 0 and len(core.tokens) == 0 and len(core.wallets) == 0


def test_the_app_keeps_market_events_in_ram_and_only_trading_records_on_disk(tmp_path):
    """The runtime wiring: the bus persists into the in-memory market store; the durable file holds the ledger."""
    from jinx import config
    from jinx.app import JinxApp
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    assert app.market.path == Path(":memory:") and app.store.path == tmp_path / "jinx.db"

    async def main():
        runner = asyncio.create_task(app.bus.run())
        await app.bus.publish(trade("S1", "M", "A", 100))
        await app.bus.drain()
        runner.cancel()
    asyncio.run(main())
    assert app.market.event_count() == 1 and app.core.tokens.get("M").buys == 1
    assert app.store.event_count() == 0                          # no raw event on disk
    app.market.close(); app.store.close()


def test_health_reads_integrity_from_the_ledger_and_counts_from_the_market_store(tmp_path):
    from jinx.health import build_health
    from jinx.ledger import PositionLedger
    from jinx.paper import PaperWallet
    ledger_store, market = Store(tmp_path / "l.db"), Store(":memory:")
    market.record_event(trade("S1", "M", "A", 100))
    feed = {"adapter": "mako", "connected": True, "last_message_ns": 1}
    h = build_health(ledger_store, PositionLedger(ledger_store), PaperWallet(ledger_store, 1_000), [feed], {}, now_ns=2,
                     market_store=market)
    assert h["store"]["ok"] is True and h["store"]["events"] == 1 and h["store"]["unapplied"] == 1
    ledger_store.close(); market.close()
