"""Source readings for the event-driven intel modules (Early Momentum, graduation, migration).

MarketEvent carries what every source supplies: trader, side, amounts, the curve's virtual reserves and the slot. A
few facts the Phase 2 evidence needs exist in one source's compact record only. This module is the one place the
intel modules read them, by the documented field names of the JINX adapter that stored them:

- PumpAPI (`pumpapi.stream`, jinx/adapters/pumpapi/normalize.py COMPACT_FIELDS): `timestamp` is PumpAPI's
  millisecond observation time; `quoteInPool` / `tokensInPool` are the real reserves (on the curve: the SOL and the
  tokens actually in it; on PumpSwap: the pool's reserves); `poolId` is the curve or pool account (on `migrate`: the
  new pool); `pool` is "pump" on the bonding curve and "pump-amm" on PumpSwap; `tradersInvolved` lists the accounts
  whose funds traded.
- PumpPortal (`mako.pumpportal`, jinx/adapters/mako/normalize.py): a migrate names the destination venue in `pool`,
  with no pool address and no time.

A missing or invalid value is None (unavailable). Nothing is defaulted or guessed. Core code does not import
adapters (tests/test_adapters.py), so the adapters' source names and PumpAPI's default decimals are repeated here;
tests/test_intel_readings.py pins them to the adapters' own constants.
"""
from __future__ import annotations

import math
from enum import StrEnum

from jinx.core.events import MarketEvent, sol_to_lamports

PUMPAPI = "pumpapi.stream"        # jinx/adapters/pumpapi/normalize.py SOURCE
PUMPPORTAL = "mako.pumpportal"    # jinx/adapters/mako/normalize.py SOURCE
DEFAULT_DECIMALS = 6              # jinx/adapters/pumpapi/normalize.py DEFAULT_DECIMALS (pump.fun mints)
CURVE_VENUE = "pump"
WSOL = "So11111111111111111111111111111111111111112"   # wrapped SOL mint: PumpAPI's `quoteMint` for SOL quotes
PUMPSWAP_VENUE = "pump-amm"
INITIAL_VIRTUAL_SOL = 30_000_000_000   # D3AD shred-sniper/sniper/src/pumpfun.rs:538 (recorded mainnet global value)


class Clock(StrEnum):
    CHAIN = "chain"         # chain block time
    PROVIDER = "provider"   # the feed's own observation time (PumpAPI `timestamp`)
    RECEIPT = "receipt"     # JINX's receipt time: the source gave no time


def _number(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return float(v)


def event_time(ev: MarketEvent) -> tuple[int, Clock]:
    """When the event happened, on the best clock the source gives, and which clock that is."""
    if ev.block_time is not None:
        return ev.block_time * 1_000_000_000, Clock.CHAIN
    if ev.source == PUMPAPI:
        ts = ev.raw.get("timestamp")
        if isinstance(ts, int) and not isinstance(ts, bool) and ts > 0:
            return ts * 1_000_000, Clock.PROVIDER          # integer milliseconds: exact, no float on the way
        if _number(ts):
            whole = int(ts)                                 # a fractional millisecond: keep the whole part exact
            return whole * 1_000_000 + int(round((ts - whole) * 1_000_000)), Clock.PROVIDER
    return ev.recv_ns, Clock.RECEIPT


def venue(ev: MarketEvent) -> str | None:
    v = ev.raw.get("pool")
    return v if isinstance(v, str) and v else None


def is_curve(ev: MarketEvent) -> bool:
    """Both adapters attach virtual reserves only to events on the pump.fun bonding curve."""
    return ev.v_sol_lamports is not None


def pool_id(ev: MarketEvent) -> str | None:
    if ev.source != PUMPAPI:
        return None
    v = ev.raw.get("poolId")
    return v if isinstance(v, str) and v else None


def _decimals(ev: MarketEvent) -> int:
    d = ev.raw.get("decimals", DEFAULT_DECIMALS)
    return d if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 12 else DEFAULT_DECIMALS


def real_reserves(ev: MarketEvent) -> tuple[int | None, int | None]:
    """(SOL lamports, raw token units) really held by the curve or pool after the event, as PumpAPI reports them."""
    if ev.source != PUMPAPI:
        return None, None
    quote, tokens = _number(ev.raw.get("quoteInPool")), _number(ev.raw.get("tokensInPool"))
    return (sol_to_lamports(quote) if quote is not None else None,
            int(round(tokens * 10 ** _decimals(ev))) if tokens is not None else None)


def verified_real_sol(ev: MarketEvent) -> int | None:
    """Real SOL (lamports) in a pre-migration pump.fun bonding curve, only when verified (owner decision B-02,
    2026-10-21): a PumpAPI record on the curve (`pool: pump`) whose quote asset is stated as SOL/WSOL, with a numeric
    non-negative `quoteInPool`. Never derived from virtual SOL; any other case is None (unverified)."""
    if ev.source != PUMPAPI or ev.raw.get("pool") != CURVE_VENUE or ev.raw.get("quoteMint") != WSOL:
        return None
    quote = _number(ev.raw.get("quoteInPool"))
    return sol_to_lamports(quote) if quote is not None and quote >= 0 else None


def traders_involved(ev: MarketEvent) -> tuple[str, ...] | None:
    v = ev.raw.get("tradersInvolved")
    if isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
        return tuple(v)
    return None


def standard_curve(v_sol_lamports: int | None, real_sol_lamports: int | None) -> bool | None:
    """Whether a curve reading's virtual minus real SOL is the standard 30 SOL a pump.fun curve starts with. Two
    amounts each rounded to the lamport can differ by one lamport (5 of 580 standard live readings did). On the live
    capture 26 of 118 curve mints were not standard (tests/test_intel_graduation.py). None when unknown."""
    if v_sol_lamports is None or real_sol_lamports is None:
        return None
    return abs(v_sol_lamports - real_sol_lamports - INITIAL_VIRTUAL_SOL) <= 1
