"""Confluence state and entry (CHAD C12 and C10, Phase 3).

- One board holds every token's signal evidence with its source, observation time, event time and freshness.
- The score needs CHAD's weights and global threshold, which CHAD does not give (B-03): without them there is no
  score and the standard path never enters. Weights here are labelled test fixtures.
- Early Momentum's fast path "Bypasses the normal scoring queue": the decision runs at once, on the caller's stack,
  when the entry re-check (B-02 §5), the safety verdict and sizing all pass.
- Hard safety vetoes and OFF stop new entries; nothing here ever exits.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jinx import config
from jinx.confluence.board import ConfluenceBoard, ConfluenceWeights
from jinx.confluence.entry import EntryCoordinator
from jinx.exits.policy import BIGMAC_PAPER, save_bigmac_config
from jinx.ledger import Status
from jinx.modes import Mode, set_mode
from jinx.sizing.kelly import KellyConfig, QuarterKelly
from tests.conftest import make_create
from tests.test_bigmac_integration import FIXTURE, Rig

S = 1_000_000_000
KELLY = KellyConfig(label="TEST_FIXTURE_entry", bucket_floors=(Decimal(0),), priors={"0": (Decimal("0.5"), Decimal("2"))},
                    cap_fraction=Decimal("0.05"), entry_filter="early_momentum", unscored_bucket=Decimal(0))


def evidence(mint="MINT", event_ns=5 * S):
    return SimpleNamespace(mint=mint, event_ns=event_ns, trade_permitted=True, blocking=(), emergency_exit=False,
                           creator="DEV", fee_payers=("A", "B", "C"))


class Checks:
    def __init__(self):
        self.result = {"all_checks_pass": True, "reserve_eligible": True, "safety_eligible": True,
                       "early_momentum_blocking": [], "migrated": False}
        self.calls = 0

    def __call__(self, mint):
        self.calls += 1
        return dict(self.result)


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path / "jinx.db")
    r.board = ConfluenceBoard()
    r.checks = Checks()
    r.coord = EntryCoordinator(r.store, r.ledger, r.gateway, QuarterKelly(r.ledger, r.wallet, KELLY, 5_000),
                               r.board, r.checks, r.tokens, config.PUMP_CURVE_FEES)
    r.tokens.apply(make_create(mint="MINT"))
    r.price("MINT", 31_000_000_000)
    set_mode(r.store, Mode.AUTO)
    yield r
    r.close()


def test_fast_path_enters_at_once_with_full_provenance(rig):
    save_bigmac_config(rig.store, FIXTURE)
    d = rig.coord.consider_fast_path(evidence())
    assert d.ok and d.reason is None and rig.checks.calls == 1              # re-checked at the decision
    pos = rig.ledger.get(d.position_id)
    assert pos.status is Status.OPEN and pos.strategy == "AUTO" and pos.signal_ns == 5 * S
    assert pos.exit_policy == BIGMAC_PAPER and '"label": "TEST_FIXTURE_bigmac_integration"' in pos.exit_policy_json
    assert pos.entry_score is None and pos.entry_filter == "early_momentum" and '"source": "prior"' in pos.sizing_json
    stages = [e["stage"] for e in rig.ledger.order_events(d.intent_id)]
    assert stages == ["DECIDED", "QUOTED", "SUBMITTED", "FILLED"]


def test_the_same_detection_never_buys_twice(rig):
    a = rig.coord.consider_fast_path(evidence())
    b = rig.coord.consider_fast_path(evidence())
    assert a.ok and b.position_id == a.position_id and b.reason == "ALREADY_EXECUTED"
    assert rig.store.conn.execute("SELECT COUNT(*) FROM fills WHERE side='buy'").fetchone()[0] == 1


def test_off_and_copy_place_no_auto_entry(rig):
    for mode, reason in ((Mode.OFF, "MODE_OFF"), (Mode.COPY, "MODE_COPY")):
        set_mode(rig.store, mode)
        d = rig.coord.consider_fast_path(evidence())
        assert not d.ok and d.reason == reason
    assert rig.store.conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert rig.store.conn.execute("SELECT COUNT(*) FROM order_events").fetchone()[0] == 0


@pytest.mark.parametrize("key,value,reason", [("all_checks_pass", False, "ENTRY_CHECK_FAILED"),
                                              ("safety_eligible", False, "SAFETY_NOT_PASSED"),
                                              ("migrated", True, "ENTRY_CHECK_FAILED")])
def test_a_failed_recheck_or_safety_never_enters(rig, key, value, reason):
    rig.checks.result[key] = value
    if key != "all_checks_pass":
        rig.checks.result["all_checks_pass"] = False
    d = rig.coord.consider_fast_path(evidence())
    assert not d.ok and d.reason in (reason, "ENTRY_CHECK_FAILED")
    assert rig.store.conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


def test_hard_safety_veto_is_permanent(rig):
    rig.board.record_safety("MINT", verdict="FAIL", veto=["mint authority held"], observed_ns=1, source="gmgn")
    d = rig.coord.consider_fast_path(evidence())
    assert not d.ok and d.reason == "SAFETY_VETO"
    assert rig.checks.calls == 0                                            # vetoed before anything else


def test_unconfigured_sizing_never_enters(rig):
    rig.coord.sizing = QuarterKelly(rig.ledger, rig.wallet, None, 5_000)
    d = rig.coord.consider_fast_path(evidence())
    assert not d.ok and d.reason == "SIZING_NOT_CONFIGURED"
    assert rig.store.conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


def test_the_owner_slot_limit_holds(rig):
    assert rig.coord.consider_fast_path(evidence()).ok
    rig.tokens.apply(make_create(mint="TWO"))
    rig.price("TWO", 31_000_000_000)
    d = rig.coord.consider_fast_path(evidence("TWO"))
    assert not d.ok and d.reason == "SLOT_LIMIT"


def test_standard_path_needs_chads_unspecified_weights_and_threshold(rig):
    rig.board.record("MINT", "graduation", value=None, source="gmgn.trenches", observed_ns=1, event_ns=1,
                     payload={"provider_status": "near_completion"})
    d = rig.coord.consider_standard("MINT")
    assert not d.ok and d.reason == "NO_CONFLUENCE_SCORE"
    assert rig.board.score("MINT") is None


def test_board_records_evidence_with_times_and_is_bounded():
    board = ConfluenceBoard(max_tokens=3)
    for i in range(5):
        board.record(f"M{i}", "early_momentum", value=None, source="pumpapi", observed_ns=10 + i, event_ns=i,
                     payload={"trade_permitted": True})
    assert len(board) == 3 and board.components("M0") == {}
    c = board.components("M4")["early_momentum"]
    assert (c.source, c.observed_ns, c.event_ns, c.payload["trade_permitted"]) == ("pumpapi", 14, 4, True)
    board.hold({"M2"})
    for i in range(5, 9):
        board.record(f"M{i}", "early_momentum", value=None, source="pumpapi", observed_ns=i, event_ns=i)
    assert board.components("M2")                                           # a held token is never evicted


def test_a_score_only_exists_with_labelled_weights_and_fresh_values():
    w = ConfluenceWeights(label="TEST_FIXTURE_weights", weights={"whales": Decimal(30), "graduation": Decimal(20)})
    board = ConfluenceBoard(weights=w)
    board.record("M", "whales", value=Decimal("0.5"), source="t", observed_ns=1, event_ns=1)
    assert board.score("M") is None                                          # graduation missing: no partial score
    board.record("M", "graduation", value=Decimal(1), source="t", observed_ns=2, event_ns=2)
    assert board.score("M") == Decimal(35)
    with pytest.raises(ValueError):
        ConfluenceWeights(label="production", weights={"whales": Decimal(1)})
