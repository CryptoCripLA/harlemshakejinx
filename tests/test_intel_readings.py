"""The one place the event intel reads an adapter's compact record (jinx/intel/readings.py)."""
from jinx.adapters.mako import normalize as mako
from jinx.adapters.pumpapi import normalize as pumpapi
from jinx.core.events import EventKind, MarketEvent
from jinx.intel import readings
from tests.pumpapi_msgs import amm_trade, buy, migrate, one


def test_source_names_and_decimals_match_the_adapters():
    assert readings.PUMPAPI == pumpapi.SOURCE and readings.PUMPPORTAL == mako.SOURCE
    assert readings.DEFAULT_DECIMALS == pumpapi.DEFAULT_DECIMALS
    assert readings.CURVE_VENUE == pumpapi.CURVE_POOL and readings.PUMPSWAP_VENUE in pumpapi.PUMP_POOLS


def test_pumpapi_events_use_pumpapis_clock_and_others_fall_back_to_receipt():
    ev = one(buy("A", 2.5), recv_delay_ms=80)
    assert readings.event_time(ev) == ((1_792_540_000_000 + 2_500) * 1_000_000, readings.Clock.PROVIDER)
    pp = MarketEvent(kind=EventKind.MIGRATION, source=mako.SOURCE, mint="M", signature="S", recv_ns=77, seq=1,
                     raw={"pool": "pump-amm"})
    assert readings.event_time(pp) == (77, readings.Clock.RECEIPT)
    chain = MarketEvent(kind=EventKind.MIGRATION, source="test", mint="M", signature="S", recv_ns=77, seq=1,
                        block_time=1_700_000_000)
    assert readings.event_time(chain) == (1_700_000_000_000_000_000, readings.Clock.CHAIN)


def test_real_reserves_pool_and_venue_come_from_pumpapis_record_only():
    curve = one(buy("A", 1, v_quote=32.5))
    sol, tokens = readings.real_reserves(curve)
    assert sol == 2_500_000_000 and tokens == round((30 * 1_073_000_000 / 32.5 - 279_900_000) * 1e6)
    assert readings.is_curve(curve) and readings.venue(curve) == "pump"
    amm = one(amm_trade("buy", pool_id="POOL9", quote_in_pool=84.5, tokens_in_pool=200_000_000.0))
    assert not readings.is_curve(amm) and readings.pool_id(amm) == "POOL9" and readings.venue(amm) == "pump-amm"
    assert readings.real_reserves(amm) == (84_500_000_000, 200_000_000_000_000)
    assert readings.pool_id(one(migrate(pool_id="NEWPOOL"))) == "NEWPOOL"
    pp = MarketEvent(kind=EventKind.MIGRATION, source=mako.SOURCE, mint="M", signature="S", recv_ns=1, seq=1,
                     raw={"pool": "pump-amm", "poolId": "X", "quoteInPool": 1.0})
    assert readings.pool_id(pp) is None and readings.real_reserves(pp) == (None, None)


def test_missing_or_invalid_values_are_unavailable_not_defaults():
    ev = one(buy("A", 1))
    bad = MarketEvent(kind=ev.kind, source=ev.source, mint=ev.mint, signature=ev.signature, trader=ev.trader,
                      side=ev.side, sol_lamports=ev.sol_lamports, token_amount=ev.token_amount,
                      v_sol_lamports=ev.v_sol_lamports, v_tokens=ev.v_tokens, recv_ns=5, seq=1,
                      raw={**ev.raw, "timestamp": None, "quoteInPool": -1.0, "tokensInPool": "x"})
    assert readings.event_time(bad) == (5, readings.Clock.RECEIPT)
    assert readings.real_reserves(bad) == (None, None)
    assert readings.traders_involved(ev) == ("A",)
