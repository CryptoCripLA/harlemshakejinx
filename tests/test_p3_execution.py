"""Phase 3 execution: one gateway, intent idempotency, quote -> submit -> confirm, delayed fills, uncertain and dropped
outcomes, reconciliation of fills and balances, no duplicate positions. Paper only.

The paper fill delay is a labelled simulation setting (how long after the decision the fill is priced), never a
claim about GMGN's real latency (GMGN states "under 0.3 seconds", unmeasured by JINX)."""
import pytest

from jinx import config
from jinx.execution import (EntryIntent, ExecutionGateway, ExitIntent, OrderState, gmgn_order_outcome)
from jinx.ledger import PositionLedger, Status
from jinx.modes import Mode, set_mode
from jinx.paper import PaperExecutor, PaperWallet, sell_proceeds
from jinx.recovery import recover
from tests.conftest import make_create, make_trade
from tests.test_bigmac_integration import K, Rig

MS = 1_000_000


def delayed_rig(tmp_path, delay_ms):
    r = Rig(tmp_path / "jinx.db")
    r.gateway = ExecutionGateway(r.store, r.ledger, r.paper, fill_delay_ns=delay_ms * MS)
    r.monitor.gateway = r.gateway
    set_mode(r.store, Mode.AUTO)
    return r


def at(rig, mint, v_sol, t_ns):
    ev = make_trade(mint=mint, v_sol=v_sol, v_tokens=K // v_sol, recv_ns=t_ns, seq=t_ns, block_time=None)
    rig.tokens.apply(ev)
    rig.monitor.on_event(ev)
    return ev


def test_a_delayed_exit_fills_at_the_later_price_and_records_the_slippage(tmp_path):
    rig = delayed_rig(tmp_path, 500)
    rig.tokens.apply(make_create(mint="MINT"))
    at(rig, "MINT", 30_000_000_000, 1_000 * MS)
    r = rig.gateway.submit_entry(EntryIntent("e", "MINT", 100_000_000, origin="AUTO"))
    assert not r.ok and r.reason == "PENDING_FILL"                      # submitted, not filled
    assert rig.ledger.get(r.position_id).status is Status.PENDING_OPEN
    at(rig, "MINT", 30_100_000_000, 1_200 * MS)                         # 200 ms later: still waiting
    assert rig.ledger.get(r.position_id).status is Status.PENDING_OPEN
    at(rig, "MINT", 30_500_000_000, 1_600 * MS)                         # 600 ms: filled at THESE reserves
    pos = rig.ledger.get(r.position_id)
    assert pos.status is Status.OPEN
    held = pos.token_amount
    q = sell_proceeds(30_500_000_000, K // 30_500_000_000, held, 100)[0]
    d = rig.gateway.submit_exit(ExitIntent("x", pos.position_id, source_strategy="AUTO", quote_lamports=q,
                                           event_timestamp=1_600 * MS))
    assert d.reason == "PENDING_FILL" and rig.ledger.get(pos.position_id).status is Status.PENDING_CLOSE
    at(rig, "MINT", 29_000_000_000, 2_200 * MS)                         # the price fell during the delay
    pos = rig.ledger.get(pos.position_id)
    assert pos.status is Status.CLOSED
    fill = rig.store.conn.execute("SELECT quote_lamports, sol_lamports FROM fills WHERE side='sell'").fetchone()
    assert fill["quote_lamports"] - fill["sol_lamports"] > 0           # stale-price slippage, recorded
    assert rig.wallet.reconcile().ok
    rig.close()


def test_a_dropped_confirmation_is_never_booked_and_restart_recovers(tmp_path):
    rig = delayed_rig(tmp_path, 500)
    rig.tokens.apply(make_create(mint="MINT"))
    at(rig, "MINT", 30_000_000_000, 1_000 * MS)
    r = rig.gateway.submit_entry(EntryIntent("e", "MINT", 100_000_000, origin="AUTO"))
    balance = rig.wallet.balance()
    rig.store.close()                                                   # the process dies before any fill
    r2 = Rig(tmp_path / "jinx.db")
    rep = recover(r2.ledger, r2.wallet)
    assert rep.released_unfilled == 1 and rep.wallet_reconciled
    pos = r2.ledger.get(r.position_id)
    assert pos.status is Status.FAILED and pos.token_amount == 0
    assert r2.wallet.balance() == balance                               # nothing was spent on an unconfirmed buy
    assert not r2.store.conn.execute("SELECT 1 FROM fills").fetchone()
    r2.close()


def test_failed_quote_at_fill_time_fails_the_entry_without_spending(tmp_path):
    rig = Rig(tmp_path / "jinx.db")
    set_mode(rig.store, Mode.AUTO)
    r = rig.gateway.submit_entry(EntryIntent("e", "NOQUOTE", 100_000_000, origin="AUTO"))
    assert not r.ok and r.reason == "NO_QUOTE"
    assert rig.ledger.get(r.position_id).status is Status.FAILED
    assert rig.wallet.balance() == config.PAPER_START_LAMPORTS
    stages = [e["stage"] for e in rig.ledger.order_events("e")]
    assert stages == ["SUBMITTED", "FAILED"]
    rig.close()


def test_insufficient_balance_is_refused_and_reconciled(tmp_path):
    rig = Rig(tmp_path / "jinx.db")
    set_mode(rig.store, Mode.AUTO)
    rig.tokens.apply(make_create(mint="MINT"))
    rig.price("MINT", 30_000_000_000)
    r = rig.gateway.submit_entry(EntryIntent("big", "MINT", config.PAPER_START_LAMPORTS, origin="AUTO"))
    assert not r.ok and r.reason == "INSUFFICIENT_BALANCE"
    assert rig.wallet.balance() == config.PAPER_START_LAMPORTS and rig.wallet.reconcile().ok
    rig.close()


def test_one_gateway_never_opens_two_positions_in_one_mint(tmp_path):
    rig = Rig(tmp_path / "jinx.db")
    set_mode(rig.store, Mode.AUTO)
    rig.store.set_setting("max_entry_slots", "2", by="owner-order")
    rig.tokens.apply(make_create(mint="MINT"))
    rig.price("MINT", 30_000_000_000)
    assert rig.gateway.submit_entry(EntryIntent("a", "MINT", 100_000_000, origin="AUTO")).ok
    dup = rig.gateway.submit_entry(EntryIntent("b", "MINT", 100_000_000, origin="AUTO"))
    assert not dup.ok and dup.reason == "DUPLICATE_POSITION"
    rig.close()


@pytest.mark.parametrize("report,state", [
    ({"order_id": "o", "status": "pending"}, OrderState.UNCERTAIN),
    ({"order_id": "o", "status": "processed"}, OrderState.UNCERTAIN),
    ({"order_id": "o", "status": "confirmed"}, OrderState.UNCERTAIN),          # no report amounts: not a fill
    ({"order_id": "o", "status": "confirmed", "report": {"input_amount": "100", "output_amount": "7"}},
     OrderState.FILLED),
    ({"order_id": "o", "status": "successful", "state": 30, "report": {"input_amount": "100", "output_amount": "7"}},
     OrderState.FILLED),
    ({"order_id": "o", "status": "failed", "error_status": "slippage"}, OrderState.FAILED),
    ({"order_id": "o", "status": "expired"}, OrderState.FAILED),
    ({"order_id": "o", "status": "weird"}, OrderState.UNCERTAIN),
    ({}, OrderState.UNCERTAIN),
    ({"order_id": "o", "status": "confirmed", "report": {"input_amount": "x", "output_amount": "7"}},
     OrderState.UNCERTAIN),
])
def test_gmgn_order_reports_map_to_one_outcome_and_an_acknowledgement_is_never_a_fill(report, state):
    out = gmgn_order_outcome(report)
    assert out.state is state
    if state is OrderState.FILLED:
        assert (out.input_amount, out.output_amount) == (100, 7)
    else:
        assert out.input_amount is None and out.output_amount is None
