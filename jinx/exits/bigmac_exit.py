# jinx/exits/bigmac_exit.py
# Port of bigmacman1129/helpers/exit-strategy.ts evaluateExit()
# Ms-PL. No grace periods. No suppressions. Peak = max observed.
#
# This file is a derivative of bigmacman1129/solana-sniper-trading-mev-bot @ 829f251d4e371102b406176c5bd459ebf4ac87ac,
# helpers/exit-strategy.ts, and is distributed under the Microsoft Public License (Ms-PL), not under JINX's MIT
# license: a complete copy is in third_party/bigmac/LICENSE.md (Ms-PL section 3(D)). Pin and hashes:
# third_party/bigmac/SOURCE.json. Portions translated from that file; changes from the original:
# - translated from TypeScript to Python (the owner's supplied port, 2026-10-21);
# - amounts are Python ints (TypeScript bigint); applyPercent's integer rounding, clamping and truncating
#   division are reproduced exactly (owner fix 2 of 2; fix 1 of 2 is this license header, which said MIT);
# - the take-profit price is computed only when take_profit_pct > 0 and after the stop check, as in the owner's
#   port (the original computes it first, so a non-finite take-profit percent throws there; BigMacPaperConfig
#   refuses non-finite percents, so the difference is unreachable in JINX);
# - waitForExitCondition (lines 49-105) is not ported: no duration/'timeout' exit (owner order 2026-10-21 §6), and
#   the peak is persisted by the ledger instead of starting at 0n on every call.
# Source equivalence is checked against the original TypeScript run by node 22: tests/test_bigmac_differential.py.
# The ExitIntent the owner supplied here is the single jinx.execution.ExitIntent, extended with these fields.

import math
from typing import Optional
from enum import Enum

class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"

def _js_floor_clamped(percent) -> int:
    """`Math.max(0, Math.floor(percent))` as a BigInt operand, for a JavaScript number."""
    # SRC: bigmac@829f251d helpers/exit-strategy.ts:108 (const bps = BigInt(Math.max(0, Math.floor(percent))))
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        raise TypeError(f"percent must be a number, got {type(percent).__name__}")
    p = float(percent)                         # a JavaScript number is an IEEE double
    if math.isnan(p):
        raise ValueError("RangeError: NaN cannot be converted to a BigInt")
    if p == -math.inf:
        return 0                               # Math.max(0, -Infinity) is 0
    if p == math.inf:
        raise ValueError("RangeError: Infinity cannot be converted to a BigInt")
    return max(0, math.floor(p))

def _bigint_div(n: int, d: int) -> int:
    """BigInt `/`: truncates toward zero (Python's // floors)."""
    q = abs(n) // abs(d)
    return q if (n >= 0) == (d >= 0) else -q

def _require_int(name: str, v) -> None:
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"{name} must be an int (TypeScript bigint), got {type(v).__name__}")

def apply_percent(amount: int, percent: float, add: bool) -> int:
    """Exact equivalent of the original applyPercent (bigint version)."""
    # SRC: bigmac@829f251d helpers/exit-strategy.ts:107-114 (applyPercent)
    _require_int("amount", amount)
    bps = _js_floor_clamped(percent)
    if add:
        return _bigint_div(amount * (100 + bps), 100)
    down = _bigint_div(amount * bps, 100)
    return amount - down if amount > down else 0

def evaluate_exit(
    current: int,
    entry: int,
    peak: int,
    take_profit_pct: float,
    stop_loss_pct: float,
    trailing_stop_pct: float,
    trailing_stop_activation_pct: float,
) -> tuple[Optional[ExitReason], int]:
    """
    Source-faithful port of evaluateExit().
    Returns (reason or None, updated_peak).
    """
    # SRC: bigmac@829f251d helpers/exit-strategy.ts:15-47 (evaluateExit)
    for name, v in (("current", current), ("entry", entry), ("peak", peak)):
        _require_int(name, v)
    new_peak = max(current, peak)

    stop_loss_price = apply_percent(entry, stop_loss_pct, add=False)
    if current <= stop_loss_price:
        return ExitReason.STOP_LOSS, new_peak

    if take_profit_pct > 0:
        take_profit_price = apply_percent(entry, take_profit_pct, add=True)
        if current >= take_profit_price:
            return ExitReason.TAKE_PROFIT, new_peak

    if trailing_stop_pct > 0:
        activation = apply_percent(entry, trailing_stop_activation_pct, add=True)
        if new_peak >= activation:
            trail_floor = apply_percent(new_peak, trailing_stop_pct, add=False)
            if current <= trail_floor:
                return ExitReason.TRAILING_STOP, new_peak

    return None, new_peak
