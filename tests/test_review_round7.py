"""Regression tests for the Codex Phase 1 review, round 7 (docs/audits/phase1-codex-20261020-round7.md)."""
import sqlite3
from pathlib import Path

import pytest

from jinx.execution import EntryIntent, ExitIntent
from jinx.store import Store
from tests.test_review_round4 import rig, v1_with_prefixed_exit

# historical intent ids an old build could have written; SQLite text functions stop at an embedded NUL
AWKWARD_IDS = ["p1:X\x00Y", "\x00", "X\x00:sell", "line\nbreak", "ünï‮code", "a:sell:sell", "p1:X"]


@pytest.mark.parametrize("intent", AWKWARD_IDS)
def test_every_awkward_historical_id_is_recorded_as_evidence(tmp_path, intent):
    path = tmp_path / "db"
    v1_with_prefixed_exit(path, intent=intent)
    s = Store(path)
    rows = s.conn.execute("SELECT fill_id, intent_id, position_id FROM historical_exits").fetchall()
    assert [tuple(r) for r in rows] == [(f"{intent}:sell", intent, 1)]
    s.close()


@pytest.mark.parametrize("intent", AWKWARD_IDS)
def test_awkward_historical_id_can_never_sell_again(tmp_path, intent):
    """Replayed at the ledger level (new ExitIntents refuse control characters, see below)."""
    from jinx.paper import sell_fill_id
    path = tmp_path / "db"
    v1_with_prefixed_exit(path, intent=intent)
    s = Store(path)
    ledger, gw = rig(s)
    for amount in (250_000_000, 100_000_000, None):
        r = ledger.claim_exit(intent, 1, amount, sell_fill_id(1, intent))
        assert (r.ok, r.reason) == ((True, "ALREADY_APPLIED") if amount == 250_000_000 else (False, "INTENT_CONFLICT"))
    assert ledger.get(1).token_amount == 750_000_000
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1
    s.close()


def test_codex_reproduction_through_the_gateway_cannot_sell_again(tmp_path):
    path = tmp_path / "db"
    v1_with_prefixed_exit(path, intent="p1:X\x00Y")
    s = Store(path)
    ledger, gw = rig(s)
    with pytest.raises(ValueError):
        ExitIntent("p1:X\x00Y", 1, token_amount=250_000_000)   # refused at the boundary
    assert ledger.get(1).token_amount == 750_000_000
    s.close()


def test_unclassifiable_provenance_free_sell_is_still_preserved(tmp_path):
    path = tmp_path / "db"
    v1_with_prefixed_exit(path, intent="Q")
    c = sqlite3.connect(path)
    c.execute("UPDATE fills SET fill_id = 'weird-id' WHERE fill_id = 'Q:sell'")   # does not end in ':sell'
    c.commit()
    c.close()
    s = Store(path)
    rows = s.conn.execute("SELECT fill_id, intent_id FROM historical_exits").fetchall()
    assert [tuple(r) for r in rows] == [("weird-id", "weird-id")]
    s.close()


@pytest.mark.parametrize("bad", ["a\x00b", "a\nb", "a\tb", "a\x7fb", "\x1b[31m"])
def test_new_intents_refuse_control_characters(bad):
    with pytest.raises(ValueError):
        ExitIntent(bad, 1)
    with pytest.raises(ValueError):
        EntryIntent(bad, "MINT", budget_lamports=1)
    with pytest.raises(ValueError):
        EntryIntent("ok", bad, budget_lamports=1)
