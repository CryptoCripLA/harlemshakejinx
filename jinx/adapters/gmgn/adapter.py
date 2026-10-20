"""GMGN adapter: GMGN's own TypeScript client in a child Node process (bridge.mjs).

The child gets only the variables it needs (no other secrets from JINX's
environment). Each child process is one "generation" that owns its pending
requests and reader tasks, so a dying or replaced bridge can only fail its own
requests. Start, restart and stop are serialized: concurrent callers that find
the bridge dead trigger exactly one replacement. A crashed bridge is reported
through health() and GmgnUnavailable; it never takes the rest of JINX down.
GMGN supplies data; it does not decide entries or exits.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinx.adapters.gmgn.budget import PLANS, CURRENT_PLAN, RateBudget, weight

BRIDGE = Path(__file__).with_name("bridge.mjs")
PASSED_VARS = ("GMGN_API_KEY", "GMGN_PRIVATE_KEY", "JINX_GMGN_HOST")
# Response-size contract: one JSON reply per line, at most this many bytes. A larger reply fails that request
# with RESPONSE_TOO_LARGE and the bridge is replaced on the next call.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
# GMGN rate-limit errors that cover the whole API key (skills/gmgn-token/SKILL.md:68-76). Requests sent during the
# cooldown extend the ban, so after one of these JINX sends nothing until the reset time.
RATE_LIMIT_ERRORS = {"RATE_LIMIT_EXCEEDED", "RATE_LIMIT_BANNED", "RATE_LIMIT_BANNED_MAX", "ERROR_RATE_LIMIT_BLOCKED"}
# When GMGN gives no reset time at all (neither the X-RateLimit-Reset header nor the body's reset_at): wait out
# its documented maximum ban, "up to 5 minutes" (skills/gmgn-token/SKILL.md:73-76). GMGN's 5 s is the step a
# request sent during a ban adds, not the ban's length, so a shorter guess could extend the ban.
UNKNOWN_BAN_PAUSE_S = 300


class GmgnError(Exception):
    """GMGN (or the bridge) answered with an error."""


class GmgnUnavailable(Exception):
    """The bridge process is not running."""


class GmgnRateLimited(GmgnError):
    """GMGN banned the key for a while; no request is sent before `reset_at` (unix seconds)."""

    def __init__(self, message: str, reset_at: float) -> None:
        super().__init__(message)
        self.reset_at = reset_at


@dataclass
class _Generation:
    proc: asyncio.subprocess.Process
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    tasks: list[asyncio.Task] = field(default_factory=list)
    failed: bool = False

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None and not self.failed

    def fail_pending(self, exc: Exception) -> None:
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(exc)


class GmgnAdapter:
    name = "gmgn"

    def __init__(self, build_dir: Path, env: dict[str, str] | None = None, home: Path | None = None,
                 node: str = "node", timeout_s: float = 15.0, max_response_bytes: int = MAX_RESPONSE_BYTES,
                 auto_restart: bool = True, allow_signing_key: bool = False, budget: RateBudget | None = None,
                 wall=time.time) -> None:
        self.build_dir = Path(build_dir)
        self.env = env
        self.home = home
        self.node = node
        self.timeout_s = timeout_s
        self.max_response_bytes = max_response_bytes
        self.auto_restart = auto_restart
        # The request-signing key reaches the bridge only when allowed: with it, the API key can trade the
        # GMGN-hosted wallet. The default withholds it; the runtime allows it for signed reads by owner decision
        # (jinx/config.py). Trading stays refused by the bridge allowlist either way.
        self.allow_signing_key = allow_signing_key
        self.budget = budget if budget is not None else RateBudget(PLANS[CURRENT_PLAN])
        self._wall = wall                      # wall clock, because GMGN's reset times are unix seconds
        self.paused_until: float | None = None
        self._gen: _Generation | None = None
        self._lock: asyncio.Lock | None = None
        self._ids = itertools.count(1)
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self.stats = {"calls": 0, "errors": 0, "unavailable": 0, "oversized": 0, "restarts": 0, "spawned": 0,
                      "rate_limited": 0}

    def child_env(self) -> dict[str, str]:
        source = self.env if self.env is not None else os.environ
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home or Path.home()),
               "JINX_GMGN_BUILD": str(self.build_dir / "dist")}
        env.update({k: source[k] for k in PASSED_VARS if source.get(k)})
        if not self.allow_signing_key:
            env.pop("GMGN_PRIVATE_KEY", None)
            env["JINX_GMGN_NO_SIGNING_KEY"] = "1"
        # GMGN's own setting for the client's sleep-and-retry on a short ban (OpenApiClient.ts
        # getAutoRetryMaxWaitMs). At 0 every ban reaches JINX at once, where one shared pause covers all calls.
        # (In the pinned build that retry never fires anyway: executePreparedRequest returns parseResponse's
        # promise without await inside its try, so the rejection skips the retry; see test_gmgn_rate_limit.py.)
        env["GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS"] = "0"
        return env

    @property
    def _lifecycle(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def running(self) -> bool:
        return self._gen is not None and self._gen.alive

    async def start(self) -> None:
        async with self._lifecycle:
            if not self.running:
                await self._spawn()

    async def _spawn(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.node, str(BRIDGE), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=self.child_env(), limit=self.max_response_bytes)
        gen = _Generation(proc)
        gen.tasks = [asyncio.create_task(self._read_stdout(gen)), asyncio.create_task(self._read_stderr(gen))]
        self._gen = gen
        self.stats["spawned"] += 1

    async def _retire(self, gen: _Generation, exc: Exception) -> None:
        gen.failed = True
        gen.fail_pending(exc)
        if gen.proc.returncode is None:
            gen.proc.kill()
            await gen.proc.wait()
        for t in gen.tasks:
            t.cancel()

    async def _ensure_running(self) -> _Generation:
        async with self._lifecycle:
            if self.running:
                return self._gen
            if self._gen is not None and self.auto_restart:  # re-check under the lock: one replacement only
                await self._retire(self._gen, GmgnUnavailable("GMGN bridge replaced"))
                await self._spawn()
                self.stats["restarts"] += 1
                return self._gen
        self.stats["unavailable"] += 1
        raise GmgnUnavailable("GMGN bridge is not running")

    async def call(self, method: str, *args: Any) -> Any:
        if not method.startswith("__"):
            self._refuse_while_banned()
            await self.budget.acquire(weight(method, *args))  # pace to the plan instead of tripping GMGN's bans
            self._refuse_while_banned()                # a ban may have started while this call waited
        msg = await self._send(method, args)
        if not msg.get("ok"):
            self.stats["errors"] += 1
            err = msg.get("error") or {}
            text = f"{err.get('code')}: {err.get('message')}"
            if err.get("api_error") in RATE_LIMIT_ERRORS:
                reset = err.get("reset_at") or self._wall() + UNKNOWN_BAN_PAUSE_S
                self.paused_until = max(self.paused_until or 0, reset)
                self.stats["rate_limited"] += 1
                raise GmgnRateLimited(f"{err.get('api_error')}: {text}", self.paused_until)
            raise GmgnError(text)
        return msg.get("result")

    def _refuse_while_banned(self) -> None:
        if self.paused_until is not None and self._wall() < self.paused_until:
            self.stats["rate_limited"] += 1
            raise GmgnRateLimited(f"RATE_LIMITED: GMGN calls paused until {self.paused_until} (unix s)",
                                  self.paused_until)

    async def _send(self, method: str, args) -> dict:
        gen = await self._ensure_running()
        if not method.startswith("__"):
            self._refuse_while_banned()   # last check, with no await between it and the write below
        req_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        gen.pending[req_id] = fut
        self.stats["calls"] += 1
        try:
            gen.proc.stdin.write((json.dumps({"id": req_id, "method": method, "args": list(args)}) + "\n").encode())
            await gen.proc.stdin.drain()
            return await asyncio.wait_for(fut, self.timeout_s)
        except (BrokenPipeError, ConnectionResetError) as e:
            self.stats["unavailable"] += 1
            raise GmgnUnavailable(f"GMGN bridge pipe closed: {e}") from None
        finally:
            gen.pending.pop(req_id, None)

    async def health(self) -> dict:
        if not self.running:
            return {"adapter": self.name, "bridge": "down", "configured": None, **self.stats}
        try:
            h = await self.call("__health")
        except (GmgnUnavailable, GmgnError, asyncio.TimeoutError):
            return {"adapter": self.name, "bridge": "down", "configured": None, **self.stats}
        return {"adapter": self.name, "bridge": "up", **h, **self.stats, "plan": self.budget.plan.name,
                "budget": dict(self.budget.stats), "paused_until": self.paused_until}

    async def stop(self) -> None:
        async with self._lifecycle:
            gen, self._gen = self._gen, None
            if gen is None:
                return
            if gen.proc.returncode is None:
                gen.proc.stdin.close()
                try:
                    await asyncio.wait_for(gen.proc.wait(), 5)
                except asyncio.TimeoutError:
                    pass
            await self._retire(gen, GmgnUnavailable("GMGN bridge stopped"))

    async def _read_stdout(self, gen: _Generation) -> None:
        while True:
            try:
                line = await gen.proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # a reply longer than the size contract: fail what this generation owes and retire it
                self.stats["oversized"] += 1
                gen.failed = True
                gen.fail_pending(GmgnError(f"RESPONSE_TOO_LARGE: reply exceeded {self.max_response_bytes} bytes"))
                gen.proc.kill()
                return
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            fut = gen.pending.get(msg.get("id"))
            if fut and not fut.done():
                fut.set_result(msg)
        gen.fail_pending(GmgnUnavailable("GMGN bridge exited"))

    async def _read_stderr(self, gen: _Generation) -> None:
        while True:
            line = await gen.proc.stderr.readline()
            if not line:
                break
            self._stderr_tail.append(line.decode(errors="replace").rstrip())
