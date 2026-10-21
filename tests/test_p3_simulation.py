"""Phase 3 required simulations (CLAUDE.md "PHASE 3 — Required simulation") that are not covered elsewhere, and the
replay of real captured events through the whole engine.

Covered elsewhere: early momentum entries, failed safety, insufficient balance, failed quotes, dropped confirmations,
repeated sell signals, profitable divergence exits, loss exits, Confluence Collapse, restart with open positions
(tests/test_p3_engine_paper_trade.py, test_p3_confluence.py, test_p3_execution.py, test_p3_executioner.py,
test_bigmac_integration.py).
"""
from pathlib import Path

import pytest

from jinx import config
from jinx.exits.policy import BigMacPaperConfig
from jinx.ledger import Status
from tests.conftest import make_trade
from tests.test_bigmac_integration import FIXTURE, K, Rig, _v_sol_for_value
from tests.test_p3_execution import MS, at, delayed_rig
from jinx.modes import Mode, set_mode

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "tests" / "fixtures" / "pumpapi-live-20261021.jsonl.gz"


def test_replay_of_real_captured_events_through_the_whole_engine():
    import importlib.util
    spec = importlib.util.spec_from_file_location("phase3_paper_replay", ROOT / "scripts" / "phase3_paper_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.run_replay(CAPTURE, delay_ms=0)
    assert out["handler_errors"] == {} and out["pending_fills_left"] == 0
    assert out["detections"] == 38                                   # same as the Phase 2 replay of this capture
    entries = out["entry_decisions"]
    assert entries.get("entered", 0) == 1 and entries.get("SLOT_LIMIT", 0) == 2   # simultaneous candidates: one slot
    totals = out["report"]["totals"]
    assert totals["wallet_reconciled"] and totals["actual_fills"] == 0
    [t] = out["report"]["trades"]
    assert t["tokens_bought"] == t["tokens_sold"] + t["tokens_held"]
    assert t["entry_latency"]["signal_to_decision_ms"] is not None
    assert "PASS for every screened candidate" in out["stand_ins"]["safety"]      # the stand-ins are labelled


def test_liquidity_collapse_during_the_fill_delay_is_paid_in_the_fill(tmp_path):
    rig = delayed_rig(tmp_path, 400)
    from tests.conftest import make_create
    from jinx.execution import EntryIntent
    rig.tokens.apply(make_create(mint="MINT"))
    at(rig, "MINT", 30_000_000_000, 1_000 * MS)
    rig.gateway.submit_entry(EntryIntent("e", "MINT", 100_000_000, origin="AUTO", exit_policy="BIGMAC_PAPER",
                                         exit_policy_config=FIXTURE.to_dict()))
    at(rig, "MINT", 30_000_000_000, 1_500 * MS)
    pid = rig.ledger.all()[0].position_id
    entry = rig.ledger.entry_basis(pid)
    v = _v_sol_for_value(rig, pid, entry * 79 // 100)
    at(rig, "MINT", v, 2_000 * MS)                                    # the stop is decided at this price
    at(rig, "MINT", v // 2, 2_500 * MS)                               # the curve is drained before the fill
    pos = rig.ledger.get(pid)
    assert pos.status is Status.CLOSED
    fill = rig.store.conn.execute("SELECT quote_lamports, sol_lamports FROM fills WHERE side='sell'").fetchone()
    assert fill["sol_lamports"] < fill["quote_lamports"] // 2         # the fill paid the deterioration
    assert pos.realized_pnl_lamports < -(entry * 50 // 100)
    rig.close()


def test_rapid_reversal_sells_once_on_the_first_qualifying_update(tmp_path):
    rig = Rig(tmp_path / "jinx.db")
    set_mode(rig.store, Mode.AUTO)
    pid = rig.buy(cfg=BigMacPaperConfig(0, 20, 12, 20, label="TEST_FIXTURE_reversal", set_by="test"))
    entry = rig.ledger.entry_basis(pid)
    up = _v_sol_for_value(rig, pid, entry * 130 // 100)
    rig.price("MINT", up)                                             # spike: the trail is armed
    d = rig.price("MINT", 25_000_000_000)                             # crash in one update: below stop AND trail
    assert d.reason_code == "stop_loss"                               # BigMac's order: the stop is checked first
    for v in (20_000_000_000, 40_000_000_000):
        assert rig.price("MINT", v) is None                           # nothing left to sell, nothing sold twice
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills WHERE side='sell'").fetchone()[0] == 1
    rig.close()


def test_a_late_out_of_order_event_never_moves_the_price_backwards(tmp_path):
    rig = Rig(tmp_path / "jinx.db")
    set_mode(rig.store, Mode.AUTO)
    pid = rig.buy()
    entry = rig.ledger.entry_basis(pid)
    high = _v_sol_for_value(rig, pid, entry * 110 // 100)
    newer = make_trade(mint="MINT", v_sol=high, v_tokens=K // high, recv_ns=50 * 10 ** 9, seq=5_000)
    rig.tokens.apply(newer)
    rig.monitor.on_event(newer)
    crash = _v_sol_for_value(rig, pid, entry * 50 // 100)
    late = make_trade(mint="MINT", v_sol=crash, v_tokens=K // crash, recv_ns=40 * 10 ** 9, seq=4_000)  # older event
    rig.tokens.apply(late)
    assert rig.tokens.get("MINT").v_sol_lamports == high              # the registry keeps the newest reserves
    assert rig.monitor.on_event(late) is None                         # so no stop on a stale price
    assert rig.ledger.get(pid).status is Status.OPEN
    rig.close()
