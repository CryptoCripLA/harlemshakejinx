"""RAM-only market data (owner orders 2026-10-21, and the storage repair the same day: "Normal raw market chatter is
transient", "RAM IS A WORKBENCH. NOT A WAREHOUSE."): raw feed events are processed in memory and never stored; the
one token state and the trackers are bounded and expire; the durable store keeps only trading records."""
import asyncio
from pathlib import Path

from jinx.core.bus import EventBus, Lane
from jinx.core.events import EventKind, MarketEvent, Side
from jinx.core_state import CoreState
from jinx.market_memory import MarketMemory
from jinx.registry import TokenRegistry
from jinx.store import Store

S = 1_000_000_000


def trade(sig, mint, trader, t_s, side=Side.BUY):
    return MarketEvent(kind=EventKind.TRADE, source="test", mint=mint, signature=sig, recv_ns=t_s * S, seq=0,
                       trader=trader, side=side, sol_lamports=1_000, token_amount=10)


def core(idle_ttl_s=3600):
    c = CoreState(TokenRegistry(idle_ttl_s=idle_ttl_s))
    return c, MarketMemory([("tokens", c.tokens)])


def ingest(c, events, bus=None):
    async def main():
        b = bus or EventBus()
        if bus is None:
            b.subscribe(Lane.FAST, c.apply, name="core")
        runner = asyncio.create_task(b.run())
        admitted = [await b.publish(e) for e in events]
        await b.drain()
        runner.cancel()
        return admitted
    return asyncio.run(main())


def test_market_state_is_ram_only_and_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c, _ = core()
    ingest(c, [trade("S1", "M", "A", 100), trade("S2", "M", "B", 101)])
    assert c.tokens.get("M").buys == 2
    assert list(tmp_path.iterdir()) == []                      # no file, no database


def test_duplicates_inside_the_window_are_refused_and_extracted_state_is_kept():
    c, _ = core()
    bus = EventBus()
    bus.subscribe(Lane.FAST, c.apply, name="core")
    assert ingest(c, [trade("OLD", "M", "A", 100), trade("NEW", "M", "B", 150)], bus) == [True, True]
    assert ingest(c, [trade("NEW", "M", "B", 150)], bus) == [False]     # inside the 120 s window
    assert c.tokens.get("M").buys == 2


def test_idle_tokens_are_evicted_by_the_maintenance_pass_but_held_tokens_are_kept():
    c, mem = core(idle_ttl_s=3600)
    c.tokens.held_provider = lambda: {"HELD"}                  # the ledger's open positions, as the app wires it
    ingest(c, [trade("S1", "IDLE", "A", 100), trade("S2", "HELD", "B", 100), trade("S3", "LIVE", "C", 5_000)])
    report = mem.tick(now_ns=6_000 * S, held={"HELD"})
    assert c.tokens.get("IDLE") is None and c.tokens.get("HELD") is not None and c.tokens.get("LIVE") is not None
    assert c.tokens.evicted == 1 and report["tokens"]["tokens"] == 2


def test_a_position_opened_between_maintenance_passes_is_never_evicted():
    """Owner order §3: "Open positions and pending orders must not be evicted merely to meet a market-cache limit"."""
    held = set()
    reg = TokenRegistry(max_tokens=2, idle_ttl_s=60)
    reg.held_provider = lambda: frozenset(held)
    reg.apply(trade("S1", "POS", "W", 100))
    held.add("POS")                                            # a position opens; no maintenance pass has run since
    for i in range(5):
        reg.apply(trade(f"T{i}", f"M{i}", "W", 1_000 + i))     # far past the idle time-out and over the cap
    assert reg.get("POS") is not None


def test_the_token_state_has_a_hard_cap_that_spares_held_tokens():
    reg = TokenRegistry(max_tokens=3, idle_ttl_s=10_000)
    reg.sweep(0, held={"HELD"})
    for i, mint in enumerate(["HELD", "A", "B", "C", "D"]):
        reg.apply(trade(f"S{i}", mint, "W", 100 + i))
    assert len(reg) == 3 and reg.get("HELD") is not None and reg.get("A") is None


def test_a_replayed_live_capture_leaves_nothing_behind_after_a_quiet_period(tmp_path):
    """The whole market side of the running app, not only the registry: every tracker is empty again."""
    from jinx import config
    from jinx.adapters.pumpapi.normalize import normalize
    from jinx.app import JinxApp
    from jinx.sim import load_capture
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    records = load_capture(Path(__file__).parent / "fixtures" / "pumpapi-live-20261021.jsonl.gz")
    seq = iter(range(1, 10**6))
    events = [e for r in records for e in normalize(r["raw"], r["recv_ns"], seq.__next__)]

    async def main():
        runner = asyncio.create_task(app.bus.run())
        for e in events:
            await app.bus.publish(e)
        await app.bus.drain()
        runner.cancel()
    asyncio.run(main())
    assert len(app.core.tokens) > 0 and app.early.tracked_mints() > 0
    last = max(r["recv_ns"] for r in records)
    app.market_tick(last + 10 * 3600 * S)
    counts = app.memory_counts()
    assert counts["tokens"] == counts["early_momentum"] == counts["graduation"] == counts["migration"] == 0
    assert counts["bundles"] == 0 and counts["bundle_pending_groups"] == 0
    app.store.close()


def test_the_app_keeps_market_data_in_ram_and_only_trading_records_on_disk(tmp_path):
    from jinx import config
    from jinx.app import JinxApp
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    assert app.store.path == tmp_path / "jinx.db" and not hasattr(app, "market")

    async def main():
        runner = asyncio.create_task(app.bus.run())
        await app.bus.publish(trade("S1", "M", "A", 100))
        await app.bus.drain()
        runner.cancel()
    asyncio.run(main())
    assert app.core.tokens.get("M").buys == 1
    tables = {r[0] for r in app.store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"events", "tokens", "token_traders", "wallets"}     # no market table on disk at all
    app.store.close()


def test_health_reads_integrity_from_the_ledger_and_event_counts_from_the_bus(tmp_path):
    from jinx.health import build_health
    from jinx.ledger import PositionLedger
    from jinx.paper import PaperWallet
    ledger_store = Store(tmp_path / "l.db")
    feed = {"adapter": "mako", "connected": True, "last_message_ns": 1}
    h = build_health(ledger_store, PositionLedger(ledger_store), PaperWallet(ledger_store, 1_000), [feed],
                     {"published": 5}, now_ns=2)
    assert h["store"]["ok"] is True and h["store"]["events"] == 5
    assert h["store"]["market_store"].startswith("none")
    ledger_store.close()
