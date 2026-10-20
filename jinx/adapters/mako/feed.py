"""Live Pump.fun feed: Mako's own PumpPortalStream (upstream, unmodified) -> JINX event bus.

JINX registers only the new-token and token-trade handlers (not account-trade,
which Mako fires for the same messages and would double them). Each new mint's
trades are subscribed through Mako's subscribe_token; the watch list is bounded
and the oldest mint is unsubscribed first.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path

from jinx.adapters.mako.loader import load_mako
from jinx.adapters.mako.normalize import SOURCE, MalformedMessage, normalize
from jinx.core.bus import EventBus


class MakoPumpPortalFeed:
    name = SOURCE

    def __init__(self, bus: EventBus, upstream_root: Path, ws_url: str | None = None,
                 max_watched_tokens: int = 300, clock_ns=time.time_ns) -> None:
        mods = load_mako(upstream_root)
        # PumpPortalStream.connect() reads this module global; tests point it at a local server.
        mods.pumpportal.PUMPPORTAL_WS_URL = ws_url or mods.default_ws_url
        self.ws_url = mods.pumpportal.PUMPPORTAL_WS_URL
        self.bus = bus
        self.clock_ns = clock_ns
        self.max_watched = max_watched_tokens
        self.stream = mods.pumpportal.PumpPortalStream()
        self.stream.on_new_token(self._on_message)
        self.stream.on_token_trade(self._on_message)
        self._watched: OrderedDict[str, None] = OrderedDict()
        self.stats = {"received": 0, "malformed": 0, "published": 0, "duplicates": 0,
                      "subscribed": 0, "unsubscribed": 0, "last_message_ns": None}

    async def run(self) -> None:
        await self.stream.connect()

    async def _on_message(self, data) -> None:
        recv = self.clock_ns()
        self.stats["received"] += 1
        self.stats["last_message_ns"] = recv
        try:
            events = normalize(data, recv, self.bus.next_seq)
        except MalformedMessage:
            self.stats["malformed"] += 1
            return
        for ev in events:
            if await self.bus.publish(ev):
                self.stats["published"] += 1
            else:
                self.stats["duplicates"] += 1
        if data.get("txType") == "create":
            await self._watch(events[0].mint)

    async def _watch(self, mint: str) -> None:
        if mint in self._watched:
            return
        self._watched[mint] = None
        await self.stream.subscribe_token(mint)
        self.stats["subscribed"] += 1
        while len(self._watched) > self.max_watched:
            oldest, _ = self._watched.popitem(last=False)
            await self.stream.unsubscribe_token(oldest)
            self.stats["unsubscribed"] += 1

    def health(self) -> dict:
        ws = self.stream.ws
        connected = ws is not None and getattr(ws, "close_code", None) is None
        return {"adapter": "mako", "source": SOURCE, "connected": connected, "ws_url": self.ws_url,
                "watched_tokens": len(self._watched), **self.stats}
