"""Phase 3: the one ExitIntent carries the owner's fields (2026-10-21), and the gateway enforces them.

Owner order §3: no duplicate exit submission, no selling more tokens than remain, no treating an unconfirmed
order as filled, no forgetting the peak after restart, no selling positions belonging to another strategy.
"""
from decimal import Decimal

import pytest

from jinx.execution import EntryIntent, ExecutionGateway, ExitIntent
from jinx.ledger import PositionLedger, Status
from jinx.modes import Mode, set_mode
from jinx.paper import CurveFees, PaperExecutor, PaperWallet
from jinx.registry import TokenRegistry
from jinx.store import Store
from tests.conftest import make_create, make_trade

FEES = CurveFees(95, 5)


def _rig(store):
    reg = TokenRegistry()
    ledger = PositionLedger(store)
    wallet = PaperWallet(store, 2_000_000_000)
    paper = PaperExecutor(store, ledger, wallet, reg, FEES, 5_000)
    return reg, ledger, wallet, ExecutionGateway(store, ledger, paper)


def _open(store, reg, gw, mint="MINT", strategy="AUTO", exit_policy=None):
    reg.apply(make_create(mint=mint))
    reg.apply(make_trade(mint=mint, v_sol=31_000_000_000, v_tokens=1_000_000_000_000_000))
    set_mode(store, Mode.AUTO)
    r = gw.submit_entry(EntryIntent(f"entry-{mint}", mint, 100_000_000, origin=strategy, exit_policy=exit_policy))
    assert r.ok, r
    return r.position_id


def test_exit_intent_has_every_owner_field():
    i = ExitIntent("bigmac:1:stop_loss", 1, source_strategy="AUTO", triggering_evidence="price 90 <= stop 90",
                   required_sell_pct=Decimal(1), event_timestamp=123, exit_urgency="protective",
                   previously_executed_fraction=Decimal(0), remaining_confirmed_balance=10, reason_code="stop_loss",
                   venue="pump.fun bonding curve", quote_status="QUOTED", exit_policy="BIGMAC_PAPER")
    assert i.idempotency_id == i.intent_id
    assert (i.source_strategy, i.reason_code, i.exit_urgency, i.venue) == ("AUTO", "stop_loss", "protective",
                                                                            "pump.fun bonding curve")


@pytest.mark.parametrize("kw", [{"required_sell_pct": Decimal(0)}, {"required_sell_pct": Decimal("1.5")},
                                {"exit_urgency": "panic"}, {"previously_executed_fraction": Decimal(1)},
                                {"remaining_confirmed_balance": -1}, {"event_timestamp": -5}])
def test_exit_intent_refuses_out_of_range_owner_fields(kw):
    with pytest.raises((ValueError, TypeError)):
        ExitIntent("x", 1, **kw)


def test_one_class_only():
    import jinx.exits.bigmac_exit as b
    assert not hasattr(b, "ExitIntent")


def test_intent_fields_are_recorded_with_the_exit(store):
    reg, ledger, wallet, gw = _rig(store)
    pid = _open(store, reg, gw)
    held = ledger.get(pid).token_amount
    r = gw.submit_exit(ExitIntent("ex-1", pid, source_strategy="AUTO", reason_code="stop_loss",
                                  exit_urgency="protective", required_sell_pct=Decimal(1),
                                  remaining_confirmed_balance=held, event_timestamp=77, venue="curve",
                                  triggering_evidence="test", quote_status="QUOTED", exit_policy=None))
    assert r.ok
    row = store.conn.execute("SELECT * FROM exit_intents WHERE intent_id = 'ex-1'").fetchone()
    assert (row["source_strategy"], row["reason_code"], row["exit_urgency"], row["event_ns"],
            row["required_sell_pct"]) == ("AUTO", "stop_loss", "protective", 77, "1")
    assert ledger.get(pid).status is Status.CLOSED


def test_another_strategys_position_is_never_sold(store):
    reg, ledger, wallet, gw = _rig(store)
    pid = _open(store, reg, gw, strategy="COPY")
    r = gw.submit_exit(ExitIntent("ex-foreign", pid, source_strategy="AUTO", reason_code="stop_loss"))
    assert not r.ok and r.reason == "FOREIGN_STRATEGY"
    assert ledger.get(pid).status is Status.OPEN
    assert not store.conn.execute("SELECT 1 FROM exit_intents WHERE intent_id = 'ex-foreign'").fetchone()


def test_a_policy_exit_only_sells_positions_that_policy_owns(store):
    reg, ledger, wallet, gw = _rig(store)
    pid = _open(store, reg, gw, exit_policy=None)
    r = gw.submit_exit(ExitIntent("bm", pid, source_strategy="AUTO", exit_policy="BIGMAC_PAPER",
                                  reason_code="take_profit"))
    assert not r.ok and r.reason == "FOREIGN_EXIT_POLICY"


def test_stale_remaining_balance_is_refused_so_nothing_is_oversold(store):
    reg, ledger, wallet, gw = _rig(store)
    pid = _open(store, reg, gw)
    held = ledger.get(pid).token_amount
    r = gw.submit_exit(ExitIntent("stale", pid, source_strategy="AUTO", remaining_confirmed_balance=held + 1,
                                  reason_code="stop_loss"))
    assert not r.ok and r.reason == "STALE_BALANCE"
    r = gw.submit_exit(ExitIntent("too-much", pid, token_amount=held + 1, source_strategy="AUTO"))
    assert not r.ok and r.reason == "EXCEEDS_POSITION"
    assert ledger.get(pid).token_amount == held


def test_the_same_intent_twice_sells_once(store):
    reg, ledger, wallet, gw = _rig(store)
    pid = _open(store, reg, gw)
    held = ledger.get(pid).token_amount
    intent = ExitIntent("nd:1:evt-9", pid, token_amount=held * 3 // 10, source_strategy="AUTO",
                        reason_code="negative_divergence", required_sell_pct=Decimal("0.3"))
    assert gw.submit_exit(intent).ok
    again = gw.submit_exit(intent)
    assert again.ok and again.reason == "ALREADY_APPLIED"
    assert store.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1
    assert ledger.get(pid).token_amount == held - held * 3 // 10


def test_peak_is_persisted_and_survives_restart(tmp_path):
    path = tmp_path / "j.db"
    s = Store(path)
    reg, ledger, wallet, gw = _rig(s)
    pid = _open(s, reg, gw, exit_policy="BIGMAC_PAPER")
    assert ledger.get(pid).peak_value_lamports is None
    assert ledger.raise_peak(pid, 120, 1) == 120
    assert ledger.raise_peak(pid, 90, 2) == 120          # a peak is never lowered
    s.close()
    s2 = Store(path)
    pos = PositionLedger(s2).get(pid)
    assert (pos.peak_value_lamports, pos.peak_ns, pos.exit_policy, pos.strategy) == (120, 1, "BIGMAC_PAPER", "AUTO")
    s2.close()


def test_partial_sell_rescales_the_peak_to_the_tokens_still_held(store):
    reg, ledger, wallet, gw = _rig(store)
    pid = _open(store, reg, gw, exit_policy="BIGMAC_PAPER")
    held = ledger.get(pid).token_amount
    ledger.raise_peak(pid, 1_000_000, 1)
    sold = held * 4 // 10
    assert gw.submit_exit(ExitIntent("part", pid, token_amount=sold, source_strategy="AUTO")).ok
    assert ledger.get(pid).peak_value_lamports == 1_000_000 * (held - sold) // held


def test_entry_basis_is_the_cost_of_the_tokens_still_held(store):
    reg, ledger, wallet, gw = _rig(store)
    pid = _open(store, reg, gw)
    pos = ledger.get(pid)
    assert ledger.entry_basis(pid) == pos.cost_lamports
    sold = pos.token_amount // 2
    gw.submit_exit(ExitIntent("half", pid, token_amount=sold, source_strategy="AUTO"))
    assert ledger.entry_basis(pid) == pos.cost_lamports * (pos.token_amount - sold) // pos.token_amount
