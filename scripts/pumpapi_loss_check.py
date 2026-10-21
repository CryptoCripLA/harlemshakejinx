"""PumpAPI event-loss check against the chain itself (owner directive 2026-10-20: measure event loss).

1. Captures PumpAPI pump/pump-amm events and PumpPortal's free creations and migrations for N seconds.
2. Creations: compares the two feeds; verifies a sample of creations seen by only one feed on chain.
3. Trades: for up to 3 active SOL-quoted bonding-curve mints, lists every successful transaction that touched the
   bonding curve (PumpAPI's poolId) within the captured slot range (getSignaturesForAddress), and compares it with
   the signatures PumpAPI delivered for that mint. Signatures missing from PumpAPI are fetched and classified by
   their program logs (a buy or sell is a lost trade; anything else is not a trade).
Public Solana RPC, paced at ~1 request/s. Output has no credentials; addresses shortened.

    .venv/bin/python scripts/pumpapi_loss_check.py --seconds 150 --out docs/evidence/pumpapi_loss_check.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.request
from collections import defaultdict

import websockets

from jinx.adapters.pumpapi.normalize import WSOL, may_be_pump

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
DETAILS: dict = {}
DETAILS_PATH = ""


def short(a) -> str:
    return f"{a[:4]}…{a[-4:]}" if isinstance(a, str) and len(a) > 12 else str(a)


RPC_ERRORS: list[str] = []


def rpc(method: str, params: list):
    """Public RPC, paced and retried; every failure is recorded rather than hidden."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request(PUBLIC_RPC, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read())
            time.sleep(2.0)
            if "error" in out:
                raise RuntimeError(str(out["error"])[:120])
            return out.get("result")
        except Exception as e:
            RPC_ERRORS.append(f"{method}: {type(e).__name__}: {str(e)[:100]}")
            time.sleep(5 * (attempt + 1))
    return None


def signatures_in_slots(address: str, lo_slot: int, hi_slot: int) -> list[dict] | None:
    """Every signature for an address down to lo_slot, paging with `before` (one page is at most 1000)."""
    out, before = [], None
    for _ in range(20):
        opts = {"limit": 1000, "commitment": "confirmed"} | ({"before": before} if before else {})
        page = rpc("getSignaturesForAddress", [address, opts])
        if page is None:
            return None
        if not page:
            break
        out += [s for s in page if lo_slot <= s["slot"] <= hi_slot]
        if page[-1]["slot"] < lo_slot:
            break
        before = page[-1]["signature"]
    return out


def classify(signature: str) -> str:
    tx = rpc("getTransaction", [signature, {"encoding": "json", "maxSupportedTransactionVersion": 1,
                                            "commitment": "confirmed"}])
    if not tx:
        return "not retrievable"
    logs = " ".join(tx.get("meta", {}).get("logMessages") or [])
    if tx.get("meta", {}).get("err") is not None:
        return "failed transaction"
    for word in ("Buy", "Sell", "Create", "Migrate"):
        if f"Instruction: {word}" in logs:
            return f"pump {word.lower()}"
    return "other instruction"


async def capture(seconds: float):
    api, pp = [], []

    async def pumpapi():
        async with asyncio.timeout(seconds):
            async with websockets.connect("wss://stream.pumpapi.io/", open_timeout=15, max_size=None) as ws:
                async for raw in ws:
                    b = raw if isinstance(raw, bytes) else raw.encode()
                    if not may_be_pump(b):
                        continue
                    e = json.loads(b)
                    if e.get("pool") in ("pump", "pump-amm"):
                        api.append({k: e.get(k) for k in ("signature", "action", "pool", "poolId", "mint", "quoteMint",
                                                          "block", "timestamp", "mayhemMode")} | {"recv": time.time()})

    async def pumpportal():
        async with asyncio.timeout(seconds):
            async with websockets.connect("wss://pumpportal.fun/api/data", open_timeout=15) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("txType") in ("create", "migrate"):
                        pp.append({"txType": m["txType"], "mint": m.get("mint"), "signature": m.get("signature"),
                                   "recv": time.time()})

    results = await asyncio.gather(pumpapi(), pumpportal(), return_exceptions=True)
    errors = [f"{type(r).__name__}" for r in results if isinstance(r, BaseException) and not isinstance(r, TimeoutError)]
    return api, pp, errors


def main(seconds: float, out: str) -> dict:
    started = time.time()
    api, pp, errors = asyncio.run(capture(seconds))
    lo, hi = started + 15, started + seconds - 15            # ignore the window's edges
    api_creates = {e["mint"]: e for e in api if e["action"] == "create" and e["pool"] == "pump"}
    pp_creates = {p["mint"]: p for p in pp if p["txType"] == "create"}
    sol = {m: e for m, e in api_creates.items() if e.get("quoteMint") == WSOL}
    pp_only = [p for m, p in pp_creates.items() if m not in api_creates and lo <= p["recv"] <= hi]
    api_only_sol = [e for m, e in sol.items() if m not in pp_creates and lo <= e["recv"] <= hi]
    doc = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)), "window_s": seconds,
           "capture_errors": errors,
           "creations": {"pumpapi_all": len(api_creates), "pumpapi_sol_quoted": len(sol), "pumpportal": len(pp_creates),
                         "both": len(set(api_creates) & set(pp_creates)),
                         "pumpportal_creations_not_quoted_in_sol": sum(1 for m in pp_creates if m in api_creates
                                                                       and api_creates[m].get("quoteMint") != WSOL),
                         "pumpportal_only_mid_window": [
                             {"mint": short(p["mint"]), "on_chain": classify(p["signature"]),
                              "pumpapi_saw_any_event_for_mint": any(e["mint"] == p["mint"] for e in api)}
                             for p in pp_only[:5]],
                         "pumpapi_only_sol_quoted_mid_window": len(api_only_sol),
                         "pumpapi_only_sample_on_chain": [
                             {"mint": short(e["mint"]), "mayhemMode": e.get("mayhemMode"),
                              "on_chain": classify(e["signature"])} for e in api_only_sol[:5]]}}
    api_mig = {e["mint"] for e in api if e["action"] == "migrate"}
    pp_mig = {p["mint"]: p for p in pp if p["txType"] == "migrate"}
    doc["migrations"] = {"pumpapi": len(api_mig), "pumpportal": len(pp_mig), "both": len(api_mig & set(pp_mig)),
                         "pumpportal_only": [{"mint": short(m), "on_chain": classify(p["signature"]),
                                              "pumpapi_events_for_mint": sum(1 for e in api if e["mint"] == m)}
                                             for m, p in pp_mig.items() if m not in api_mig][:5]}
    by_mint = defaultdict(list)
    for e in api:
        if e["pool"] == "pump" and e.get("quoteMint") == WSOL:
            by_mint[e["mint"]].append(e)
    candidates = sorted((m for m, es in by_mint.items() if 15 <= len(es) <= 150), key=lambda m: -len(by_mint[m]))[:3]
    trade_rows = []
    for m in candidates:
        es = by_mint[m]
        curve = es[0]["poolId"]
        lo_slot, hi_slot = min(e["block"] for e in es), max(e["block"] for e in es)
        pools = sorted({e["poolId"] for e in es})
        sigs = signatures_in_slots(curve, lo_slot, hi_slot)
        if sigs is None:
            trade_rows.append({"mint": short(m), "error": "signature listing failed"})
            continue
        chain = {s["signature"] for s in sigs if s.get("err") is None}
        delivered = {e["signature"] for e in es}
        missing = sorted(chain - delivered)
        extra = sorted(delivered - chain)
        trade_rows.append({"mint": short(m), "mint_ends_in_pump": m.endswith("pump"), "distinct_poolIds": len(pools),
                           "slot_range": hi_slot - lo_slot, "chain_successful_txs": len(chain),
                           "chain_failed_txs": sum(1 for s in sigs if s.get("err") is not None),
                           "pumpapi_txs": len(delivered), "in_both": len(chain & delivered),
                           "missing_from_pumpapi": len(missing),
                           "missing_classified": [classify(s) for s in missing[:8]],
                           "pumpapi_not_in_chain_listing": len(extra),
                           "extra_classified": [classify(s) for s in extra[:5]]})
        DETAILS[m] = {"curve": curve, "pools": pools, "missing": missing, "extra": extra}
    doc["bonding_curve_trades_vs_chain"] = trade_rows
    doc["rpc_errors"] = RPC_ERRORS[:20]
    doc["rpc_error_count"] = len(RPC_ERRORS)
    with open(out, "w") as f:
        f.write(json.dumps(doc, indent=2) + "\n")
    if DETAILS_PATH:
        with open(DETAILS_PATH, "w") as f:
            json.dump(DETAILS, f)
    return doc


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=150)
    p.add_argument("--out", default="docs/evidence/pumpapi_loss_check.json")
    p.add_argument("--details", default="", help="full signatures for review (not for the repo)")
    a = p.parse_args()
    DETAILS_PATH = a.details
    print(json.dumps(main(a.seconds, a.out), indent=1)[:4000])
