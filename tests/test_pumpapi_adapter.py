"""PumpAPI.io Data Stream adapter (owner directive 2026-10-20: test the free PumpAPI feed before any paid feed).

Field meanings from https://pumpapi.io/stream (read 2026-10-20): `txSigner` is "the account that sent the
transaction and paid the priorityFee" (the fee payer CHAD's Early Momentum counts); `tradersInvolved` holds the
accounts whose funds traded; `block` is the slot; `timestamp` is PumpAPI's millisecond observation time; `pool`
is "pump" on the bonding curve and "pump-amm" after migration."""
from itertools import count

import pytest

from jinx.core.events import EventKind, Side

WSOL = "So11111111111111111111111111111111111111112"
CREATE = {"signature": "CS1", "action": "create", "pool": "pump", "poolId": "CURVE1", "mint": "MintApump",
          "quoteMint": WSOL, "txSigner": "DEV", "initialBuy": 35_000_000.0, "quoteAmount": 1.0,
          "vQuoteInBondingCurve": 31.0, "vTokensInBondingCurve": 1_038_000_000.0, "decimals": 6,
          "tradersInvolved": {"DEV": {}}, "block": 459_416_000, "timestamp": 1_792_540_000_123,
          "preBalances": {"DEV": {"sol": 5.0}}, "postBalances": {"DEV": {"sol": 4.0}}, "name": "A", "symbol": "A"}
BUY = {"signature": "TS1", "action": "buy", "pool": "pump", "poolId": "CURVE1", "mint": "MintApump",
       "quoteMint": WSOL, "txSigner": "PAYER1", "tokenAmount": 1_000_000.5, "quoteAmount": 0.25,
       "vQuoteInBondingCurve": 31.25, "vTokensInBondingCurve": 1_037_000_000.0, "decimals": 6,
       "tradersInvolved": {"PAYER1": {}}, "block": 459_416_001, "timestamp": 1_792_540_000_523,
       "price": 3.1e-8, "marketCapQuote": 31.0}
SELL = dict(BUY, signature="TS2", action="sell", txSigner="PAYER2", tradersInvolved={"PAYER2": {}})
AMM_BUY = dict(BUY, signature="TS3", pool="pump-amm", poolId="AMM1", vQuoteInBondingCurve=None,
               vTokensInBondingCurve=None, tokensInPool=200_000_000.0, quoteInPool=85.0)
MIGRATE = {"signature": "MS1", "action": "migrate", "pool": "pump-amm", "poolId": "AMM1", "mint": "MintApump",
           "quoteMint": WSOL, "txSigner": "MIGRATOR", "block": 459_416_100, "timestamp": 1_792_540_040_000}


def norm(msg, recv=7):
    from jinx.adapters.pumpapi.normalize import normalize
    return normalize(msg, recv, count(1).__next__)


def test_a_create_becomes_creation_plus_the_creators_buy_with_curve_reserves():
    created, dev_buy = norm(CREATE)
    assert (created.kind, created.trader, created.slot, created.mint) == (EventKind.TOKEN_CREATED, "DEV", 459_416_000, "MintApump")
    assert (dev_buy.kind, dev_buy.side, dev_buy.trader) == (EventKind.TRADE, Side.BUY, "DEV")
    assert (dev_buy.sol_lamports, dev_buy.token_amount) == (1_000_000_000, 35_000_000_000_000)
    assert (dev_buy.v_sol_lamports, dev_buy.v_tokens) == (31_000_000_000, 1_038_000_000_000_000)


def test_the_trader_is_the_fee_payer_and_the_slot_is_kept():
    [ev] = norm(BUY)
    assert (ev.kind, ev.side, ev.trader, ev.slot) == (EventKind.TRADE, Side.BUY, "PAYER1", 459_416_001)
    assert (ev.sol_lamports, ev.token_amount) == (250_000_000, 1_000_000_500_000)
    assert ev.raw["timestamp"] == 1_792_540_000_523 and ev.raw["tradersInvolved"] == ["PAYER1"]
    assert norm(SELL)[0].side is Side.SELL


def test_only_compact_fields_are_kept():
    """PumpAPI messages carry balances, lookup tables and token metadata; storing them all would fill the disk."""
    [_, dev_buy] = norm(CREATE)
    assert "preBalances" not in dev_buy.raw and "postBalances" not in dev_buy.raw and "name" not in dev_buy.raw
    assert {"signature", "action", "pool", "poolId", "mint", "txSigner", "block", "timestamp"} <= set(dev_buy.raw)


def test_post_migration_trades_carry_no_curve_reserves():
    [ev] = norm(AMM_BUY)
    assert ev.v_sol_lamports is None and ev.v_tokens is None and ev.raw["pool"] == "pump-amm"


def test_a_migrate_event_names_the_new_pool():
    [ev] = norm(MIGRATE)
    assert (ev.kind, ev.mint, ev.signature, ev.trader) == (EventKind.MIGRATION, "MintApump", "MS1", None)
    assert ev.raw["poolId"] == "AMM1" and ev.raw["pool"] == "pump-amm"


@pytest.mark.parametrize("msg", [
    dict(BUY, pool="meteora-dlmm"), dict(BUY, action="transfer"), dict(BUY, action="leaders"),
    {"action": "leaders", "signature": None},
])
def test_other_venues_and_actions_are_ignored_not_errors(msg):
    assert norm(msg) == []


def test_a_trade_not_quoted_in_sol_is_skipped_rather_than_mispriced():
    from jinx.adapters.pumpapi.normalize import NotQuotedInSol
    with pytest.raises(NotQuotedInSol):
        norm(dict(BUY, quoteMint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"))


@pytest.mark.parametrize("field", ["signature", "mint", "txSigner", "tokenAmount", "quoteAmount", "block"])
def test_an_incomplete_pump_event_is_refused(field):
    from jinx.adapters.pumpapi.normalize import MalformedEvent
    with pytest.raises(MalformedEvent):
        norm({k: v for k, v in BUY.items() if k != field})


def test_the_same_fact_from_pumpapi_and_pumpportal_has_one_identity():
    """Both feeds report the creation; the creator is the fee payer in both, so the bus stores it once."""
    from jinx.adapters.mako.normalize import normalize as mako_normalize
    pp = {"signature": "CS1", "mint": "MintApump", "traderPublicKey": "DEV", "txType": "create", "initialBuy": 35.0,
          "solAmount": 1.0, "pool": "pump", "vSolInBondingCurve": 31.0, "vTokensInBondingCurve": 1_038_000_000.0}
    assert norm(CREATE)[0].idempotency_key == mako_normalize(pp, 7, count(1).__next__)[0].idempotency_key


def test_byte_prefilter_passes_only_pump_family_messages():
    import json
    from jinx.adapters.pumpapi.normalize import may_be_pump
    assert may_be_pump(json.dumps(BUY, separators=(",", ":")).encode())
    assert may_be_pump(json.dumps(AMM_BUY, separators=(",", ":")).encode())
    assert not may_be_pump(json.dumps(dict(BUY, pool="raydium-v4"), separators=(",", ":")).encode())
    assert not may_be_pump(b'{"action":"transfer","signature":"X"}')


# ---- feed ------------------------------------------------------------------------------------------
def dumps(m):
    import json
    return json.dumps(m, separators=(",", ":")).encode()


class RecordingBus:
    def __init__(self, fail_first=0):
        self.events, self.keys, self.fail_left, self._seq = [], set(), fail_first, 0

    def next_seq(self):
        self._seq += 1
        return self._seq

    async def publish(self, ev):
        if self.fail_left:
            self.fail_left -= 1
            raise OSError("disk busy")
        if ev.idempotency_key in self.keys:
            return False
        self.keys.add(ev.idempotency_key)
        self.events.append(ev)
        return True


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_post_migration_trades_are_kept_only_for_followed_or_held_mints():
    from jinx.adapters.pumpapi.feed import PumpApiFeed
    bus = RecordingBus()
    feed = PumpApiFeed(bus, held_mints_provider=lambda: {"HELD"})

    async def main():
        await feed.handle(dumps(BUY), 1)                                         # curve trade: follows MintApump
        await feed.handle(dumps(dict(AMM_BUY, signature="A1")), 2)               # followed: kept
        await feed.handle(dumps(dict(AMM_BUY, signature="A2", mint="OTHER")), 3)  # never seen on the curve
        await feed.handle(dumps(dict(AMM_BUY, signature="A3", mint="HELD")), 4)   # JINX holds it
        await feed.handle(dumps(dict(MIGRATE, mint="OTHER")), 5)                  # migrations are always kept
    run(main())
    assert [(e.signature, e.mint) for e in bus.events] == [("TS1", "MintApump"), ("A1", "MintApump"),
                                                           ("A3", "HELD"), ("MS1", "OTHER")]
    assert feed.stats["amm_not_followed"] == 1


def test_other_venues_bad_events_and_non_sol_quotes_are_counted_not_published():
    from jinx.adapters.pumpapi.feed import PumpApiFeed
    bus = RecordingBus()
    feed = PumpApiFeed(bus)

    async def main():
        await feed.handle(b'{"action":"transfer","signature":"X"}', 1)
        await feed.handle(dumps(dict(BUY, pool="raydium-v4")), 2)
        await feed.handle(dumps({k: v for k, v in BUY.items() if k != "txSigner"}), 3)
        await feed.handle(dumps(dict(BUY, quoteMint="USDC")), 4)
        await feed.handle(b'{"pool":"pump", not json', 5)
    run(main())
    assert bus.events == []
    assert (feed.stats["filtered"], feed.stats["malformed"], feed.stats["not_quoted_in_sol"]) == (2, 2, 1)


def test_the_followed_set_is_bounded_oldest_first():
    from jinx.adapters.pumpapi.feed import PumpApiFeed
    bus = RecordingBus()
    feed = PumpApiFeed(bus, max_followed=2)

    async def main():
        for i, m in enumerate(("M1", "M2", "M3")):
            await feed.handle(dumps(dict(BUY, signature=f"S{i}", mint=m)), i)
        await feed.handle(dumps(dict(AMM_BUY, signature="X1", mint="M1")), 9)   # evicted: not kept
        await feed.handle(dumps(dict(AMM_BUY, signature="X3", mint="M3")), 9)
    run(main())
    assert [e.signature for e in bus.events][-1] == "X3" and feed.stats["amm_not_followed"] == 1


def test_a_failed_store_write_is_retried_not_lost():
    from jinx.adapters.pumpapi.feed import PumpApiFeed
    bus = RecordingBus(fail_first=2)
    feed = PumpApiFeed(bus, retry_initial_s=0.001)
    run(feed.handle(dumps(BUY), 1))
    assert [e.signature for e in bus.events] == ["TS1"] and feed.stats["publish_retries"] == 2


def test_the_feed_reconnects_and_deduplicates_over_a_real_websocket():
    import asyncio
    import websockets
    from jinx.adapters.pumpapi.feed import PumpApiFeed

    async def main():
        conns = []
        async def server(ws):
            conns.append(ws)
            if len(conns) == 1:
                for m in (CREATE, BUY, BUY, {"action": "transfer", "signature": "T"}):
                    await ws.send(dumps(m))
                await ws.close(code=1001)                   # server-side update: the client must reconnect
            else:
                await ws.send(dumps(MIGRATE))
                await asyncio.sleep(1)
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            bus = RecordingBus()
            feed = PumpApiFeed(bus, ws_url=f"ws://127.0.0.1:{port}", reconnect_initial_s=0.01)
            task = asyncio.create_task(feed.run())
            for _ in range(300):
                await asyncio.sleep(0.01)
                if any(e.kind is EventKind.MIGRATION for e in bus.events):
                    break
            health = feed.health()
            task.cancel()
            return bus, feed.stats, health
    bus, stats, health = run(main())
    assert [(e.kind, e.signature) for e in bus.events] == [(EventKind.TOKEN_CREATED, "CS1"), (EventKind.TRADE, "CS1"),
                                                           (EventKind.TRADE, "TS1"), (EventKind.MIGRATION, "MS1")]
    assert stats["duplicates"] == 1 and stats["connections"] == 2 and stats["disconnects"] >= 1
    assert health["connected"] is True and health["filtered"] == 1


def test_a_compact_record_normalizes_to_the_same_events():
    """Captures store compact records; replaying them must give exactly what the live message gave."""
    from jinx.adapters.pumpapi.normalize import compact
    for msg in (CREATE, BUY, SELL, AMM_BUY, MIGRATE):
        live, again = norm(msg), norm(compact(msg))
        assert [(e.idempotency_key, e.sol_lamports, e.token_amount, e.v_sol_lamports, e.slot) for e in live] == \
               [(e.idempotency_key, e.sol_lamports, e.token_amount, e.v_sol_lamports, e.slot) for e in again]
        assert [dict(e.raw) for e in live] == [dict(e.raw) for e in again]


def test_several_trades_of_one_side_in_one_transaction_stay_separate_facts():
    """Seen live 2026-10-21: one transaction carried 4 sells of one mint by one fee payer, as 4 events with different
    amounts. They are distinct trades, so each keeps its own identity; a resent copy of the same leg still dedups."""
    legs = [dict(SELL, tokenAmount=a, quoteAmount=a / 1e6) for a in (0.463476, 0.360481, 0.669465, 0.223156)]
    keys = [norm(m)[0].idempotency_key for m in legs]
    assert len(set(keys)) == 4
    assert norm(legs[0])[0].idempotency_key == keys[0]                 # a resent copy is the same fact


def test_a_trade_leg_is_part_of_the_identity_the_bus_deduplicates_on():
    """Two legs of one transaction are two facts; a resend of the same leg is a duplicate (no store round trip
    exists since the storage repair of 2026-10-21: the bus filter is the only place market events are compared)."""
    from jinx.core.dedup import Deduplicator
    [a] = norm(dict(SELL, tokenAmount=0.463476))
    [b] = norm(dict(SELL, tokenAmount=0.5))
    d = Deduplicator()
    assert d.first_time(a.idempotency_key, 1) and d.first_time(b.idempotency_key, 1)
    assert d.first_time(a.idempotency_key, 2) is False


def test_creation_events_keep_the_cross_feed_identity():
    """The leg applies to stream buys/sells only; creations and the creator's buy match PumpPortal's identity."""
    created, dev_buy = norm(CREATE)
    assert ":leg=" not in created.idempotency_key and ":leg=" not in dev_buy.idempotency_key


def test_a_silent_connection_is_replaced_quickly_instead_of_waiting_for_the_ping_timeout():
    """Seen live 2026-10-21: a PumpAPI connection went silent and was only dropped by the 20 s keepalive ping
    timeout, losing about 40 s of events. The stream normally carries ~900 messages/s, so the feed treats a short
    silence as a dead connection and reconnects."""
    import asyncio
    import websockets
    from jinx.adapters.pumpapi.feed import PumpApiFeed

    async def main():
        conns = []
        async def server(ws):
            conns.append(ws)
            if len(conns) == 1:
                await ws.send(dumps(BUY))
                await asyncio.sleep(3)                    # silent but open (10x the stall timeout): what the live stream did
            else:
                await ws.send(dumps(MIGRATE))
                await asyncio.sleep(1)
        async with websockets.serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            bus = RecordingBus()
            feed = PumpApiFeed(bus, ws_url=f"ws://127.0.0.1:{port}", stall_timeout_s=0.3, reconnect_initial_s=0.01)
            task = asyncio.create_task(feed.run())
            t0 = asyncio.get_running_loop().time()
            for _ in range(300):
                await asyncio.sleep(0.01)
                if any(e.kind is EventKind.MIGRATION for e in bus.events):
                    break
            elapsed = asyncio.get_running_loop().time() - t0
            log = list(feed.connection_log)
            task.cancel()
            return bus, feed.stats, elapsed, log
    bus, stats, elapsed, log = run(main())
    assert [e.signature for e in bus.events] == ["TS1", "MS1"]
    assert stats["stalls"] == 1 and stats["connections"] == 2 and elapsed < 2.0
    assert log[0]["end_reason"] == "stall" and log[0]["messages"] == 1
