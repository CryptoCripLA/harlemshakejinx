"""Regression tests for the Codex Phase 1 review, round 3 (docs/audits/phase1-codex-20261020-round3.md)."""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from jinx.core.events import Side
from jinx.core_state import CoreState
from jinx.execution import ExecutionGateway, ExitIntent
from jinx.ledger import PositionLedger, Status
from jinx.modes import Mode
from jinx.paper import CurveFees, PaperExecutor, PaperWallet
from jinx.recovery import recover
from jinx.registry import TokenRegistry
from jinx.store import SCHEMA_VERSION, Store
from tests.conftest import make_create, make_trade

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent


def v1_with_exits(path: Path) -> None:
    """A 1ad0d65-era database: position 1 had a 250M partial sell by intent X (still PENDING_CLOSE, as v1
    left it); position 2 was fully closed by intent Z."""
    c = sqlite3.connect(path)
    c.executescript((FIXTURES / "schema_v1_1ad0d65.sql").read_text())
    c.execute("INSERT INTO positions (entry_intent_id, exit_intent_id, mint, status, token_amount, cost_lamports,"
              " proceeds_lamports, simulated, opened_ns) VALUES ('E1','X','A','PENDING_CLOSE',750000000,10000000,"
              " 2000000, 1, 1)")
    c.execute("INSERT INTO fills VALUES ('E1:buy', 1, 'buy', 1000000000, 10000000, 99009, 5000, 1, 1)")
    c.execute("INSERT INTO fills VALUES ('X:sell', 1, 'sell', 250000000, 2000000, 20000, 5000, 1, 2)")
    c.execute("INSERT INTO positions (entry_intent_id, exit_intent_id, mint, status, token_amount, cost_lamports,"
              " proceeds_lamports, realized_pnl_lamports, simulated, opened_ns, closed_ns) VALUES"
              " ('E2','Z','B','CLOSED',0,10000000,9000000,-1010000,1,3,4)")
    c.execute("INSERT INTO fills VALUES ('E2:buy', 2, 'buy', 500000000, 10000000, 99009, 5000, 1, 3)")
    c.execute("INSERT INTO fills VALUES ('Z:sell', 2, 'sell', 500000000, 9000000, 90000, 5000, 1, 4)")
    c.execute("INSERT INTO settings VALUES ('mode','AUTO',1,'owner')")
    c.commit()
    c.close()


@pytest.fixture
def migrated(tmp_path):
    path = tmp_path / "v1.db"
    v1_with_exits(path)
    s = Store(path)
    reg = TokenRegistry()
    reg.apply(make_create(mint="A"))
    reg.apply(make_trade(mint="A", v_sol=32_000_000_000, v_tokens=1_000_000_000_000_000))
    ledger = PositionLedger(s)
    wallet = PaperWallet(s, 2_000_000_000)
    recover(ledger, wallet)  # v1 left position 1 PENDING_CLOSE
    gw = ExecutionGateway(s, ledger, PaperExecutor(s, ledger, wallet, reg, CurveFees(95, 5), 5_000))
    yield s, ledger, gw
    s.close()


# ---- R3-1: migrated historical exits could execute again --------------------------------
def test_replaying_a_historical_partial_exit_does_not_sell_again(migrated):
    s, ledger, gw = migrated
    r = gw.submit_exit(ExitIntent("X", 1, token_amount=250_000_000))
    assert r.ok and r.reason == "ALREADY_APPLIED"
    assert ledger.get(1).token_amount == 750_000_000
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE position_id = 1 AND side = 'sell'").fetchone()[0] == 1


@pytest.mark.parametrize("position_id,amount", [(1, 300_000_000), (1, None), (2, 250_000_000)])
def test_ambiguous_or_cross_position_reuse_of_a_historical_exit_is_refused(migrated, position_id, amount):
    s, ledger, gw = migrated
    r = gw.submit_exit(ExitIntent("X", position_id, token_amount=amount))
    assert not r.ok and r.reason == "INTENT_CONFLICT"


def test_replaying_a_historical_full_exit_is_already_applied(migrated):
    s, ledger, gw = migrated
    r = gw.submit_exit(ExitIntent("Z", 2))
    assert r.ok and r.reason == "ALREADY_APPLIED"


def test_a_new_exit_on_a_migrated_position_still_works(migrated):
    s, ledger, gw = migrated
    assert ledger.get(1).status is Status.OPEN
    assert gw.submit_exit(ExitIntent("Y", 1)).ok
    assert ledger.get(1).status is Status.CLOSED


def test_v2_database_upgrades_to_the_current_schema(tmp_path):
    path = tmp_path / "v2.db"
    c = sqlite3.connect(path)
    c.executescript((FIXTURES / "schema_v2_e1c5b06.sql").read_text())
    c.execute("PRAGMA user_version = 2")
    c.commit()
    c.close()
    s = Store(path)
    assert s.schema_version() == SCHEMA_VERSION
    assert "legacy_fill_id" in s._columns("exit_intents")
    s.close()


# ---- R3-2: a failed OFF unsubscribe was forgotten ----------------------------------------
def test_failed_unsubscribe_is_retried_and_visible_until_it_succeeds():
    import websockets
    from jinx.adapters.mako.feed import MakoPumpPortalFeed
    from jinx.core.bus import EventBus
    from jinx.health import build_health
    from tests.test_adapters import CREATE

    state = {"mode": Mode.AUTO}

    async def main():
        received = []
        async def server(ws):
            async for msg in ws:
                received.append(json.loads(msg))
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            feed = MakoPumpPortalFeed(EventBus(), ROOT / "upstream" / "mako", ws_url=f"ws://127.0.0.1:{port}",
                                      subscribe_trades=True, mode_provider=lambda: state["mode"])
            task = asyncio.create_task(feed.run())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if feed.stream.ws is not None:
                    break
            await feed._on_message(dict(CREATE, mint="A", signature="S-A"))
            real = feed.stream.unsubscribe_token
            calls = {"n": 0}
            async def flaky(mint):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise ConnectionError("send failed")
                await real(mint)
            feed.stream.unsubscribe_token = flaky
            state["mode"] = Mode.OFF
            await feed.sync_subscriptions()
            after_failure = feed.health()
            await feed.sync_subscriptions()
            await asyncio.sleep(0.05)
            task.cancel()
            return after_failure, feed.health(), received
    after_failure, after_retry, received = asyncio.run(main())
    assert after_failure["pending_unsubscribes"] == 1
    assert after_retry["pending_unsubscribes"] == 0
    assert {"method": "unsubscribeTokenTrade", "keys": ["A"]} in received


def test_health_is_degraded_while_an_unsubscribe_is_pending(store):
    from jinx.health import build_health
    feed = {"adapter": "mako", "connected": True, "last_message_ns": 1, "pending_unsubscribes": 1}
    h = build_health(store, PositionLedger(store), PaperWallet(store, 1_000), [feed], {}, now_ns=2)
    assert h["status"] == "degraded"


# ---- R3-3: failed applies were counted as state latency ---------------------------------
def test_state_latency_is_recorded_only_when_the_apply_succeeds(store):
    from jinx.core.bus import EventBus, Lane

    core = CoreState(TokenRegistry())
    real = core.tokens.apply
    core.tokens.apply = lambda e: (_ for _ in ()).throw(RuntimeError("apply failed"))
    bus = EventBus()
    bus.subscribe(Lane.FAST, core.apply, name="core_state")

    async def main():
        task = asyncio.create_task(bus.run())
        await bus.publish(make_trade(mint="A"))
        await bus.drain()
        failed = (bus.latency(Lane.FAST).summary()["n"], core.receive_latency().summary()["n"],
                  dict(bus.stats["handler_errors"]))
        core.tokens.apply = real
        await bus.publish(make_trade(mint="A"))
        await bus.drain()
        task.cancel()
        return failed
    assert asyncio.run(main()) == (0, 0, {"core_state": 1})
    assert core.receive_latency().summary()["n"] == 1   # counted once, when the state was actually applied
