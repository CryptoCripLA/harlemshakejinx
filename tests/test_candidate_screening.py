"""End to end in the app: an Early Momentum detection (C10) is screened at once by the safety gate (C01: GMGN
security, then RugCheck at top priority; C08 developer history), re-checked while RugCheck has not indexed the
token, and the buyers' funders are fetched from GMGN for CHAD's Sybil check. Nothing here places an order."""
import asyncio
import json
from pathlib import Path

from jinx import config
from jinx.adapters.rugcheck.client import interpret, unknown
from jinx.app import JinxApp
from jinx.intel.safety import SafetyService
from tests.pumpapi_msgs import buy, create, events

FIX = Path(__file__).parent / "fixtures"
SEC = json.loads((FIX / "gmgn_security_sample_20261021.json").read_text())
WALLET = json.loads((FIX / "gmgn_wallet_stats_sample_20261021.json").read_text())["stats"]
RC = json.loads((FIX / "rugcheck_report_sample_20261021.json").read_text())["report"]


class Gmgn:
    running = True

    def __init__(self):
        self.calls = []

    async def call(self, method, chain, arg, *rest):
        self.calls.append(method)
        if method == "getTokenSecurity":
            return SEC["security"]
        if method == "getCreatedTokens":
            return SEC["created_tokens_head"]
        if method == "getWalletStats":     # like the live API: several addresses -> only the first wallet's stats
            return dict(WALLET, wallet_address=arg[0], common=dict(WALLET["common"], fund_from_address=f"F-{arg[0]}"))


class RugCheck:
    def __init__(self):
        self.calls = []

    async def report(self, mint, priority=10):
        self.calls.append(priority)
        return unknown("not_indexed") if len(self.calls) == 1 else interpret(RC)


def test_a_detection_is_screened_rechecked_until_indexed_and_funders_are_recorded(tmp_path):
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    app.SCREEN_RETRY_S = 0.01
    app.gmgn, rc = Gmgn(), RugCheck()
    app.safety = SafetyService(app.gmgn, rugcheck=rc)

    async def main():
        for m in [create(initial_buy=0.0, v_quote=30.0), buy("A", 1), buy("B", 10), buy("C", 20)]:
            for ev in events(m):
                app.early.on_event(ev)
        for _ in range(100):
            await asyncio.sleep(0.01)
            if not app._background:
                break
    asyncio.run(main())
    [(mint, entry)] = app.candidates.items()
    assert entry["safety"]["verdict"] == "PASS" and entry["safety"]["attempts"] == 2
    assert rc.calls == [0, 0]                                   # fast path: top priority, re-asked once indexed
    assert entry["trade_permitted"] is True                     # every Early Momentum predicate passes
    assert entry["entry_check"]["all_checks_pass"] is False     # no live PumpAPI feed in this test: reserves not current
    assert "stale" in entry["entry_check"]["reserve_reason"]
    ev = app.early.evidence(mint)
    sybil = [p for p in ev.predicates if "sybil" in p.name][0]
    assert sybil.status.value == "PASS"                         # all three buyers' funders known, none shared
    assert app.gmgn.calls.count("getWalletStats") == 3          # one request per wallet
    assert "getWalletStats" in app.gmgn.calls
    app.market.close(); app.store.close()
