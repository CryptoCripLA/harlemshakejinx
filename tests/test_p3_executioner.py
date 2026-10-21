"""CHAD Bot 9, The Executioner, and CHAD's Sybil emergency exit, on the one monitor -> gateway -> ledger path.

CHAD (docs/CHAD_BLUEPRINT.md): "Watches the live Confluence Score for every open position every 10 seconds.
Auto-exits on Confluence Collapse — when the score drops below 40% of the entry threshold." "A 10-minute grace
window applies after entry." Negative Divergence: "Score 75+ 30% exit … 65–74 40% … 60–64 50% … < 60 60%",
"Only fires when you're in profit", "Re-entry allowed immediately". Sybil: "If 3+ wallets share the same SOL funder
→ emergency exit fires instantly". Scores and thresholds below are labelled test fixtures (B-03: CHAD gives no
confluence weights or entry threshold).
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jinx import config
from jinx.execution import EntryIntent
from jinx.exits.chad import SybilEmergencyExit
from jinx.exits.executioner import (CHECK_INTERVAL_S, COLLAPSE_FRACTION, GRACE_S, ConfluenceCollapse, Divergence,
                                    Executioner, divergence_exit_fraction)
from jinx.exits.policy import BIGMAC_PAPER
from jinx.ledger import Status
from jinx.modes import Mode, set_mode
from tests.conftest import make_create
from tests.test_bigmac_integration import FIXTURE, K, Rig

S = 1_000_000_000


class Clock:
    def __init__(self):
        self.now = 1_000 * S

    def __call__(self):
        return self.now


class Scores:
    """A labelled test fixture standing in for the confluence board (CHAD gives no weights: B-03)."""
    def __init__(self):
        self.values = {}

    def __call__(self, mint):
        return self.values.get(mint)


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    r = Rig(tmp_path / "jinx.db")
    r.ledger.clock_ns = clock
    r.clock = clock
    r.scores = Scores()
    r.collapse = ConfluenceCollapse(r.scores, r.ledger)
    r.monitor.protective = [r.collapse]
    r.monitor.clock_ns = clock
    r.executioner = Executioner(r.monitor, r.ledger, config.NETWORK_FEE_LAMPORTS)
    set_mode(r.store, Mode.AUTO)
    yield r
    r.close()


def _buy(rig, mint="MINT", score="80", threshold="70", policy=None):
    rig.tokens.apply(make_create(mint=mint))
    rig.price(mint, 30_000_000_000)
    r = rig.gateway.submit_entry(EntryIntent(f"e-{mint}-{rig.t}", mint, 100_000_000, origin="AUTO",
                                             entry_score=Decimal(score) if score else None,
                                             entry_threshold=Decimal(threshold) if threshold else None,
                                             exit_policy=policy,
                                             exit_policy_config=FIXTURE.to_dict() if policy else None))
    assert r.ok
    return r.position_id


def test_chad_numbers_are_exactly_chads():
    assert (COLLAPSE_FRACTION, GRACE_S, CHECK_INTERVAL_S) == (Decimal("0.40"), 600, 10)
    assert [divergence_exit_fraction(Decimal(s)) for s in ("90", "75", "74.9", "65", "64", "60", "59.9", "0")] == \
        [Decimal(x) for x in ("0.30", "0.30", "0.40", "0.40", "0.50", "0.50", "0.60", "0.60")]
    assert divergence_exit_fraction(None) is None


def test_confluence_collapse_full_exit_after_the_grace_window(rig):
    pid = _buy(rig, threshold="70")
    rig.scores.values["MINT"] = Decimal("27.9")              # below 70 * 0.40 = 28
    rig.clock.now += (GRACE_S - 1) * S
    assert rig.monitor.sweep() == []                          # inside the 10-minute grace window: no exit
    rig.clock.now += 1 * S
    [d] = rig.monitor.sweep()
    assert d.reason_code == "confluence_collapse" and d.result.ok and d.source == "CHAD_CONFLUENCE_COLLAPSE"
    pos = rig.ledger.get(pid)
    assert pos.status is Status.CLOSED and pos.token_amount == 0


def test_no_collapse_at_or_above_the_boundary_or_without_a_score(rig):
    pid = _buy(rig, threshold="70")
    rig.clock.now += GRACE_S * S
    rig.scores.values["MINT"] = Decimal("28")                 # exactly 40 %: "below" is required
    assert rig.monitor.sweep() == []
    rig.scores.values.pop("MINT")                             # no live score: nothing is assumed
    assert rig.monitor.sweep() == []
    assert rig.ledger.get(pid).status is Status.OPEN
    assert rig.collapse.unavailable >= 1


def test_no_collapse_without_an_entry_threshold(rig):
    pid = _buy(rig, score=None, threshold=None)               # a fast-path entry has no score or threshold
    rig.clock.now += GRACE_S * S
    rig.scores.values["MINT"] = Decimal("0")
    assert rig.monitor.sweep() == []
    assert rig.ledger.get(pid).status is Status.OPEN


def test_protective_chad_exit_runs_before_the_bigmac_policy(rig):
    pid = _buy(rig, threshold="70", policy=BIGMAC_PAPER)
    rig.clock.now += GRACE_S * S
    rig.scores.values["MINT"] = Decimal("1")
    entry = rig.ledger.entry_basis(pid)
    v = 20_000_000_000                                        # BigMac's stop would also fire here
    assert rig.value(pid, v) < entry * 80 // 100
    d = rig.price("MINT", v)
    assert d.source == "CHAD_CONFLUENCE_COLLAPSE" and d.reason_code == "confluence_collapse"
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills WHERE side='sell'").fetchone()[0] == 1


def test_negative_divergence_partial_by_entry_score_only_in_profit_and_once_per_event(rig):
    pid = _buy(rig, score="66")                               # 65-74 -> 40 %
    held = rig.ledger.get(pid).token_amount
    rig.price("MINT", 29_000_000_000)                         # in a loss: Negative Divergence never fires
    assert rig.executioner.on_divergence(Divergence("MINT", "evt-1", rig.clock.now, "test")) == []
    rig.price("MINT", 45_000_000_000)                         # now in profit after every cost
    [d] = rig.executioner.on_divergence(Divergence("MINT", "evt-2", rig.clock.now, "test"))
    assert d.reason_code == "negative_divergence" and d.result.ok
    assert rig.ledger.get(pid).token_amount == held - held * 40 // 100
    assert rig.executioner.on_divergence(Divergence("MINT", "evt-2", rig.clock.now, "test")) == []   # same event
    assert rig.executioner.stats["repeat_event"] == 1
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills WHERE side='sell'").fetchone()[0] == 1
    [d3] = rig.executioner.on_divergence(Divergence("MINT", "evt-3", rig.clock.now, "test"))   # a new event
    assert d3.result.ok and rig.ledger.get(pid).status is Status.OPEN
    ex = rig.store.conn.execute("SELECT required_sell_pct, exit_urgency FROM exit_intents WHERE intent_id = ?",
                                (d.intent_id,)).fetchone()
    assert (ex["required_sell_pct"], ex["exit_urgency"]) == ("0.40", "normal")


@pytest.mark.parametrize("score,fraction", [("80", "0.30"), ("70", "0.40"), ("62", "0.50"), ("50", "0.60")])
def test_negative_divergence_sizes(rig, score, fraction):
    pid = _buy(rig, score=score)
    held = rig.ledger.get(pid).token_amount
    rig.price("MINT", 45_000_000_000)
    [d] = rig.executioner.on_divergence(Divergence("MINT", "e", rig.clock.now, "test"))
    assert rig.ledger.get(pid).token_amount == held - int(held * Decimal(fraction))


def test_negative_divergence_needs_an_entry_score(rig):
    pid = _buy(rig, score=None, threshold=None)
    rig.price("MINT", 45_000_000_000)
    assert rig.executioner.on_divergence(Divergence("MINT", "e", rig.clock.now, "test")) == []
    assert rig.executioner.stats["no_entry_score"] == 1


def test_reentry_is_allowed_immediately_after_an_exit(rig):
    pid = _buy(rig, threshold="70")
    rig.clock.now += GRACE_S * S
    rig.scores.values["MINT"] = Decimal("1")
    [d] = rig.monitor.sweep()
    assert rig.ledger.get(pid).status is Status.CLOSED
    rig.scores.values.pop("MINT")
    again = rig.gateway.submit_entry(EntryIntent("e-again", "MINT", 100_000_000, origin="AUTO"))
    assert again.ok                                            # no cooldown


def test_sybil_shared_funder_fires_the_emergency_exit(rig):
    pid = _buy(rig, score=None, threshold=None)
    flagged = {}
    rig.monitor.protective = [SybilEmergencyExit(lambda m: flagged.get(m))]
    assert rig.monitor.sweep() == []
    flagged["MINT"] = SimpleNamespace(emergency_exit=True, fee_payers=("A", "B", "C"))
    [d] = rig.monitor.sweep()
    assert d.reason_code == "sybil_funder_emergency" and d.result.ok and d.source == "CHAD_SYBIL_EMERGENCY"
    row = rig.store.conn.execute("SELECT exit_urgency FROM exit_intents WHERE intent_id = ?", (d.intent_id,)).fetchone()
    assert row["exit_urgency"] == "protective" and rig.ledger.get(pid).status is Status.CLOSED


def test_off_mode_does_not_stop_the_executioner(rig):
    pid = _buy(rig, threshold="70")
    set_mode(rig.store, Mode.OFF)
    rig.clock.now += GRACE_S * S
    rig.scores.values["MINT"] = Decimal("1")
    [d] = rig.monitor.sweep()
    assert d.result.ok and rig.ledger.get(pid).status is Status.CLOSED
