"""Owner storage requirement 2026-10-21 (permanent): nothing grows without bound. Bounded outbox, RAM hard caps,
expiring and capped Redis state, backups with JINX-enforced retention, an end-of-day clean-up, storage monitoring,
capped captures and self-cleaning tests. Also the Neon keys wiring (object storage, AI Gateway)."""
import asyncio
import gzip
import json
import tomllib
from pathlib import Path

import httpx
import pytest

from jinx.store import OUTBOX_TABLES, Store
from tests.test_market_memory import trade
from tests.test_persistence import ledger_activity

ROOT = Path(__file__).resolve().parents[1]
S = 1_000_000_000


# ---- outbox (rule 7: bounded, completed entries removed) -------------------------------------------
def test_the_outbox_holds_at_most_one_pending_row_per_trading_record(tmp_path):
    store = Store(tmp_path / "l.db")
    ledger_activity(store)
    for i in range(500):                                   # 500 changes to one record
        store.set_setting("probe", str(i), by="test", now_ns=i)
    pending = store.conn.execute("SELECT tbl, pk, COUNT(*) FROM outbox GROUP BY tbl, pk HAVING COUNT(*) > 1").fetchall()
    assert pending == []
    assert store.conn.execute("SELECT COUNT(*) FROM outbox WHERE tbl = 'settings' AND pk = 'probe'").fetchone()[0] == 1
    row = json.loads(store.conn.execute("SELECT row_json FROM outbox WHERE tbl = 'settings' AND pk = 'probe'").fetchone()[0])
    assert row["value"] == "499"                           # the pending row is the latest state
    records = sum(store.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in OUTBOX_TABLES)
    assert store.outbox_backlog() == records               # never more rows than records
    store.close()


def test_without_postgres_the_end_of_day_pass_clears_the_outbox(tmp_path):
    from jinx import config
    from jinx.app import JinxApp
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    ledger_activity(app.store)
    assert app.store.outbox_backlog() > 0
    report = asyncio.run(app.end_of_day(10 * S))
    assert report["outbox_cleared"] > 0 and app.store.outbox_backlog() == 0
    assert app.daily.due(10 * S) is False and app.daily.due(10 * S + 86_400 * S) is True
    assert app.store.conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1   # trade history kept
    app.store.close()


# ---- RAM (rule 2: no unbounded caches) ---------------------------------------------------------------
def test_ram_hard_caps_hold_even_inside_the_ttl_window():
    from jinx.registry import TokenRegistry
    reg = TokenRegistry(max_tokens=5, idle_ttl_s=10_000)
    reg.held_provider = lambda: frozenset({"M0"})
    for i in range(120):
        reg.apply(trade(f"S{i}", f"M{i % 12}", f"W{i}", 100 + i))
    assert len(reg) <= 6 and reg.get("M0") is not None                          # 5 + the held one
    newest = max(int(t.mint[1:]) for t in reg.all() if t.mint != "M0")
    assert newest == 119 % 12                                                    # most recent tokens survive


def test_no_market_structure_keeps_raw_events_after_a_live_capture(tmp_path):
    """Owner storage repair 2026-10-21 §2: "Do not copy complete provider payloads into every tracker"."""
    from jinx import config
    from jinx.adapters.pumpapi.normalize import normalize
    from jinx.app import JinxApp
    from jinx.sim import load_capture
    from tests.test_memory_trackers import _holds_market_events
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    seq = iter(range(1, 10**6))
    events = [e for r in load_capture(ROOT / "tests" / "fixtures" / "pumpapi-live-20261021.jsonl.gz")
              for e in normalize(r["raw"], r["recv_ns"], seq.__next__)]

    async def main():
        runner = asyncio.create_task(app.bus.run())
        for e in events:
            await app.bus.publish(e)
        await app.bus.drain()
        runner.cancel()
    asyncio.run(main())
    for name, obj in (("tokens", app.core.tokens), ("early", app.early), ("graduation", app.graduation),
                      ("migration", app.migration), ("bundles", app.bundles), ("whales", app.whales)):
        assert not _holds_market_events(obj), name
    app.store.close()


# ---- Redis (rule 4: bounded, expiring) ---------------------------------------------------------------
def test_redis_writes_only_the_most_recent_tokens_and_every_key_expires():
    import fakeredis
    from jinx.persistence.redis_state import RedisState
    from jinx.registry import TokenState

    async def main():
        r = fakeredis.FakeAsyncRedis()
        state = RedisState(r, token_ttl_s=60, health_ttl_s=60, max_tokens=3)
        tokens = [TokenState(mint=f"T{i}", latest_time_ns=i, latest_seq=i) for i in range(10)]
        await state.snapshot({"status": "ok"}, tokens)
        keys = sorted(k.decode() for k in await r.keys("*"))
        ttls = [await r.ttl(k) for k in keys]
        return keys, ttls, await state.memory()
    keys, ttls, mem = asyncio.run(main())
    assert keys == ["jinx:health", "jinx:token:T7", "jinx:token:T8", "jinx:token:T9"]
    assert all(0 < t <= 60 for t in ttls)
    assert mem["keys"] == 4


# ---- backups (rule 8: defined retention) and the Neon object-storage key wiring ------------------------
class FakeS3:
    """The S3 calls LedgerBackups makes, in memory."""
    def __init__(self):
        self.buckets: dict[str, dict[str, bytes]] = {}

    def list_buckets(self):
        return {"Buckets": [{"Name": n} for n in self.buckets]}

    def create_bucket(self, Bucket):
        self.buckets.setdefault(Bucket, {})

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.buckets[Bucket][Key] = Body

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        items = sorted((k, len(v)) for k, v in self.buckets[Bucket].items() if k.startswith(Prefix))
        return {"Contents": [{"Key": k, "Size": n} for k, n in items], "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.buckets[Bucket].pop(o["Key"], None)

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.buckets[Bucket][Key])}


def test_daily_ledger_backups_keep_only_the_newest_n_and_restore(tmp_path):
    from jinx.persistence.object_store import LedgerBackups, restore
    store = Store(tmp_path / "l.db")
    ledger_activity(store)
    s3 = FakeS3()
    backups = LedgerBackups(s3, bucket="jinx-backups", keep=3)
    for day in range(6):
        asyncio.run(backups.backup(store, now_ns=(1_760_000_000 + day * 86_400) * S))
    asyncio.run(backups.backup(store, now_ns=(1_760_000_000 + 5 * 86_400 + 60) * S))   # same day: replaced
    keys = sorted(s3.buckets["jinx-backups"])
    assert len(keys) == 3 and backups.stats["deleted"] == 3 and backups.stats["objects"] == 3
    copy = restore(asyncio.run(backups.latest()))
    assert copy.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
    tables = {r[0] for r in copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"events", "tokens", "token_traders", "wallets"}       # no market data in a backup
    store.close()


def test_a_failed_upload_is_counted_and_never_raises(tmp_path):
    from jinx.persistence.object_store import LedgerBackups

    class Down(FakeS3):
        def list_buckets(self):
            raise ConnectionError("unreachable")
    store = Store(tmp_path / "l.db")
    backups = LedgerBackups(Down(), keep=2)
    assert asyncio.run(backups.backup(store)) is None and backups.stats["failures"] == 1
    store.close()


def test_the_neon_keys_are_read_from_the_environment_and_never_shown(tmp_path, monkeypatch):
    from jinx import config
    from jinx.app import JinxApp
    secret = "nsk_live_" + "0" * 40
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://br-x.storage.c-1.us-east-2.aws.neon.tech")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "nak_live_" + "1" * 32)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)
    monkeypatch.setenv("NEON_AI_GATEWAY_BASE_URL", "https://br-x-api.ai.c-1.us-east-2.aws.neon.tech")
    monkeypatch.setenv("NEON_AI_GATEWAY_TOKEN", "nt_live_" + "2" * 30)
    monkeypatch.setenv("JINX_DATABASE_URL", "postgresql://u:" + "p" * 12 + "@h/db")
    s = config.load_settings(str(tmp_path))
    assert s.s3_endpoint and s.ai_gateway_url and s.database_url
    assert "p" * 12 not in repr(s)                                   # connection passwords stay out of reprs
    app = JinxApp(s)
    app._build_core()
    app._build_services()
    assert app.backups is not None and app.ai_gateway is not None and app.replicator is not None
    shown = json.dumps([app.backups.health(), app.ai_gateway.health()], default=str)
    assert secret not in shown and "2" * 30 not in shown
    app.store.close()


def test_the_ai_gateway_client_speaks_the_openai_compatible_api_with_bounded_requests():
    from jinx.adapters.neonai.gateway import NeonAiGateway
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers["authorization"]))
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})
        assert json.loads(request.content)["max_tokens"] == 16
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    gw = NeonAiGateway("https://gw.example", "tok", transport=httpx.MockTransport(handler), max_in_flight=1)
    assert asyncio.run(gw.models()) == ["m1", "m2"]
    assert asyncio.run(gw.chat("m1", [{"role": "user", "content": "hi"}], max_tokens=16)) == "ok"
    assert seen[0] == ("GET", "/v1/models", "Bearer tok") and gw.health()["connected"] is True
    assert "tok" not in json.dumps(gw.health())


def test_nothing_on_the_trading_path_imports_the_ai_gateway():
    for path in (ROOT / "jinx").rglob("*.py"):
        if path.name == "app.py" or "neonai" in path.parts:
            continue
        assert "neonai" not in path.read_text(), path


# ---- monitoring (rule 10) and end of day (rule 9) ------------------------------------------------------
def test_the_storage_monitor_flags_level_and_growth_before_capacity_is_reached():
    from jinx.storage import StorageMonitor
    m = StorageMonitor({"disk": 10_000})
    m.record("disk", 100, 0)
    m.record("disk", 110, 3600)                     # 10 bytes/hour: years left
    assert m.reading("disk")["status"] == "ok"
    m.record("disk", 3000, 7200)                    # 30% used, but 1450 bytes/hour: under 24 hours left
    r = m.reading("disk")
    assert r["status"] == "warn" and r["hours_to_budget"] < 24
    m.record("disk", 9600, 7300)                    # 96% used
    assert m.report()["status"] == "critical"
    m.record("redis", None, 0, {"error": "ConnectionError"})
    assert m.report()["components"]["redis"]["status"] == "unknown"


def test_the_app_samples_every_configured_component(tmp_path):
    from jinx import config
    from jinx.app import JinxApp
    app = JinxApp(config.load_settings(str(tmp_path)))
    app._build_core()
    asyncio.run(app.sample_storage())
    comps = app.monitor.report()["components"]
    assert {"ram", "disk", "filesystem"} <= set(comps)
    assert comps["ram"]["used_bytes"] > 0 and comps["disk"]["ledger_bytes"] > 0
    app.store.close()


def test_maintenance_releases_space_and_truncates_the_wal(tmp_path):
    store = Store(tmp_path / "l.db")
    assert store.conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2           # INCREMENTAL on a new database
    for i in range(3000):
        store.set_setting(f"k{i}", "x" * 200, now_ns=i)
    with store.transaction() as c:
        c.execute("DELETE FROM settings_audit")
        c.execute("DELETE FROM settings")
        c.execute("DELETE FROM outbox")
    report = store.maintain()
    assert report["bytes_after"] < report["bytes_before"]
    assert not (tmp_path / "l.db-wal").exists() or (tmp_path / "l.db-wal").stat().st_size == 0
    store.close()


# ---- captures (rule 6) and tests that clean up after themselves ----------------------------------------
def test_an_engineering_capture_stops_at_its_size_cap(tmp_path):
    from jinx import config
    from jinx.app import JinxApp
    import dataclasses
    s = dataclasses.replace(config.load_settings(str(tmp_path)), capture_max_bytes=1_000)
    app = JinxApp(s, capture_path=tmp_path / "c.jsonl.gz")
    app._capture = gzip.open(app.capture_path, "at")
    for i in range(100):
        app._tap(i, {"n": i, "pad": "x" * 50})
    app._capture.close()
    assert app._capture_bytes <= 1_000 + 100
    assert len(gzip.open(app.capture_path, "rt").read().splitlines()) < 100


def test_pytest_removes_its_temporary_directories():
    opts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert opts["tmp_path_retention_policy"] == "none" and opts["tmp_path_retention_count"] == 0
    assert "no:cacheprovider" in opts["addopts"]
    conftest = (ROOT / "tests" / "conftest.py").read_text()
    assert "mkdtemp(prefix=\"jinx-pytest-\")" in conftest and "shutil.rmtree(_RUN_TMP" in conftest


def test_a_replay_without_db_leaves_no_temporary_database(tmp_path, monkeypatch):
    import tempfile
    from jinx.__main__ import main
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    fixture = ROOT / "tests" / "fixtures" / "pumpapi-live-20261021.jsonl.gz"
    if not fixture.exists():
        pytest.skip("fixture missing")
    assert main(["replay", str(fixture)]) == 0
    assert list(tmp_path.iterdir()) == []


def test_the_execution_server_config_caps_logs_memory_and_temporary_files():
    unit = (ROOT / "deploy" / "contabo" / "jinx.service").read_text()
    journal = (ROOT / "deploy" / "contabo" / "journald-jinx.conf").read_text()
    for line in ("PrivateTmp=yes", "StandardOutput=journal", "MemoryMax=", "StateDirectory=jinx",
                 "EnvironmentFile=/etc/jinx/jinx.env"):
        assert line in unit
    for line in ("Compress=yes", "SystemMaxUse=", "MaxRetentionSec=", "SystemKeepFree="):
        assert line in journal
    example = (ROOT / "deploy" / "contabo" / "jinx.env.example").read_text()
    assert all(l.endswith("=") or l.startswith("#") or l.endswith("us-east-2") or not l
               for l in example.splitlines())                     # names only, no values
