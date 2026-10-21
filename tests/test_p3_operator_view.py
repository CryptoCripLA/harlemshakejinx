"""One compact operating view (CLAUDE.md Phase 3 "Operator interface"; CHAD C19 ConfluenceRadar): active signals,
entry qualification, safety state, open positions, entry versus current confluence, outstanding orders, position age,
exit reason and status, available balance, realized cost-adjusted P&L, latency and feed health. Money for the owner
is shown in US dollars with the rate's source and time; it is never on the trading path."""
import asyncio
import json
from decimal import Decimal

from jinx.operator_view import SolUsd, operator_view, render_text
from jinx.ledger import Status
from tests.pumpapi_msgs import buy, events
from tests.test_p3_engine_paper_trade import engine, launch, run
from tests.test_app_event_intel import close


def test_operator_view_shows_every_required_section(tmp_path):
    app = engine(tmp_path)
    run(app, (launch(), None))
    app.sol_usd = SolUsd(Decimal("105.70"), "GMGN getGasPrice native_token_usd_price (TEST)", 1_792_540_000 * 10 ** 9)
    v = operator_view(app)
    for key in ("mode", "execution", "live_signing", "signals", "open_positions", "outstanding_orders",
                "recent_exits", "balance", "pnl", "latency", "feeds", "usd_rate"):
        assert key in v, key
    assert v["mode"] == "AUTO" and v["execution"] == "PAPER" and v["live_signing"] == "DISABLED"
    [sig] = v["signals"]
    assert sig["mint"] == "MintApump" and sig["entry_decision"]["ok"] is True and sig["safety"] == "PASS"
    [pos] = v["open_positions"]
    assert pos["exit_policy"] == "BIGMAC_PAPER" and pos["age_s"] >= 0
    assert pos["entry_confluence"] is None and pos["current_confluence"] is None   # B-03: no score exists
    assert v["balance"]["usd"] < 2 * 105.70 and v["outstanding_orders"] == []
    json.dumps(v, default=str)
    text = render_text(v)
    body = [line for line in text.splitlines() if not line.startswith("Dollar rate:")]
    assert "$" in text and not any(" SOL" in line for line in body) and "lamport" not in text.lower()
    assert text.count("105.70") == 1 and "per SOL from GMGN" in text   # the rate, stated once, owner's wording
    close(app)


def test_the_view_and_report_never_touch_the_trading_path(tmp_path):
    import inspect
    import jinx.operator_view as ov
    src = inspect.getsource(ov)
    assert "submit_entry" not in src and "submit_exit" not in src and ".sell(" not in src and ".buy(" not in src


def test_the_dollar_rate_comes_from_gmgn_gas_price_and_a_failure_keeps_the_last_rate(tmp_path):
    from tests.test_app_event_intel import make_app

    class Gmgn:
        running = True
        answers = [{"native_token_usd_price": 105.7}, RuntimeError("down")]

        async def call(self, method, *args):
            assert (method, args) == ("getGasPrice", ("sol",))
            a = self.answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
    app = make_app(tmp_path, gmgn=Gmgn())
    app.SOL_USD_POLL_S = 0

    async def two_reads():
        task = asyncio.create_task(app._sol_usd_loop())
        for _ in range(50):
            await asyncio.sleep(0)
            if not app.gmgn.answers:
                break
        task.cancel()
    asyncio.run(two_reads())
    assert app.sol_usd.rate == Decimal("105.7") and "native_token_usd_price" in app.sol_usd.source
    close(app)
