"""V11 order duplication and delayed confirmations; V12 transaction balance reconciliation.

Owner production contract (CLAUDE.md): "each position has one accountable exit owner. No stale bags, phantom fills or
unaccounted fees." Mandate 2026-10-20: "Never call an API acknowledgement a fill"; "A GMGN protective order and a JINX
exit must never oversell the same position". All of it runs through the one order gateway (`ExecutionGateway`) and the
one ledger on paper, priced on real captured curve reserves. A buy that is reserved but not yet filled stands for an
order awaiting confirmation: it holds its slot, holds no tokens and cannot be sold."""
import random

import pytest

from jinx.execution import EntryIntent, ExitIntent
from jinx.ledger import Fill, FillConflict, Status
from jinx.core.events import Side
from jinx.modes import Mode, set_mode
from jinx.sim import load_capture
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import M, baseline_msgs
from tests.pumpapi_msgs import events

NETWORK_FEE = 5_000


@pytest.fixture
def app(tmp_path):
    a = h.build_app(tmp_path)
    h.run(a, [events(m) for m in baseline_msgs()])
    set_mode(a.store, Mode.AUTO, by="phase4-test")
    yield a
    h.close(a)


def wallet_entries(app):
    return {r[0]: r[1] for r in app.store.conn.execute("SELECT entry_id, delta_lamports FROM paper_wallet_entries")}


def test_the_same_entry_intent_replayed_twenty_times_buys_once(app):
    results = [app.gateway.submit_entry(EntryIntent("dup-entry", M, 50_000_000, origin="phase4-test"))
               for _ in range(20)]
    assert results[0].ok and all(r.ok and r.reason == "ALREADY_EXECUTED" for r in results[1:])
    assert len(app.ledger.all()) == 1 and len(wallet_entries(app)) == 1
    assert app.wallet.balance() == app.wallet.reconcile().starting_lamports - 50_000_000 - NETWORK_FEE


def test_the_same_entry_intent_with_a_different_size_or_token_is_refused(app):
    assert app.gateway.submit_entry(EntryIntent("dup-entry", M, 50_000_000)).ok
    assert app.gateway.submit_entry(EntryIntent("dup-entry", M, 60_000_000)).reason == "INTENT_CONFLICT"
    assert app.gateway.submit_entry(EntryIntent("dup-entry", "OtherMintpump", 50_000_000)).reason == "INTENT_CONFLICT"
    assert len(app.ledger.all()) == 1


def test_a_second_entry_on_a_held_token_is_refused(app):
    from jinx.ops.entry_slots import set_entry_slots
    set_entry_slots(app.store, 2, owner_order=True)          # labelled test fixture: a free slot exists
    assert app.gateway.submit_entry(EntryIntent("e1", M, 50_000_000)).ok
    assert app.gateway.submit_entry(EntryIntent("e2", M, 50_000_000)).reason == "DUPLICATE_POSITION"


def test_the_same_exit_intent_replayed_twenty_times_sells_once(app):
    pos = app.gateway.submit_entry(EntryIntent("e1", M, 50_000_000)).position_id
    results = [app.gateway.submit_exit(ExitIntent("x1", pos)) for _ in range(20)]
    assert results[0].ok and all(r.ok and r.reason == "ALREADY_APPLIED" for r in results[1:])
    sells = app.store.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0]
    assert sells == 1 and len(wallet_entries(app)) == 2 and app.wallet.reconcile().ok


def test_a_buy_awaiting_confirmation_holds_its_slot_holds_no_tokens_and_cannot_be_sold(app):
    r = app.ledger.reserve_entry("pending-buy", M, 50_000_000)       # submitted; fill not confirmed
    assert r.ok
    pos = app.ledger.get(r.position_id)
    assert pos.status is Status.PENDING_OPEN and pos.token_amount == 0
    assert app.gateway.submit_entry(EntryIntent("other", "OtherMintpump", 10_000_000)).reason == "SLOT_LIMIT"
    assert app.gateway.submit_exit(ExitIntent("sell-early", r.position_id)).reason == "NOT_OPEN:PENDING_OPEN"
    assert app.wallet.balance() == app.wallet.reconcile().starting_lamports           # nothing spent before a fill


def test_a_delayed_sell_confirmation_keeps_one_exit_owner_and_a_late_duplicate_changes_nothing(app):
    pos = app.gateway.submit_entry(EntryIntent("e1", M, 50_000_000)).position_id
    held = app.ledger.get(pos).token_amount
    fill_id = f"p{pos}:x1:sell"
    assert app.ledger.claim_exit("x1", pos, None, fill_id).ok            # sell submitted, confirmation delayed
    assert app.gateway.submit_exit(ExitIntent("x2", pos)).reason == "EXIT_IN_PROGRESS"     # no second seller
    assert app.gateway.submit_exit(ExitIntent("x3", pos, held)).reason == "EXIT_IN_PROGRESS"
    confirmed = Fill(fill_id, Side.SELL, held, 48_000_000, 480_000, NETWORK_FEE, simulated=True, ts_ns=1)
    app.ledger.record_sell_fill(pos, confirmed)                          # the confirmation arrives
    app.ledger.record_sell_fill(pos, confirmed)                          # and arrives again (resend)
    with pytest.raises(FillConflict):                                    # the same id with other contents
        app.ledger.record_sell_fill(pos, Fill(fill_id, Side.SELL, held, 1, 0, NETWORK_FEE, simulated=True, ts_ns=2))
    closed = app.ledger.get(pos)
    assert closed.status is Status.CLOSED and closed.proceeds_lamports == 48_000_000
    assert app.store.conn.execute("SELECT COUNT(*) FROM fills WHERE side='sell'").fetchone()[0] == 1


def test_partial_exits_can_never_sell_more_than_is_held(app):
    pos = app.gateway.submit_entry(EntryIntent("e1", M, 50_000_000)).position_id
    held = app.ledger.get(pos).token_amount
    first = held * 40 // 100
    assert app.gateway.submit_exit(ExitIntent("part-1", pos, first)).ok
    left = app.ledger.get(pos).token_amount
    assert left == held - first and app.ledger.get(pos).status is Status.OPEN
    assert app.gateway.submit_exit(ExitIntent("part-2", pos, left + 1)).reason == "EXCEEDS_POSITION"
    assert app.gateway.submit_exit(ExitIntent("part-2", pos, left)).ok
    sold = app.store.conn.execute("SELECT SUM(token_amount) FROM fills WHERE side='sell'").fetchone()[0]
    assert sold == held and app.ledger.get(pos).status is Status.CLOSED


def test_mode_off_blocks_new_entries_but_never_an_exit(app):
    pos = app.gateway.submit_entry(EntryIntent("e1", M, 50_000_000)).position_id
    set_mode(app.store, Mode.OFF, by="phase4-test")
    assert app.gateway.submit_entry(EntryIntent("e2", "OtherMintpump", 10_000_000)).reason == "MODE_OFF"
    assert app.gateway.submit_exit(ExitIntent("x1", pos)).ok


# ---- V12: reconciliation over many round trips on real reserves ------------------------------------------------------

LIVE = load_capture(h.FIXTURES / "pumpapi-live-20261021.jsonl.gz")


def test_every_lamport_reconciles_over_randomized_round_trips_on_real_reserves(tmp_path):
    """Seeded random entries, partial and full exits, replays and refusals on the live capture's reserves. After each
    step: the wallet equals its start plus its entries; each fill has exactly one wallet entry with the right amount;
    the change in the wallet equals realized P&L minus the cost still held in open positions."""
    app = h.build_app(tmp_path)
    h.run(app, h.deliveries(LIVE, "pumpapi"))
    set_mode(app.store, Mode.AUTO, by="phase4-test")
    mints = sorted({t.mint for t in app.core.tokens.all() if t.v_sol_lamports})
    rng = random.Random(20261021)
    start = app.wallet.balance()
    for step in range(400):
        active = app.ledger.active()
        if not active or rng.random() < 0.4:
            app.gateway.submit_entry(EntryIntent(f"r-e{step}", rng.choice(mints), rng.randint(1, 50) * 1_000_000))
        else:
            pos = active[0]
            amount = None if rng.random() < 0.5 else max(1, pos.token_amount * rng.randint(10, 90) // 100)
            intent = ExitIntent(f"r-x{step}", pos.position_id, amount)
            app.gateway.submit_exit(intent)
            if rng.random() < 0.3:
                app.gateway.submit_exit(intent)                       # a replay must change nothing
        rec = app.wallet.reconcile()
        assert rec.ok, step
    entries = wallet_entries(app)
    fills = app.store.conn.execute("SELECT fill_id, side, sol_lamports, network_fee_lamports FROM fills").fetchall()
    assert len(entries) == len(fills)
    for fid, side, sol, net in fills:
        assert entries[fid] == (-(sol + net) if side == "buy" else sol - net), fid
    closed = [p for p in app.ledger.all() if p.status is Status.CLOSED]
    held_cost = sum(p.cost_lamports + p.network_fees_lamports - p.proceeds_lamports for p in app.ledger.active())
    realized = sum(p.realized_pnl_lamports for p in closed)
    assert closed and app.wallet.balance() - start == realized - held_cost
    h.close(app)
