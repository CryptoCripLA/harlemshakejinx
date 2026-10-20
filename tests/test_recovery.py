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
from jinx.registry import TokenRegistry, WalletRegistry
from jinx.store import Store
from tests.conftest import make_create, make_trade

ROOT = Path(__file__).resolve().parent.parent


def open_state(path):
    s = Store(path)
    return s, CoreState(s, TokenRegistry(s), WalletRegistry(s))


def test_event_stored_but_not_applied_before_a_crash_is_applied_once_on_restart(tmp_path):
    path = tmp_path / "j.db"
    s, core = open_state(path)
    ev = make_trade(mint="A", trader="W1")
    assert s.record_event(ev)          # persisted by the bus...
    s.close()                          # ...process dies before the fast-lane handler ran
    s, core = open_state(path)
    report = recover(s, core, PositionLedger(s), PaperWallet(s, 1_000))
    assert report.replayed_events == 1
    assert core.tokens.get("A").buys == 1 and core.wallets.get("W1").buys == 1
    assert recover(s, core, PositionLedger(s), PaperWallet(s, 1_000)).replayed_events == 0
    assert core.tokens.get("A").buys == 1
    s.close()


def test_applied_events_are_not_applied_again_after_restart(tmp_path):
    path = tmp_path / "j.db"
    s, core = open_state(path)
    for ev in (make_create(mint="A"), make_trade(mint="A")):
        s.record_event(ev)
        core.apply(ev)
    s.close()
    s, core = open_state(path)
    assert recover(s, core, PositionLedger(s), PaperWallet(s, 1_000)).replayed_events == 0
    assert core.tokens.get("A").buys == 1
    s.close()


def test_failed_apply_rolls_back_everything_and_stays_replayable(tmp_path):
    s, core = open_state(tmp_path / "j.db")
    ev = make_trade(mint="A", trader="W1")
    s.record_event(ev)
    original = core.wallets.apply
    core.wallets.apply = lambda e: (_ for _ in ()).throw(RuntimeError("disk hiccup"))
    with pytest.raises(RuntimeError):
        core.apply(ev)
    assert core.tokens.get("A") is None            # token update was rolled back in memory too
    assert len(s.unapplied_events()) == 1          # and the event is still waiting
    core.wallets.apply = original
    core.apply(ev)
    assert core.tokens.get("A").buys == 1 and s.unapplied_events() == []
    s.close()


def test_hard_kill_inside_a_write_loses_nothing_and_double_counts_nothing(tmp_path):
    path = tmp_path / "j.db"
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from jinx.store import Store
        from jinx.core_state import CoreState
        from jinx.registry import TokenRegistry, WalletRegistry
        from tests.conftest import make_trade
        s = Store({str(path)!r})
        core = CoreState(s, TokenRegistry(s), WalletRegistry(s))
        events = [make_trade(mint="K", trader=f"W{{i}}", sig=f"K{{i}}", seq=i, recv_ns=i) for i in range(1, 11)]
        real = core.wallets.apply
        def dying(e):
            if e.signature == "K7":
                os._exit(9)          # SIGKILL-like: no cleanup, mid-transaction
            return real(e)
        core.wallets.apply = dying
        for e in events:
            s.record_event(e)
            core.apply(e)
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 9, proc.stderr
    s, core = open_state(path)
    assert s.event_count() == 7                     # K1..K7 were persisted
    report = recover(s, core, PositionLedger(s), PaperWallet(s, 1_000))
    assert report.replayed_events == 1              # K7 was persisted but its apply died
    assert core.tokens.get("K").buys == 7           # nothing lost, nothing counted twice
    s.close()


def test_sequence_numbers_resume_after_the_last_stored_event(tmp_path):
    from jinx.core.bus import EventBus
    s, core = open_state(tmp_path / "j.db")
    s.record_event(make_trade(seq=500, recv_ns=500))
    bus = EventBus()
    report = recover(s, core, PositionLedger(s), PaperWallet(s, 1_000), bus=bus)
    assert bus.next_seq() > 500 and report.resumed_seq_after == 500
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
    report = recover(s, core, led, PaperWallet(s, 1_000))
    assert led.get(stuck_open).status is Status.FAILED
    assert led.get(exiting).status is Status.OPEN and led.get(exiting).token_amount == 10
    assert (report.released_unfilled, report.reopened_exits) == (1, 1)
    s.close()


def test_recovery_reports_a_paper_wallet_that_does_not_reconcile(tmp_path):
    s, core = open_state(tmp_path / "j.db")
    w = PaperWallet(s, 1_000)
    s.conn.execute("UPDATE paper_wallet SET balance_lamports = 999 WHERE id = 1")  # corruption
    report = recover(s, core, PositionLedger(s), w)
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


def test_applying_the_same_stored_event_twice_counts_it_once(tmp_path):
    s, core = open_state(tmp_path / "j.db")
    ev = make_trade(mint="A", trader="W1")
    s.record_event(ev)
    core.apply(ev)
    core.apply(ev)  # e.g. recovery replay racing the live bus
    assert core.tokens.get("A").buys == 1 and core.wallets.get("W1").buys == 1
    s.close()
