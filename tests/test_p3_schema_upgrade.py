"""Schema v9 (main before Phase 3) -> v10: columns are added in place, the order_events table is created, and every
existing trading row is kept unchanged (AGENT_COORDINATION: test the upgrade from the version on main)."""
import sqlite3
import subprocess
from pathlib import Path

from jinx.ledger import PositionLedger, Status
from jinx.store import PHASE3_COLUMNS, SCHEMA_VERSION, Store

ROOT = Path(__file__).resolve().parent.parent
V9_COMMIT = "c3b490a"   # main when Phase 3 landed; its store.py is SCHEMA_VERSION = 9


def _v9_store_source() -> str | None:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{V9_COMMIT}:jinx/store.py"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 and "SCHEMA_VERSION = 9" in r.stdout else None


def test_upgrade_from_v9_keeps_every_row_and_adds_the_phase3_columns(tmp_path):
    src = _v9_store_source()
    path = tmp_path / "v9.db"
    if src is not None:                                   # build the database with main's own v9 code
        ns: dict = {}
        exec(compile(src, "store_v9.py", "exec"), ns)
        old = ns["Store"](path)
        old.conn.execute("INSERT INTO positions (entry_intent_id, entry_budget_lamports, mint, status, token_amount,"
                         " cost_lamports, simulated, opened_ns) VALUES ('e1', 100, 'M', 'OPEN', 7, 100, 1, 5)")
        old.conn.execute("INSERT INTO fills (fill_id, position_id, side, token_amount, sol_lamports,"
                         " protocol_fee_lamports, network_fee_lamports, simulated, ts_ns) VALUES"
                         " ('p1:e1:buy', 1, 'buy', 7, 100, 1, 5000, 1, 5)")
        assert old.schema_version() == 9
        old.close()
    else:                                                 # no git history (e.g. an export): skip honestly
        import pytest
        pytest.skip("main's v9 store.py is not available in this checkout")
    s = Store(path)
    assert s.schema_version() == SCHEMA_VERSION >= 10       # v11 (storage repair) builds on v10
    for tbl, cols in PHASE3_COLUMNS.items():
        have = {r[1] for r in s.conn.execute(f"PRAGMA table_info({tbl})")}
        assert {c for c, _ in cols} <= have
    assert "order_events" in {r[0] for r in s.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    pos = PositionLedger(s).get(1)
    assert (pos.status, pos.token_amount, pos.cost_lamports, pos.strategy, pos.peak_value_lamports) == \
        (Status.OPEN, 7, 100, None, None)
    assert tuple(s.conn.execute("SELECT sol_lamports, quote_lamports FROM fills").fetchone()) == (100, None)
    s.close()
    s2 = Store(path)                                      # idempotent on the next open
    assert s2.schema_version() == SCHEMA_VERSION and PositionLedger(s2).get(1).token_amount == 7
    s2.close()
