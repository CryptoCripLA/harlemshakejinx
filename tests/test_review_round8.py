"""Regression tests for the Codex Phase 1 review, round 8 (docs/audits/phase1-codex-20261020-round8.md).

H1: a historical sell whose fill id SQLite stores as a BLOB (or NULL) made every database open fail. Such a value
is a distinct SQLite key from any text id, so it is never decoded or merged into text evidence. It is recorded
as unclassified evidence, and its position refuses new exit claims until the owner reconciles it."""
import sqlite3

import pytest

from jinx.ledger import Fill, Side
from jinx.store import Store
from tests.test_review_round4 import rig, v1_with_prefixed_exit

ODD_VALUES = [b"Q:sell", b"\x00\xff", None]


def legacy_db_with_odd_sell_id(path, version: int, value) -> None:
    v1_with_prefixed_exit(path, intent="Q")
    if version > 1:
        Store(path).close()                      # bring it to the current layout first
    c = sqlite3.connect(path)
    c.execute("UPDATE fills SET fill_id = ? WHERE fill_id = 'Q:sell'", (value,))
    if version > 1:
        c.execute("DELETE FROM historical_exits")
        c.execute(f"PRAGMA user_version = {version}")
    c.commit()
    c.close()


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("value", ODD_VALUES, ids=["blob-sell", "blob-bytes", "null"])
def test_a_non_text_historical_sell_id_no_longer_prevents_startup(tmp_path, version, value):
    path = tmp_path / "db"
    legacy_db_with_odd_sell_id(path, version, value)
    s = Store(path)                                       # raised TypeError before the fix
    kind = "null" if value is None else "blob"
    ref = f"{kind}:{value.hex() if value is not None else ''}"
    assert [tuple(r) for r in s.conn.execute("SELECT position_id, fill_ref FROM unclassified_exits")] == [(1, ref)]
    assert s.conn.execute("SELECT COUNT(*) FROM historical_exits").fetchone()[0] == 0   # nothing decoded or merged
    s.close()
    Store(path).close()                                   # reopening is idempotent


@pytest.mark.parametrize("value", ODD_VALUES, ids=["blob-sell", "blob-bytes", "null"])
def test_a_position_with_unclassified_sell_history_refuses_new_exits(tmp_path, value):
    from jinx.paper import sell_fill_id
    path = tmp_path / "db"
    legacy_db_with_odd_sell_id(path, 1, value)
    s = Store(path)
    ledger, gw = rig(s)
    for intent, amount in (("Q", 250_000_000), ("Q", None), ("NEW", 100_000_000)):
        r = ledger.claim_exit(intent, 1, amount, sell_fill_id(1, intent))
        assert (r.ok, r.reason) == (False, "UNCLASSIFIED_HISTORY")
    assert ledger.get(1).token_amount == 750_000_000
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1
    s.close()


@pytest.mark.parametrize("bad", [b"X:sell", 7, None])
def test_new_fills_must_have_text_ids(bad):
    with pytest.raises(ValueError):
        Fill(bad, Side.SELL, 1, 0, 0, 0, True, 1)


# ---- Codex round 9 (docs/audits/phase1-codex-20261020-round9.md) ---------------------------------
def v1_closed_by_two_tied_sells(path) -> None:
    """A v1 position of 10 tokens closed by two historical 5-token sells recorded at the same time."""
    from tests.test_review_round4 import FIXTURES
    c = sqlite3.connect(path)
    c.executescript((FIXTURES / "schema_v1_1ad0d65.sql").read_text())
    c.execute("INSERT INTO positions (entry_intent_id, exit_intent_id, mint, status, token_amount, cost_lamports,"
              " proceeds_lamports, simulated, opened_ns) VALUES ('E1','B','A','CLOSED',0,10000000,4000000,1,1)")
    c.execute("INSERT INTO fills VALUES ('E1:buy', 1, 'buy', 10, 10000000, 99009, 5000, 1, 1)")
    c.execute("INSERT INTO fills VALUES ('A:sell', 1, 'sell', 5, 2000000, 20000, 5000, 1, 7)")
    c.execute("INSERT INTO fills VALUES ('B:sell', 1, 'sell', 5, 2000000, 20000, 5000, 1, 7)")
    c.execute("INSERT INTO settings VALUES ('mode','AUTO',1,'owner')")
    c.commit()
    c.close()


def test_whole_position_replay_needs_a_uniquely_established_closing_sell(tmp_path):    # R9-M3
    from jinx.ledger import PositionLedger
    from jinx.paper import sell_fill_id
    path = tmp_path / "db"
    v1_closed_by_two_tied_sells(path)
    s = Store(path)
    ledger = PositionLedger(s)
    for intent in ("A", "B"):
        r = ledger.claim_exit(intent, 1, None, sell_fill_id(1, intent))
        assert (r.ok, r.reason) == (False, "INTENT_CONFLICT")          # tied timestamps: not provable
        r = ledger.claim_exit(intent, 1, 5, sell_fill_id(1, intent))
        assert (r.ok, r.reason) == (True, "ALREADY_APPLIED")            # the exact amount still matches
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 2
    s.close()
