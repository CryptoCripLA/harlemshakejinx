"""PumpAPI stream messages for the Phase 2 intel tests, in the live field layout (tests/test_pumpapi_adapter.py and
tests/fixtures/pumpapi-live-20261021.jsonl.gz), turned into MarketEvents by the real adapter.

Curve readings follow the pump.fun constant product from D3AD's recorded mainnet values (pumpfun.rs:538-540):
virtual SOL starts at 30 SOL and virtual tokens at 1,073,000,000; the real reserves PumpAPI reports
(`quoteInPool`, `tokensInPool`) are the virtual ones minus those initial offsets (verified on the live fixture by
tests/test_intel_graduation.py)."""
from __future__ import annotations

import itertools

from jinx.adapters.pumpapi.normalize import normalize

WSOL = "So11111111111111111111111111111111111111112"
T0_MS = 1_792_540_000_000
K = 30.0 * 1_073_000_000.0
VIRTUAL_TOKEN_OFFSET = 1_073_000_000.0 - 793_100_000.0
_seq = itertools.count(1)
_sig = itertools.count(1)


def curve_fields(v_quote: float) -> dict:
    v_tokens = K / v_quote
    return {"vQuoteInBondingCurve": v_quote, "vTokensInBondingCurve": v_tokens,
            "quoteInPool": round(v_quote - 30.0, 9), "tokensInPool": v_tokens - VIRTUAL_TOKEN_OFFSET}


def create(mint="MintApump", dev="DEV", t_ms=T0_MS, slot=100, v_quote=31.0, initial_buy=34_000_000.0,
           quote=1.0, sig=None) -> dict:
    return {"signature": sig or f"C{next(_sig)}", "action": "create", "pool": "pump", "poolId": f"CURVE-{mint}",
            "mint": mint, "quoteMint": WSOL, "txSigner": dev, "initialBuy": initial_buy, "quoteAmount": quote,
            "decimals": 6, "tradersInvolved": {dev: {}}, "block": slot, "timestamp": t_ms, **curve_fields(v_quote)}


def trade(action, mint="MintApump", payer="P1", t_ms=T0_MS, slot=101, v_quote=31.5, sol=0.25, tokens=1_000_000.0,
          sig=None) -> dict:
    return {"signature": sig or f"T{next(_sig)}", "action": action, "pool": "pump", "poolId": f"CURVE-{mint}",
            "mint": mint, "quoteMint": WSOL, "txSigner": payer, "tokenAmount": tokens, "quoteAmount": sol,
            "decimals": 6, "tradersInvolved": {payer: {}}, "block": slot, "timestamp": t_ms, **curve_fields(v_quote)}


def buy(payer, t_s, v_quote=31.5, mint="MintApump", **kw) -> dict:
    return trade("buy", mint=mint, payer=payer, t_ms=T0_MS + int(round(t_s * 1000)), v_quote=v_quote, **kw)


def sell(payer, t_s, v_quote=31.0, mint="MintApump", **kw) -> dict:
    return trade("sell", mint=mint, payer=payer, t_ms=T0_MS + int(round(t_s * 1000)), v_quote=v_quote, **kw)


def amm_trade(action, mint="MintApump", payer="P1", t_ms=T0_MS, slot=500, pool_id="AMM1", quote_in_pool=85.0,
              tokens_in_pool=206_900_000.0, sol=0.5, tokens=1_000_000.0, sig=None) -> dict:
    return {"signature": sig or f"A{next(_sig)}", "action": action, "pool": "pump-amm", "poolId": pool_id,
            "mint": mint, "quoteMint": WSOL, "txSigner": payer, "tokenAmount": tokens, "quoteAmount": sol,
            "decimals": 6, "tradersInvolved": {payer: {}}, "block": slot, "timestamp": t_ms,
            "quoteInPool": quote_in_pool, "tokensInPool": tokens_in_pool}


def migrate(mint="MintApump", pool_id="AMM1", t_ms=T0_MS, slot=499, sig=None) -> dict:
    return {"signature": sig or f"M{next(_sig)}", "action": "migrate", "pool": "pump-amm", "poolId": pool_id,
            "mint": mint, "quoteMint": WSOL, "txSigner": "MIGRATOR", "block": slot, "timestamp": t_ms}


def events(msg: dict, recv_delay_ms: float = 50.0, recv_ns: int | None = None):
    """MarketEvents for one message, received `recv_delay_ms` after PumpAPI's timestamp unless `recv_ns` is given."""
    if recv_ns is None:
        recv_ns = int(round((msg["timestamp"] + recv_delay_ms) * 1_000_000))
    return normalize(msg, recv_ns, _seq.__next__)


def one(msg: dict, **kw):
    [ev] = events(msg, **kw)
    return ev
