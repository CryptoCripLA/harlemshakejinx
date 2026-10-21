"""The durable ledger store and the one RAM token state (owner storage repair 2026-10-21: market chatter is
transient, so no raw event is stored and the token state is not reloaded after a restart)."""
import random

from jinx.core.events import Side
from jinx.registry import TokenRegistry
from jinx.store import Store
from tests.conftest import make_create, make_trade


def test_the_ledger_store_has_no_way_to_store_a_market_event(tmp_path):
    s = Store(tmp_path / "jinx.db")
    assert not hasattr(s, "record_event") and not hasattr(s, "unapplied_events")
    s.close()


def test_store_uses_write_ahead_logging(store):
    assert store.journal_mode() == "wal"


def test_creation_then_trades_update_reserves_counts_and_traders(store):
    reg = TokenRegistry()
    reg.apply(make_create(mint="A", creator="DEV"))
    reg.apply(make_trade(mint="A", trader="W1", side=Side.BUY, v_sol=31_000_000_000))
    reg.apply(make_trade(mint="A", trader="W2", side=Side.BUY, v_sol=32_000_000_000))
    reg.apply(make_trade(mint="A", trader="W1", side=Side.SELL, v_sol=31_500_000_000))
    t = reg.get("A")
    assert t.creator == "DEV"
    assert (t.buys, t.sells) == (2, 1)
    assert not hasattr(t, "unique_traders")          # no per-token trader set (storage repair)
    assert t.v_sol_lamports == 31_500_000_000
    assert t.peak_v_sol_lamports == 32_000_000_000


def test_late_event_never_overwrites_newer_reserves_but_still_counts(store):
    reg = TokenRegistry()
    newer = make_trade(mint="A", v_sol=40_000_000_000, recv_ns=200, seq=200)
    older = make_trade(mint="A", v_sol=45_000_000_000, recv_ns=100, seq=100)
    reg.apply(newer)
    reg.apply(older)  # arrives late
    t = reg.get("A")
    assert t.v_sol_lamports == 40_000_000_000      # current state comes from the newest event
    assert t.peak_v_sol_lamports == 45_000_000_000  # the peak still saw the older, higher reserve
    assert t.buys == 2
    assert reg.late_events == 1


def test_trade_before_creation_is_kept_and_creation_fills_in_creator(store):
    reg = TokenRegistry()
    reg.apply(make_trade(mint="B", recv_ns=50, seq=50))
    reg.apply(make_create(mint="B", creator="DEV", recv_ns=10, seq=10))
    t = reg.get("B")
    assert t.creator == "DEV"
    assert t.buys == 1
    assert t.v_sol_lamports == 31_000_000_000  # the creation is older than the trade


def test_shuffled_arrival_order_gives_identical_final_state(tmp_path):
    events = [make_create(mint="C", recv_ns=1, seq=1)]
    for i in range(2, 60):
        events.append(make_trade(mint="C", trader=f"W{i % 7}", side=Side.BUY if i % 3 else Side.SELL,
                                 v_sol=30_000_000_000 + i * 10_000_000, recv_ns=i, seq=i))
    digests = []
    for seed in range(5):
        order = events[:]
        random.Random(seed).shuffle(order)
        s = Store(tmp_path / f"s{seed}.db")
        reg = TokenRegistry()
        for e in order:
            reg.apply(e)
        digests.append(reg.digest())
        s.close()
    assert len(set(digests)) == 1


def test_a_new_registry_starts_empty_after_a_restart():
    reg = TokenRegistry()
    reg.apply(make_create(mint="D"))
    reg.apply(make_trade(mint="D", trader="W9"))
    assert len(reg) == 1 and len(TokenRegistry()) == 0       # nothing persisted, nothing reloaded


def test_there_is_no_per_wallet_registry():
    """Owner storage repair 2026-10-21: wallet history comes from GMGN when needed, not from a local copy."""
    import jinx.registry as registry
    assert not hasattr(registry, "WalletRegistry")
