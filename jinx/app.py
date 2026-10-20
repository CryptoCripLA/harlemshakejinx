"""JINX runtime: the composition root that wires adapters to core.

Phase 1 runtime: live Pump.fun events (Mako's stream) -> bus -> durable store
and registries; paper wallet, ledger and order gateway ready but idle (there
is no strategy until Phases 2 and 3); GMGN bridge health; a /health endpoint
for the host.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import signal
import time
from pathlib import Path

from jinx import config
from jinx.adapters.d3ad.adapter import D3adExecutionAdapter
from jinx.adapters.datanomad.adapter import DataNomadResearchAdapter
from jinx.adapters.gmgn.adapter import GmgnAdapter
from jinx.adapters.mako.feed import MakoPumpPortalFeed
from jinx.core.bus import EventBus, Lane
from jinx.core_state import CoreState
from jinx.execution import ExecutionGateway
from jinx.health import build_health
from jinx.ledger import PositionLedger
from jinx.paper import PaperExecutor, PaperWallet
from jinx.recovery import recover
from jinx.registry import TokenRegistry, WalletRegistry
from jinx.store import Store

log = logging.getLogger("jinx")


class JinxApp:
    def __init__(self, settings: config.Settings, capture_path: Path | None = None, ws_url: str | None = None) -> None:
        self.s = settings
        self.capture_path = capture_path
        self.ws_url = ws_url
        self._capture = None
        self._tasks: list[asyncio.Task] = []
        self._server = None

    async def start(self) -> None:
        s = self.s
        self.store = Store(s.db_path)
        self.core = CoreState(self.store, TokenRegistry(self.store), WalletRegistry(self.store))
        self.ledger = PositionLedger(self.store)
        self.wallet = PaperWallet(self.store, config.PAPER_START_LAMPORTS)
        self.paper = PaperExecutor(self.store, self.ledger, self.wallet, self.core.tokens, config.PUMP_CURVE_FEES,
                                   config.NETWORK_FEE_LAMPORTS)
        self.gateway = ExecutionGateway(self.store, self.ledger, self.paper)
        self.bus = EventBus(persist=self.store.record_event)
        self.bus.subscribe(Lane.FAST, self.core.apply, name="core_state")
        self.recovery = recover(self.store, self.core, self.ledger, self.wallet, bus=self.bus)
        log.info("recovery: %s", self.recovery)
        if self.capture_path:
            self.capture_path.parent.mkdir(parents=True, exist_ok=True)
            self._capture = gzip.open(self.capture_path, "at")
        self.feed = MakoPumpPortalFeed(self.bus, s.upstream_dir / "mako", ws_url=self.ws_url,
                                       max_watched_tokens=s.max_watched_tokens, tap=self._tap if self._capture else None,
                                       api_key=os.environ.get("PUMPPORTAL_API_KEY") or None)
        self.gmgn = GmgnAdapter(s.gmgn_build_dir)
        self.d3ad = D3adExecutionAdapter(s.upstream_dir / "d3ad")
        self.datanomad = DataNomadResearchAdapter(s.upstream_dir / "datanomad")
        if (s.gmgn_build_dir / "dist" / "client" / "OpenApiClient.js").exists():
            await self.gmgn.start()
        self._tasks = [asyncio.create_task(self.bus.run(), name="bus"),
                       asyncio.create_task(self.feed.run(), name="mako-feed"),
                       asyncio.create_task(self._status_loop(), name="status")]
        self._server = await asyncio.start_server(self._http, "0.0.0.0", s.http_port)
        log.info("JINX up: db=%s port=%s mode=%s", s.db_path, s.http_port, (await self.health())["mode"])

    def _tap(self, recv_ns: int, raw) -> None:
        self._capture.write(json.dumps({"recv_ns": recv_ns, "raw": raw}) + "\n")

    async def health(self) -> dict:
        adapters = [self.feed.health(), await self.gmgn.health(), self.d3ad.health(), self.datanomad.health()]
        return build_health(self.store, self.ledger, self.wallet, adapters, self.bus.stats, time.time_ns(),
                            latency_ns=self.bus.latency_ns(Lane.FAST),
                            feed_stale_after_ns=self.s.feed_stale_after_s * 1_000_000_000)

    async def _http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), 5)
            path = request.split()[1].decode() if len(request.split()) > 1 else "/"
            if path == "/health":
                h = await self.health()
                body, code = json.dumps(h, default=str).encode(), (200 if h["status"] != "failed" else 503)
            else:
                body, code = b'{"error": "not found"}', 404
            writer.write(f"HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                         f"Connection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except Exception:
            log.exception("health endpoint error")
        finally:
            writer.close()

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            h = await self.health()
            log.info("status=%s events=%s tokens=%s feed=%s latency=%s", h["status"], h["store"]["events"],
                     len(self.core.tokens), {k: h["adapters"][0].get(k) for k in ("connected", "received", "malformed")},
                     h["ingest_to_state_ms"])

    async def stop(self) -> None:
        for t in self._tasks:
            if t.get_name() != "bus":
                t.cancel()
        try:
            await asyncio.wait_for(self.bus.drain(), 10)
        except asyncio.TimeoutError:
            log.warning("bus did not drain in 10s; unapplied events will be replayed on next start")
        for t in self._tasks:
            t.cancel()
        if self._server:
            self._server.close()
        await self.gmgn.stop()
        if self._capture:
            self._capture.close()
        self.store.close()


async def run(settings: config.Settings, seconds: float | None = None, capture_path: Path | None = None) -> dict:
    app = JinxApp(settings, capture_path=capture_path)
    await app.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await asyncio.wait_for(stop.wait(), seconds) if seconds else await stop.wait()
    except asyncio.TimeoutError:
        pass
    final = await app.health()
    await app.stop()
    return final
