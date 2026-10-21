"""Regression tests for the Codex Phase 1 review, round 4 (docs/audits/phase1-codex-20261020-round4.md)."""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from jinx.core_state import CoreState
from jinx.execution import ExecutionGateway, ExitIntent
from jinx.ledger import PositionLedger, Status
from jinx.modes import Mode
from jinx.paper import CurveFees, PaperExecutor, PaperWallet
from jinx.recovery import recover
from jinx.registry import TokenRegistry
from jinx.store import Store
from tests.conftest import make_create, make_trade

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent


def v1_with_prefixed_exit(path: Path, intent: str = "p1:X") -> None:
    """A v1 database whose historical exit intent id happens to look like a modern fill-id prefix."""
    c = sqlite3.connect(path)
    c.executescript((FIXTURES / "schema_v1_1ad0d65.sql").read_text())
    c.execute("INSERT INTO positions (entry_intent_id, exit_intent_id, mint, status, token_amount, cost_lamports,"
              " proceeds_lamports, simulated, opened_ns) VALUES ('E1',?,'A','PENDING_CLOSE',750000000,10000000,"
              " 2000000, 1, 1)", (intent,))
    c.execute("INSERT INTO fills VALUES ('E1:buy', 1, 'buy', 1000000000, 10000000, 99009, 5000, 1, 1)")
    c.execute("INSERT INTO fills VALUES (?, 1, 'sell', 250000000, 2000000, 20000, 5000, 1, 2)", (f"{intent}:sell",))
    c.execute("INSERT INTO settings VALUES ('mode','AUTO',1,'owner')")
    c.commit()
    c.close()


def without_outbox(c: sqlite3.Connection) -> None:
    """Remove the v9 outbox, as in any database written before v9 (its triggers would also block a raw DROP COLUMN)."""
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'outbox_%'").fetchall():
        c.execute(f"DROP TRIGGER {name}")
    c.execute("DROP TABLE IF EXISTS outbox")


def rig(s: Store):
    reg = TokenRegistry()
    reg.apply(make_create(mint="A"))
    reg.apply(make_trade(mint="A", v_sol=32_000_000_000, v_tokens=1_000_000_000_000_000))
    ledger = PositionLedger(s)
    wallet = PaperWallet(s, 2_000_000_000)
    recover(ledger, wallet)
    return ledger, ExecutionGateway(s, ledger, PaperExecutor(s, ledger, wallet, reg, CurveFees(95, 5), 5_000))


# ---- R4-1: legacy ids that look modern ---------------------------------------------------
def test_legacy_exit_id_with_a_modern_looking_prefix_cannot_sell_again(tmp_path):
    path = tmp_path / "v1.db"
    v1_with_prefixed_exit(path)
    s = Store(path)
    ledger, gw = rig(s)
    r = gw.submit_exit(ExitIntent("p1:X", 1, token_amount=250_000_000))
    assert r.ok and r.reason == "ALREADY_APPLIED"
    assert ledger.get(1).token_amount == 750_000_000
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1
    s.close()


def test_database_already_migrated_with_the_old_rule_is_repaired(tmp_path):
    path = tmp_path / "v1.db"
    v1_with_prefixed_exit(path)
    Store(path).close()
    # simulate what the 631da32 migration left behind: no binding for the prefixed legacy fill
    c = sqlite3.connect(path)
    c.execute("DELETE FROM exit_intents")
    c.execute("PRAGMA user_version = 3")
    c.commit()
    c.close()
    s = Store(path)
    ledger, gw = rig(s)
    assert not gw.submit_exit(ExitIntent("p1:X", 1, token_amount=300_000_000)).ok       # ambiguous: refused
    assert gw.submit_exit(ExitIntent("p1:X", 1, token_amount=250_000_000)).reason == "ALREADY_APPLIED"
    assert ledger.get(1).token_amount == 750_000_000
    s.close()


def test_modern_exit_fills_are_not_turned_into_legacy_bindings_on_restart(tmp_path):
    path = tmp_path / "v1.db"
    v1_with_prefixed_exit(path, intent="OLD")
    s = Store(path)
    ledger, gw = rig(s)
    assert gw.submit_exit(ExitIntent("NEW", 1, token_amount=100_000_000)).ok
    s.close()
    s = Store(path)
    rows = s.conn.execute("SELECT intent_id, legacy_fill_id FROM exit_intents ORDER BY intent_id").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("NEW", None)]
    # since schema v6 historical evidence lives in its own table; the modern NEW fill is never in it
    hist = s.conn.execute("SELECT fill_id, intent_id FROM historical_exits ORDER BY fill_id").fetchall()
    assert [tuple(r) for r in hist] == [("OLD:sell", "OLD")]
    s.close()


# ---- R4-2: a retried unsubscribe could drop a mint that had become held -----------------
def test_pending_unsubscribe_is_cancelled_when_the_mint_becomes_held():
    import websockets
    from jinx.adapters.mako.feed import MakoPumpPortalFeed
    from jinx.core.bus import EventBus
    from tests.test_adapters import CREATE

    state = {"mode": Mode.AUTO, "held": set()}

    async def main():
        server_subs: set[str] = set()
        async def server(ws):
            async for msg in ws:
                m = json.loads(msg)
                if m["method"] == "subscribeTokenTrade":
                    server_subs.update(m["keys"])
                elif m["method"] == "unsubscribeTokenTrade":
                    server_subs.difference_update(m["keys"])
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
            await feed.sync_subscriptions()     # 1. unsubscribe of A fails; A pending
            state["held"] = {"A"}               # 2. A becomes a held position
            await feed.sync_subscriptions()     # 3. A is wanted again: the pending unsubscribe is obsolete
            await feed.sync_subscriptions()     # 4. later syncs must not unsubscribe a held mint
            await asyncio.sleep(0.05)
            h = feed.health()
            task.cancel()
            return server_subs, h
    server_subs, h = asyncio.run(main())
    assert server_subs == {"A"}                 # the held position is still observed on the server
    assert h["pending_unsubscribes"] == 0 and h["unconfirmed_subscriptions"] == 0


def test_health_is_degraded_while_a_wanted_subscription_is_unconfirmed(store):
    from jinx.health import build_health
    feed = {"adapter": "mako", "connected": True, "last_message_ns": 1, "unconfirmed_subscriptions": 1}
    h = build_health(store, PositionLedger(store), PaperWallet(store, 1_000), [feed], {}, now_ns=2)
    assert h["status"] == "degraded"
