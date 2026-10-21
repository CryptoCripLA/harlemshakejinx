"""Clean-slate storage (owner order 2026-10-21): PostgreSQL holds durable trading records, Redis holds transient
state with expiry, and neither sits on the DETECT -> SIGN -> FIRE path.

The ledger store writes every change of a trading record into an outbox in the same transaction (SQLite
triggers); a background replicator upserts the outbox into PostgreSQL and then removes it. PostgreSQL tests run
against a brand-new throwaway cluster created in a temp directory (no existing database is touched)."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from jinx.ledger import PositionLedger
from jinx.modes import Mode, set_mode
from jinx.paper import PaperWallet
from jinx.store import Store

PG_BIN = Path("/usr/lib/postgresql/16/bin")
MIRRORED = ("positions", "fills", "exit_intents", "paper_wallet", "paper_wallet_entries", "settings", "settings_audit")


@pytest.fixture(scope="module")
def fresh_pg(tmp_path_factory):
    if not (PG_BIN / "initdb").exists():
        pytest.skip("PostgreSQL binaries not available for a throwaway cluster")
    root = tmp_path_factory.mktemp("pg")
    data, sock = root / "data", root / "sock"
    sock.mkdir()
    subprocess.run([PG_BIN / "initdb", "-D", data, "-A", "trust", "-U", "jinx_test", "--no-sync"], check=True,
                   capture_output=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    subprocess.run([PG_BIN / "pg_ctl", "-D", data, "-w", "-l", root / "log", "-o",
                    f"-c listen_addresses='' -c unix_socket_directories={sock} -p {port} -c fsync=off", "start"],
                   check=True, capture_output=True)
    yield f"host={sock} port={port} dbname=postgres user=jinx_test"
    subprocess.run([PG_BIN / "pg_ctl", "-D", data, "-w", "-m", "immediate", "stop"], capture_output=True)
    shutil.rmtree(root, ignore_errors=True)


def ledger_activity(store):
    """Real ledger operations: owner settings, a paper position opened and fully closed."""
    from jinx.core.events import EventKind, MarketEvent, Side
    from jinx.registry import TokenRegistry
    from jinx.paper import PaperExecutor
    from jinx.execution import EntryIntent, ExecutionGateway, ExitIntent
    from jinx import config
    tokens = TokenRegistry(store)
    tokens.apply(MarketEvent(kind=EventKind.TRADE, source="t", mint="M", signature="S", recv_ns=1, seq=1, trader="A",
                             side=Side.BUY, sol_lamports=10_000_000, token_amount=350_000_000_000,
                             v_sol_lamports=31_000_000_000, v_tokens=1_038_000_000_000_000))
    ledger, wallet = PositionLedger(store), PaperWallet(store, 2_000_000_000)
    set_mode(store, Mode.AUTO, by="test")
    gw = ExecutionGateway(store, ledger, PaperExecutor(store, ledger, wallet, tokens, config.PUMP_CURVE_FEES, 5_000))
    opened = gw.submit_entry(EntryIntent("E1", "M", budget_lamports=10_000_000))
    assert opened.ok, opened
    closed = gw.submit_exit(ExitIntent("X1", opened.position_id))
    assert closed.ok, closed
    return ledger, wallet


def test_every_ledger_change_lands_in_the_outbox_in_the_same_transaction(tmp_path):
    store = Store(tmp_path / "l.db")
    ledger_activity(store)
    rows = store.conn.execute("SELECT tbl, op, row_json FROM outbox ORDER BY seq").fetchall()
    tables = {r["tbl"] for r in rows}
    assert {"positions", "fills", "paper_wallet", "paper_wallet_entries", "settings", "settings_audit"} <= tables
    last_position = [json.loads(r["row_json"]) for r in rows if r["tbl"] == "positions"][-1]
    assert last_position == dict(store.conn.execute("SELECT * FROM positions").fetchone())
    store.close()


def test_market_events_never_enter_the_outbox(tmp_path):
    from jinx.core.events import EventKind, MarketEvent, Side
    store = Store(tmp_path / "l.db")
    store.record_event(MarketEvent(kind=EventKind.TRADE, source="t", mint="M", signature="S", recv_ns=1, seq=1,
                                   trader="A", side=Side.BUY, sol_lamports=1, token_amount=1))
    assert store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    store.close()


def test_a_fresh_database_gets_the_schema_from_the_current_code(fresh_pg):
    import psycopg
    from jinx.persistence.postgres import PostgresReplicator, SCHEMA_VERSION
    rep = PostgresReplicator(Store(":memory:"), fresh_pg)
    asyncio.run(rep.init_schema())
    with psycopg.connect(fresh_pg) as c:
        tables = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        assert set(MIRRORED) | {"jinx_meta"} <= tables
        assert c.execute("SELECT value FROM jinx_meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        assert all(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0 for t in MIRRORED)   # starts empty
    asyncio.run(rep.init_schema())                                                                # idempotent


def test_trading_records_replicate_row_for_row_and_the_outbox_drains(fresh_pg, tmp_path):
    import psycopg
    from jinx.persistence.postgres import PostgresReplicator
    store = Store(tmp_path / "l.db")
    ledger_activity(store)
    rep = PostgresReplicator(store, fresh_pg)
    asyncio.run(rep.init_schema())
    sent = asyncio.run(rep.flush())
    assert sent > 0 and store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    with psycopg.connect(fresh_pg) as c:
        for t in MIRRORED:
            local = sorted(tuple(r) for r in store.conn.execute(f"SELECT * FROM {t}"))
            remote = sorted(tuple(r) for r in c.execute(f"SELECT * FROM {t}"))
            assert remote == local, t
    store.close()


def test_postgres_down_keeps_the_outbox_and_resending_is_harmless(fresh_pg, tmp_path):
    import psycopg
    from jinx.persistence.postgres import PostgresReplicator
    store = Store(tmp_path / "l.db")
    ledger_activity(store)
    backlog = store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
    down = PostgresReplicator(store, "host=/nonexistent port=1 dbname=x user=x connect_timeout=1")
    assert asyncio.run(down.flush()) == 0 and down.stats["failures"] == 1
    assert store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == backlog          # nothing lost
    rows = store.conn.execute("SELECT seq, tbl, op, row_json FROM outbox ORDER BY seq").fetchall()
    rep = PostgresReplicator(store, fresh_pg)
    asyncio.run(rep.init_schema())
    asyncio.run(rep.apply_rows([dict(r) for r in rows]))      # a crash after the remote commit: the same rows again
    asyncio.run(rep.flush())
    with psycopg.connect(fresh_pg) as c:
        assert c.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 2
    store.close()


def test_redis_snapshots_expire_and_unchanged_tokens_are_not_rewritten():
    import fakeredis
    from jinx.persistence.redis_state import RedisState
    from jinx.registry import TokenState

    async def main():
        r = fakeredis.FakeAsyncRedis()
        state = RedisState(r, token_ttl_s=7200, health_ttl_s=120)
        a = TokenState(mint="A", buys=3, latest_time_ns=5, latest_seq=7)
        first = await state.snapshot({"status": "ok"}, [a])
        second = await state.snapshot({"status": "ok"}, [a])                 # unchanged token: not written again
        a2 = TokenState(mint="A", buys=4, latest_time_ns=6, latest_seq=8)
        third = await state.snapshot({"status": "ok"}, [a2])
        ttl_token, ttl_health = await r.ttl("jinx:token:A"), await r.ttl("jinx:health")
        stored = json.loads(await r.get("jinx:token:A"))
        return first, second, third, ttl_token, ttl_health, stored
    first, second, third, ttl_token, ttl_health, stored = asyncio.run(main())
    assert (first, second, third) == (1, 0, 1)
    assert 0 < ttl_token <= 7200 and 0 < ttl_health <= 120 and stored["buys"] == 4


def test_database_urls_with_passwords_are_caught_by_the_secret_guard():
    from tests.test_secrets import PATTERNS, find
    secret = b"s3cret" + b"passw0rd"   # built from pieces so this file holds no credential-shaped string
    assert find(PATTERNS, b"postgresql://jinx_owner:" + secret + b"@ep-x.us-east-1.aws.neon.tech/jinx")
    assert find(PATTERNS, b"rediss://default:" + secret + b"@redis-1.cloud.redislabs.com:12345")
    assert not find(PATTERNS, b"postgresql://user@host/db and JINX_DATABASE_URL=<set in /etc/jinx/jinx.env>")


def test_research_store_accumulates_wallet_totals_across_ram_eviction_and_prunes(fresh_pg):
    """Railway research store (owner order 2026-10-21): totals keep adding up after a wallet leaves RAM and returns;
    retention removes wallets unseen for the retention window and old performance samples."""
    import psycopg
    from jinx.persistence.research import ResearchStore
    from jinx.registry import WalletState
    day = 86_400 * 1_000_000_000
    now = 100 * day
    rs = ResearchStore(fresh_pg, wallet_retention_days=30, perf_retention_days=30)
    a1 = WalletState("A", buys=2, sells=1, buy_lamports=20, sell_lamports=5, first_time_ns=now - day, last_time_ns=now)
    old = WalletState("OLD", buys=1, buy_lamports=1, first_time_ns=now - 40 * day, last_time_ns=now - 40 * day)
    assert asyncio.run(rs.flush([a1, old], {"receive_to_state_ms": {"p99": 1.0}})) == 2
    a2 = WalletState("A", buys=5, sells=1, buy_lamports=50, sell_lamports=5, first_time_ns=now - day, last_time_ns=now + 1)
    assert asyncio.run(rs.flush([a2, old])) == 1                      # only A changed: +3 buys, +30 lamports
    a3 = WalletState("A", buys=1, buy_lamports=7, first_time_ns=now + 9, last_time_ns=now + 9)   # evicted, came back
    assert asyncio.run(rs.flush([a3])) == 1
    with psycopg.connect(fresh_pg) as c:
        assert c.execute("SELECT buys, sells, buy_lamports, first_time_ns, last_time_ns FROM wallet_stats"
                         " WHERE address = 'A'").fetchone() == (6, 1, 57, now - day, now + 9)
        assert c.execute("SELECT COUNT(*) FROM perf_samples").fetchone()[0] == 1
    pruned = asyncio.run(rs.prune(now))
    assert pruned["wallets"] == 1 and rs.stats["db_bytes"] > 0
    with psycopg.connect(fresh_pg) as c:
        assert [r[0] for r in c.execute("SELECT address FROM wallet_stats")] == ["A"]


def test_replicator_resync_restores_a_cleared_outbox(fresh_pg, tmp_path):
    import psycopg
    from jinx.persistence.postgres import PostgresReplicator
    store = Store(tmp_path / "l.db")
    ledger_activity(store)
    store.clear_outbox()                                   # e.g. ran for a while without PostgreSQL configured
    rep = PostgresReplicator(store, fresh_pg)
    asyncio.run(rep.start())
    with psycopg.connect(fresh_pg) as c:
        for t in MIRRORED:
            local = sorted(tuple(r) for r in store.conn.execute(f"SELECT * FROM {t}"))
            remote = sorted(tuple(r) for r in c.execute(f"SELECT * FROM {t}"))
            assert remote == local, t
    assert rep.backlog() == 0 and rep.stats["resynced"] > 0
    store.close()
