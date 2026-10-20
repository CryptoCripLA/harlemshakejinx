"""Live Pump.fun feed: Mako's own PumpPortalStream (upstream, unmodified) -> JINX event bus.

JINX registers only the new-token and token-trade handlers (not account-trade,
which Mako fires for the same messages and would double them). Each new mint's
trades are subscribed through Mako's subscribe_token; the watch list is bounded
and the oldest mint that JINX does not hold is unsubscribed first.

Migrations: PumpPortal streams them free (`subscribeMigration`, verified without
a key 2026-10-20). Mako neither subscribes to them nor routes migrate messages
(its dispatcher only handles creations and trades), so this adapter wraps the
stream object's dispatch: on the first message of each connection it sends
`subscribeMigration` once, and it hands migrate messages to the normal publish
path. Mako's code is not modified. Free like creations, so not mode-gated.

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
import json
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
        self._confirmed: set[str] = set()
        self._pending_unsub: set[str] = set()
        self._sub_lock = asyncio.Lock()
        self._conn_seen = None
        self._migration_conn = None            # connection the migration subscription was sent on
        self._migration_confirmed_conn = None  # connection on which PumpPortal confirmed it
        upstream_dispatch = self.stream._dispatch

        async def dispatch(data):
            await self._before_dispatch(data)
            await upstream_dispatch(data)
        self.stream._dispatch = dispatch
        self.stream.on_reconnect(self._on_reconnect)
        self.stats = {"received": 0, "malformed": 0, "published": 0, "duplicates": 0, "publish_retries": 0,
                      "subscribe_failures": 0, "unsubscribe_failures": 0,
                      "ingest_stalled": False, "subscribed": 0, "unsubscribed": 0, "last_message_ns": None,
                      "migration_subscriptions": 0, "migrations": 0}

    async def run(self) -> None:
        await self.stream.connect()

    async def _before_dispatch(self, data) -> None:
        """Every message Mako parsed, before Mako routes it: keep the free migration subscription on the current
        connection and take the migrate messages Mako would drop."""
        ws = self.stream.ws
        if ws is not None and ws is not self._migration_conn:
            self._migration_conn = ws
            try:
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                self.stats["migration_subscriptions"] += 1
            except Exception:
                self._migration_conn = None   # try again on the next message
                self.stats["subscribe_failures"] += 1
                log.exception("migration subscription failed; will retry")
        if not isinstance(data, dict):
            return
        note = str(data.get("message", "")).lower()
        if "subscribed" in note and "migration" in note:
            self._migration_confirmed_conn = ws
        if data.get("txType") == "migrate":
            published = self.stats["published"]
            await self._on_message(data)
            self.stats["migrations"] += self.stats["published"] - published

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

    # ---- paid trade subscriptions: reconcile wanted against confirmed ----------------------
    # _watched    wanted creation mints, oldest first (bounded; only while paid subscriptions are allowed)
    # held        mints with an open position: always wanted, never evicted (exit observation)
    # _confirmed  mints whose subscribe was sent on the current connection and not since unsubscribed
    # _pending_unsub  confirmed mints that are no longer wanted and whose unsubscribe has not been sent yet
    # Every change goes through one locked reconcile, so a stale unsubscribe can never remove a mint that is
    # wanted again: obsolete unsubscribes are cancelled before anything is sent.

    async def sync_subscriptions(self) -> None:
        if not self.subscribe_trades:
            return
        async with self._sub_lock:
            await self._reconcile()

    async def _reconcile(self) -> None:
        ws = self.stream.ws
        if ws is not self._conn_seen:
            # a new connection (including a clean reconnect, which Mako does not report): it carries exactly
            # the tokens Mako re-subscribed, and nothing from the old connection is left to unsubscribe
            self._conn_seen = ws
            self._confirmed = set(self.stream._watched_tokens)
            self._pending_unsub = set()
        held = set(self.held_mints_provider())
        if not self._paid_subscriptions_allowed():
            for mint in [m for m in self._watched if m not in held]:
                del self._watched[mint]
        while len(self._watched) > self.max_watched:
            evictable = next((m for m in self._watched if m not in held), None)
            if evictable is None:
                break  # every watched mint is a held position; never stop observing those
            del self._watched[evictable]
        wanted = set(self._watched) | held
        # an unsubscribe that is wanted again is cancelled; its send may have failed after Mako had already
        # dropped the mint from its own set, so subscribe it again to restore Mako's reconnect tracking
        cancelled = self._pending_unsub & wanted
        self._confirmed -= cancelled
        self._pending_unsub = (self._pending_unsub | (self._confirmed - wanted)) - wanted
        for mint in sorted(wanted - self._confirmed):
            try:
                await self.stream.subscribe_token(mint)
            except Exception:
                self.stats["subscribe_failures"] += 1
                log.exception("subscribe of %s failed; will retry", mint)
                continue
            self._confirmed.add(mint)
            self.stats["subscribed"] += 1
        for mint in sorted(self._pending_unsub):
            try:
                await self.stream.unsubscribe_token(mint)
            except Exception:
                self.stats["unsubscribe_failures"] += 1
                log.exception("unsubscribe of %s failed; will retry", mint)
                continue
            self._pending_unsub.discard(mint)
            self._confirmed.discard(mint)
            self.stats["unsubscribed"] += 1

    async def _on_reconnect(self) -> None:
        # called by Mako after reconnecting from an error; _reconcile notices the new connection itself
        async with self._sub_lock:
            await self._reconcile()

    async def _watch(self, mint: str) -> None:
        async with self._sub_lock:
            if mint not in self._watched:
                self._watched[mint] = None
            await self._reconcile()

    def health(self) -> dict:
        ws = self.stream.ws
        connected = ws is not None and getattr(ws, "close_code", None) is None
        return {"adapter": "mako", "source": SOURCE, "connected": connected, "ws_url": self.ws_url,
                "trade_stream": "on" if self.subscribe_trades else "BLOCKED: PumpPortal API key funded with 0.02 SOL required",
                "migration_stream": "on" if self.stream.ws is not None and self._migration_confirmed_conn is self.stream.ws
                else "pending",
                "watched_tokens": len(self._watched), "pending_unsubscribes": len(self._pending_unsub),
                "unconfirmed_subscriptions": len((set(self._watched) | set(self.held_mints_provider())) - self._confirmed)
                if self.subscribe_trades else 0, **self.stats}
