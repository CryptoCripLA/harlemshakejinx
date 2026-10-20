"""Typed, normalized market events: the only shape that crosses into JINX core.

Every source adapter converts its upstream payload into a MarketEvent. Core
code never reads an upstream's raw format. Amounts are integers in base units
(lamports for SOL, raw token units for tokens) so that accounting never drifts
on float rounding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

LAMPORTS_PER_SOL = 1_000_000_000


class EventKind(StrEnum):
    TOKEN_CREATED = "token_created"
    TRADE = "trade"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


def sol_to_lamports(sol: float | int | None) -> int | None:
    """Convert a SOL amount reported as a float by a feed to whole lamports."""
    if sol is None:
        return None
    return int(round(float(sol) * LAMPORTS_PER_SOL))


_AMOUNT_FIELDS = ("sol_lamports", "token_amount", "v_sol_lamports", "v_tokens")


@dataclass(frozen=True, slots=True)
class MarketEvent:
    kind: EventKind
    source: str                 # adapter that produced it, e.g. "mako.pumpportal"
    mint: str
    signature: str              # on-chain transaction signature
    recv_ns: int                # wall-clock nanoseconds when JINX received it
    seq: int                    # monotonic ingest sequence number
    trader: str | None = None   # fee payer / trader; creator for TOKEN_CREATED
    side: Side | None = None
    sol_lamports: int | None = None     # SOL moved by this trade
    token_amount: int | None = None     # tokens moved, raw base units
    v_sol_lamports: int | None = None   # bonding-curve virtual SOL reserve after the event
    v_tokens: int | None = None         # bonding-curve virtual token reserve after the event
    slot: int | None = None
    block_time: int | None = None       # unix seconds, when the source provides chain time
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.mint:
            raise ValueError("event needs a mint")
        if not self.signature:
            raise ValueError("event needs a transaction signature")
        if not self.source:
            raise ValueError("event needs a source")
        for name in _AMOUNT_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int in base units, got {type(value).__name__}")
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.kind is EventKind.TRADE:
            if self.side is None or not self.trader or self.sol_lamports is None or self.token_amount is None:
                raise ValueError("trade events need side, trader, sol_lamports and token_amount")
        elif self.kind is EventKind.TOKEN_CREATED:
            if not self.trader:
                raise ValueError("token_created events need the creator as trader")
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))

    @property
    def idempotency_key(self) -> str:
        """Identity of the on-chain fact, independent of when or how often it was received."""
        return f"{self.signature}:{self.kind}:{self.mint}:{self.side or '-'}:{self.trader or '-'}"

    @property
    def event_time_ns(self) -> int:
        """Chain time when known, otherwise receive time."""
        if self.block_time is not None:
            return self.block_time * 1_000_000_000
        return self.recv_ns

    @property
    def order_key(self) -> tuple[int, int]:
        return (self.event_time_ns, self.seq)
