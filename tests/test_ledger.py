"""One authoritative position ledger with the owner's global entry-slot limit."""
import pytest

from jinx.ledger import Fill, PositionLedger, Status
from jinx.modes import Mode, set_mode
from jinx.ops.entry_slots import OwnerOrderRequired, set_entry_slots
from jinx.store import Store
from jinx.core.events import Side


def buy_fill(fid="F1", tokens=1_000, sol=100_000, protocol_fee=1_000, network_fee=5_000):
    return Fill(fill_id=fid, side=Side.BUY, token_amount=tokens, sol_lamports=sol,
                protocol_fee_lamports=protocol_fee, network_fee_lamports=network_fee, simulated=True, ts_ns=1)


def sell_fill(fid="S1", tokens=1_000, sol=150_000, protocol_fee=1_500, network_fee=5_000):
    return Fill(fill_id=fid, side=Side.SELL, token_amount=tokens, sol_lamports=sol,
                protocol_fee_lamports=protocol_fee, network_fee_lamports=network_fee, simulated=True, ts_ns=2)


@pytest.fixture
def ledger(store):
    set_mode(store, Mode.AUTO)
    return PositionLedger(store)


def test_default_is_one_global_slot(ledger):
    assert ledger.max_slots() == 1
    assert ledger.reserve_entry("I1", "A").ok
    r = ledger.reserve_entry("I2", "B")
    assert not r.ok and r.reason == "SLOT_LIMIT"


def test_slot_frees_after_full_close(ledger):
    p = ledger.reserve_entry("I1", "A").position_id
    ledger.record_buy_fill(p, buy_fill())
    ledger.begin_exit(p, "X1")
    ledger.record_sell_fill(p, sell_fill())
    assert ledger.get(p).status is Status.CLOSED
    assert ledger.reserve_entry("I2", "B").ok


def test_two_slots_only_by_owner_order_and_never_three(store, ledger):
    with pytest.raises(OwnerOrderRequired):
        set_entry_slots(store, 2, owner_order=False)
    with pytest.raises(ValueError):
        set_entry_slots(store, 3, owner_order=True)
    set_entry_slots(store, 2, owner_order=True)
    assert ledger.reserve_entry("I1", "A").ok
    assert ledger.reserve_entry("I2", "B").ok
    assert ledger.reserve_entry("I3", "C").reason == "SLOT_LIMIT"


def test_environment_cannot_raise_the_slot_limit(monkeypatch, ledger):
    monkeypatch.setenv("JINX_MAX_ENTRY_SLOTS", "2")
    monkeypatch.setenv("MAX_POSITIONS", "10")
    assert ledger.max_slots() == 1


def test_corrupt_slot_state_fails_closed(store, ledger):
    store.set_setting("max_entry_slots", "7")
    r = ledger.reserve_entry("I1", "A")
    assert not r.ok and r.reason == "SLOT_STATE_INVALID"


def test_same_intent_twice_returns_the_same_position(ledger):
    a = ledger.reserve_entry("SAME", "A")
    b = ledger.reserve_entry("SAME", "A")
    assert a.ok and b.ok and a.position_id == b.position_id
    assert ledger.active_slots() == 1


def test_no_second_position_on_a_mint_already_held(store, ledger):
    set_entry_slots(store, 2, owner_order=True)
    assert ledger.reserve_entry("I1", "A").ok
    r = ledger.reserve_entry("I2", "A")
    assert not r.ok and r.reason == "DUPLICATE_POSITION"


def test_failed_open_releases_the_slot(ledger):
    p = ledger.reserve_entry("I1", "A").position_id
    ledger.mark_open_failed(p, "quote unavailable")
    assert ledger.get(p).status is Status.FAILED
    assert ledger.reserve_entry("I2", "B").ok


def test_unreconciled_sell_still_holds_the_slot(ledger):
    p = ledger.reserve_entry("I1", "A").position_id
    ledger.record_buy_fill(p, buy_fill())
    ledger.begin_exit(p, "X1")
    assert ledger.get(p).status is Status.PENDING_CLOSE
    assert ledger.reserve_entry("I2", "B").reason == "SLOT_LIMIT"


def test_mode_off_blocks_entries_but_never_exits(store, ledger):
    p = ledger.reserve_entry("I1", "A").position_id
    ledger.record_buy_fill(p, buy_fill())
    set_mode(store, Mode.OFF)
    assert ledger.reserve_entry("I2", "B").reason == "MODE_OFF"
    assert ledger.begin_exit(p, "X1").ok
    ledger.record_sell_fill(p, sell_fill())
    assert ledger.get(p).status is Status.CLOSED


def test_partial_sell_keeps_position_open_until_inventory_is_zero(ledger):
    p = ledger.reserve_entry("I1", "A").position_id
    ledger.record_buy_fill(p, buy_fill(tokens=1_000))
    ledger.begin_exit(p, "X1")
    ledger.record_sell_fill(p, sell_fill(fid="S1", tokens=400, sol=60_000))
    pos = ledger.get(p)
    assert pos.token_amount == 600 and pos.status is Status.PENDING_CLOSE
    ledger.record_sell_fill(p, sell_fill(fid="S2", tokens=600, sol=90_000))
    assert ledger.get(p).status is Status.CLOSED


def test_realized_pnl_counts_every_cost(ledger):
    p = ledger.reserve_entry("I1", "A").position_id
    ledger.record_buy_fill(p, buy_fill(sol=100_000, protocol_fee=1_000, network_fee=5_000))
    ledger.begin_exit(p, "X1")
    ledger.record_sell_fill(p, sell_fill(sol=150_000, protocol_fee=1_500, network_fee=5_000))
    pos = ledger.get(p)
    # buy sol already includes its protocol fee; sell sol is already net of its protocol fee
    assert pos.cost_lamports == 100_000
    assert pos.proceeds_lamports == 150_000
    assert pos.network_fees_lamports == 10_000
    assert pos.realized_pnl_lamports == 150_000 - 100_000 - 10_000


def test_replayed_fill_is_applied_once(ledger):
    p = ledger.reserve_entry("I1", "A").position_id
    ledger.record_buy_fill(p, buy_fill(fid="F1"))
    ledger.record_buy_fill(p, buy_fill(fid="F1"))
    assert ledger.get(p).token_amount == 1_000


def test_cannot_sell_more_than_held(ledger):
    p = ledger.reserve_entry("I1", "A").position_id
    ledger.record_buy_fill(p, buy_fill(tokens=100))
    ledger.begin_exit(p, "X1")
    with pytest.raises(ValueError):
        ledger.record_sell_fill(p, sell_fill(tokens=101))


def test_positions_survive_restart(tmp_path):
    path = tmp_path / "jinx.db"
    s = Store(path)
    set_mode(s, Mode.AUTO)
    led = PositionLedger(s)
    p = led.reserve_entry("I1", "A").position_id
    led.record_buy_fill(p, buy_fill())
    s.close()
    s2 = Store(path)
    led2 = PositionLedger(s2)
    pos = led2.get(p)
    assert pos.status is Status.OPEN and pos.token_amount == 1_000
    assert led2.reserve_entry("I2", "B").reason == "SLOT_LIMIT"
    s2.close()
