"""Regression tests for the Codex Phase 1 review, round 2 (docs/audits/phase1-codex-20261020-round2.md)."""
import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from jinx.core.events import Side
from jinx.execution import EntryIntent, ExecutionGateway, ExitIntent
from jinx.ledger import PositionLedger, Status
from jinx.modes import Mode, set_mode
from jinx.paper import CurveFees, PaperExecutor, PaperWallet
from jinx.registry import TokenRegistry
from jinx.store import SCHEMA_VERSION, Store
from tests.conftest import make_create, make_trade

FIXTURES = Path(__file__).parent / "fixtures"
START = 2_000_000_000


def v1_database(path: Path) -> None:
    """A database written by the 1ad0d65 build: one open position with its buy fill, one failed reservation."""
    c = sqlite3.connect(path)
    c.executescript((FIXTURES / "schema_v1_1ad0d65.sql").read_text())
    c.execute("INSERT INTO positions (entry_intent_id, mint, status, token_amount, cost_lamports, protocol_fees_lamports,"
              " network_fees_lamports, simulated, opened_ns) VALUES ('E1','A','OPEN',1000,100000,990,5000,1,1)")
    c.execute("INSERT INTO fills VALUES ('E1:buy', 1, 'buy', 1000, 100000, 990, 5000, 1, 1)")
    c.execute("INSERT INTO positions (entry_intent_id, mint, status, simulated, opened_ns, closed_ns, note)"
              " VALUES ('E2','B','FAILED',1,2,3,'NO_QUOTE')")
    c.execute("INSERT INTO settings VALUES ('mode','AUTO',1,'owner')")
    c.commit()
    c.close()


# ---- B1: existing Phase 1 databases could not upgrade ----------------------------------
def test_v1_database_is_migrated_in_place_and_keeps_its_positions(tmp_path):
    path = tmp_path / "old.db"
    v1_database(path)
    s = Store(path)
    assert s.schema_version() == SCHEMA_VERSION
    ledger = PositionLedger(s)
    open_pos = ledger.get(1)
    assert open_pos.status is Status.OPEN and open_pos.token_amount == 1000
    assert open_pos.entry_budget_lamports == 100000      # reconstructed from its buy fill
    failed = ledger.get(2)
    assert failed.status is Status.FAILED and failed.entry_budget_lamports is None  # unknown, not invented
    assert ledger.reserve_entry("E3", "C", budget_lamports=1).reason == "SLOT_LIMIT"  # the open one holds the slot
    s.close()


def test_migrated_database_enforces_the_new_constraints(tmp_path):
    path = tmp_path / "old.db"
    v1_database(path)
    s = Store(path)
    with pytest.raises(sqlite3.IntegrityError):
        s.conn.execute("INSERT INTO fills (fill_id, position_id, side, token_amount, sol_lamports,"
                       " protocol_fee_lamports, network_fee_lamports, simulated, ts_ns)"
                       " VALUES ('bad', 1, 'sell', -5, 0, 0, 0, 1, 1)")
    s.close()


def test_migration_runs_once_and_is_recorded(tmp_path):
    path = tmp_path / "old.db"
    v1_database(path)
    Store(path).close()
    s = Store(path)
    assert s.schema_version() == SCHEMA_VERSION
    assert len(PositionLedger(s).all()) == 2
    s.close()


def test_replaying_a_migrated_intent_with_unknown_budget_is_not_rejected_as_a_conflict(tmp_path):
    path = tmp_path / "old.db"
    v1_database(path)
    s = Store(path)
    r = PositionLedger(s).reserve_entry("E2", "B", budget_lamports=500)
    assert r.ok and r.reason == "SAME_INTENT"
    s.close()


# ---- B2: a rejected oversell locked the position -----------------------------------------
@pytest.fixture
def rig(store):
    set_mode(store, Mode.AUTO)
    reg = TokenRegistry()
    reg.apply(make_create(mint="A"))
    reg.apply(make_trade(mint="A", v_sol=32_000_000_000, v_tokens=1_000_000_000_000_000))
    ledger = PositionLedger(store)
    wallet = PaperWallet(store, START)
    paper = PaperExecutor(store, ledger, wallet, reg, CurveFees(95, 5), network_fee_lamports=5_000)
    return store, reg, ledger, wallet, ExecutionGateway(store, ledger, paper)


def test_oversell_is_refused_without_taking_exit_ownership(rig):
    store, reg, ledger, wallet, gw = rig
    p = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000)).position_id
    held = ledger.get(p).token_amount
    r = gw.submit_exit(ExitIntent("X1", p, token_amount=held + 1))
    assert not r.ok and r.reason == "EXCEEDS_POSITION"
    assert ledger.get(p).status is Status.OPEN
    assert gw.submit_exit(ExitIntent("X1", p, token_amount=held)).ok   # the corrected retry works
    assert ledger.get(p).status is Status.CLOSED


def test_any_rejected_paper_sell_leaves_the_position_open_and_retryable(rig, monkeypatch):
    store, reg, ledger, wallet, gw = rig
    p = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000)).position_id
    real = ledger.record_sell_fill
    def reject(*a, **k):
        raise ValueError("ledger rejected the sell")
    monkeypatch.setattr(ledger, "record_sell_fill", reject)
    r = gw.submit_exit(ExitIntent("X1", p))
    assert not r.ok and r.reason.startswith("SELL_REJECTED")
    assert ledger.get(p).status is Status.OPEN
    monkeypatch.setattr(ledger, "record_sell_fill", real)
    assert gw.submit_exit(ExitIntent("X2", p)).ok


# ---- B3: a failed COMMIT wedged the connection ---------------------------------------------
class CommitFailsOnce:
    """Wraps a sqlite3 connection; the first COMMIT raises, everything else passes through."""
    def __init__(self, conn):
        self._conn, self.failed = conn, False

    def execute(self, sql, *a):
        if sql == "COMMIT" and not self.failed:
            self.failed = True
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_failed_commit_rolls_back_and_the_next_write_succeeds(tmp_path):
    s = Store(tmp_path / "j.db")
    real = s.conn
    s.conn = CommitFailsOnce(real)
    with pytest.raises(sqlite3.OperationalError):
        s.set_setting("probe", "1", by="test")           # a ledger write (no market events since 2026-10-21)
    assert real.in_transaction is False
    s.set_setting("probe", "2", by="test")              # the retry is admitted and stored
    assert s.get_setting("probe") == "2"
    s.conn = real
    s.close()


# ---- B5: OFF must not keep paid trade subscriptions running ---------------------------------
def test_off_mode_stops_paid_trade_subscriptions_but_keeps_held_positions_watched():
    import json
    import websockets
    from jinx.adapters.mako.feed import MakoPumpPortalFeed
    from jinx.core.bus import EventBus
    from tests.test_adapters import CREATE

    root = Path(__file__).resolve().parent.parent
    state = {"mode": Mode.OFF, "held": set()}

    async def main():
        sent = []
        async def server(ws):
            async for msg in ws:
                sent.append(json.loads(msg))
                if sent[-1]["method"] == "subscribeNewToken":
                    await ws.send(json.dumps(dict(CREATE, mint="OFFMINT", signature="S-OFF")))
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            bus = EventBus()
            feed = MakoPumpPortalFeed(bus, root / "upstream" / "mako", ws_url=f"ws://127.0.0.1:{port}",
                                      subscribe_trades=True, mode_provider=lambda: state["mode"],
                                      held_mints_provider=lambda: state["held"])
            runner, task = asyncio.create_task(bus.run()), asyncio.create_task(feed.run())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if feed.stats["published"] >= 2:
                    break
            off_subs = [m for m in sent if m["method"] == "subscribeTokenTrade"]
            state["mode"] = Mode.AUTO
            await feed._on_message(dict(CREATE, mint="AUTOMINT", signature="S-AUTO"))
            await feed._on_message(dict(CREATE, mint="HELDMINT", signature="S-HELD"))
            state["held"] = {"HELDMINT"}
            state["mode"] = Mode.OFF
            await feed.sync_subscriptions()
            await asyncio.sleep(0.05)
            task.cancel(); runner.cancel()
            return off_subs, sent, feed.health()
    off_subs, sent, health = asyncio.run(main())
    assert off_subs == []                                                   # nothing paid while OFF
    unsubs = [k for m in sent if m["method"] == "unsubscribeTokenTrade" for k in m["keys"]]
    assert unsubs == ["AUTOMINT"]                                           # OFF drops it...
    assert "HELDMINT" not in unsubs and health["watched_tokens"] == 1       # ...but keeps the held position


# ---- B6: latency must cover the durable write ------------------------------------------------
def test_reported_latency_includes_the_durable_write(store):
    from jinx.core.bus import EventBus, Lane
    from jinx.core_state import CoreState

    core = CoreState(TokenRegistry())

    async def main():
        def slow_persist(ev):                          # the bus's optional durable hook (unused for market data)
            time.sleep(0.05)
            return True
        bus = EventBus(persist=slow_persist)
        bus.subscribe(Lane.FAST, core.apply, name="core_state")
        task = asyncio.create_task(bus.run())
        await bus.publish(make_trade(recv_ns=time.time_ns()))
        await bus.drain()
        task.cancel()
        return bus.latency(Lane.FAST).summary()
    admission = asyncio.run(main())
    receive = core.receive_latency().summary()
    assert admission["max"] >= 50 and receive["max"] >= 50          # milliseconds
