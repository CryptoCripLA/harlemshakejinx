"""PumpAPI.io Data Stream -> JINX event bus (free; one connection per client, per PumpAPI's docs).

PumpAPI sends every Solana DEX event (~800 messages/s measured 2026-10-20), so this feed:
- drops anything that is not pump/pump-amm with a byte check before JSON parsing;
- publishes every bonding-curve event (creations, buys, sells: CHAD's Early Momentum needs all of them) and every
  migration;
- publishes PumpSwap (pump-amm) trades only for mints it saw on the bonding curve (the most recent
  `max_followed`) and mints JINX holds, which bounds bus load and storage;
- keeps a compact record per event (normalize.COMPACT_FIELDS), not the full message.
Publishing is durable with retry (the same contract as the PumpPortal feed). Reconnects with backoff, as PumpAPI
requires. Only one instance may run per host (one connection per IP).

Stall watchdog: a live connection went silent on 2026-10-21 and was only dropped by the 20 s keepalive ping
timeout, losing ~40 s of events. The stream normally carries ~900 messages/s, so `stall_timeout_s` of silence
closes the connection and the feed reconnects. Every connection is logged (`connection_log`) with its duration,
message count and end reason, so gaps are visible rather than silent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone

import websockets

from jinx.adapters.pumpapi.normalize import (CURVE_POOL, SOURCE, MalformedEvent, NotQuotedInSol, may_be_pump,
                                             normalize)
from jinx.core.bus import EventBus
from jinx.core.events import EventKind

log = logging.getLogger(__name__)
PUMPAPI_WS = "wss://stream.pumpapi.io/"


class PumpApiFeed:
    name = SOURCE

    def __init__(self, bus: EventBus, ws_url: str = PUMPAPI_WS, clock_ns=time.time_ns, max_followed: int = 5000,
                 held_mints_provider=None, tap=None, retry_initial_s: float = 0.05, retry_max_s: float = 5.0,
                 reconnect_initial_s: float = 1.0, reconnect_max_s: float = 30.0, connect=None,
                 stall_timeout_s: float = 5.0, on_connect=None) -> None:
        self.bus = bus
        self.ws_url = ws_url
        self.clock_ns = clock_ns
        self.max_followed = max_followed
        self.held_mints_provider = held_mints_provider or (lambda: set())
        self.tap = tap   # optional callable(recv_ns, raw_bytes) to record captures for replay
        self.retry_initial_s, self.retry_max_s = retry_initial_s, retry_max_s
        self.reconnect_initial_s, self.reconnect_max_s = reconnect_initial_s, reconnect_max_s
        self._connect = connect or (lambda url: websockets.connect(url, open_timeout=15, max_size=None,
                                                                   ping_interval=20))
        self.stall_timeout_s = stall_timeout_s
        # called with the receipt clock at every (re)connection: trades sent while disconnected are lost, so the
        # owner can mark the history before this moment incomplete
        self.on_connect = on_connect
        self._last_mono = time.monotonic()
        self.connection_log: deque[dict] = deque(maxlen=200)
        self._followed: OrderedDict[str, None] = OrderedDict()
        self._lag_ms: deque[float] = deque(maxlen=2000)
        self.connected = False
        self.stats = {"received": 0, "bytes": 0, "filtered": 0, "malformed": 0, "not_quoted_in_sol": 0,
                      "amm_not_followed": 0, "published": 0, "duplicates": 0, "publish_retries": 0,
                      "ingest_stalled": False, "connections": 0, "disconnects": 0, "stalls": 0,
                      "last_message_ns": None}

    async def run(self) -> None:
        delay = self.reconnect_initial_s
        while True:
            entry = {"start_utc": None, "seconds": 0.0, "messages": 0, "end_reason": "connect failed"}
            started, received0 = time.monotonic(), self.stats["received"]
            stalled = {"flag": False}
            watchdog = None
            try:
                async with self._connect(self.ws_url) as ws:
                    self.connected = True
                    self.stats["connections"] += 1
                    if self.on_connect is not None:
                        try:
                            self.on_connect(self.clock_ns())
                        except Exception:
                            log.exception("on_connect listener failed")
                    entry["start_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    entry["end_reason"] = "closed by server"
                    delay = self.reconnect_initial_s
                    self._last_mono = time.monotonic()
                    watchdog = asyncio.create_task(self._watch(ws, stalled))
                    async for raw in ws:
                        self._last_mono = time.monotonic()
                        await self.handle(raw, self.clock_ns())
            except asyncio.CancelledError:
                entry["end_reason"] = "stopped"
                raise
            except Exception as e:
                entry["end_reason"] = f"{type(e).__name__}"
                log.warning("PumpAPI stream dropped (%s); reconnecting in %.1fs", type(e).__name__, delay)
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                if stalled["flag"]:
                    entry["end_reason"] = "stall"
                if self.connected:
                    self.stats["disconnects"] += 1
                self.connected = False
                entry["seconds"] = round(time.monotonic() - started, 1)
                entry["messages"] = self.stats["received"] - received0
                self.connection_log.append(entry)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.reconnect_max_s)

    async def _watch(self, ws, stalled: dict) -> None:
        """Close a connection that has been silent for stall_timeout_s; run() then reconnects."""
        while True:
            await asyncio.sleep(self.stall_timeout_s / 4)
            if time.monotonic() - self._last_mono > self.stall_timeout_s:
                stalled["flag"] = True
                self.stats["stalls"] += 1
                await ws.close()
                return

    async def handle(self, raw, recv_ns: int) -> None:
        b = raw if isinstance(raw, bytes) else raw.encode()
        self.stats["received"] += 1
        self.stats["bytes"] += len(b)
        self.stats["last_message_ns"] = recv_ns
        if not may_be_pump(b):
            self.stats["filtered"] += 1
            return
        if self.tap is not None:
            self.tap(recv_ns, b)
        try:
            msg = json.loads(b)
            events = normalize(msg, recv_ns, self.bus.next_seq)
        except NotQuotedInSol:
            self.stats["not_quoted_in_sol"] += 1
            return
        except (MalformedEvent, ValueError):
            self.stats["malformed"] += 1
            return
        if not events:
            self.stats["filtered"] += 1
            return
        ts = msg.get("timestamp")
        if isinstance(ts, (int, float)):
            self._lag_ms.append(recv_ns / 1e6 - ts)
        for ev in events:
            if ev.raw.get("pool") == CURVE_POOL:
                self._follow(ev.mint)
            elif ev.kind is EventKind.TRADE and ev.mint not in self._followed and ev.mint not in self.held_mints_provider():
                self.stats["amm_not_followed"] += 1
                continue
            await self._publish_durably(ev)

    def _follow(self, mint: str) -> None:
        self._followed[mint] = None
        self._followed.move_to_end(mint)
        while len(self._followed) > self.max_followed:
            self._followed.popitem(last=False)

    async def _publish_durably(self, ev) -> None:
        delay = self.retry_initial_s
        while True:
            try:
                admitted = await self.bus.publish(ev)
            except Exception:
                self.stats["publish_retries"] += 1
                self.stats["ingest_stalled"] = True
                log.exception("could not store %s; retrying in %.2fs", ev.idempotency_key, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.retry_max_s)
                continue
            self.stats["ingest_stalled"] = False
            self.stats["published" if admitted else "duplicates"] += 1
            return

    def health(self) -> dict:
        lag = sorted(self._lag_ms)
        p = (lambda q: round(lag[min(len(lag) - 1, int(q * (len(lag) - 1) + 0.5))], 1) if lag else None)
        return {"adapter": self.name, "ws_url": self.ws_url, "connected": self.connected,
                "recent_connections": list(self.connection_log)[-5:],
                "followed_mints": len(self._followed), "receipt_lag_ms": {"p50": p(0.5), "p95": p(0.95), "n": len(lag)},
                **self.stats}
