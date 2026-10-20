"""GMGN rate-limit bans are honored, not extended (owner mandate 2026-10-20 §2: preserve the client's rate-limit
behavior). GMGN: "repeated requests during the cooldown can extend the ban by 5 seconds each time, up to 5
minutes"; on RATE_LIMIT_BANNED_MAX "Stop all GMGN calls ... any request before reset_at resets the ban"
(skills/gmgn-token/SKILL.md:68-76). The pinned client's own short-ban retry is switched off (and never fires in
the pinned build), so every ban reaches JINX, which stops sending on the key until the reset time."""
import asyncio
import json
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_BUILT = (ROOT / "build" / "gmgn" / "dist" / "client" / "OpenApiClient.js").exists()
needs_bridge = pytest.mark.skipif(not (BRIDGE_BUILT and shutil.which("node")),
                                  reason="run scripts/build_gmgn_bridge.sh first")


class LimitedGmgn(BaseHTTPRequestHandler):
    seen: list = []
    reset_at: int | None = None        # sent as the X-RateLimit-Reset header
    body_reset_at: int | None = None   # sent only in the JSON body, as GMGN documents for bans
    error = "RATE_LIMIT_BANNED"

    def do_GET(self):
        address = parse_qs(urlparse(self.path).query).get("address", [None])[0]
        LimitedGmgn.seen.append(address)
        if address == "LIMITED":
            status, body = 429, {"code": 429, "error": LimitedGmgn.error, "message": "rate limited"}
            if LimitedGmgn.body_reset_at is not None:
                body["reset_at"] = LimitedGmgn.body_reset_at
        elif address == "PRO":
            status, body = 403, {"code": 403, "error": "PRO_PLAN_REQUIRED", "message": "pro only"}
        else:
            status, body = 200, {"code": 0, "data": {"address": address}}
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-RateLimit-Limit", "20")
        self.send_header("X-RateLimit-Remaining", "19")
        if status == 429 and LimitedGmgn.reset_at is not None:
            self.send_header("X-RateLimit-Reset", str(LimitedGmgn.reset_at))
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    do_POST = do_GET

    def log_message(self, *a):
        pass


@pytest.fixture
def limited_gmgn():
    LimitedGmgn.seen, LimitedGmgn.reset_at, LimitedGmgn.error = [], None, "RATE_LIMIT_BANNED"
    LimitedGmgn.body_reset_at = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), LimitedGmgn)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class Wall:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def adapter(host, tmp_path, wall):
    from jinx.adapters.gmgn.adapter import GmgnAdapter
    return GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k", "JINX_GMGN_HOST": host}, home=tmp_path,
                       wall=wall)


@needs_bridge
@pytest.mark.parametrize("error", ["RATE_LIMIT_EXCEEDED", "RATE_LIMIT_BANNED", "RATE_LIMIT_BANNED_MAX",
                                   "ERROR_RATE_LIMIT_BLOCKED"])
def test_a_ban_pauses_every_gmgn_call_until_the_reset_time(limited_gmgn, tmp_path, error):
    from jinx.adapters.gmgn.adapter import GmgnRateLimited
    now = int(time.time())
    LimitedGmgn.reset_at, LimitedGmgn.error = now + 120, error   # longer than the client's own 5 s retry
    wall = Wall(now)

    async def main():
        a = adapter(limited_gmgn, tmp_path, wall)
        await a.start()
        try:
            with pytest.raises(GmgnRateLimited) as first:
                await a.call("getTokenInfo", "sol", "LIMITED")
            sent_after_ban = len(LimitedGmgn.seen)
            with pytest.raises(GmgnRateLimited):          # a different route: the ban covers the whole key
                await a.call("getTokenSecurity", "sol", "OK")
            assert len(LimitedGmgn.seen) == sent_after_ban  # nothing was sent during the cooldown
            health = await a.health()
            wall.t = now + 121                             # after the reset, calls flow again
            result = await a.call("getTokenInfo", "sol", "OK")
            return first.value, health, result
        finally:
            await a.stop()
    err, health, result = asyncio.run(main())
    assert err.reset_at == now + 120 and error in str(err)
    assert health["paused_until"] == now + 120 and health["rate_limited"] >= 1
    assert result == {"address": "OK"}


@needs_bridge
def test_a_ban_without_any_reset_time_pauses_for_gmgns_documented_maximum(limited_gmgn, tmp_path):
    """Round 9 M2: GMGN's 5 s is the ban *step*, not its length. With no reset time at all, JINX waits out the
    documented maximum ban ("up to 5 minutes", skills/gmgn-token/SKILL.md:73-76) rather than guess shorter."""
    from jinx.adapters.gmgn.adapter import UNKNOWN_BAN_PAUSE_S, GmgnRateLimited
    now = 1_800_000_000
    wall = Wall(now)

    async def main():
        a = adapter(limited_gmgn, tmp_path, wall)
        await a.start()
        try:
            with pytest.raises(GmgnRateLimited) as e:
                await a.call("getTokenInfo", "sol", "LIMITED")
            return e.value
        finally:
            await a.stop()
    assert UNKNOWN_BAN_PAUSE_S == 300
    assert asyncio.run(main()).reset_at == now + 300


@needs_bridge
def test_a_reset_time_given_only_in_the_body_is_honored(limited_gmgn, tmp_path):
    """Round 9 M2: GMGN documents `reset_at` in the 429 body (skills/gmgn-token/SKILL.md:73); the pinned client
    reads only the X-RateLimit-Reset header, so the bridge carries the body value into that header."""
    from jinx.adapters.gmgn.adapter import GmgnRateLimited
    now = int(time.time())
    LimitedGmgn.body_reset_at = now + 240

    async def main():
        a = adapter(limited_gmgn, tmp_path, Wall(now))
        await a.start()
        try:
            with pytest.raises(GmgnRateLimited) as e:
                await a.call("getTokenInfo", "sol", "LIMITED")
            return e.value
        finally:
            await a.stop()
    assert asyncio.run(main()).reset_at == now + 240


def test_a_call_waiting_for_the_bridge_is_not_sent_once_a_ban_has_started():
    """Round 9 M1: the ban is checked again after the bridge is ready, immediately before the write."""
    from jinx.adapters.gmgn.adapter import GmgnAdapter, GmgnRateLimited

    class Stdin:
        writes = []

        def write(self, data):
            Stdin.writes.append(data)

        async def drain(self):
            pass

    class Proc:
        stdin = Stdin()
        returncode = None

    class Gen:
        proc = Proc()
        pending = {}
        alive = True

    wall = Wall(1_000)
    a = GmgnAdapter(ROOT / "build" / "gmgn", env={}, wall=wall)

    async def main():
        ready = asyncio.Event()

        async def ensure_running():
            await ready.wait()           # this caller is queued behind a bridge (re)start
            return Gen()
        a._ensure_running = ensure_running
        call = asyncio.create_task(a.call("getTokenInfo", "sol", "M"))
        await asyncio.sleep(0)           # the call has passed its first ban checks and is waiting
        a.paused_until = 1_400           # another response registers a ban meanwhile
        ready.set()
        with pytest.raises(GmgnRateLimited):
            await call
    asyncio.run(main())
    assert Stdin.writes == []


@needs_bridge
def test_a_plan_refusal_is_an_ordinary_error_and_pauses_nothing(limited_gmgn, tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnError, GmgnRateLimited

    async def main():
        a = adapter(limited_gmgn, tmp_path, Wall(int(time.time())))
        await a.start()
        try:
            with pytest.raises(GmgnError) as e:
                await a.call("getTokenInfo", "sol", "PRO")
            assert not isinstance(e.value, GmgnRateLimited)
            return await a.call("getTokenInfo", "sol", "OK")
        finally:
            await a.stop()
    assert asyncio.run(main()) == {"address": "OK"}


@needs_bridge
def test_health_reports_gmgns_own_rate_limit_headers(limited_gmgn, tmp_path):
    async def main():
        a = adapter(limited_gmgn, tmp_path, Wall(int(time.time())))
        await a.start()
        try:
            before = await a.health()
            await a.call("getTokenInfo", "sol", "OK")
            return before, await a.health()
        finally:
            await a.stop()
    before, after = asyncio.run(main())
    assert before["rate_limit"] is None                      # nothing measured before the first GMGN reply
    assert after["rate_limit"] == {"x-ratelimit-limit": "20", "x-ratelimit-remaining": "19"}


def test_the_clients_own_silent_retry_is_turned_off():
    """Round 8 M4: the pinned client sleeps and retries by itself when a ban ends within 5 s; meanwhile JINX
    would keep sending on other calls. GMGN's own knob (OpenApiClient.ts getAutoRetryMaxWaitMs) set to 0 makes
    every ban reach JINX at once, where one shared pause covers all calls."""
    from jinx.adapters.gmgn.adapter import GmgnAdapter
    env = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k"}).child_env()
    assert env["GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS"] == "0"


@needs_bridge
def test_a_short_ban_reaches_jinx_at_once_instead_of_a_hidden_retry(limited_gmgn, tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnRateLimited
    now = int(time.time())
    LimitedGmgn.reset_at = now + 3                       # inside the client's 5 s auto-retry window

    async def main():
        a = adapter(limited_gmgn, tmp_path, Wall(now))
        await a.start()
        try:
            t0 = time.monotonic()
            with pytest.raises(GmgnRateLimited):
                await a.call("getTokenInfo", "sol", "LIMITED")
            return time.monotonic() - t0
        finally:
            await a.stop()
    elapsed = asyncio.run(main())
    assert LimitedGmgn.seen.count("LIMITED") == 1 and elapsed < 2.0


@needs_bridge
def test_concurrent_callers_during_a_ban_send_nothing(limited_gmgn, tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnRateLimited
    now = int(time.time())
    LimitedGmgn.reset_at = now + 300

    async def main():
        a = adapter(limited_gmgn, tmp_path, Wall(now))
        await a.start()
        try:
            with pytest.raises(GmgnRateLimited):
                await a.call("getTokenInfo", "sol", "LIMITED")
            sent = len(LimitedGmgn.seen)
            results = await asyncio.gather(*(a.call(m, "sol", "OK") for m in
                                             ("getTokenInfo", "getTokenSecurity", "getTokenPoolInfo",
                                              "getTokenTopHolders", "getTokenTopTraders")), return_exceptions=True)
            return sent, results
        finally:
            await a.stop()
    sent, results = asyncio.run(main())
    assert all(isinstance(r, GmgnRateLimited) for r in results) and len(LimitedGmgn.seen) == sent
