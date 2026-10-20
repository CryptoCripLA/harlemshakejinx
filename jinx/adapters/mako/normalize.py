"""PumpPortal data-feed messages (as delivered by Mako's PumpPortalStream) -> MarketEvents.

A create message is two on-chain facts in one transaction: the token creation
and the creator's initial buy, so it becomes a TOKEN_CREATED event plus a BUY
trade by the creator. A migrate message (PumpPortal's free migration stream)
becomes one MIGRATION event; it names the destination venue ("pool":
"pump-amm"), not the new pool's address, and carries no chain time. Bonding-curve reserves are attached only when the
message says the trade happened on the pump.fun curve ("pool": "pump"). A
message missing a required field raises MalformedMessage; nothing is guessed.
"""
from __future__ import annotations

from typing import Any, Callable

from jinx.core.events import EventKind, MarketEvent, Side, sol_to_lamports

SOURCE = "mako.pumpportal"
TOKEN_DECIMALS = 6  # pump.fun mints use 6 decimals
CURVE_POOL = "pump"


class MalformedMessage(ValueError):
    pass


def _num(msg: dict, key: str) -> float:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise MalformedMessage(f"{key} missing or not a number")
    if v < 0:
        raise MalformedMessage(f"{key} is negative")
    return float(v)


def _str(msg: dict, key: str) -> str:
    v = msg.get(key)
    if not isinstance(v, str) or not v:
        raise MalformedMessage(f"{key} missing")
    return v


def _tokens(ui_amount: float) -> int:
    return int(round(ui_amount * 10**TOKEN_DECIMALS))


def normalize(msg: Any, recv_ns: int, next_seq: Callable[[], int]) -> list[MarketEvent]:
    if not isinstance(msg, dict):
        raise MalformedMessage("message is not an object")
    tx_type = msg.get("txType")
    if tx_type == "migrate":
        _str(msg, "pool")   # the destination venue must be named
        return [MarketEvent(kind=EventKind.MIGRATION, source=SOURCE, mint=_str(msg, "mint"),
                            signature=_str(msg, "signature"), recv_ns=recv_ns, seq=next_seq(), raw=msg)]
    if tx_type not in ("create", "buy", "sell"):
        raise MalformedMessage(f"unknown txType {tx_type!r}")
    signature, mint, trader = _str(msg, "signature"), _str(msg, "mint"), _str(msg, "traderPublicKey")
    v_sol = v_tokens = None
    if msg.get("pool") == CURVE_POOL:
        v_sol = sol_to_lamports(_num(msg, "vSolInBondingCurve"))
        v_tokens = _tokens(_num(msg, "vTokensInBondingCurve"))
    common = dict(source=SOURCE, mint=mint, signature=signature, trader=trader, v_sol_lamports=v_sol,
                  v_tokens=v_tokens, recv_ns=recv_ns, raw=msg)
    if tx_type == "create":
        dev_tokens = _tokens(_num(msg, "initialBuy"))
        dev_sol = sol_to_lamports(_num(msg, "solAmount"))
        events = [MarketEvent(kind=EventKind.TOKEN_CREATED, seq=next_seq(), **common)]
        if dev_tokens > 0:
            events.append(MarketEvent(kind=EventKind.TRADE, side=Side.BUY, sol_lamports=dev_sol,
                                      token_amount=dev_tokens, seq=next_seq(), **common))
        return events
    return [MarketEvent(kind=EventKind.TRADE, side=Side(tx_type), sol_lamports=sol_to_lamports(_num(msg, "solAmount")),
                        token_amount=_tokens(_num(msg, "tokenAmount")), seq=next_seq(), **common)]
