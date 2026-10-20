"""Regression tests for the Codex Phase 1 review, round 1 (docs/audits/phase1-codex-20261020-round1.md).
Each test reproduces the reviewer's failure scenario."""
import sqlite3

import pytest

from jinx.core.events import Side
from jinx.execution import EntryIntent, ExecutionGateway, ExitIntent
from jinx.ledger import Fill, FillConflict, PositionLedger, Status
from jinx.modes import Mode, set_mode
from jinx.ops.entry_slots import set_entry_slots
from jinx.paper import CurveFees, PaperExecutor, PaperWallet
from jinx.recovery import recover
from jinx.core_state import CoreState
from jinx.registry import TokenRegistry, WalletRegistry
from tests.conftest import make_create, make_trade

START = 2_000_000_000


@pytest.fixture
def rig(store):
    set_mode(store, Mode.AUTO)
    reg = TokenRegistry(store)
    reg.apply(make_create(mint="A"))
    reg.apply(make_trade(mint="A", v_sol=32_000_000_000, v_tokens=1_000_000_000_000_000))
    ledger = PositionLedger(store)
    wallet = PaperWallet(store, START)
    paper = PaperExecutor(store, ledger, wallet, reg, CurveFees(95, 5), network_fee_lamports=5_000)
    return store, reg, ledger, wallet, ExecutionGateway(store, ledger, paper)


# ---- Finding 1: negative amounts manufacture capital and inventory ---------------
@pytest.mark.parametrize("budget", [-40_000_000_000, 0, 1.5, True])
def test_entry_budget_must_be_a_positive_integer(budget):
    with pytest.raises((ValueError, TypeError)):
        EntryIntent("E", "A", budget_lamports=budget)


@pytest.mark.parametrize("amount", [-1_000, 0, 2.0])
def test_exit_amount_must_be_positive_or_whole_position(amount):
    with pytest.raises((ValueError, TypeError)):
        ExitIntent("X", 1, token_amount=amount)
    assert ExitIntent("X", 1).token_amount is None


@pytest.mark.parametrize("field,value", [("token_amount", 0), ("token_amount", -5), ("sol_lamports", -1),
                                         ("protocol_fee_lamports", -1), ("network_fee_lamports", -1)])
def test_fills_reject_negative_or_empty_amounts(field, value):
    kw = dict(fill_id="F", side=Side.BUY, token_amount=10, sol_lamports=100, protocol_fee_lamports=1,
              network_fee_lamports=5, simulated=True, ts_ns=1)
    kw[field] = value
    with pytest.raises(ValueError):
        Fill(**kw)


def test_database_refuses_negative_fills_even_if_code_is_bypassed(rig):
    store, *_ = rig
    store.conn.execute("INSERT INTO positions (entry_intent_id, mint, status, simulated, opened_ns, entry_budget_lamports)"
                       " VALUES ('I','A','OPEN',1,1,1)")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT INTO fills (fill_id, position_id, side, token_amount, sol_lamports,"
                           " protocol_fee_lamports, network_fee_lamports, simulated, ts_ns)"
                           " VALUES ('F', 1, 'sell', -1000, 0, 0, 0, 1, 1)")


def test_wallet_cannot_be_inflated_through_the_gateway(rig):
    store, reg, ledger, wallet, gw = rig
    with pytest.raises(ValueError):
        gw.submit_entry(EntryIntent("neg", "A", budget_lamports=-40_000_000_000))
    assert wallet.balance() == START and ledger.all() == []


# ---- Finding 2: retrying a failed entry reported success --------------------------
def test_retrying_a_failed_entry_returns_the_original_failure(rig):
    store, reg, ledger, wallet, gw = rig
    first = gw.submit_entry(EntryIntent("E1", "UNSEEN", budget_lamports=1_000))
    assert not first.ok and first.reason == "NO_QUOTE"
    again = gw.submit_entry(EntryIntent("E1", "UNSEEN", budget_lamports=1_000))
    assert not again.ok and again.reason == "NO_QUOTE"


def test_retrying_an_entry_released_by_recovery_is_not_reported_as_executed(rig):
    store, reg, ledger, wallet, gw = rig
    ledger.reserve_entry("E1", "A", budget_lamports=1_000)  # reserved, then the process died
    recover(store, CoreState(store, reg, WalletRegistry(store)), ledger, wallet)
    r = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000))
    assert not r.ok and r.reason == "RECOVERED_UNFILLED"


@pytest.mark.parametrize("mint,budget", [("A", 2_000), ("B", 1_000)])
def test_reusing_an_entry_intent_with_different_parameters_is_refused(rig, mint, budget):
    store, reg, ledger, wallet, gw = rig
    assert gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000)).ok
    r = gw.submit_entry(EntryIntent("E1", mint, budget_lamports=budget))
    assert not r.ok and r.reason == "INTENT_CONFLICT"


def test_successful_entry_replay_is_reported_as_already_executed(rig):
    store, reg, ledger, wallet, gw = rig
    gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000))
    r = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000))
    assert r.ok and r.reason == "ALREADY_EXECUTED"


# ---- Finding 3: exit ownership and fill identity ---------------------------------
def test_reused_exit_id_on_another_position_is_refused_not_faked(rig):
    store, reg, ledger, wallet, gw = rig
    p1 = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000)).position_id
    assert gw.submit_exit(ExitIntent("X", p1)).ok
    p2 = gw.submit_entry(EntryIntent("E2", "A", budget_lamports=1_000_000)).position_id
    held = ledger.get(p2).token_amount
    r = gw.submit_exit(ExitIntent("X", p2))
    assert not r.ok and r.reason == "INTENT_CONFLICT"
    pos = ledger.get(p2)
    assert pos.status is Status.OPEN and pos.token_amount == held


def test_a_second_exit_cannot_take_over_an_exit_in_progress(rig):
    store, reg, ledger, wallet, gw = rig
    p = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000)).position_id
    assert ledger.begin_exit(p, "X").ok          # X owns the exit, its sell is in flight
    r = gw.submit_exit(ExitIntent("Y", p))
    assert not r.ok and r.reason == "EXIT_IN_PROGRESS"
    assert ledger.get(p).status is Status.PENDING_CLOSE


def test_partial_exit_returns_the_position_to_open_for_the_next_exit(rig):
    store, reg, ledger, wallet, gw = rig
    p = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000)).position_id
    half = ledger.get(p).token_amount // 2
    assert gw.submit_exit(ExitIntent("X1", p, token_amount=half)).ok
    assert ledger.get(p).status is Status.OPEN
    assert gw.submit_exit(ExitIntent("X2", p)).ok
    assert ledger.get(p).status is Status.CLOSED


def test_replayed_partial_exit_after_restart_is_already_applied_and_not_stuck(rig):
    store, reg, ledger, wallet, gw = rig
    p = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000)).position_id
    half = ledger.get(p).token_amount // 2
    gw.submit_exit(ExitIntent("X1", p, token_amount=half))
    recover(store, CoreState(store, reg, WalletRegistry(store)), ledger, wallet)
    before = ledger.get(p).token_amount
    r = gw.submit_exit(ExitIntent("X1", p, token_amount=half))
    assert r.ok and r.reason == "ALREADY_APPLIED"
    pos = ledger.get(p)
    assert pos.status is Status.OPEN and pos.token_amount == before


def test_same_exit_id_with_a_different_amount_is_refused(rig):
    store, reg, ledger, wallet, gw = rig
    p = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=1_000_000)).position_id
    gw.submit_exit(ExitIntent("X1", p, token_amount=10))
    r = gw.submit_exit(ExitIntent("X1", p, token_amount=11))
    assert not r.ok and r.reason == "INTENT_CONFLICT"


def test_fill_id_collision_with_different_contents_raises(rig):
    store, reg, ledger, wallet, gw = rig
    set_entry_slots(store, 2, owner_order=True)
    p1 = ledger.reserve_entry("I1", "A", budget_lamports=100).position_id
    p2 = ledger.reserve_entry("I2", "B", budget_lamports=100).position_id
    f = Fill("SAME", Side.BUY, 10, 100, 1, 5, True, 1)
    ledger.record_buy_fill(p1, f)
    ledger.record_buy_fill(p1, f)  # true replay: ignored
    with pytest.raises(FillConflict):
        ledger.record_buy_fill(p2, f)


# ---- Finding 4: a transient persistence failure lost a live event ---------------------
def test_live_event_survives_a_transient_write_failure_through_makos_dispatcher():
    import asyncio, json
    from pathlib import Path
    import websockets
    from jinx.adapters.mako.feed import MakoPumpPortalFeed
    from jinx.core.bus import EventBus, Lane
    from tests.test_adapters import CREATE, BUY

    root = Path(__file__).resolve().parent.parent

    async def main():
        stored, failures = [], {"left": 2}
        def flaky(ev):
            if failures["left"]:
                failures["left"] -= 1
                raise OSError("database is locked")
            stored.append(ev.idempotency_key)
            return True
        async def server(ws):
            async for msg in ws:
                if json.loads(msg)["method"] == "subscribeNewToken":
                    await ws.send(json.dumps(CREATE))
                    await ws.send(json.dumps(BUY))
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            bus = EventBus(persist=flaky)
            feed = MakoPumpPortalFeed(bus, root / "upstream" / "mako", ws_url=f"ws://127.0.0.1:{port}",
                                      retry_initial_s=0.01)
            runner, task = asyncio.create_task(bus.run()), asyncio.create_task(feed.run())
            for _ in range(200):
                await asyncio.sleep(0.01)
                if len(stored) >= 3:
                    break
            task.cancel(); runner.cancel()
            return stored, feed.stats
    stored, stats = asyncio.run(main())
    assert len(stored) == 3                    # creation, creator buy, buyer trade: none lost
    assert stats["publish_retries"] == 2 and stats["ingest_stalled"] is False


# ---- Finding 6: health and supervision -------------------------------------------
def test_health_is_degraded_while_stored_events_wait_to_be_applied(store):
    from jinx.health import build_health
    ledger, wallet = PositionLedger(store), PaperWallet(store, 1_000)
    store.record_event(make_trade(mint="A"))  # stored, not yet applied
    feed = {"adapter": "mako", "connected": True, "last_message_ns": 1}
    assert build_health(store, ledger, wallet, [feed], {}, now_ns=2)["status"] == "degraded"


def test_health_is_degraded_while_ingestion_is_stalled_on_writes(store):
    from jinx.health import build_health
    feed = {"adapter": "mako", "connected": True, "last_message_ns": 1, "ingest_stalled": True}
    h = build_health(store, PositionLedger(store), PaperWallet(store, 1_000), [feed], {}, now_ns=2)
    assert h["status"] == "degraded"


def test_status_loop_survives_a_failing_tick(tmp_path):
    import asyncio
    from jinx import config
    from jinx.app import JinxApp
    app = JinxApp(config.load_settings(str(tmp_path)))
    calls = {"n": 0}
    async def tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("apply_pending failed")
    app._status_tick = tick

    async def main():
        loop = asyncio.create_task(app._status_loop(interval_s=0.01))
        await asyncio.sleep(0.1)
        loop.cancel()
    asyncio.run(main())
    assert calls["n"] >= 3 and app.status_errors == 1


def test_supervisor_restarts_a_crashed_feed_task(tmp_path):
    import asyncio
    from jinx import config
    from jinx.app import JinxApp
    app = JinxApp(config.load_settings(str(tmp_path)))

    class FakeFeed:
        runs = 0
        async def run(self):
            FakeFeed.runs += 1
            if FakeFeed.runs == 1:
                raise ConnectionError("feed crashed")
            await asyncio.sleep(3600)

    async def main():
        app.feed = FakeFeed()
        app._tasks = {"mako-feed": asyncio.create_task(app.feed.run())}
        await asyncio.sleep(0.01)
        app._supervise()
        await asyncio.sleep(0.01)
        alive = not app._tasks["mako-feed"].done()
        app._tasks["mako-feed"].cancel()
        return alive
    assert asyncio.run(main()) is True
    assert FakeFeed.runs == 2 and app.task_restarts == {"mako-feed": 1}
