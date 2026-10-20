"""The four source adapters: each isolated, each failing safely."""
import ast
import asyncio
import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import websockets

from jinx.core.bus import EventBus, Lane
from jinx.core.events import EventKind, Side

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "jinx" / "adapters"
NAMES = ("gmgn", "mako", "d3ad", "datanomad")

CREATE = {"signature": "CS1", "mint": "MintAAApump", "traderPublicKey": "Dev111", "txType": "create",
          "initialBuy": 35_000_000.5, "solAmount": 1.0, "bondingCurveKey": "BC1",
          "vTokensInBondingCurve": 1_038_000_000.25, "vSolInBondingCurve": 31.0, "marketCapSol": 29.86,
          "name": "Aaa", "symbol": "AAA", "uri": "https://x", "pool": "pump"}
BUY = {"signature": "TS1", "mint": "MintAAApump", "traderPublicKey": "Buyer1", "txType": "buy",
       "tokenAmount": 1_000_000.0, "solAmount": 0.0301, "newTokenBalance": 1_000_000.0, "bondingCurveKey": "BC1",
       "vTokensInBondingCurve": 1_037_000_000.25, "vSolInBondingCurve": 31.0301, "marketCapSol": 29.9, "pool": "pump"}


# ---- isolation ----------------------------------------------------------------
def _imports(path: Path) -> set[str]:
    mods = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


@pytest.mark.parametrize("name", NAMES)
def test_no_adapter_imports_another_adapter(name):
    others = [n for n in NAMES if n != name]
    for path in (ADAPTERS / name).rglob("*.py"):
        for mod in _imports(path):
            assert not any(mod.startswith(f"jinx.adapters.{o}") for o in others), f"{path.name} imports {mod}"


@pytest.mark.parametrize("name", NAMES)
def test_no_adapter_touches_another_upstream_tree(name):
    others = [n for n in NAMES if n != name]
    for path in (ADAPTERS / name).rglob("*"):
        if path.is_file() and path.suffix in (".py", ".mjs"):
            text = path.read_text()
            for o in others:
                assert f'"{o}"' not in text and f"upstream/{o}" not in text and f"'{o}'" not in text, \
                    f"{path.name} references upstream {o}"


COMPOSITION_ROOT = {"app.py", "__main__.py", "config.py", "sim.py"}  # wiring and replay harness


def test_core_never_imports_adapters_or_reads_upstream_paths():
    for path in (ROOT / "jinx").rglob("*.py"):
        if "adapters" in path.parts or path.name in COMPOSITION_ROOT:
            continue
        tree = ast.parse(path.read_text())
        assert not any(m.startswith("jinx.adapters") for m in _imports(path)), f"{path} imports an adapter"
        literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not any("upstream/" in s or s == "upstream" for s in literals), f"{path} reads an upstream path"


def test_each_adapter_has_its_own_state_directory(tmp_path):
    from jinx.adapters.base import state_dir
    dirs = {state_dir(tmp_path, n) for n in NAMES}
    assert len(dirs) == 4 and all(d.parent == tmp_path / "adapters" for d in dirs)


# ---- Mako: upstream PumpPortalStream behind a clean import -----------------------
def test_mako_upstream_loads_without_leaking_globals(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("JINX_LEAK_SENTINEL=leaked\n")
    monkeypatch.chdir(tmp_path)
    before_path = list(sys.path)
    before_env = dict(os.environ)
    from jinx.adapters.mako.loader import load_mako
    mods = load_mako(ROOT / "upstream" / "mako")
    assert mods.pumpportal.__file__.endswith("upstream/mako/src/pumpportal.py")
    assert "config" not in sys.modules and "pumpportal" not in sys.modules
    assert sys.path == before_path
    assert dict(os.environ) == before_env
    assert hasattr(mods.pumpportal, "PumpPortalStream")


def test_mako_adapter_never_instantiates_the_trading_client():
    for path in (ADAPTERS / "mako").rglob("*.py"):
        assert "PumpPortal(" not in path.read_text()


def test_create_message_becomes_creation_plus_dev_buy():
    from jinx.adapters.mako.normalize import normalize
    events = normalize(CREATE, recv_ns=10, next_seq=iter(range(1, 9)).__next__)
    kinds = [(e.kind, e.side) for e in events]
    assert kinds == [(EventKind.TOKEN_CREATED, None), (EventKind.TRADE, Side.BUY)]
    created, dev_buy = events
    assert created.trader == dev_buy.trader == "Dev111"
    assert dev_buy.sol_lamports == 1_000_000_000
    assert dev_buy.token_amount == 35_000_000_500_000  # 6-decimal raw units
    assert created.v_sol_lamports == 31_000_000_000 and created.v_tokens == 1_038_000_000_250_000
    assert created.idempotency_key != dev_buy.idempotency_key


def test_trade_message_is_normalized():
    from jinx.adapters.mako.normalize import normalize
    [e] = normalize(BUY, recv_ns=11, next_seq=iter([5]).__next__)
    assert (e.kind, e.side, e.trader, e.sol_lamports) == (EventKind.TRADE, Side.BUY, "Buyer1", 30_100_000)
    assert e.v_sol_lamports == 31_030_100_000


def test_non_curve_pool_trade_carries_no_curve_reserves():
    from jinx.adapters.mako.normalize import normalize
    [e] = normalize(dict(BUY, pool="pump-amm"), recv_ns=1, next_seq=iter([1]).__next__)
    assert e.v_sol_lamports is None and e.v_tokens is None


@pytest.mark.parametrize("bad", [{}, {"mint": "x"}, dict(BUY, solAmount="lots"), dict(BUY, txType="mint"),
                                 dict(BUY, solAmount=-1.0), "not-a-dict"])
def test_malformed_messages_raise_instead_of_inventing_data(bad):
    from jinx.adapters.mako.normalize import MalformedMessage, normalize
    with pytest.raises(MalformedMessage):
        normalize(bad, recv_ns=1, next_seq=iter([1]).__next__)


def test_mako_feed_end_to_end_over_a_local_websocket():
    """Upstream PumpPortalStream, unmodified, fed by a local server: dupes once, garbage counted, no false events."""
    from jinx.adapters.mako.feed import MakoPumpPortalFeed

    async def main():
        subscriptions = []
        async def server(ws):
            async for msg in ws:
                subscriptions.append(json.loads(msg))
                if subscriptions[-1]["method"] == "subscribeNewToken":
                    for m in (CREATE, BUY, BUY, {"txType": "buy", "traderPublicKey": "X"}, "garbage"):
                        await ws.send(json.dumps(m) if not isinstance(m, str) else m)
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            bus = EventBus()
            got = []
            bus.subscribe(Lane.FAST, got.append)
            feed = MakoPumpPortalFeed(bus, ROOT / "upstream" / "mako", ws_url=f"ws://127.0.0.1:{port}",
                                      max_watched_tokens=10, subscribe_trades=True)
            runner = asyncio.create_task(bus.run())
            task = asyncio.create_task(feed.run())
            for _ in range(100):
                await asyncio.sleep(0.02)
                if len(got) >= 3 and feed.stats["malformed"] >= 1:
                    break
            await bus.drain()
            task.cancel(); runner.cancel()
            return got, feed.stats, subscriptions, bus.stats
    got, stats, subs, bus_stats = asyncio.run(main())
    assert [(e.kind, e.signature) for e in got] == [(EventKind.TOKEN_CREATED, "CS1"), (EventKind.TRADE, "CS1"),
                                                     (EventKind.TRADE, "TS1")]
    assert bus_stats["duplicates"] == 1
    assert stats["malformed"] == 1  # the incomplete trade; plain "garbage" is not JSON and Mako drops it upstream
    assert {"method": "subscribeTokenTrade", "keys": ["MintAAApump"]} in subs


# ---- GMGN: GMGN's own compiled client behind a read-only bridge ------------------------
BRIDGE_BUILT = (ROOT / "build" / "gmgn" / "dist" / "client" / "OpenApiClient.js").exists()
needs_bridge = pytest.mark.skipif(not (BRIDGE_BUILT and shutil.which("node")),
                                  reason="run scripts/build_gmgn_bridge.sh first")


class FakeGmgn(BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):
        u = urlparse(self.path)
        FakeGmgn.seen.append({"path": u.path, "query": parse_qs(u.query), "apikey": self.headers.get("X-APIKEY"),
                              "signature": self.headers.get("X-Signature")})
        address = parse_qs(u.query).get("address", [None])[0]
        data = {"address": address, "blob": "x" * 70_000} if address == "BIG" else {"address": address}
        body = json.dumps({"code": 0, "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_gmgn():
    FakeGmgn.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeGmgn)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@needs_bridge
def test_gmgn_read_call_goes_through_gmgns_own_client(fake_gmgn, tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnAdapter

    async def main():
        a = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "test-key", "JINX_GMGN_HOST": fake_gmgn},
                        home=tmp_path)
        await a.start()
        try:
            return await a.call("getTokenInfo", "sol", "MINTX"), await a.health()
        finally:
            await a.stop()
    result, health = asyncio.run(main())
    assert result == {"address": "MINTX"}
    [req] = FakeGmgn.seen
    assert req["path"] == "/v1/token/info" and req["apikey"] == "test-key"
    assert {"chain", "address", "timestamp", "client_id"} <= set(req["query"])
    assert health["configured"] is True and health["bridge"] == "up"


@needs_bridge
@pytest.mark.parametrize("method", ["swap", "multiSwap", "createStrategyOrder", "cancelStrategyOrder", "createToken"])
def test_gmgn_trading_methods_are_refused_before_any_request(fake_gmgn, tmp_path, method):
    from jinx.adapters.gmgn.adapter import GmgnAdapter, GmgnError

    async def main():
        a = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k", "JINX_GMGN_HOST": fake_gmgn}, home=tmp_path)
        await a.start()
        try:
            with pytest.raises(GmgnError, match="METHOD_NOT_ENABLED"):
                await a.call(method, {})
        finally:
            await a.stop()
    asyncio.run(main())
    assert FakeGmgn.seen == []


@needs_bridge
def test_gmgn_without_credentials_reports_unconfigured_and_sends_nothing(fake_gmgn, tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnAdapter, GmgnError

    async def main():
        a = GmgnAdapter(ROOT / "build" / "gmgn", env={"JINX_GMGN_HOST": fake_gmgn}, home=tmp_path)
        await a.start()
        try:
            health = await a.health()
            with pytest.raises(GmgnError, match="NOT_CONFIGURED"):
                await a.call("getTokenInfo", "sol", "M")
            return health
        finally:
            await a.stop()
    assert asyncio.run(main())["configured"] is False
    assert FakeGmgn.seen == []


@needs_bridge
def test_gmgn_bridge_crash_is_reported_not_propagated(tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnAdapter, GmgnUnavailable

    async def main():
        a = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k"}, home=tmp_path, auto_restart=False)
        await a.start()
        a._gen.proc.kill()
        await a._gen.proc.wait()
        with pytest.raises(GmgnUnavailable):
            await a.call("getTokenInfo", "sol", "M")
        h = await a.health()
        await a.stop()
        return h
    assert asyncio.run(main())["bridge"] == "down"


def test_without_a_pumpportal_key_the_feed_sends_no_trade_subscriptions_and_says_so():
    from jinx.adapters.mako.feed import MakoPumpPortalFeed

    async def main():
        subs = []
        async def server(ws):
            async for msg in ws:
                subs.append(json.loads(msg)["method"])
                if subs[-1] == "subscribeNewToken":
                    await ws.send(json.dumps(CREATE))
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            bus = EventBus()
            feed = MakoPumpPortalFeed(bus, ROOT / "upstream" / "mako", ws_url=f"ws://127.0.0.1:{port}")
            runner, task = asyncio.create_task(bus.run()), asyncio.create_task(feed.run())
            for _ in range(100):
                await asyncio.sleep(0.02)
                if feed.stats["published"] >= 2:
                    break
            task.cancel(); runner.cancel()
            return subs, feed.health()
    subs, health = asyncio.run(main())
    assert subs == ["subscribeNewToken"]
    assert health["trade_stream"].startswith("BLOCKED")


def test_pumpportal_key_is_used_for_the_connection_but_never_reported():
    from jinx.adapters.mako.feed import MakoPumpPortalFeed
    feed = MakoPumpPortalFeed(EventBus(), ROOT / "upstream" / "mako", api_key="SECRETKEY")
    assert feed.subscribe_trades is True
    assert "SECRETKEY" not in json.dumps(feed.health())
    from jinx.adapters.mako.loader import load_mako
    assert load_mako(ROOT / "upstream" / "mako").pumpportal.PUMPPORTAL_WS_URL.endswith("?api-key=SECRETKEY")
    MakoPumpPortalFeed(EventBus(), ROOT / "upstream" / "mako")  # reset the module URL for later tests


@needs_bridge
def test_gmgn_large_response_is_delivered(fake_gmgn, tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnAdapter

    async def main():
        a = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k", "JINX_GMGN_HOST": fake_gmgn}, home=tmp_path)
        await a.start()
        try:
            return await a.call("getTokenInfo", "sol", "BIG")
        finally:
            await a.stop()
    assert len(asyncio.run(main())["blob"]) == 70_000


@needs_bridge
def test_gmgn_oversized_response_fails_cleanly_and_the_bridge_recovers(fake_gmgn, tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnAdapter, GmgnError

    async def main():
        a = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k", "JINX_GMGN_HOST": fake_gmgn},
                        home=tmp_path, max_response_bytes=4096, timeout_s=10)
        await a.start()
        try:
            with pytest.raises(GmgnError, match="RESPONSE_TOO_LARGE"):
                await a.call("getTokenInfo", "sol", "BIG")
            small = await a.call("getTokenInfo", "sol", "SMALL")   # the bridge restarts on demand
            return small, a.stats
        finally:
            await a.stop()
    small, stats = asyncio.run(main())
    assert small == {"address": "SMALL"} and stats["oversized"] == 1 and stats["restarts"] == 1


@needs_bridge
def test_concurrent_calls_after_bridge_death_restart_it_exactly_once(fake_gmgn, tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnAdapter

    async def main():
        a = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k", "JINX_GMGN_HOST": fake_gmgn}, home=tmp_path)
        await a.start()
        first = a._gen.proc
        first.kill()
        await first.wait()
        results = await asyncio.gather(*(a.call("getTokenInfo", "sol", f"M{i}") for i in range(4)))
        live = a._gen.proc
        await a.stop()
        return results, dict(a.stats), first, live
    results, stats, first, live = asyncio.run(main())
    assert [r["address"] for r in results] == ["M0", "M1", "M2", "M3"]   # no request lost
    assert stats["restarts"] == 1 and stats["spawned"] == 2              # one replacement, not one per caller
    assert first.returncode is not None and live.returncode is not None  # stop() leaves no child behind


def test_gmgn_adapter_passes_only_its_own_variables_to_the_bridge(tmp_path, monkeypatch):
    from jinx.adapters.gmgn.adapter import GmgnAdapter
    monkeypatch.setenv("SOME_OTHER_SECRET", "x")
    a = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k"}, home=tmp_path)
    env = a.child_env()
    assert "SOME_OTHER_SECRET" not in env and env["GMGN_API_KEY"] == "k"
    assert "GMGN_DEBUG" not in env  # debug mode would print request headers, including the API key


# ---- D3AD and DataNomad: pinned, isolated, not yet wired ------------------------
def test_d3ad_execution_adapter_is_pinned_but_cannot_execute_in_phase_1():
    from jinx.adapters.d3ad.adapter import D3adExecutionAdapter
    from jinx.execution import LiveExecutionUnavailable
    a = D3adExecutionAdapter(ROOT / "upstream" / "d3ad")
    h = a.health()
    assert h["pinned"] is True and h["integrated"] is False
    with pytest.raises(LiveExecutionUnavailable):
        a.submit(object())


def test_datanomad_is_reference_only_without_a_license():
    from jinx.adapters.datanomad.adapter import DataNomadResearchAdapter, LicenseNotGranted
    a = DataNomadResearchAdapter(ROOT / "upstream" / "datanomad")
    assert a.health()["license"] == "NONE"
    with pytest.raises(LicenseNotGranted):
        a.load_code("deploy_parser")
