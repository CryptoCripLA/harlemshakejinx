"""Live Pump.fun feed: Mako's own PumpPortalStream (upstream, unmodified) -> JINX event bus.

JINX registers only the new-token and token-trade handlers (not account-trade,
which Mako fires for the same messages and would double them). Each new mint's
trades are subscribed through Mako's subscribe_token; the watch list is bounded
and the oldest mint is unsubscribed first.

PumpPortal only streams token trades to connections that carry an API key
funded with at least 0.02 SOL (server reply observed 2026-10-20), and charges
for streamed trades. Without a key the feed delivers token creations only
(which include the creator's initial buy) and sends no trade subscriptions.
With a key, paid trade subscriptions follow the operating mode: in OFF no new
mint is subscribed and existing subscriptions are dropped, except mints JINX
still holds, whose exits must keep being observed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from pathlib import Path

from jinx.adapters.mako.loader import load_mako
from jinx.adapters.mako.normalize import SOURCE, MalformedMessage, normalize
from jinx.core.bus import EventBus

log = logging.getLogger(__name__)


class MakoPumpPortalFeed:
    name = SOURCE

    def __init__(self, bus: EventBus, upstream_root: Path, ws_url: str | None = None,
                 max_watched_tokens: int = 300, clock_ns=time.time_ns, tap=None, api_key: str | None = None,
                 subscribe_trades: bool | None = None, retry_initial_s: float = 0.05, retry_max_s: float = 5.0,
                 mode_provider=None, held_mints_provider=None) -> None:
        mods = load_mako(upstream_root)
        base = ws_url or mods.default_ws_url
        # PumpPortalStream.connect() reads this module global; tests point it at a local server.
        mods.pumpportal.PUMPPORTAL_WS_URL = f"{base}?api-key={api_key}" if api_key else base
        self.ws_url = base  # never report the key
        self.subscribe_trades = bool(api_key) if subscribe_trades is None else subscribe_trades
        self.bus = bus
        self.clock_ns = clock_ns
        self.tap = tap  # optional callable(recv_ns, raw_message), used to record captures for replay
        self.max_watched = max_watched_tokens
        self.stream = mods.pumpportal.PumpPortalStream()
        self.stream.on_new_token(self._on_message)
        self.stream.on_token_trade(self._on_message)
        self.retry_initial_s, self.retry_max_s = retry_initial_s, retry_max_s
        self.mode_provider = mode_provider                    # () -> Mode; None means no mode gating
        self.held_mints_provider = held_mints_provider or (lambda: set())
        self._watched: OrderedDict[str, None] = OrderedDict()
        self.stats = {"received": 0, "malformed": 0, "published": 0, "duplicates": 0, "publish_retries": 0,
                      "ingest_stalled": False, "subscribed": 0, "unsubscribed": 0, "last_message_ns": None}

    async def run(self) -> None:
        await self.stream.connect()

    async def _on_message(self, data) -> None:
        recv = self.clock_ns()
        self.stats["received"] += 1
        self.stats["last_message_ns"] = recv
        if self.tap is not None:
            self.tap(recv, data)
        try:
            events = normalize(data, recv, self.bus.next_seq)
        except MalformedMessage:
            self.stats["malformed"] += 1
            return
        for ev in events:
            await self._publish_durably(ev)
        if data.get("txType") == "create" and self._paid_subscriptions_allowed():
            await self._watch(events[0].mint)

    async def _publish_durably(self, ev) -> None:
        """Keep retrying until the event is durably admitted. Mako's dispatcher swallows handler errors, so
        giving up here would lose the event; holding here backpressures the feed instead, and health shows it."""
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

    def _paid_subscriptions_allowed(self) -> bool:
        if not self.subscribe_trades:
            return False
        return self.mode_provider is None or str(self.mode_provider()) != "OFF"

    async def sync_subscriptions(self) -> None:
        """Bring paid trade subscriptions in line with the mode: held mints always stay watched (exit
        observation); everything else is dropped while the mode is OFF."""
        if not self.subscribe_trades:
            return
        held = set(self.held_mints_provider())
        for mint in held - set(self._watched):
            await self._watch(mint, protected=True)
        if not self._paid_subscriptions_allowed():
            for mint in [m for m in self._watched if m not in held]:
                del self._watched[mint]
                await self.stream.unsubscribe_token(mint)
                self.stats["unsubscribed"] += 1

    async def _watch(self, mint: str, protected: bool = False) -> None:
        if mint in self._watched:
            return
        self._watched[mint] = None
        await self.stream.subscribe_token(mint)
        self.stats["subscribed"] += 1
        held = set(self.held_mints_provider())
        while len(self._watched) > self.max_watched:
            evictable = next((m for m in self._watched if m not in held), None)
            if evictable is None:
                break  # every watched mint is a held position; never stop observing those
            del self._watched[evictable]
            await self.stream.unsubscribe_token(evictable)
            self.stats["unsubscribed"] += 1

    def health(self) -> dict:
        ws = self.stream.ws
        connected = ws is not None and getattr(ws, "close_code", None) is None
        return {"adapter": "mako", "source": SOURCE, "connected": connected, "ws_url": self.ws_url,
                "trade_stream": "on" if self.subscribe_trades else "BLOCKED: PumpPortal API key funded with 0.02 SOL required",
                "watched_tokens": len(self._watched), **self.stats}
