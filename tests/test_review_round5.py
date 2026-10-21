"""Regression tests for the Codex Phase 1 review, round 5 (docs/audits/phase1-codex-20261020-round5.md)."""
import asyncio
import json
import sqlite3
from pathlib import Path

from jinx.execution import ExitIntent
from jinx.ledger import Status
from jinx.modes import Mode
from jinx.store import Store
from tests.test_review_round4 import rig, v1_with_prefixed_exit, without_outbox

ROOT = Path(__file__).resolve().parent.parent


def escaped_v4_state(path: Path) -> None:
    """What a 1b6d797-era database could hold after Codex's sequence: the historical p1:X fill had no binding
    (missed by the v3 prefix rule), a later intent X was bound and then rejected on the fill-id collision, and
    fills carried no provenance column."""
    v1_with_prefixed_exit(path)
    Store(path).close()
    c = sqlite3.connect(path)
    c.execute("DELETE FROM exit_intents")
    c.execute("INSERT INTO exit_intents (intent_id, position_id, token_amount, created_ns, legacy_fill_id)"
              " VALUES ('X', 1, 100000000, 5, NULL)")
    without_outbox(c)
    c.execute("ALTER TABLE fills DROP COLUMN exit_intent_id")
    c.execute("PRAGMA user_version = 4")
    c.commit()
    c.close()


# ---- R5-1: a binding alone must not count as proof that a fill is modern -------------------
def test_rejected_binding_before_upgrade_cannot_hide_a_historical_fill(tmp_path):
    path = tmp_path / "escaped.db"
    escaped_v4_state(path)
    s = Store(path)
    ledger, gw = rig(s)
    r = gw.submit_exit(ExitIntent("p1:X", 1, token_amount=250_000_000))
    assert r.ok and r.reason == "ALREADY_APPLIED"
    assert ledger.get(1).token_amount == 750_000_000
    x = gw.submit_exit(ExitIntent("X", 1, token_amount=100_000_000))
    assert not x.ok                                    # X never executed; it must not be reported as applied
    assert ledger.get(1).token_amount == 750_000_000
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1
    s.close()


def test_new_intent_whose_fill_id_collides_with_history_is_refused_before_taking_ownership(tmp_path):
    path = tmp_path / "v1.db"
    v1_with_prefixed_exit(path)
    s = Store(path)
    ledger, gw = rig(s)
    r = gw.submit_exit(ExitIntent("X", 1, token_amount=100_000_000))
    assert not r.ok and r.reason == "FILL_ID_COLLISION"
    assert ledger.get(1).status is Status.OPEN
    assert s.conn.execute("SELECT 1 FROM exit_intents WHERE intent_id = 'X'").fetchone() is None
    s.close()


def test_sell_fills_record_the_exit_intent_that_produced_them(tmp_path):
    path = tmp_path / "v1.db"
    v1_with_prefixed_exit(path, intent="OLD")
    s = Store(path)
    ledger, gw = rig(s)
    assert gw.submit_exit(ExitIntent("NEW", 1, token_amount=100_000_000)).ok
    rows = dict(s.conn.execute("SELECT fill_id, exit_intent_id FROM fills WHERE side = 'sell'").fetchall())
    assert rows == {"OLD:sell": None, "p1:NEW:sell": "NEW"}
    assert gw.submit_exit(ExitIntent("NEW", 1, token_amount=100_000_000)).reason == "ALREADY_APPLIED"
    s.close()


# ---- R5-2: a clean reconnect must not lose a held mint ------------------------------------
def test_held_mint_survives_a_cancelled_unsubscribe_and_a_clean_reconnect():
    import websockets
    from jinx.adapters.mako.feed import MakoPumpPortalFeed
    from jinx.core.bus import EventBus
    from tests.test_adapters import CREATE

    state = {"mode": Mode.AUTO, "held": set()}

    async def main():
        conns = []      # one subscription set per connection; the last one is the live server state
        sockets = []
        async def server(ws):
            subs = set()
            conns.append(subs)
            sockets.append(ws)
            async for msg in ws:
                m = json.loads(msg)
                if m["method"] == "subscribeTokenTrade":
                    subs.update(m["keys"])
                elif m["method"] == "unsubscribeTokenTrade":
                    subs.difference_update(m["keys"])
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            feed = MakoPumpPortalFeed(EventBus(), ROOT / "upstream" / "mako", ws_url=f"ws://127.0.0.1:{port}",
                                      subscribe_trades=True, mode_provider=lambda: state["mode"],
                                      held_mints_provider=lambda: state["held"])
            task = asyncio.create_task(feed.run())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if feed.stream.ws is not None:
                    break
            await feed._on_message(dict(CREATE, mint="A", signature="S-A"))
            real = feed.stream.unsubscribe_token
            failures = {"left": 2}
            async def flaky(mint):
                if failures["left"]:
                    failures["left"] -= 1
                    feed.stream._watched_tokens.discard(mint)  # Mako forgets the mint before its send fails
                    raise ConnectionError("send failed")
                await real(mint)
            feed.stream.unsubscribe_token = flaky
            state["mode"] = Mode.OFF
            await feed.sync_subscriptions()
            state["held"] = {"A"}
            await feed.sync_subscriptions()
            await feed.sync_subscriptions()
            await sockets[-1].close(code=1000)          # a clean close: Mako reconnects without its callback
            for _ in range(200):
                await asyncio.sleep(0.01)
                if len(conns) >= 2:
                    break
            await asyncio.sleep(0.05)
            for _ in range(3):
                await feed.sync_subscriptions()
            await asyncio.sleep(0.05)
            h = feed.health()
            task.cancel()
            return conns, set(feed.stream._watched_tokens), h
    conns, mako_watched, h = asyncio.run(main())
    assert len(conns) >= 2
    assert "A" in conns[-1]                            # the held mint is subscribed on the new connection
    assert "A" in mako_watched                         # and Mako will restore it on any future reconnect
    assert h["unconfirmed_subscriptions"] == 0 and h["pending_unsubscribes"] == 0
