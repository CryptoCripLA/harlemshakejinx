"""Paper wallet, paper fills and the single order gateway with its real-money boundary."""
import random
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from jinx.core.events import Side
from jinx.execution import EntryIntent, ExecutionGateway, ExitIntent, LiveExecutionUnavailable
from jinx.ledger import PositionLedger, Status
from jinx.modes import EXECUTION_KEY, Mode, set_mode
from jinx.paper import (CurveFees, InsufficientBalance, PaperExecutor, PaperWallet, curve_sol_for_budget,
                        curve_tokens_out, sell_proceeds)
from jinx.registry import TokenRegistry
from tests.conftest import make_create, make_trade

ROOT = Path(__file__).resolve().parent.parent
D3AD_SRC = ROOT / "upstream" / "d3ad" / "shred-sniper" / "sniper" / "src"
FEES = CurveFees(fee_bps=95, creator_fee_bps=5)  # D3AD pumpfun.rs test params ("live mainnet values at the time of writing")
START = 2_000_000_000
NET_FEE = 5_000


# ---- differential: JINX curve math vs D3AD's own Rust source ------------------
def _extract(path: Path, first: int, last: int, must_start: str) -> str:
    lines = path.read_text().splitlines()[first - 1:last]
    assert must_start in lines[0] or must_start in "\n".join(lines[:3]), f"pinned span moved in {path.name}"
    return "\n".join(lines)


@pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not installed")
def test_curve_math_matches_d3ad_source_exactly(tmp_path):
    pumpfun = D3AD_SRC / "pumpfun.rs"
    position = D3AD_SRC / "position.rs"
    harness = "\n".join([
        "#![allow(dead_code)]",
        _extract(pumpfun, 396, 403, "#[derive(Debug, Clone, Copy)]"),   # struct CurveParams
        _extract(pumpfun, 405, 428, "/// What to put in the buy"),       # struct BuyPlan
        _extract(pumpfun, 430, 529, "impl CurveParams {"),               # initial_buy_price, plan_buy, ...
        _extract(position, 627, 646, "/// Lamports out for selling"),    # sell_proceeds
        """
use std::io::BufRead;
fn main() {
    for line in std::io::stdin().lock().lines() {
        let v: Vec<u64> = line.unwrap().split_whitespace().map(|x| x.parse().unwrap()).collect();
        let (vsol, vtok, budget, amount, fee, cfee) = (v[0], v[1], v[2], v[3], v[4], v[5]);
        let c = CurveParams { initial_virtual_sol_reserves: vsol, initial_virtual_token_reserves: vtok,
            initial_real_token_reserves: u64::MAX, fee_basis_points: fee, creator_fee_basis_points: cfee };
        let plan = c.plan_buy(0, budget, 0, 0);
        println!("{} {}", plan.amount, sell_proceeds(&c, vsol, vtok, amount));
    }
}
""",
    ])
    src = tmp_path / "harness.rs"
    src.write_text(harness)
    exe = tmp_path / "harness"
    subprocess.run(["rustc", "-O", "-o", str(exe), str(src)], check=True, capture_output=True)
    rng = random.Random(20261020)
    cases = []
    for _ in range(2000):
        vsol = rng.randint(1_000_000_000, 90_000_000_000)
        vtok = rng.randint(200_000_000_000_000, 1_073_000_000_000_000)
        cases.append((vsol, vtok, rng.randint(0, 5_000_000_000), rng.randint(0, vtok), rng.randint(0, 300), rng.randint(0, 100)))
    cases += [(30_000_000_000, 1_073_000_000_000_000, 0, 0, 95, 5), (1, 1, 1, 1, 0, 0)]
    out = subprocess.run([str(exe)], input="\n".join(" ".join(map(str, c)) for c in cases), capture_output=True,
                         text=True, check=True).stdout.split("\n")
    for case, line in zip(cases, out):
        vsol, vtok, budget, amount, fee, cfee = case
        fees = CurveFees(fee, cfee)
        tokens = curve_tokens_out(vsol, vtok, curve_sol_for_budget(budget, fees.total_bps))
        net, _ = sell_proceeds(vsol, vtok, amount, fees.total_bps)
        assert f"{tokens} {net}" == line, f"mismatch for {case}"


def test_round_trip_at_flat_price_loses_only_fee_shaped_amount():
    # Python port of D3AD position.rs `selling_back_immediately_loses_exactly_the_fees`
    spend = 1_000_000_000
    vsol, vtok = 30_000_000_000, 1_073_000_000_000_000
    tokens = curve_tokens_out(vsol, vtok, curve_sol_for_budget(spend, FEES.total_bps))
    out, _ = sell_proceeds(vsol + spend, vtok - tokens, tokens, FEES.total_bps)
    assert out < spend
    assert 100 <= (spend - out) * 10_000 // spend <= 400


def test_degenerate_curve_inputs_are_zero():
    assert sell_proceeds(0, 1, 1, 100) == (0, 0)
    assert sell_proceeds(1, 0, 1, 100) == (0, 0)
    assert sell_proceeds(1, 1, 0, 100) == (0, 0)
    assert curve_tokens_out(30, 100, 0) == 0


# ---- paper wallet -------------------------------------------------------------
def test_paper_wallet_entries_apply_once_and_reconcile(store):
    w = PaperWallet(store, START)
    assert w.apply("e1", -100, "buy") is True
    assert w.apply("e1", -100, "buy") is False
    w.apply("e2", 40, "sell")
    assert w.balance() == START - 60
    assert w.reconcile().ok


def test_paper_wallet_never_goes_negative(store):
    w = PaperWallet(store, 100)
    with pytest.raises(InsufficientBalance):
        w.apply("e1", -101, "buy")
    assert w.balance() == 100


def test_paper_wallet_is_not_reset_by_a_new_starting_amount(tmp_path):
    from jinx.store import Store
    s = Store(tmp_path / "j.db")
    PaperWallet(s, START).apply("e1", -500, "buy")
    assert PaperWallet(s, 9 * START).balance() == START - 500
    s.close()


# ---- gateway + paper executor -------------------------------------------------
@pytest.fixture
def rig(store):
    set_mode(store, Mode.AUTO)
    reg = TokenRegistry(store)
    reg.apply(make_create(mint="A"))
    reg.apply(make_trade(mint="A", v_sol=32_000_000_000, v_tokens=1_000_000_000_000_000))
    ledger = PositionLedger(store)
    wallet = PaperWallet(store, START)
    paper = PaperExecutor(store, ledger, wallet, reg, FEES, network_fee_lamports=NET_FEE)
    return store, reg, ledger, wallet, ExecutionGateway(store, ledger, paper)


def test_paper_buy_opens_position_and_debits_wallet(rig):
    store, reg, ledger, wallet, gw = rig
    r = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=100_000_000))
    pos = ledger.get(r.position_id)
    assert pos.status is Status.OPEN and pos.simulated
    expected = curve_tokens_out(32_000_000_000, 1_000_000_000_000_000, curve_sol_for_budget(100_000_000, 100))
    assert pos.token_amount == expected
    assert wallet.balance() == START - 100_000_000 - NET_FEE
    assert wallet.reconcile().ok


def test_replayed_entry_intent_is_not_executed_twice(rig):
    store, reg, ledger, wallet, gw = rig
    gw.submit_entry(EntryIntent("E1", "A", budget_lamports=100_000_000))
    gw.submit_entry(EntryIntent("E1", "A", budget_lamports=100_000_000))
    assert wallet.balance() == START - 100_000_000 - NET_FEE
    assert len(ledger.all()) == 1


def test_buy_without_a_quote_fails_cleanly_and_frees_the_slot(rig):
    store, reg, ledger, wallet, gw = rig
    r = gw.submit_entry(EntryIntent("E1", "UNSEEN", budget_lamports=1_000))
    assert not r.ok and r.reason == "NO_QUOTE"
    assert ledger.active_slots() == 0
    assert wallet.balance() == START


def test_insufficient_paper_balance_fails_without_touching_the_wallet(rig):
    store, reg, ledger, wallet, gw = rig
    r = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=START))  # plus network fee > balance
    assert not r.ok and r.reason == "INSUFFICIENT_BALANCE"
    assert wallet.balance() == START and ledger.active_slots() == 0


def test_full_round_trip_realized_pnl_equals_wallet_change(rig):
    store, reg, ledger, wallet, gw = rig
    pid = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=100_000_000)).position_id
    reg.apply(make_trade(mint="A", v_sol=40_000_000_000, v_tokens=800_000_000_000_000))  # curve moved up
    r = gw.submit_exit(ExitIntent("X1", pid))
    assert r.ok
    pos = ledger.get(pid)
    assert pos.status is Status.CLOSED and pos.token_amount == 0
    assert wallet.balance() - START == pos.realized_pnl_lamports
    assert wallet.reconcile().ok


def test_exits_still_run_when_mode_is_off(rig):
    store, reg, ledger, wallet, gw = rig
    pid = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=100_000_000)).position_id
    set_mode(store, Mode.OFF)
    assert not gw.submit_entry(EntryIntent("E2", "A", budget_lamports=1)).ok
    assert gw.submit_exit(ExitIntent("X1", pid)).ok
    assert ledger.get(pid).status is Status.CLOSED


def test_live_execution_mode_is_refused_in_phase_1(rig):
    store, reg, ledger, wallet, gw = rig
    store.set_setting(EXECUTION_KEY, "LIVE")
    with pytest.raises(LiveExecutionUnavailable):
        gw.submit_entry(EntryIntent("E1", "A", budget_lamports=100_000_000))
    assert ledger.all() == [] and wallet.balance() == START


def test_paper_execution_makes_no_network_connection(rig, monkeypatch):
    store, reg, ledger, wallet, gw = rig
    def refuse(*a, **k):
        raise AssertionError("paper execution tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    pid = gw.submit_entry(EntryIntent("E1", "A", budget_lamports=100_000_000)).position_id
    assert gw.submit_exit(ExitIntent("X1", pid)).ok


def test_core_package_contains_no_signing_or_submission_code():
    forbidden = ("Keypair", "sign_transaction", "send_transaction", "sendTransaction", "PRIVATE_KEY",
                 "PumpPortal(", ".swap(", "multiSwap")
    offenders = []
    for path in (ROOT / "jinx").rglob("*.py"):
        if "adapters" in path.parts:
            continue  # adapters are policed by their own isolation tests
        text = path.read_text()
        offenders += [f"{path.relative_to(ROOT)}: {f}" for f in forbidden if f in text]
    assert offenders == []
