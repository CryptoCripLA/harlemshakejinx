"""V05: every hard safety rejection, verified end to end in the running engine.

CHAD L16 (docs/CHAD_BLUEPRINT.md): "Runs RugCheck, honeypot detection, and mint/freeze authority analysis on every
candidate. ... If it fails here, it never reaches your bot." CHAD L106: "RUG_FILTER tokens are blocked from execution
regardless of safety score" (no X Scout data source: B-05, BLOCKED in the matrix). Owner B-19/B-20 (2026-10-21): the
hard vetoes are exactly RugCheck's own `rugged: true` and a mint or freeze authority still held (GMGN or RugCheck);
RugCheck "danger" findings are evidence and never block. Missing, late or unreadable evidence is UNKNOWN and never
eligible (owner mandate 2026-10-20: "fails closed when mandatory evidence is unknown").

Each case runs the same Early Momentum detection through `JinxApp` (every other predicate passing) with one safety
fact changed, and reads the entry decision view. The GMGN security body is the recorded live response
(tests/fixtures/gmgn_security_sample_20261021.json), RugCheck's is the recorded live report mapped by the real
adapter function (`jinx.adapters.rugcheck.client.interpret`)."""
import json
from pathlib import Path

import pytest

from jinx.adapters.rugcheck.client import interpret
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import DISTINCT, LiveFeed, M, baseline_msgs
from tests.pumpapi_msgs import events

FIX = Path(__file__).resolve().parent.parent / "fixtures"
SEC = json.loads((FIX / "gmgn_security_sample_20261021.json").read_text())["security"]
RC = json.loads((FIX / "rugcheck_report_sample_20261021.json").read_text())["report"]


class ReportRugCheck(h.FakeRugCheck):
    """Answers with the real adapter's mapping of a (changed) recorded RugCheck report."""

    def __init__(self, report: dict):
        super().__init__()
        self.raw = report

    async def report(self, mint, priority=10):
        self.calls.append(mint)
        return interpret(self.raw)


def screen(tmp_path, security=SEC, rc_report=RC, gmgn_mode="ok"):
    gmgn = h.FakeGmgn(mode=gmgn_mode, security=security, wallet_stats=DISTINCT, fail_methods={"getTokenSecurity"})
    rugcheck = ReportRugCheck(rc_report)
    app = h.build_app(tmp_path, gmgn=gmgn, rugcheck=rugcheck)
    app.pumpapi = LiveFeed()
    h.run(app, [events(m) for m in baseline_msgs()])
    out = app.entry_check(M), app.candidates[M]["safety"], gmgn, rugcheck
    h.close(app)
    return out


def test_the_recorded_live_responses_pass_every_check(tmp_path):
    check, safety, _, _ = screen(tmp_path)
    assert safety["verdict"] == "PASS" and check["all_checks_pass"] is True


def test_L16_a_reported_honeypot_is_a_hard_veto(tmp_path):
    check, safety, _, rc = screen(tmp_path, security={**SEC, "honeypot": 1})
    assert safety["verdict"] == "FAIL" and safety["checks"]["honeypot"] == "FAIL"
    assert check["all_checks_pass"] is False
    assert rc.calls == []                                   # vetoed by GMGN: no RugCheck request spent on it


def test_L16_a_token_that_cannot_be_sold_is_a_hard_veto(tmp_path):
    check, safety, _, _ = screen(tmp_path, security={**SEC, "can_not_sell": 1})
    assert safety["checks"]["honeypot"] == "FAIL" and check["all_checks_pass"] is False


def test_L16_a_held_mint_authority_is_a_hard_veto(tmp_path):
    check, safety, _, _ = screen(tmp_path, security={**SEC, "renounced_mint": False})
    assert safety["checks"]["mint_authority"] == "FAIL" and check["all_checks_pass"] is False


def test_L16_a_held_freeze_authority_is_a_hard_veto(tmp_path):
    check, safety, _, _ = screen(tmp_path, security={**SEC, "renounced_freeze_account": False})
    assert safety["checks"]["freeze_authority"] == "FAIL" and check["all_checks_pass"] is False


@pytest.mark.parametrize("change", [{"rugged": True}, {"mintAuthority": "Auth1"}, {"freezeAuthority": "Auth2"}])
def test_L16_rugchecks_own_hard_findings_are_a_hard_veto(tmp_path, change):
    check, safety, _, _ = screen(tmp_path, rc_report={**RC, **change})
    assert safety["checks"]["rugcheck"] == "FAIL" and safety["verdict"] == "FAIL"
    assert check["all_checks_pass"] is False


def test_B20_rugcheck_danger_findings_alone_never_block(tmp_path):
    danger = {**RC, "risks": [{"name": "Creator history of rugged tokens", "level": "danger", "score": 9000},
                              {"name": "Large Amount of LP Unlocked", "level": "danger", "score": 9999}]}
    check, safety, _, _ = screen(tmp_path, rc_report=danger)
    assert safety["verdict"] == "PASS" and check["all_checks_pass"] is True


def test_L16_no_security_data_is_unknown_and_never_eligible(tmp_path):
    check, safety, _, _ = screen(tmp_path, gmgn_mode="down")
    assert safety["verdict"] == "UNKNOWN" and check["all_checks_pass"] is False


@pytest.mark.parametrize("field", ["honeypot", "renounced_mint", "renounced_freeze_account"])
def test_L16_an_unreadable_safety_flag_is_unknown_never_a_pass(tmp_path, field):
    sec = {**SEC, field: "maybe"}
    if field == "honeypot":
        sec.pop("is_honeypot", None)
    check, safety, _, _ = screen(tmp_path, security=sec)
    assert safety["verdict"] in ("UNKNOWN", "FAIL") and check["all_checks_pass"] is False


def test_L16_a_malformed_rugcheck_report_is_unknown_never_a_pass(tmp_path):
    check, safety, _, _ = screen(tmp_path, rc_report={"risks": []})
    assert safety["checks"]["rugcheck"] == "UNKNOWN" and check["all_checks_pass"] is False


def test_L16_a_vetoed_token_stays_ineligible_whatever_its_momentum_does_next(tmp_path):
    gmgn = h.FakeGmgn(security={**SEC, "renounced_mint": False}, wallet_stats=DISTINCT)
    app = h.build_app(tmp_path, gmgn=gmgn, rugcheck=ReportRugCheck(RC))
    app.pumpapi = LiveFeed()
    h.run(app, [events(m) for m in baseline_msgs()])
    from tests.pumpapi_msgs import buy
    h.run(app, [events(buy(w, 4 + i, v_quote=33.0 + i, mint=M)) for i, w in enumerate(("D", "E", "F"))])
    assert app.entry_check(M)["all_checks_pass"] is False and app.candidates[M]["safety"]["verdict"] == "FAIL"
    h.close(app)
