"""PumpAPI.io Data Stream events -> MarketEvents (pump.fun bonding curve and PumpSwap only).

Field meanings from https://pumpapi.io/stream (read 2026-10-20):
- `txSigner`: "the account that sent the transaction and paid the priorityFee", i.e. the fee payer. It is the
  event's trader, because CHAD's Early Momentum counts distinct fee payers. `tradersInvolved` (the accounts whose
  funds traded, which can differ) is kept in the compact record.
- `block`: the slot. `timestamp`: PumpAPI's millisecond observation time, kept in the record; it is not chain
  block time, so `block_time` stays unset.
- `pool`: "pump" on the bonding curve, "pump-amm" after migration. Bonding-curve reserves are attached only on
  "pump" events.
- One transaction can carry several trades of one side by one fee payer as separate events (seen live
  2026-10-21: 4 sells in one transaction). Each stream buy/sell is marked with a `leg` (its exact token amount)
  in the stored record, so they stay separate facts while a resent copy still deduplicates. Creations and the
  creator's buy carry no leg, so their identity matches PumpPortal's for the same fact. A migrate carries the new pool
  as its leg: PumpPortal's report of the same migration has no pool, and both must reach the migration module.

PumpAPI sends every Solana DEX event, so only pump/pump-amm create/buy/sell/migrate events become MarketEvents;
everything else returns []. Each MarketEvent keeps a compact record of the message (COMPACT_FIELDS), not its
balances, lookup tables or metadata. A pump event missing a required field raises MalformedEvent; a trade not
quoted in SOL raises NotQuotedInSol (JINX's amounts are lamports). Nothing is guessed.
"""
from __future__ import annotations

from typing import Any, Callable

from jinx.core.events import EventKind, MarketEvent, Side, sol_to_lamports

SOURCE = "pumpapi.stream"
WSOL = "So11111111111111111111111111111111111111112"
PUMP_POOLS = ("pump", "pump-amm")
CURVE_POOL = "pump"
DEFAULT_DECIMALS = 6   # pump.fun mints
COMPACT_FIELDS = ("signature", "action", "pool", "poolId", "mint", "quoteMint", "txSigner", "block", "timestamp",
                  "tokenAmount", "quoteAmount", "initialBuy", "price", "marketCapQuote", "vQuoteInBondingCurve",
                  "vTokensInBondingCurve", "tokensInPool", "quoteInPool", "decimals", "priorityFee")
_MARKERS = tuple(f'"pool":"{p}"'.encode() for p in PUMP_POOLS)


class MalformedEvent(ValueError):
    pass


class NotQuotedInSol(ValueError):
    pass


def may_be_pump(raw: bytes) -> bool:
    """Cheap byte check before JSON parsing: PumpAPI sends ~800 messages/s, most of them for other venues."""
    return any(m in raw for m in _MARKERS)


def _str(msg: dict, key: str) -> str:
    v = msg.get(key)
    if not isinstance(v, str) or not v:
        raise MalformedEvent(f"{key} missing")
    return v


def _num(msg: dict, key: str) -> float:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise MalformedEvent(f"{key} missing or not a number")
    if v < 0:
        raise MalformedEvent(f"{key} is negative")
    return float(v)


def _slot(msg: dict) -> int:
    v = msg.get("block")
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise MalformedEvent("block (slot) missing")
    return v


def compact(msg: dict) -> dict:
    out = {k: msg[k] for k in COMPACT_FIELDS if msg.get(k) is not None}
    traders = msg.get("tradersInvolved")
    if isinstance(traders, (dict, list)):   # a live message has a dict; a compact record already has the list
        out["tradersInvolved"] = sorted(traders)
    return out


def normalize(msg: Any, recv_ns: int, next_seq: Callable[[], int]) -> list[MarketEvent]:
    if not isinstance(msg, dict) or msg.get("pool") not in PUMP_POOLS:
        return []
    action = msg.get("action")
    if action not in ("create", "buy", "sell", "migrate"):
        return []
    signature, mint, slot = _str(msg, "signature"), _str(msg, "mint"), _slot(msg)
    raw = compact(msg)
    if action == "migrate":
        pool = _str(msg, "poolId")   # the new pool must be named
        if _num(msg, "timestamp") <= 0:  # and its time: PumpAPI documents it for every migrate
            raise MalformedEvent("timestamp missing")
        # PumpPortal reports the same migration without the pool. The pool makes this a richer fact, so it gets its
        # own identity (`leg`) and both reports reach the migration module, which merges them into one record
        # (owner order 2026-10-21: deduplicate effects, not information). A resend of this report still deduplicates.
        return [MarketEvent(kind=EventKind.MIGRATION, source=SOURCE, mint=mint, signature=signature, slot=slot,
                            recv_ns=recv_ns, seq=next_seq(), raw=dict(raw, leg=f"pool:{pool}"))]
    if msg.get("quoteMint", WSOL) != WSOL:
        raise NotQuotedInSol(f"{signature} is quoted in {msg.get('quoteMint')}")
    payer = _str(msg, "txSigner")
    decimals = msg.get("decimals", DEFAULT_DECIMALS)
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 12:
        raise MalformedEvent("decimals invalid")
    v_sol = v_tokens = None
    if msg["pool"] == CURVE_POOL:
        v_sol = sol_to_lamports(_num(msg, "vQuoteInBondingCurve"))
        v_tokens = int(round(_num(msg, "vTokensInBondingCurve") * 10**decimals))
    common = dict(source=SOURCE, mint=mint, signature=signature, trader=payer, slot=slot, v_sol_lamports=v_sol,
                  v_tokens=v_tokens, recv_ns=recv_ns, raw=raw)
    if action == "create":
        events = [MarketEvent(kind=EventKind.TOKEN_CREATED, seq=next_seq(), **common)]
        dev_tokens = int(round(_num(msg, "initialBuy") * 10**decimals))
        if dev_tokens > 0:
            events.append(MarketEvent(kind=EventKind.TRADE, side=Side.BUY, sol_lamports=sol_to_lamports(_num(msg, "quoteAmount")),
                                      token_amount=dev_tokens, seq=next_seq(), **common))
        return events
    token_ui = _num(msg, "tokenAmount")
    common["raw"] = dict(raw, leg=repr(token_ui))
    return [MarketEvent(kind=EventKind.TRADE, side=Side(action), sol_lamports=sol_to_lamports(_num(msg, "quoteAmount")),
                        token_amount=int(round(token_ui * 10**decimals)), seq=next_seq(), **common)]
