"""V06: every approved exit condition, through the running engine (V16: adverse moves and failed exits).

Entries are made by the engine itself: an Early Momentum detection, the safety screen, the fast-path EntryCoordinator
with quarter-Kelly sizing, the one gateway (HEAR -> SIZE -> FIRE). Prices then arrive as PumpAPI trades through
JinxApp's bus, and the position monitor decides (EXIT): CHAD's protective exits first, then the position's BIGMAC_PAPER
policy. CHAD lines are docs/CHAD_BLUEPRINT.md. Numbers that CHAD or the owner have not set are labelled TEST_FIXTURE
settings (Phase 3's own replay fixtures: BigMac 50/20/12/20, Kelly cap 5 %), never production values (B-30, B-32).
Phase 3's unit tests (tests/test_p3_executioner.py, tests/test_bigmac_integration.py) cover the arithmetic; this file
checks the same exits wired end to end, plus restart, mode, ownership and failure behaviour."""
import sys
from decimal import Decimal

import pytest

from jinx.execution import EntryIntent
from jinx.exits.executioner import Divergence
from jinx.exits.policy import save_bigmac_config
from jinx.ledger import Status
from jinx.modes import Mode, set_mode
from jinx.sizing.kelly import save_kelly_config
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import DISTINCT, LiveFeed, M
from tests.pumpapi_msgs import T0_MS, buy, create, events, sell

sys.path.insert(0, str(h.FIXTURES.parent.parent / "scripts"))
import phase3_paper_replay as p3  # noqa: E402  (TEST_FIXTURE settings, labelled there)

S = 1_000_000_000
GRACE_S = 600            # CHAD L219 "A 10-minute grace window applies after entry" (literal, not imported)


def launch(entry_v_quote=32.5):
    """A launch that passes every entry check; the third buyer leaves the curve at `entry_v_quote` virtual SOL."""
    steps = [31.0 + (entry_v_quote - 31.0) * k / 3 for k in (1, 2, 3)]
    return [create(mint=M, dev="DEV", v_quote=31.0)] + [
        buy(w, t, v_quote=v, mint=M, sol=max(0.1, v - 31.0)) for w, t, v in zip(("A", "B", "C"), (1, 2, 3), steps)]


def price(v_quote, t_s, side="sell", wallet="MKT"):
    msg = (sell if side == "sell" else buy)(wallet, t_s, v_quote=v_quote, mint=M, sol=0.5)
    return events(msg)


@pytest.fixture
def engine(tmp_path):
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn(wallet_stats=DISTINCT), rugcheck=h.FakeRugCheck())
    app.pumpapi = LiveFeed()
    set_mode(app.store, Mode.AUTO, by="phase4-test")
    save_kelly_config(app.store, p3.KELLY_FIXTURE, by="phase4-test")
    save_bigmac_config(app.store, p3.BIGMAC_FIXTURE)
    yield app
    h.close(app)


def entered(app, entry_v_quote=32.5):
    h.run(app, [events(m) for m in launch(entry_v_quote)])
    [pos] = app.ledger.all()
    assert pos.status is Status.OPEN and pos.strategy == "AUTO" and pos.exit_policy == "BIGMAC_PAPER"
    return pos


def closed_reason(app, pid):
    row = app.store.conn.execute("SELECT exit_intent_id FROM fills WHERE position_id = ? AND side = 'sell' "
                                 "ORDER BY ts_ns DESC LIMIT 1", (pid,)).fetchone()
    return row[0] if row else None


def reconciles(app):
    rec = app.wallet.reconcile()
    pos = app.ledger.all()
    cost = sum(p.cost_lamports + p.network_fees_lamports - p.proceeds_lamports for p in pos if p.status is not Status.CLOSED)
    realized = sum(p.realized_pnl_lamports or 0 for p in pos)
    return rec.ok and rec.balance_lamports - rec.starting_lamports == realized - cost


# ---- BIGMAC_PAPER through the bus (owner order 2026-10-21, TEST_FIXTURE 50 / 20 / 12 / 20) --------------------------

def test_a_dump_after_entry_exits_on_the_stop_and_books_the_loss(engine):
    pos = entered(engine, entry_v_quote=38.0)
    h.run(engine, [price(31.0, 10)])                                   # value falls by about a third
    p = engine.ledger.get(pos.position_id)
    assert p.status is Status.CLOSED and "stop_loss" in closed_reason(engine, pos.position_id)
    assert p.realized_pnl_lamports < 0 and reconciles(engine)          # a loss is shown as a loss


def test_a_run_up_exits_on_take_profit_and_books_the_gain_after_fees(engine):
    pos = entered(engine)
    h.run(engine, [price(41.5, 10, side="buy")])
    p = engine.ledger.get(pos.position_id)
    assert p.status is Status.CLOSED and "take_profit" in closed_reason(engine, pos.position_id)
    assert p.realized_pnl_lamports > 0 and reconciles(engine)


def test_a_rise_then_a_fall_exits_on_the_trailing_stop_from_the_persisted_peak(engine, tmp_path):
    pos = entered(engine)
    h.run(engine, [price(37.5, 10, side="buy")])                       # above the 20 % activation, below take profit
    peak = engine.ledger.get(pos.position_id).peak_value_lamports
    assert engine.ledger.get(pos.position_id).status is Status.OPEN and peak > pos.cost_lamports
    h.close(engine)
    again = h.build_app(tmp_path, gmgn=h.FakeGmgn(wallet_stats=DISTINCT), rugcheck=h.FakeRugCheck())
    again.pumpapi = LiveFeed()                                           # a restart: RAM forgotten, ledger kept
    assert again.ledger.get(pos.position_id).peak_value_lamports == peak
    h.run(again, [price(37.5, 11, side="buy"), price(35.0, 12)])        # more than 12 % below the peak
    p = again.ledger.get(pos.position_id)
    assert p.status is Status.CLOSED and "trailing_stop" in closed_reason(again, pos.position_id)
    h.close(again)


def test_mode_off_stops_new_entries_but_never_the_exit_of_a_held_position(engine):
    pos = entered(engine, entry_v_quote=38.0)
    set_mode(engine.store, Mode.OFF, by="phase4-test")
    h.run(engine, [price(31.0, 10)])
    assert engine.ledger.get(pos.position_id).status is Status.CLOSED


# ---- CHAD Bot 9: Confluence Collapse (L56, L218-219), checked by the sweep ----------------------------------------

def test_L218_confluence_collapse_waits_out_the_grace_window_and_uses_a_strict_boundary(tmp_path):
    app = h.build_app(tmp_path)
    set_mode(app.store, Mode.AUTO, by="phase4-test")
    h.run(app, [events(create(mint="ScoredMintpump", dev="D2", v_quote=31.0)),
                events(buy("Q", 1, v_quote=32.0, mint="ScoredMintpump"))])
    r = app.gateway.submit_entry(EntryIntent("p4-scored", "ScoredMintpump", 50_000_000, origin="AUTO",
                                             entry_score=Decimal("80"), entry_threshold=Decimal("70")))
    assert r.ok
    opened = app.ledger.get(r.position_id).opened_ns
    scores = {"ScoredMintpump": Decimal("27.9")}                       # TEST_FIXTURE score: below 0.40 x 70 = 28
    app.collapse.score_for = scores.get
    clock = {"now": opened + (GRACE_S - 1) * S}
    app.exit_monitor.clock_ns = lambda: clock["now"]                   # the sweep's wall clock, moved forward
    assert app.exit_monitor.sweep(clock["now"]) == []                  # inside the 10-minute grace window
    clock["now"] = opened + GRACE_S * S
    scores["ScoredMintpump"] = Decimal("28")                          # exactly 40 %: not below it
    assert app.exit_monitor.sweep(clock["now"]) == []
    scores["ScoredMintpump"] = Decimal("27.99")
    clock["now"] += 10 * S
    [d] = app.exit_monitor.sweep(clock["now"])
    assert d.reason_code == "confluence_collapse" and app.ledger.get(r.position_id).status is Status.CLOSED
    h.close(app)


def test_a_fast_path_position_has_no_entry_threshold_so_collapse_never_fires_on_it(engine):
    pos = entered(engine)
    engine.collapse.score_for = lambda m: Decimal(0)                     # the worst possible score
    assert engine.exit_monitor.sweep(pos.opened_ns + 10 * GRACE_S * S) == []
    assert engine.ledger.get(pos.position_id).status is Status.OPEN and engine.collapse.no_threshold >= 1


# ---- CHAD Negative Divergence (L188-213): score-adaptive partial, only in profit, once per event, no cooldown ------

@pytest.mark.parametrize("score,fraction", [("75", "0.30"), ("74.9", "0.40"), ("65", "0.40"), ("64.9", "0.50"),
                                            ("60", "0.50"), ("59.9", "0.60")])
def test_L196_negative_divergence_sells_the_bucket_fraction_once_and_only_in_profit(tmp_path, score, fraction):
    app = h.build_app(tmp_path)
    set_mode(app.store, Mode.AUTO, by="phase4-test")
    h.run(app, [events(create(mint=M, dev="D", v_quote=31.0)), events(buy("Q", 1, v_quote=32.0, mint=M))])
    r = app.gateway.submit_entry(EntryIntent("p4-div", M, 50_000_000, origin="AUTO", entry_score=Decimal(score),
                                             entry_threshold=Decimal("60")))
    held = app.ledger.get(r.position_id).token_amount
    assert app.executioner.on_divergence(Divergence(M, "ev-loss", T0_MS * 1_000_000, "test")) == []  # flat: no profit
    h.run(app, [events(buy("UP", 3, v_quote=36.0, mint=M))])          # now in profit after every cost
    [d] = app.executioner.on_divergence(Divergence(M, "ev-1", T0_MS * 1_000_000, "test"))
    left = app.ledger.get(r.position_id).token_amount
    assert d.result.ok and held - left == int(held * Decimal(fraction))
    assert app.executioner.on_divergence(Divergence(M, "ev-1", T0_MS * 1_000_000, "test")) == []    # same event
    h.close(app)


def test_L200_after_the_divergence_remainder_is_closed_the_token_can_be_entered_again_at_once(tmp_path):
    app = h.build_app(tmp_path)
    set_mode(app.store, Mode.AUTO, by="phase4-test")
    h.run(app, [events(create(mint=M, dev="D", v_quote=31.0)), events(buy("Q", 1, v_quote=32.0, mint=M))])
    r = app.gateway.submit_entry(EntryIntent("p4-div", M, 50_000_000, origin="AUTO", entry_score=Decimal("80"),
                                             entry_threshold=Decimal("60")))
    h.run(app, [events(buy("UP", 3, v_quote=36.0, mint=M))])
    app.executioner.on_divergence(Divergence(M, "ev-1", T0_MS * 1_000_000, "test"))
    from jinx.execution import ExitIntent
    assert app.gateway.submit_exit(ExitIntent("p4-close-rest", r.position_id)).ok
    again = app.gateway.submit_entry(EntryIntent("p4-reenter", M, 50_000_000, origin="AUTO"))
    assert again.ok                                                    # "No exit cooldown"
    h.close(app)


# ---- CHAD Bot 10 Sybil emergency exit (L81-82) ---------------------------------------------------------------------

def test_L82_shared_funders_found_after_entry_fire_the_emergency_exit_on_the_next_price(engine):
    pos = entered(engine)
    from jinx.intel.early_momentum import Funding
    for w in ("DEV", "A", "B", "C"):                                   # late GMGN answers: one funder for them all
        engine.early.record_funding(Funding(w, "ONE_FUNDER", "test: shared funder", 0))
    assert engine.early.evidence(M).emergency_exit is True
    h.run(engine, [price(32.6, 10, side="buy")])
    p = engine.ledger.get(pos.position_id)
    assert p.status is Status.CLOSED and "SYBIL" in closed_reason(engine, pos.position_id)


# ---- ownership, failed exits (V16) ---------------------------------------------------------------------------------

def test_a_failed_sell_keeps_the_position_owned_and_the_next_price_retries_it(engine):
    pos = entered(engine, entry_v_quote=38.0)
    real = engine.paper.sell
    calls = []

    def no_quote(position_id, intent_id, token_amount, **kw):
        calls.append(intent_id)
        if len(calls) == 1:
            engine.ledger.abort_exit(position_id, "NO_QUOTE (phase4 injected)")
            from jinx.paper import ExecResult
            return ExecResult(False, position_id, "NO_QUOTE")
        return real(position_id, intent_id, token_amount, **kw)
    engine.paper.sell = no_quote
    h.run(engine, [price(31.0, 10)])
    assert engine.ledger.get(pos.position_id).status is Status.OPEN                # still held and still owned
    h.run(engine, [price(30.9, 11)])
    p = engine.ledger.get(pos.position_id)
    assert p.status is Status.CLOSED and len(calls) == 2 and reconciles(engine)


def test_two_exit_deciders_on_one_position_never_sell_more_than_is_held(tmp_path):
    app = h.build_app(tmp_path)
    set_mode(app.store, Mode.AUTO, by="phase4-test")
    save_bigmac_config(app.store, p3.BIGMAC_FIXTURE)
    h.run(app, [events(create(mint=M, dev="D", v_quote=31.0)), events(buy("Q", 1, v_quote=32.0, mint=M))])
    r = app.gateway.submit_entry(EntryIntent("p4-both", M, 50_000_000, origin="AUTO", entry_score=Decimal("80"),
                                             entry_threshold=Decimal("60"), exit_policy="BIGMAC_PAPER",
                                             exit_policy_config=p3.BIGMAC_FIXTURE.to_dict()))
    held = app.ledger.get(r.position_id).token_amount
    h.run(app, [events(buy("UP", 3, v_quote=42.0, mint=M))])         # take profit fires on this price
    app.executioner.on_divergence(Divergence(M, "ev-1", T0_MS * 1_000_000, "test"))   # and a divergence arrives
    sold = app.store.conn.execute("SELECT COALESCE(SUM(token_amount), 0) FROM fills WHERE position_id = ? AND "
                                  "side = 'sell'", (r.position_id,)).fetchone()[0]
    assert sold <= held and app.ledger.get(r.position_id).token_amount == held - sold
    assert app.wallet.reconcile().ok
    h.close(app)
