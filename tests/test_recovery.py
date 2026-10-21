"""Restart recovery and health checks."""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from jinx.core_state import CoreState
from jinx.health import build_health
from jinx.ledger import PositionLedger, Status
from jinx.modes import Mode, set_mode
from jinx.paper import PaperWallet
from jinx.recovery import recover
from jinx.registry import TokenRegistry
from jinx.store import Store
from tests.conftest import make_create, make_trade

ROOT = Path(__file__).resolve().parent.parent


def open_state(path):
    s = Store(path)
    return s, CoreState(TokenRegistry())


def _open_position(s, reg, mint="A", intent="I1"):
    """A filled paper position bought on the token's current reserves."""
    from jinx.execution import EntryIntent, ExecutionGateway
    from jinx.paper import CurveFees, PaperExecutor
    reg.apply(make_create(mint=mint))
    reg.apply(make_trade(mint=mint, v_sol=32_000_000_000, v_tokens=1_000_000_000_000_000))
    ledger, wallet = PositionLedger(s), PaperWallet(s, 2_000_000_000)
    gw = ExecutionGateway(s, ledger, PaperExecutor(s, ledger, wallet, reg, CurveFees(95, 5), 5_000))
    set_mode(s, Mode.AUTO)
    r = gw.submit_entry(EntryIntent(intent, mint, 100_000_000, origin="test"))
    assert r.ok, r
    return ledger, wallet, r.position_id


def test_restart_keeps_positions_fills_and_wallet_and_starts_the_market_view_empty(tmp_path):
    """Owner storage repair 2026-10-21: market chatter is transient; trading records survive a restart."""
    path = tmp_path / "j.db"
    s, core = open_state(path)
    ledger, wallet, pid = _open_position(s, core.tokens)
    held, balance = ledger.get(pid).token_amount, wallet.balance()
    s.close()                                                     # restart
    s, core = open_state(path)
    ledger, wallet = PositionLedger(s), PaperWallet(s, 2_000_000_000)
    report = recover(ledger, wallet)
    assert report.open_positions == 1 and report.wallet_reconciled
    assert ledger.get(pid).status is Status.OPEN and ledger.get(pid).token_amount == held
    assert wallet.balance() == balance
    assert len(core.tokens) == 0                                  # the market view starts empty: nothing replayed
    tables = {r[0] for r in s.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"events", "tokens", "token_traders", "wallets"}
    s.close()


def test_hard_kill_during_an_exit_with_an_open_position_loses_nothing(tmp_path):
    """Restart with active positions (owner order §4): a SIGKILL-like death inside the exit leaves the position
    open with its full quantity and its exit owner, and the wallet reconciles."""
    path = tmp_path / "j.db"
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from jinx.store import Store
        from jinx.registry import TokenRegistry
        from tests.test_recovery import _open_position
        s = Store({str(path)!r})
        ledger, wallet, pid = _open_position(s, TokenRegistry())
        print(pid, ledger.get(pid).token_amount, wallet.balance(), flush=True)
        ledger.begin_exit(pid, "X1")
        os._exit(9)                                               # dies before any sell fill
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 9, proc.stderr
    pid, held, balance = (int(x) for x in proc.stdout.split())
    s, _ = open_state(path)
    ledger, wallet = PositionLedger(s), PaperWallet(s, 2_000_000_000)
    report = recover(ledger, wallet)
    assert report.reopened_exits == 1 and report.wallet_reconciled
    pos = ledger.get(pid)
    assert pos.status is Status.OPEN and pos.token_amount == held and wallet.balance() == balance
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 0
    s.close()


def test_unfilled_paper_reservation_is_released_and_unfinished_exit_returns_to_open(tmp_path):
    from jinx.ledger import Fill
    from jinx.core.events import Side
    from jinx.ops.entry_slots import set_entry_slots
    s, core = open_state(tmp_path / "j.db")
    set_mode(s, Mode.AUTO)
    set_entry_slots(s, 2, owner_order=True)
    led = PositionLedger(s)
    stuck_open = led.reserve_entry("I1", "A", budget_lamports=100).position_id
    exiting = led.reserve_entry("I2", "B", budget_lamports=100).position_id
    led.record_buy_fill(exiting, Fill("F2", Side.BUY, 10, 100, 1, 5, True, 1))
    led.begin_exit(exiting, "X2")
    report = recover(led, PaperWallet(s, 1_000))
    assert led.get(stuck_open).status is Status.FAILED
    assert led.get(exiting).status is Status.OPEN and led.get(exiting).token_amount == 10
    assert (report.released_unfilled, report.reopened_exits) == (1, 1)
    s.close()


def test_recovery_reports_a_paper_wallet_that_does_not_reconcile(tmp_path):
    s, core = open_state(tmp_path / "j.db")
    w = PaperWallet(s, 1_000)
    s.conn.execute("UPDATE paper_wallet SET balance_lamports = 999 WHERE id = 1")  # corruption
    report = recover(PositionLedger(s), w)
    assert report.wallet_reconciled is False
    s.close()


def test_health_is_ok_only_when_store_wallet_and_feed_are_healthy(tmp_path):
    s, core = open_state(tmp_path / "j.db")
    led, w = PositionLedger(s), PaperWallet(s, 1_000)
    feed_up = {"adapter": "mako", "connected": True, "last_message_ns": 1}
    h = build_health(s, led, w, adapters=[feed_up], bus_stats={}, now_ns=2)
    assert h["status"] == "ok"
    assert h["mode"] == "OFF" and h["execution_mode"] == "PAPER"
    assert h["slots"] == {"max": 1, "active": 0}
    h = build_health(s, led, w, adapters=[dict(feed_up, connected=False)], bus_stats={}, now_ns=2)
    assert h["status"] == "degraded"
    s.conn.execute("UPDATE paper_wallet SET balance_lamports = 1 WHERE id = 1")
    assert build_health(s, led, w, adapters=[feed_up], bus_stats={}, now_ns=2)["status"] == "failed"
    s.close()
