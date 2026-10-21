"""Typed normalized market events and duplicate protection."""
import pytest

from jinx.core.events import EventKind, MarketEvent, Side, sol_to_lamports
from jinx.core.dedup import Deduplicator


def trade(**over):
    base = dict(
        kind=EventKind.TRADE, source="mako.pumpportal", mint="MINT1", signature="SIG1",
        trader="WALLET1", side=Side.BUY, sol_lamports=500_000_000, token_amount=1_000_000,
        v_sol_lamports=31_000_000_000, v_tokens=1_000_000_000_000, recv_ns=1_000, seq=1,
    )
    base.update(over)
    return MarketEvent(**base)


def test_trade_requires_side_trader_and_amounts():
    with pytest.raises(ValueError):
        trade(side=None)
    with pytest.raises(ValueError):
        trade(trader=None)
    with pytest.raises(ValueError):
        trade(sol_lamports=None)


def test_amounts_must_be_non_negative_integers():
    with pytest.raises(ValueError):
        trade(sol_lamports=-1)
    with pytest.raises(TypeError):
        trade(sol_lamports=0.5)


def test_token_created_needs_mint_and_creator():
    ev = MarketEvent(kind=EventKind.TOKEN_CREATED, source="mako.pumpportal", mint="M", signature="S",
                     trader="CREATOR", recv_ns=5, seq=1)
    assert ev.side is None
    with pytest.raises(ValueError):
        MarketEvent(kind=EventKind.TOKEN_CREATED, source="x", mint="", signature="S", trader="C", recv_ns=5, seq=1)


def test_idempotency_key_ignores_receive_time_and_sequence():
    a = trade(recv_ns=1, seq=1)
    b = trade(recv_ns=999, seq=77)
    assert a.idempotency_key == b.idempotency_key


def test_idempotency_key_separates_distinct_legs_of_one_transaction():
    base = trade()
    assert base.idempotency_key != trade(trader="WALLET2").idempotency_key
    assert base.idempotency_key != trade(side=Side.SELL).idempotency_key
    assert base.idempotency_key != trade(mint="MINT2").idempotency_key


def test_order_key_prefers_chain_time_then_receive_time_then_sequence():
    chain_late = trade(block_time=200, recv_ns=1, seq=1)
    chain_early = trade(block_time=100, recv_ns=50 * 10**9 * 10, seq=2)
    assert chain_early.order_key < chain_late.order_key
    no_chain_a = trade(recv_ns=10, seq=2)
    no_chain_b = trade(recv_ns=10, seq=3)
    assert no_chain_a.order_key < no_chain_b.order_key


def test_events_are_immutable():
    ev = trade()
    with pytest.raises(AttributeError):
        ev.mint = "OTHER"


def test_sol_to_lamports_rounds_to_nearest_lamport():
    assert sol_to_lamports(30.000000001) == 30_000_000_001
    assert sol_to_lamports(0.1 + 0.2) == 300_000_000
    assert sol_to_lamports(None) is None


def test_deduplicator_counts_each_key_once():
    d = Deduplicator(capacity=3)
    assert d.first_time("a", 1) is True
    assert d.first_time("a", 2) is False
    assert d.first_time("b", 3) is True
    assert d.duplicates == 1


def test_deduplicator_is_bounded_and_evicts_oldest():
    d = Deduplicator(capacity=2)
    for i, k in enumerate(("a", "b", "c")):
        d.first_time(k, i)
    assert len(d) == 2
    # evicted from memory: market chatter may be seen again; orders stay idempotent in the durable ledger
    assert d.first_time("a", 3) is True
