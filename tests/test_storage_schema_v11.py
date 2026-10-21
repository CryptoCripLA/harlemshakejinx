"""Schema v10 (main with Phase 3) -> v11 (storage repair 2026-10-21): the legacy market tables are retired. An empty
one is dropped; one that still holds rows is kept untouched and reported, never deleted by an upgrade ("Do not destroy
existing data until the replacement passes migration and recovery tests"). Every trading row is kept unchanged.
Built with main's own v10 store code (AGENT_COORDINATION: test the upgrade from the version on main)."""
import subprocess
from pathlib import Path

import pytest

from jinx.ledger import PositionLedger, Status
from jinx.store import LEGACY_MARKET_TABLES, SCHEMA_VERSION, Store

ROOT = Path(__file__).resolve().parent.parent
V10_COMMIT = "1f01beb"   # main when the storage repair landed; its store.py is SCHEMA_VERSION = 10


def _v10_store(path: Path, market_rows: bool):
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{V10_COMMIT}:jinx/store.py"], capture_output=True, text=True)
    if r.returncode != 0 or "SCHEMA_VERSION = 10" not in r.stdout:
        pytest.skip("main's v10 store.py is not available in this checkout")
    ns: dict = {}
    exec(compile(r.stdout, "store_v10.py", "exec"), ns)
    old = ns["Store"](path)
    old.conn.execute("INSERT INTO positions (entry_intent_id, entry_budget_lamports, mint, status, token_amount,"
                     " cost_lamports, simulated, opened_ns) VALUES ('e1', 100, 'M', 'OPEN', 7, 100, 1, 5)")
    old.conn.execute("INSERT INTO fills (fill_id, position_id, side, token_amount, sol_lamports,"
                     " protocol_fee_lamports, network_fee_lamports, simulated, ts_ns) VALUES"
                     " ('p1:e1:buy', 1, 'buy', 7, 100, 1, 5000, 1, 5)")
    if market_rows:
        old.conn.execute("INSERT INTO tokens (mint, buys, sells, unique_traders) VALUES ('OLDMINT', 3, 1, 2)")
    assert old.schema_version() == 10
    tables = {r[0] for r in old.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(LEGACY_MARKET_TABLES) <= tables                   # v10 still created them
    old.close()


def _tables(s: Store) -> set[str]:
    return {r[0] for r in s.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_upgrade_drops_empty_market_tables_and_keeps_every_trading_row(tmp_path):
    path = tmp_path / "v10.db"
    _v10_store(path, market_rows=False)
    s = Store(path)
    assert s.schema_version() == SCHEMA_VERSION == 11
    assert not _tables(s) & set(LEGACY_MARKET_TABLES) and s.legacy_market_rows() == {}
    pos = PositionLedger(s).get(1)
    assert (pos.status, pos.token_amount, pos.cost_lamports) == (Status.OPEN, 7, 100)
    assert s.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    s.close()
    s2 = Store(path)                                             # idempotent on the next open
    assert s2.schema_version() == 11 and PositionLedger(s2).get(1).token_amount == 7
    s2.close()


def test_upgrade_never_deletes_a_market_table_that_still_holds_rows(tmp_path):
    path = tmp_path / "v10.db"
    _v10_store(path, market_rows=True)
    s = Store(path)
    assert "tokens" in _tables(s) and s.legacy_market_rows() == {"tokens": 1}
    assert s.conn.execute("SELECT buys FROM tokens WHERE mint = 'OLDMINT'").fetchone()[0] == 3
    assert not _tables(s) & {"events", "token_traders", "wallets"}         # the empty ones are gone
    assert PositionLedger(s).get(1).token_amount == 7
    s.close()


def test_health_reports_kept_legacy_market_rows(tmp_path):
    from jinx.health import build_health
    from jinx.paper import PaperWallet
    path = tmp_path / "v10.db"
    _v10_store(path, market_rows=True)
    s = Store(path)
    h = build_health(s, PositionLedger(s), PaperWallet(s, 1_000), [], {}, now_ns=1)
    assert h["store"]["legacy_market_rows"] == {"tokens": 1}
    s.close()
