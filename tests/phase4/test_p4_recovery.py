"""V09 position recovery after interruption; V12 balance reconciliation across it.

A real child process builds the engine on a durable ledger, trades on paper and is killed with os._exit (no cleanup,
no flush, no atexit: the same as SIGKILL or a power cut) at a chosen point: with an open position whose exit is
pending and a second buy reserved but not filled, or in the middle of a buy or sell fill. The parent then starts a new
engine on the same data directory, as systemd would, and checks what the owner requires: no lost position, no
phantom fill, no stranded exit owner, a paper wallet that reconciles to the lamport, and positions that can still be
exited. Two entry slots are used only as an explicitly labelled test fixture (owner rule: one slot by default, two
only by owner order; `set_entry_slots(..., owner_order=True)` is the owner's own command path)."""
import subprocess
import sys
import textwrap

import pytest

from jinx.execution import EntryIntent, ExitIntent
from jinx.ledger import Status
from tests.phase4 import harness as h

ROOT = h.FIXTURES.parent.parent

SETUP = """
import os, sys
sys.path.insert(0, {root!r})
from jinx.execution import EntryIntent, ExitIntent
from jinx.modes import Mode, set_mode
from jinx.ops.entry_slots import set_entry_slots
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import M, baseline_msgs
from tests.pumpapi_msgs import buy, create, events
app = h.build_app({data!r})
h.run(app, [events(m) for m in baseline_msgs()])
h.run(app, [events(create(mint="P4Secondpump", dev="DEV2", v_quote=31.0))])
set_mode(app.store, Mode.AUTO, by="phase4-test")
set_entry_slots(app.store, 2, owner_order=True)      # labelled test fixture, see module docstring
"""


def run_child(data_dir, body: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(SETUP.format(root=str(ROOT), data=str(data_dir))) + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)


def restart(data_dir):
    app = h.build_app(data_dir)                        # recovery runs inside _build_core, before any event
    return app


def test_hard_kill_with_an_open_position_a_pending_exit_and_an_unfilled_buy(tmp_path):
    proc = run_child(tmp_path, """
        r1 = app.gateway.submit_entry(EntryIntent("p4-entry-1", M, 50_000_000, origin="phase4-test"))
        assert r1.ok, r1
        claim = app.ledger.claim_exit("p4-exit-1", r1.position_id, None, "p%d:p4-exit-1:sell" % r1.position_id)
        assert claim.ok, claim                          # exit owner taken, sell not filled yet
        r2 = app.ledger.reserve_entry("p4-entry-2", "P4Secondpump", 20_000_000)
        assert r2.ok, r2                                # buy reserved, fill never arrives
        print("BALANCE", app.wallet.balance(), flush=True)
        os._exit(9)
    """)
    assert proc.returncode == 9, proc.stderr
    balance_at_kill = int(proc.stdout.split("BALANCE")[1])
    app = restart(tmp_path)
    rep = app.recovery
    assert rep.released_unfilled == 1 and rep.reopened_exits == 1 and rep.wallet_reconciled is True
    p1, p2 = app.ledger.all()
    assert p1.status is Status.OPEN and p1.token_amount > 0 and p1.note == "RECOVERED_UNFINISHED_EXIT"
    assert p2.status is Status.FAILED and p2.note == "RECOVERED_UNFILLED" and p2.cost_lamports == 0
    assert app.wallet.balance() == balance_at_kill                       # the unfilled buy cost nothing
    assert app.ledger.active_slots() == 1                                 # its slot was released
    # the market view is RAM only: after a restart the exit needs a fresh quote first, as in production
    from tests.phase4.test_p4_chad_entry_predicates import baseline_msgs
    from tests.pumpapi_msgs import events
    h.run(app, [events(m) for m in baseline_msgs()])
    again = app.gateway.submit_exit(ExitIntent("p4-exit-1", p1.position_id))   # the same intent, retried
    assert again.ok, again
    closed = app.ledger.get(p1.position_id)
    assert closed.status is Status.CLOSED and closed.token_amount == 0
    assert closed.realized_pnl_lamports == closed.proceeds_lamports - closed.cost_lamports - closed.network_fees_lamports
    rec = app.wallet.reconcile()
    assert rec.ok and app.wallet.balance() == balance_at_kill + closed.proceeds_lamports - 5_000
    h.close(app)


@pytest.mark.parametrize("where", ["buy", "sell"])
def test_a_hard_kill_inside_a_fill_leaves_no_phantom_fill_and_no_lost_money(tmp_path, where):
    proc = run_child(tmp_path, f"""
        from jinx.ledger import PositionLedger
        real_buy, real_sell = PositionLedger.record_buy_fill, PositionLedger.record_sell_fill
        def die(*a, **k):
            os._exit(9)          # after the wallet change, before the fill: inside one transaction
        start = app.wallet.balance()
        if "{where}" == "buy":
            PositionLedger.record_buy_fill = die
            print("BALANCE", start, flush=True)
            app.gateway.submit_entry(EntryIntent("p4-entry-1", M, 50_000_000, origin="phase4-test"))
        else:
            r = app.gateway.submit_entry(EntryIntent("p4-entry-1", M, 50_000_000, origin="phase4-test"))
            print("BALANCE", app.wallet.balance(), flush=True)
            PositionLedger.record_sell_fill = die
            app.gateway.submit_exit(ExitIntent("p4-exit-1", r.position_id))
        os._exit(1)              # not reached
    """)
    assert proc.returncode == 9, proc.stderr
    balance_before_fill = int(proc.stdout.split("BALANCE")[1])
    app = restart(tmp_path)
    [pos] = app.ledger.all()
    fills = app.store.conn.execute("SELECT side FROM fills WHERE position_id = ?", (pos.position_id,)).fetchall()
    assert app.wallet.balance() == balance_before_fill and app.wallet.reconcile().ok
    if where == "buy":
        assert pos.status is Status.FAILED and fills == []                 # no position appears without its fill
    else:
        assert pos.status is Status.OPEN and [f[0] for f in fills] == ["buy"]   # the tokens are still held
        assert pos.token_amount > 0 and pos.proceeds_lamports == 0
    h.close(app)


def test_restart_recovery_runs_before_any_event_and_is_idempotent(tmp_path):
    proc = run_child(tmp_path, """
        r1 = app.gateway.submit_entry(EntryIntent("p4-entry-1", M, 50_000_000, origin="phase4-test"))
        app.ledger.claim_exit("p4-exit-1", r1.position_id, None, "p%d:p4-exit-1:sell" % r1.position_id)
        os._exit(9)
    """)
    assert proc.returncode == 9, proc.stderr
    first = restart(tmp_path)
    snapshot = h.state_view(first)["ledger"], first.wallet.balance()
    h.close(first)
    second = restart(tmp_path)                                  # a second crash-restart changes nothing more
    assert second.recovery.reopened_exits == 0 and second.recovery.released_unfilled == 0
    assert (h.state_view(second)["ledger"], second.wallet.balance()) == snapshot
    h.close(second)


def test_after_a_restart_no_token_first_seen_before_it_can_pass_the_entry_rule(tmp_path):
    """The market view is RAM only (owner order 2026-10-21), so a restart forgets every token's history. A token whose
    launch happened before the restart has no provable peak afterwards and must stay blocked (B-02 §7); a token
    launched after the restart is judged on its own complete history."""
    from jinx.sim import load_capture
    from tests.phase4.test_p4_chad_entry_predicates import LiveFeed
    live = load_capture(h.FIXTURES / "pumpapi-live-20261021.jsonl.gz")
    half = len(live) // 2
    before = h.build_app(tmp_path)
    h.run(before, h.deliveries(live[:half], "pumpapi"))
    seen_before = {t.mint for t in before.core.tokens.all()}
    h.close(before)
    after = h.build_app(tmp_path)                                   # restart on the same data directory
    after.pumpapi = LiveFeed()
    assert len(after.core.tokens) == 0                              # the market view starts empty by design
    h.run(after, h.deliveries(live, "pumpapi")[half:])
    carried = [m for m in seen_before if after.core.tokens.get(m) is not None]
    assert carried, "the second half must trade some tokens first seen before the restart"
    assert not any(after.early.reserve_check(m, feed_live=True)["eligible"] for m in carried)
    h.close(after)
