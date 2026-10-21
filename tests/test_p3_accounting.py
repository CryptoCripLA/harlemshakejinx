"""Phase 3 accounting: simulated and actual fills kept apart, every cost recorded, realized and unrealized P&L
separate, every partial and full exit reconciled, open positions accounted for, failure costs recorded, and one
trade-by-trade report. Dollar figures for the owner come from a stated SOL/USD rate (source and time included)."""
from decimal import Decimal

from jinx import config
from jinx.execution import EntryIntent, ExitIntent
from jinx.ledger import Status
from jinx.modes import Mode, set_mode
from jinx.operator_view import SolUsd, trade_report
from tests.conftest import make_create
from tests.test_bigmac_integration import FIXTURE, K, Rig


def _open(rig, mint):
    rig.tokens.apply(make_create(mint=mint))
    rig.price(mint, 30_000_000_000)
    r = rig.gateway.submit_entry(EntryIntent(f"e-{mint}", mint, 100_000_000, origin="AUTO"))
    assert r.ok
    return r.position_id


def test_trade_report_reconciles_partials_full_exits_and_open_positions(tmp_path):
    rig = Rig(tmp_path / "j.db")
    set_mode(rig.store, Mode.AUTO)
    a = _open(rig, "AAA")
    held = rig.ledger.get(a).token_amount
    rig.price("AAA", 36_000_000_000)
    quote = rig.value(a, 36_000_000_000) * 3 // 10
    assert rig.gateway.submit_exit(ExitIntent("part", a, token_amount=held * 3 // 10, source_strategy="AUTO",
                                              reason_code="negative_divergence", quote_lamports=quote)).ok
    assert rig.gateway.submit_exit(ExitIntent("rest", a, source_strategy="AUTO", reason_code="take_profit")).ok
    set_mode(rig.store, Mode.AUTO)
    rig.ledger.store.set_setting("max_entry_slots", "1", by="test")
    b = _open(rig, "BBB")                                     # stays open
    rig.price("BBB", 33_000_000_000)                          # a price update marks the open position
    rep = trade_report(rig.ledger, rig.wallet, SolUsd(Decimal("105.70"), "TEST rate", 1))
    by = {t["position_id"]: t for t in rep["trades"]}
    ta, tb = by[a], by[b]
    assert ta["status"] == "CLOSED" and ta["simulated"] is True
    assert [e["reason"] for e in ta["exits"]] == ["negative_divergence", "take_profit"]
    assert ta["tokens_sold"] == ta["tokens_bought"] and ta["tokens_held"] == 0
    pos = rig.ledger.get(a)
    assert ta["realized_pnl_lamports"] == pos.realized_pnl_lamports == \
        pos.proceeds_lamports - pos.cost_lamports - pos.network_fees_lamports
    assert ta["protocol_fees_lamports"] == pos.protocol_fees_lamports and ta["network_fees_lamports"] == 15_000
    assert ta["exits"][0]["slippage_lamports"] == quote - ta["exits"][0]["proceeds_lamports"]
    assert ta["tips_lamports"] == 0
    assert tb["status"] == "OPEN" and tb["unrealized_pnl_lamports"] == tb["mark_lamports"] - tb["entry_basis_lamports"]
    assert tb["realized_pnl_lamports"] is None
    totals = rep["totals"]
    assert totals["wallet_reconciled"] is True
    assert totals["realized_pnl_lamports"] == ta["realized_pnl_lamports"]
    assert totals["paper_balance_lamports"] == rig.wallet.balance()
    assert totals["paper_balance_lamports"] == config.PAPER_START_LAMPORTS + sum(
        t["proceeds_lamports"] - t["cost_lamports"] - t["network_fees_lamports"] for t in rep["trades"])
    assert totals["actual_fills"] == 0 and totals["simulated_fills"] == 4
    usd = rep["usd"]
    assert usd["rate"] == "105.70" and usd["source"] == "TEST rate"
    assert usd["realized_pnl"] == round(float(Decimal(ta["realized_pnl_lamports"]) / 10 ** 9 * Decimal("105.70")), 2)
    rig.close()


def test_failure_costs_and_failed_attempts_are_in_the_report(tmp_path):
    rig = Rig(tmp_path / "j.db")
    set_mode(rig.store, Mode.AUTO)
    a = _open(rig, "AAA")
    rig.ledger.record_order_event("x-fail", a, "sell", "FAILED", 5, True, cost_lamports=5_000, detail="TEST")
    rep = trade_report(rig.ledger, rig.wallet, None)
    t = rep["trades"][0]
    assert t["failure_costs_lamports"] == 5_000 and t["failed_attempts"] == 1
    assert rep["usd"] is None                                   # no rate: no dollar figures are invented
    rig.close()
