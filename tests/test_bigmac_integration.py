"""Owner order 2026-10-21 §4: STRATEGY=AUTO, EXECUTION=PAPER, LIVE_SIGNING=DISABLED.

PAPER BUY -> PRICE CHANGES -> HARD STOP / TAKE PROFIT / TRAILING STOP -> PAPER SELL -> LEDGER RECONCILIATION, each
exit separately and with several conditions true at once; OFF blocks new entries but protective monitoring and exits
continue. The exit runs through the existing path only: PositionMonitor -> ExecutionGateway.submit_exit ->
PaperExecutor.sell -> PositionLedger. The BigMac numbers here are labelled TEST_FIXTURE settings, not strategy.
"""
from decimal import Decimal

import pytest

from jinx import config
from jinx.core.events import EventKind
from jinx.execution import EntryIntent, ExecutionGateway, LiveExecutionUnavailable
from jinx.exits.bigmac_exit import evaluate_exit
from jinx.exits.monitor import PositionMonitor
from jinx.exits.policy import BIGMAC_PAPER, BigMacPaperConfig, load_bigmac_config, save_bigmac_config
from jinx.ledger import PositionLedger, Status
from jinx.modes import EXECUTION_KEY, Mode, get_execution_mode, set_mode
from jinx.paper import PaperExecutor, PaperWallet, sell_proceeds
from jinx.registry import TokenRegistry
from jinx.store import Store
from tests.conftest import make_create, make_trade

K = 30_000_000_000 * 1_073_000_000_000_000          # constant product of a fresh pump.fun curve (virtual reserves)
BUDGET = 100_000_000                                 # TEST_FIXTURE paper buy size (0.1 SOL), not a sizing rule
FIXTURE = BigMacPaperConfig(take_profit_pct=50, stop_loss_pct=20, trailing_stop_pct=12,
                            trailing_stop_activation_pct=20, label="TEST_FIXTURE_bigmac_integration", set_by="test")
TRAIL_ONLY = BigMacPaperConfig(take_profit_pct=0, stop_loss_pct=20, trailing_stop_pct=12,
                               trailing_stop_activation_pct=20, label="TEST_FIXTURE_trail_only", set_by="test")


class Rig:
    def __init__(self, path):
        self.store = Store(path)
        self.tokens = TokenRegistry()                 # market state is RAM only (storage repair 2026-10-21)
        self.ledger = PositionLedger(self.store)
        self.wallet = PaperWallet(self.store, config.PAPER_START_LAMPORTS)
        self.paper = PaperExecutor(self.store, self.ledger, self.wallet, self.tokens, config.PUMP_CURVE_FEES,
                                   config.NETWORK_FEE_LAMPORTS)
        self.gateway = ExecutionGateway(self.store, self.ledger, self.paper)
        self.monitor = PositionMonitor(self.ledger, self.tokens, self.gateway, config.PUMP_CURVE_FEES)
        self.t = 0

    def price(self, mint, v_sol):
        """A trade that leaves the curve at `v_sol` virtual SOL (constant product): one valid price update."""
        self.t += 1_000_000_000
        ev = make_trade(mint=mint, v_sol=v_sol, v_tokens=K // v_sol, recv_ns=self.t, seq=self.t)
        self.tokens.apply(ev)
        return self.monitor.on_event(ev)

    def buy(self, mint="MINT", cfg=FIXTURE, origin="AUTO", policy=BIGMAC_PAPER, v_sol=30_000_000_000):
        self.tokens.apply(make_create(mint=mint))
        self.price(mint, v_sol)
        r = self.gateway.submit_entry(EntryIntent(f"entry-{mint}-{self.t}", mint, BUDGET, origin=origin,
                                                  exit_policy=policy, exit_policy_config=cfg and cfg.to_dict()))
        assert r.ok, r
        return r.position_id

    def value(self, pid, v_sol):
        held = self.ledger.get(pid).token_amount
        return sell_proceeds(v_sol, K // v_sol, held, config.PUMP_CURVE_FEES.total_bps)[0]

    def close(self):
        self.store.close()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path / "jinx.db")
    set_mode(r.store, Mode.AUTO)
    assert get_execution_mode(r.store).value == "PAPER" and r.gateway.live is None   # LIVE_SIGNING=DISABLED
    yield r
    r.close()


def _v_sol_for_value(rig, pid, target):
    """The curve SOL at which the held tokens are worth about `target` (searches the integer curve)."""
    lo, hi = 1_000_000_000, 400_000_000_000
    while lo < hi:
        mid = (lo + hi) // 2
        if rig.value(pid, mid) < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _reconciled(rig, pid):
    pos = rig.ledger.get(pid)
    rec = rig.wallet.reconcile()
    sells = rig.store.conn.execute("SELECT COALESCE(SUM(sol_lamports), 0), COALESCE(SUM(token_amount), 0)"
                                   " FROM fills WHERE position_id = ? AND side = 'sell'", (pid,)).fetchone()
    bought = rig.ledger.bought_tokens(pid)
    assert rec.ok
    assert sells[1] + pos.token_amount == bought                  # every token accounted for
    assert sells[0] == pos.proceeds_lamports
    # non-trading wallet entries (a test's own drain/refund) are counted apart from the position
    other = rig.store.conn.execute("SELECT COALESCE(SUM(delta_lamports), 0) FROM paper_wallet_entries"
                                   " WHERE entry_id LIKE 'test-%'").fetchone()[0]
    assert rec.balance_lamports == config.PAPER_START_LAMPORTS - pos.cost_lamports - pos.network_fees_lamports \
        + pos.proceeds_lamports + other
    return pos


def test_hard_stop_paper_sell_and_reconciliation(rig):
    pid = rig.buy()
    entry = rig.ledger.entry_basis(pid)
    stop = entry - entry * 20 // 100
    v = _v_sol_for_value(rig, pid, stop + 1) + 50_000_000       # still above the stop: no exit
    assert rig.price("MINT", v) is None and rig.ledger.get(pid).status is Status.OPEN
    v = _v_sol_for_value(rig, pid, stop) - 1                     # value <= stop
    d = rig.price("MINT", v)
    assert d.reason_code == "stop_loss" and d.result.ok
    pos = _reconciled(rig, pid)
    assert pos.status is Status.CLOSED and pos.token_amount == 0
    assert pos.realized_pnl_lamports < 0                         # a stop is a loss, shown as one
    ex = rig.store.conn.execute("SELECT * FROM exit_intents WHERE position_id = ?", (pid,)).fetchone()
    assert (ex["exit_policy"], ex["reason_code"], ex["exit_urgency"], ex["source_strategy"]) == \
        (BIGMAC_PAPER, "stop_loss", "protective", "AUTO")


def test_take_profit_paper_sell_and_reconciliation(rig):
    pid = rig.buy()
    entry = rig.ledger.entry_basis(pid)
    take = entry * 150 // 100
    assert rig.price("MINT", _v_sol_for_value(rig, pid, take) - 2_000_000) is None
    d = rig.price("MINT", _v_sol_for_value(rig, pid, take))
    assert d.reason_code == "take_profit" and d.result.ok
    pos = _reconciled(rig, pid)
    assert pos.status is Status.CLOSED and pos.realized_pnl_lamports > 0


def test_trailing_stop_after_activation_paper_sell_and_reconciliation(rig):
    pid = rig.buy(cfg=TRAIL_ONLY)
    entry = rig.ledger.entry_basis(pid)
    activation = entry * 120 // 100
    top = _v_sol_for_value(rig, pid, activation * 11 // 10)      # past activation: the trail is armed
    assert rig.price("MINT", top) is None
    peak = rig.ledger.get(pid).peak_value_lamports
    assert peak >= activation
    floor = peak - peak * 12 // 100
    assert rig.price("MINT", _v_sol_for_value(rig, pid, floor + 1) + 20_000_000) is None
    d = rig.price("MINT", _v_sol_for_value(rig, pid, floor) - 1)
    assert d.reason_code == "trailing_stop" and d.result.ok
    pos = _reconciled(rig, pid)
    assert pos.status is Status.CLOSED


def test_the_monitor_decides_exactly_what_the_port_decides(rig):
    pid = rig.buy()
    seen = []
    for v in (30_500_000_000, 33_000_000_000, 29_000_000_000, 36_000_000_000, 31_000_000_000, 26_000_000_000):
        pos = rig.ledger.get(pid)
        if pos.status is not Status.OPEN:
            break
        expected, _ = evaluate_exit(rig.value(pid, v), rig.ledger.entry_basis(pid), pos.peak_value_lamports or 0,
                                    50, 20, 12, 20)
        d = rig.price("MINT", v)
        seen.append((expected and expected.value, d and d.reason_code))
    assert all(a == b for a, b in seen) and seen[-1][0] is not None


def test_several_conditions_true_at_once_sell_once_by_decision_order(rig):
    # take profit 0.5 % floors to 0 % (= the entry value) and the stop 0 % is the entry value too: at exactly the
    # entry value both are true; stop_loss comes first, and only one sell happens.
    cfg = BigMacPaperConfig(0.5, 0, 0, 0, label="TEST_FIXTURE_both_true", set_by="test")
    pid = rig.buy(cfg=cfg)
    entry = rig.ledger.entry_basis(pid)
    v = _v_sol_for_value(rig, pid, entry)
    assert rig.value(pid, v) >= entry and rig.value(pid, v - 1) < entry
    d = rig.price("MINT", v - 1)
    assert d.reason_code == "stop_loss"
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1


def test_the_same_price_update_twice_never_sells_twice(rig):
    pid = rig.buy()
    entry = rig.ledger.entry_basis(pid)
    v = _v_sol_for_value(rig, pid, entry * 70 // 100)
    ev = make_trade(mint="MINT", v_sol=v, v_tokens=K // v, recv_ns=10 ** 15, seq=10 ** 15)
    rig.tokens.apply(ev)
    assert rig.monitor.on_event(ev).result.ok
    assert rig.monitor.on_event(ev) is None                      # no open position left to sell
    rig.monitor.sweep(10 ** 15 + 1)
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1


def test_off_blocks_new_entries_but_protective_exits_continue(rig):
    pid = rig.buy()
    set_mode(rig.store, Mode.OFF)
    rig.tokens.apply(make_create(mint="OTHER"))
    rig.price("OTHER", 30_000_000_000)
    refused = rig.gateway.submit_entry(EntryIntent("entry-off", "OTHER", BUDGET, origin="AUTO"))
    assert not refused.ok and refused.reason == "MODE_OFF"
    entry = rig.ledger.entry_basis(pid)
    d = rig.price("MINT", _v_sol_for_value(rig, pid, entry * 70 // 100))
    assert d.reason_code == "stop_loss" and d.result.ok
    assert _reconciled(rig, pid).status is Status.CLOSED


def test_peak_survives_restart_and_the_trail_still_fires(tmp_path):
    path = tmp_path / "jinx.db"
    r = Rig(path)
    set_mode(r.store, Mode.AUTO)
    pid = r.buy(cfg=TRAIL_ONLY)
    entry = r.ledger.entry_basis(pid)
    top = _v_sol_for_value(r, pid, entry * 140 // 100)
    r.price("MINT", top)
    peak = r.ledger.get(pid).peak_value_lamports
    market_state = r.tokens.get("MINT")
    r.store.close()
    # restart: a new process, a fresh (empty) market view, the ledger from disk
    r2 = Rig(path)
    r2.tokens.apply(make_create(mint="MINT"))
    assert r2.ledger.get(pid).peak_value_lamports == peak
    floor = peak - peak * 12 // 100
    v = _v_sol_for_value(r2, pid, floor) - 1
    # what BigMac's own loop would do after a restart (peak reset to 0): the current value becomes the peak, no exit
    fresh_reason, _ = evaluate_exit(r2.value(pid, v), entry, 0, 0, 20, 12, 20)
    d = r2.price("MINT", v)
    assert d.reason_code == "trailing_stop" and d.result.ok
    assert fresh_reason is None                                   # the persisted peak is what made it fire
    assert market_state.v_sol_lamports == top
    r2.close()


def test_positions_of_another_strategy_or_policy_are_never_touched(rig):
    copy_pid = rig.buy(mint="COPYMINT", origin="COPY", policy=None, cfg=None)
    rig.price("COPYMINT", 10_000_000_000)                       # a crash: BigMac would sell, but it is not its position
    assert rig.ledger.get(copy_pid).status is Status.OPEN
    assert not rig.store.conn.execute("SELECT 1 FROM fills WHERE position_id = ? AND side='sell'",
                                      (copy_pid,)).fetchone()


def test_a_failed_paper_sell_is_not_a_fill_and_the_position_stays_owned(rig):
    pid = rig.buy()
    rig.wallet.apply("test-drain", -rig.wallet.balance(), "TEST: drain so the sell cannot pay its network fee")
    v = 10_000_000                                              # a near-total crash: proceeds below the 5,000 fee
    assert rig.value(pid, v) < config.NETWORK_FEE_LAMPORTS
    d = rig.price("MINT", v)
    assert not d.result.ok and d.result.reason == "INSUFFICIENT_BALANCE_FOR_FEE"
    pos = rig.ledger.get(pid)
    assert pos.status is Status.OPEN and pos.token_amount == rig.ledger.bought_tokens(pid)
    assert not rig.store.conn.execute("SELECT 1 FROM fills WHERE side = 'sell'").fetchone()
    stages = [e["stage"] for e in rig.ledger.order_events(d.intent_id)]
    assert stages[-1] == "FAILED" and "FILLED" not in stages
    rig.wallet.apply("test-refund", 10_000_000, "TEST: refund")
    d2 = rig.price("MINT", v - 1_000)                           # the same stop retries on the next price update
    assert d2.intent_id == d.intent_id and d2.result.ok
    assert _reconciled(rig, pid).status is Status.CLOSED


def test_lifecycle_stages_and_latency_are_recorded(rig):
    pid = rig.buy()
    entry = rig.ledger.entry_basis(pid)
    d = rig.price("MINT", _v_sol_for_value(rig, pid, entry * 60 // 100))
    ev = rig.ledger.order_events(d.intent_id)
    assert [e["stage"] for e in ev] == ["DECIDED", "SUBMITTED", "FILLED"]
    assert all(e["simulated"] == 1 for e in ev)
    assert ev[0]["ts_ns"] <= ev[1]["ts_ns"] <= ev[2]["ts_ns"]
    fill = rig.store.conn.execute("SELECT quote_lamports, sol_lamports FROM fills WHERE side='sell'").fetchone()
    assert fill["quote_lamports"] == fill["sol_lamports"]        # paper fill at the decision reserves: no slippage


def test_live_execution_stays_unavailable(rig):
    rig.store.set_setting(EXECUTION_KEY, "LIVE", by="test")
    with pytest.raises(LiveExecutionUnavailable):
        rig.gateway.submit_entry(EntryIntent("live-entry", "MINT", BUDGET, origin="AUTO"))


def test_bigmac_settings_are_explicit_labelled_and_never_defaults(store):
    assert load_bigmac_config(store) is None                     # nothing configured: the policy is inactive
    with pytest.raises(ValueError):
        BigMacPaperConfig(10, 10, 12, 20, label="LIVE", set_by="x")
    with pytest.raises(TypeError):
        BigMacPaperConfig(None, 10, 12, 20, label=BIGMAC_PAPER, set_by="x")
    save_bigmac_config(store, BigMacPaperConfig(30, 15, 12, 20, label=BIGMAC_PAPER, set_by="owner"))
    cfg = load_bigmac_config(store)
    assert (cfg.take_profit_pct, cfg.stop_loss_pct, cfg.label) == (30, 15, BIGMAC_PAPER)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_paper_config_refuses_non_finite_percents(bad):
    with pytest.raises(ValueError):
        BigMacPaperConfig(bad, 10, 12, 20, label=BIGMAC_PAPER, set_by="x")
    with pytest.raises(ValueError):
        BigMacPaperConfig(10, 10, 12, bad, label=BIGMAC_PAPER, set_by="x")


def test_a_trade_without_reserves_is_not_a_price_update(rig):
    pid = rig.buy()
    from jinx.core.events import MarketEvent, Side
    ev = MarketEvent(kind=EventKind.TRADE, source="test", mint="MINT", signature="NORES", recv_ns=1, seq=1,
                     trader="W", side=Side.SELL, sol_lamports=1, token_amount=1)
    assert rig.monitor.on_event(ev) is None
    assert rig.ledger.get(pid).status is Status.OPEN
