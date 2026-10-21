"""The whole HEAR -> SIZE -> FIRE -> EXIT path inside the running engine, through its own bus (paper only).

STRATEGY=AUTO, EXECUTION=PAPER, LIVE_SIGNING=DISABLED. Early Momentum hears three distinct fee payers within 30 s on
a curve inside the owner's B-02 range; the safety screen (a labelled stand-in for GMGN + RugCheck, which are not
reachable from tests) passes; the fast path re-checks, sizes with labelled quarter-Kelly fixture settings and buys
on paper at once; later trades move the price; the position monitor, on the same fast lane, sells through BigMac's
rule with labelled fixture settings; the ledger and paper wallet reconcile to the lamport.
"""
from decimal import Decimal
from types import SimpleNamespace

from jinx.exits.policy import BIGMAC_PAPER, save_bigmac_config
from jinx.intel.early_momentum import Funding
from jinx.ledger import Status
from jinx.modes import Mode, set_mode
from jinx.sizing.kelly import save_kelly_config
from tests.pumpapi_msgs import buy, create, events, sell
from tests.test_app_event_intel import close, make_app
from tests.test_bigmac_integration import FIXTURE
from tests.test_p3_confluence import KELLY

PASS = SimpleNamespace(safety=SimpleNamespace(verdict=SimpleNamespace(value="PASS"), eligible=True, veto_reasons=[],
                                              facts={}, checks=[]), latency_ms=1.0)


def engine(tmp_path):
    app = make_app(tmp_path)
    app._trade_feed_live = lambda: True                       # the stream is live in this scenario

    async def safety_pass(mint, creator, priority=10):        # TEST stand-in for GMGN + RugCheck: PASS
        return PASS
    app.check_candidate = safety_pass
    app.safety = object()
    set_mode(app.store, Mode.AUTO)
    save_kelly_config(app.store, KELLY, by="test")
    save_bigmac_config(app.store, FIXTURE)
    for w, f in (("A", "F1"), ("B", "F2"), ("C", "F3")):
        app.early.record_funding(Funding(w, f, "test", 1))
    return app


def run(app, *phases):
    """Publish each phase's deliveries through the engine's own bus in ONE event loop, calling the phase's check
    (if any) once its events and background screening have finished."""
    import asyncio

    async def main():
        runner = asyncio.create_task(app.bus.run())
        out = []
        for deliveries, check in phases:
            for evs in deliveries:
                for ev in evs or ():
                    await app.bus.publish(ev)
            await app.bus.drain()
            for _ in range(200):
                if not app._background:
                    break
                await asyncio.sleep(0.01)
            out.append(check() if check else None)
        runner.cancel()
        return out
    return asyncio.run(main())


def launch():
    return [events(create(initial_buy=0.0, v_quote=30.0)), events(buy("A", 1, v_quote=31.0)),
            events(buy("B", 2, v_quote=32.0)), events(buy("C", 3, v_quote=33.0))]


def _reconciles(app, pid):
    pos = app.ledger.get(pid)
    rec = app.wallet.reconcile()
    assert rec.ok
    assert rec.balance_lamports == 2_000_000_000 - pos.cost_lamports - pos.network_fees_lamports + pos.proceeds_lamports
    return pos


def test_detection_to_paper_buy_to_take_profit_to_reconciliation(tmp_path):
    app = engine(tmp_path)
    up = [events(buy(f"X{i}", 4 + i, v_quote=33.0 + 1.5 * i)) for i in range(1, 9)]
    [opened, _] = run(app, (launch(), lambda: list(app.ledger.active())), (up, None))
    assert app.bus.stats["handler_errors"] == {}
    [pos] = opened
    assert pos.status is Status.OPEN and pos.strategy == "AUTO" and pos.exit_policy == BIGMAC_PAPER
    assert app.candidates["MintApump"]["entry_decision"]["ok"] is True
    buy_stages = [e["stage"] for e in app.ledger.order_events(pos.entry_intent_id)]
    assert buy_stages == ["DECIDED", "QUOTED", "SUBMITTED", "FILLED"]
    # other traders pushed the price up: BigMac's take profit (TEST_FIXTURE 50 %) sold every held token
    closed = _reconciles(app, pos.position_id)
    assert closed.status is Status.CLOSED and closed.token_amount == 0 and closed.realized_pnl_lamports > 0
    ex = app.store.conn.execute("SELECT reason_code, exit_policy FROM exit_intents WHERE position_id = ?",
                                (pos.position_id,)).fetchone()
    assert (ex["reason_code"], ex["exit_policy"]) == ("take_profit", BIGMAC_PAPER)
    assert app.bus.stats["handler_errors"] == {}
    close(app)


def test_detection_to_paper_buy_to_hard_stop(tmp_path):
    app = engine(tmp_path)
    down = [events(sell(f"S{i}", 4 + i, v_quote=33.0 - 0.8 * i)) for i in range(1, 6)]
    [[pos], _] = run(app, (launch(), lambda: list(app.ledger.active())), (down, None))
    closed = _reconciles(app, pos.position_id)
    assert closed.status is Status.CLOSED and closed.realized_pnl_lamports < 0
    assert app.store.conn.execute("SELECT reason_code FROM exit_intents").fetchone()[0] == "stop_loss"
    close(app)


def test_off_mode_hears_but_never_buys(tmp_path):
    app = engine(tmp_path)
    set_mode(app.store, Mode.OFF)
    run(app, (launch(), None))
    assert not app.ledger.all()
    assert app.candidates["MintApump"]["entry_decision"]["reason"] == "MODE_OFF"
    assert app.store.conn.execute("SELECT COUNT(*) FROM order_events").fetchone()[0] == 0
    close(app)


def test_unconfigured_sizing_hears_but_never_buys(tmp_path):
    app = engine(tmp_path)
    app.store.conn.execute("DELETE FROM settings WHERE key = 'sizing.kelly'")
    run(app, (launch(), None))
    assert not app.ledger.all()
    assert app.candidates["MintApump"]["entry_decision"]["reason"] == "SIZING_NOT_CONFIGURED"
    close(app)


def test_signal_to_decision_and_decision_to_submission_are_measured_apart_from_the_fill(tmp_path):
    app = engine(tmp_path)
    run(app, (launch(), None))
    [pos] = app.ledger.active()
    ev = {e["stage"]: e for e in app.ledger.order_events(pos.entry_intent_id)}
    assert pos.signal_ns is not None
    assert pos.signal_ns <= ev["DECIDED"]["ts_ns"] <= ev["SUBMITTED"]["ts_ns"] <= ev["FILLED"]["ts_ns"]
    assert ev["FILLED"]["simulated"] == 1                     # a paper fill: no landing time is claimed
    assert app.entries.stats["entered"] == 1
    close(app)
