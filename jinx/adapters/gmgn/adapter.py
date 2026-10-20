"""GMGN adapter: GMGN's own TypeScript client in a child Node process (bridge.mjs).

The child gets only the variables it needs (no other secrets from JINX's
environment). A crashed bridge is reported through health() and GmgnUnavailable;
it never takes the rest of JINX down. GMGN supplies data; it does not decide
entries or exits.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
from collections import deque
from pathlib import Path
from typing import Any

BRIDGE = Path(__file__).with_name("bridge.mjs")
PASSED_VARS = ("GMGN_API_KEY", "GMGN_PRIVATE_KEY", "JINX_GMGN_HOST")
# Response-size contract: one JSON reply per line, at most this many bytes. A larger reply fails that request
# with RESPONSE_TOO_LARGE and the bridge is restarted on the next call.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class GmgnError(Exception):
    """GMGN (or the bridge) answered with an error."""


class GmgnUnavailable(Exception):
    """The bridge process is not running."""


class GmgnAdapter:
    name = "gmgn"

    def __init__(self, build_dir: Path, env: dict[str, str] | None = None, home: Path | None = None,
                 node: str = "node", timeout_s: float = 15.0, max_response_bytes: int = MAX_RESPONSE_BYTES,
                 auto_restart: bool = True) -> None:
        self.build_dir = Path(build_dir)
        self.env = env
        self.home = home
        self.node = node
        self.timeout_s = timeout_s
        self.max_response_bytes = max_response_bytes
        self.auto_restart = auto_restart
        self._started = False
        self._reader_failed = False
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._tasks: list[asyncio.Task] = []
        self.stats = {"calls": 0, "errors": 0, "unavailable": 0, "oversized": 0, "restarts": 0}

    def child_env(self) -> dict[str, str]:
        source = self.env if self.env is not None else os.environ
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home or Path.home()),
               "JINX_GMGN_BUILD": str(self.build_dir / "dist")}
        env.update({k: source[k] for k in PASSED_VARS if source.get(k)})
        return env

    async def start(self) -> None:
        self._reader_failed = False
        self._proc = await asyncio.create_subprocess_exec(
            self.node, str(BRIDGE), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=self.child_env(), limit=self.max_response_bytes)
        self._tasks = [asyncio.create_task(self._read_stdout()), asyncio.create_task(self._read_stderr())]
        self._started = True

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and not self._reader_failed

    async def call(self, method: str, *args: Any) -> Any:
        if not self.running and self.auto_restart and self._started:
            await self._restart()
        if not self.running:
            self.stats["unavailable"] += 1
            raise GmgnUnavailable("GMGN bridge is not running")
        req_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        self.stats["calls"] += 1
        try:
            self._proc.stdin.write((json.dumps({"id": req_id, "method": method, "args": list(args)}) + "\n").encode())
            await self._proc.stdin.drain()
            msg = await asyncio.wait_for(fut, self.timeout_s)
        except (BrokenPipeError, ConnectionResetError) as e:
            self.stats["unavailable"] += 1
            raise GmgnUnavailable(f"GMGN bridge pipe closed: {e}") from None
        finally:
            self._pending.pop(req_id, None)
        if not msg.get("ok"):
            self.stats["errors"] += 1
            err = msg.get("error") or {}
            raise GmgnError(f"{err.get('code')}: {err.get('message')}")
        return msg.get("result")

    async def health(self) -> dict:
        if not self.running:
            return {"adapter": self.name, "bridge": "down", "configured": None, **self.stats}
        try:
            h = await self.call("__health")
        except (GmgnUnavailable, asyncio.TimeoutError):
            return {"adapter": self.name, "bridge": "down", "configured": None, **self.stats}
        return {"adapter": self.name, "bridge": "up", **h, **self.stats}

    async def stop(self) -> None:
        if self.running:
            self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        for t in self._tasks:
            t.cancel()

    async def _restart(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()
        for t in self._tasks:
            t.cancel()
        self.stats["restarts"] += 1
        await self.start()

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)

    async def _read_stdout(self) -> None:
        proc = self._proc
        while True:
            try:
                line = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # a reply longer than the size contract: fail what is waiting and retire this bridge
                self.stats["oversized"] += 1
                self._reader_failed = True
                self._fail_pending(GmgnError(f"RESPONSE_TOO_LARGE: reply exceeded {self.max_response_bytes} bytes"))
                proc.kill()
                return
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            fut = self._pending.get(msg.get("id"))
            if fut and not fut.done():
                fut.set_result(msg)
        self._fail_pending(GmgnUnavailable("GMGN bridge exited"))

    async def _read_stderr(self) -> None:
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            self._stderr_tail.append(line.decode(errors="replace").rstrip())
