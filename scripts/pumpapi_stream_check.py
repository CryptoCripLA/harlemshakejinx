"""PumpAPI.io free Data Stream check (owner directive 2026-10-20, "PHASE 2 DATA-SOURCE CORRECTION").

Holds ONE connection to wss://stream.pumpapi.io/ (PumpAPI allows one per client) and, alongside it, PumpPortal's
free creation + migration stream as an independent reference. Records evidence for the owner's checklist:
completeness, duplicates, provider-timestamp latency, cross-feed latency and loss, stability, CPU, fee payer,
three distinct fee payers within 30 s, and migration continuity. Then checks `txSigner` against the chain's fee
payer (accountKeys[0]) for a sample, through the public Solana RPC.

Writes a summary JSON (no credentials; wallet addresses shortened) and a gzip sample of the raw pump/pump-amm
events for replay tests.

    .venv/bin/python scripts/pumpapi_stream_check.py --seconds 300 --out docs/evidence/pumpapi_stream_check.json \\
        --sample tests/fixtures/pumpapi-live-sample.jsonl.gz --sample-limit 6000
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import resource
import statistics
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import websockets

PUMPAPI_WS = "wss://stream.pumpapi.io/"
PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
PUMP_POOLS = {"pump", "pump-amm"}
WINDOW_MS = 30_000            # CHAD Early Momentum: "3+ unique wallets buying within 30 seconds"
REQUIRED = {                  # fields CHAD's Early Momentum and the lifecycle need, per action
    "create": ("signature", "mint", "txSigner", "block", "timestamp", "pool", "poolId", "initialBuy",
               "quoteAmount", "vQuoteInBondingCurve", "vTokensInBondingCurve"),
    "buy": ("signature", "mint", "txSigner", "block", "timestamp", "pool", "poolId", "tokenAmount", "quoteAmount"),
    "sell": ("signature", "mint", "txSigner", "block", "timestamp", "pool", "poolId", "tokenAmount", "quoteAmount"),
    "migrate": ("signature", "mint", "txSigner", "block", "timestamp", "pool", "poolId"),
}


def short(a) -> str:
    return f"{a[:4]}…{a[-4:]}" if isinstance(a, str) and len(a) > 12 else str(a)


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))], 1)


def dist(values) -> dict:
    return {"n": len(values), "p50": pct(values, 0.50), "p95": pct(values, 0.95), "p99": pct(values, 0.99),
            "min": round(min(values), 1) if values else None, "max": round(max(values), 1) if values else None}


PUMP_MARKERS = (b'"pool":"pump"', b'"pool":"pump-amm"')   # cheap pre-filter before JSON parsing


async def pumpapi(seconds: float, sink: list, stats: dict, sample: list, sample_limit: int) -> None:
    """PumpAPI sends every Solana DEX event (~800/s measured). A per-message timeout task or parsing every message
    falls behind and builds a backlog (first version of this check: receipt lag grew to 16 s), so messages are
    read with `async for` and only pump/pump-amm messages, found by a byte check, are parsed."""
    end = time.monotonic() + seconds
    t_start = time.monotonic()
    while time.monotonic() < end:
        try:
            async with asyncio.timeout(max(0.1, end - time.monotonic())):
                async with websockets.connect(PUMPAPI_WS, open_timeout=15, max_size=None, ping_interval=20) as ws:
                    stats["connections"] += 1
                    last = time.monotonic()
                    async for raw in ws:
                        now_ms = time.time() * 1000
                        mono = time.monotonic()
                        stats["max_gap_s"] = max(stats["max_gap_s"], mono - last)
                        last = mono
                        stats["messages"] += 1
                        stats["bytes"] += len(raw)
                        b = raw if isinstance(raw, bytes) else raw.encode()
                        if not any(m in b for m in PUMP_MARKERS):
                            stats["filtered_out"] += 1
                            continue
                        t0 = time.process_time()
                        try:
                            ev = json.loads(b)
                        except ValueError:
                            stats["unparseable"] += 1
                            continue
                        stats["parse_cpu_s"] += time.process_time() - t0
                        stats["by_action_pool"][f"{ev.get('action')}/{ev.get('pool')}"] += 1
                        if ev.get("pool") in PUMP_POOLS and ev.get("action") in REQUIRED:
                            ev["_recv_ms"] = now_ms
                            if isinstance(ev.get("timestamp"), (int, float)):
                                stats["lag_by_10s"][int(mono - t_start) // 10].append(now_ms - ev["timestamp"])
                            sink.append(ev)
                            if len(sample) < sample_limit:
                                sample.append(b.decode())
        except TimeoutError:
            break
        except Exception as e:  # count and reconnect, as PumpAPI's docs require
            stats["disconnects"] += 1
            stats["errors"].append(f"{type(e).__name__}: {str(e)[:120]}")
            await asyncio.sleep(1)


async def pumpportal(seconds: float, sink: list, stats: dict) -> None:
    end = time.monotonic() + seconds
    try:
        async with websockets.connect(PUMPPORTAL_WS, open_timeout=15) as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            await ws.send(json.dumps({"method": "subscribeMigration"}))
            while time.monotonic() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                m = json.loads(raw)
                if m.get("txType") in ("create", "migrate"):
                    m["_recv_ms"] = time.time() * 1000
                    sink.append(m)
    except Exception as e:
        stats["pumpportal_error"] = f"{type(e).__name__}: {str(e)[:120]}"


def fee_payer_check(events: list, n: int = 10) -> dict:
    """txSigner vs the transaction's fee payer (accountKeys[0]) for a sample of buys, via the public RPC."""
    picked, seen = [], set()
    for ev in events:
        if ev["action"] == "buy" and ev["signature"] not in seen:
            seen.add(ev["signature"])
            picked.append(ev)
        if len(picked) >= n:
            break
    rows = []
    for ev in picked:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                           "params": [ev["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 1,
                                                        "commitment": "confirmed"}]}).encode()
        try:
            req = urllib.request.Request(PUBLIC_RPC, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                tx = json.loads(r.read()).get("result")
        except Exception as e:
            rows.append({"signature": short(ev["signature"]), "error": f"{type(e).__name__}"})
            time.sleep(1.5)
            continue
        if not tx:
            rows.append({"signature": short(ev["signature"]), "error": "not found"})
        else:
            payer = tx["transaction"]["message"]["accountKeys"][0]
            traders = list((ev.get("tradersInvolved") or {}).keys())
            rows.append({"signature": short(ev["signature"]), "fee_payer_is_txSigner": payer == ev["txSigner"],
                         "chain_slot_equals_block": tx.get("slot") == ev.get("block"),
                         "chain_block_time_s": tx.get("blockTime"), "event_timestamp_ms": ev.get("timestamp"),
                         "txSigner_in_tradersInvolved": ev["txSigner"] in traders,
                         "traders_involved": len(traders)})
        time.sleep(1.5)   # stay well inside the public RPC's limits
    ok = [r for r in rows if "error" not in r]
    return {"sampled": len(rows), "checked": len(ok),
            "fee_payer_matches_txSigner": sum(r["fee_payer_is_txSigner"] for r in ok),
            "slot_matches_block": sum(r["chain_slot_equals_block"] for r in ok), "rows": rows}


def three_payers(events: list) -> dict:
    """Per pump-curve mint: the first moment 3 distinct fee payers (txSigner) have bought within 30 s."""
    buys = defaultdict(list)
    creates = {}
    for ev in events:
        if ev["pool"] != "pump":
            continue
        if ev["action"] == "create":
            creates.setdefault(ev["mint"], ev)
        elif ev["action"] == "buy":
            buys[ev["mint"]].append(ev)
    qualified, examples, signer_ne_trader = [], [], 0
    for mint, bs in buys.items():
        bs.sort(key=lambda e: (e["block"], e["timestamp"]))
        signer_ne_trader += sum(1 for e in bs if e["txSigner"] not in (e.get("tradersInvolved") or {}))
        for i in range(len(bs)):
            window = [b for b in bs[i:] if b["timestamp"] - bs[i]["timestamp"] <= WINDOW_MS]
            payers = []
            for b in window:
                if b["txSigner"] not in payers:
                    payers.append(b["txSigner"])
                if len(payers) >= 3:
                    third = b
                    c = creates.get(mint)
                    qualified.append({"mint": mint, "third_buy_ts": third["timestamp"],
                                      "since_create_s": round((third["timestamp"] - c["timestamp"]) / 1000, 1) if c else None,
                                      "window_s": round((third["timestamp"] - bs[i]["timestamp"]) / 1000, 1),
                                      "seen_create": c is not None})
                    break
            else:
                continue
            break
    for q in qualified[:5]:
        examples.append({**q, "mint": short(q["mint"])})
    with_create = [q for q in qualified if q["seen_create"]]
    return {"mints_with_buys": len(buys), "mints_with_3_payers_in_30s": len(qualified),
            "of_which_creation_also_seen": len(with_create),
            "seconds_from_creation_to_third_payer": dist([q["since_create_s"] for q in with_create]),
            "buys_where_txSigner_is_not_a_trader_involved": signer_ne_trader, "examples": examples}


def migration_continuity(events: list, pp: list) -> dict:
    migrated = {}
    for ev in events:
        if ev["action"] == "migrate":
            migrated.setdefault(ev["mint"], ev)
    rows = []
    for mint, m in migrated.items():
        before = [e for e in events if e["mint"] == mint and e["pool"] == "pump" and e["action"] in ("buy", "sell")]
        after = [e for e in events if e["mint"] == mint and e["pool"] == "pump-amm" and e["action"] in ("buy", "sell")]
        pp_m = next((p for p in pp if p.get("txType") == "migrate" and p.get("mint") == mint), None)
        rows.append({"mint": short(mint), "migrate_pool_field": m["pool"], "curve_trades_before": len(before),
                     "amm_trades_after": len(after),
                     "amm_trades_use_migrate_poolId": all(e["poolId"] == m["poolId"] for e in after) if after else None,
                     "first_amm_trade_after_s": round((min(e["timestamp"] for e in after) - m["timestamp"]) / 1000, 1)
                     if after else None,
                     "pumpportal_saw_it": pp_m is not None,
                     "pumpportal_minus_pumpapi_recv_ms": round(pp_m["_recv_ms"] - m["_recv_ms"], 1) if pp_m else None})
    pp_only = [p for p in pp if p.get("txType") == "migrate" and p.get("mint") not in migrated]
    return {"migrations_seen": len(rows), "pumpportal_migrations_missing_from_pumpapi": len(pp_only), "rows": rows}


async def main(args) -> dict:
    events, pp, sample = [], [], []
    stats = {"connections": 0, "disconnects": 0, "messages": 0, "bytes": 0, "unparseable": 0, "parse_cpu_s": 0.0,
             "max_gap_s": 0.0, "by_action_pool": Counter(), "errors": [], "filtered_out": 0,
             "lag_by_10s": defaultdict(list)}
    started = time.time()
    cpu0 = resource.getrusage(resource.RUSAGE_SELF)
    await asyncio.gather(pumpapi(args.seconds, events, stats, sample, args.sample_limit),
                         pumpportal(args.seconds, pp, stats))
    cpu1 = resource.getrusage(resource.RUSAGE_SELF)
    wall = time.time() - started
    cpu = (cpu1.ru_utime - cpu0.ru_utime) + (cpu1.ru_stime - cpu0.ru_stime)

    missing = Counter()
    for ev in events:
        for f in REQUIRED[ev["action"]]:
            if ev.get(f) in (None, ""):
                missing[f"{ev['action']}.{f}"] += 1
    keys = Counter((e["signature"], e["action"], e["mint"]) for e in events)
    latency = [e["_recv_ms"] - e["timestamp"] for e in events if isinstance(e.get("timestamp"), (int, float))]

    api_create = {e["mint"]: e for e in events if e["action"] == "create" and e["pool"] == "pump"}
    pp_create = {p["mint"]: p for p in pp if p.get("txType") == "create"}
    edge = 10_000   # ignore creations within 10 s of either end of the window
    t_lo, t_hi = started * 1000 + edge, started * 1000 + wall * 1000 - edge
    pp_mid = {m: p for m, p in pp_create.items() if t_lo <= p["_recv_ms"] <= t_hi}
    api_mid = {m: e for m, e in api_create.items() if t_lo <= e["_recv_ms"] <= t_hi}
    both = [m for m in pp_create if m in api_create]
    cross = [pp_create[m]["_recv_ms"] - api_create[m]["_recv_ms"] for m in both]
    payer_vs_pp = [api_create[m]["txSigner"] == pp_create[m].get("traderPublicKey") for m in both]

    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "window_s": round(wall, 1),
        "stream": {**{k: v for k, v in stats.items() if k not in ("by_action_pool", "lag_by_10s")},
                   "receipt_lag_ms_by_10s": {f"{b * 10}-{b * 10 + 10}s": dist(v) for b, v in sorted(stats["lag_by_10s"].items())},
                   "by_action_pool_top": dict(stats["by_action_pool"].most_common(25)),
                   "messages_per_s": round(stats["messages"] / wall, 1), "mb_per_s": round(stats["bytes"] / wall / 1e6, 3),
                   "parse_cpu_s": round(stats["parse_cpu_s"], 2)},
        "process_cpu_share_of_one_core": round(cpu / wall, 3),
        "pump_events_kept": len(events),
        "pump_events_by_action": dict(Counter(f"{e['action']}/{e['pool']}" for e in events)),
        "missing_required_fields": dict(missing),
        "duplicate_events": sum(c - 1 for c in keys.values() if c > 1),
        "provider_timestamp_to_jinx_receipt_ms": dist(latency),
        "creations": {"pumpapi": len(api_create), "pumpportal": len(pp_create), "seen_by_both": len(both),
                      "pumpportal_minus_pumpapi_receipt_ms": dist(cross),
                      "pumpportal_only_mid_window": len([m for m in pp_mid if m not in api_create]),
                      "pumpapi_only_mid_window": len([m for m in api_mid if m not in pp_create]),
                      "creator_txSigner_equals_pumpportal_traderPublicKey": f"{sum(payer_vs_pp)}/{len(payer_vs_pp)}"},
        "three_fee_payers_in_30s": three_payers(events),
        "migration_continuity": migration_continuity(events, pp),
        "fee_payer_on_chain": fee_payer_check(events, args.rpc_samples),
    }
    if args.sample:
        with gzip.open(args.sample, "wt") as f:
            for line in sample:
                f.write(line.rstrip("\n") + "\n")
        doc["sample"] = {"path": str(args.sample), "events": len(sample)}
    Path(args.out).write_text(json.dumps(doc, indent=2, default=str) + "\n")
    return doc


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=300)
    p.add_argument("--out", default="docs/evidence/pumpapi_stream_check.json")
    p.add_argument("--sample", default="")
    p.add_argument("--sample-limit", type=int, default=6000)
    p.add_argument("--rpc-samples", type=int, default=10)
    d = asyncio.run(main(p.parse_args()))
    print(json.dumps({k: d[k] for k in ("window_s", "pump_events_kept", "duplicate_events", "missing_required_fields",
                                        "provider_timestamp_to_jinx_receipt_ms", "process_cpu_share_of_one_core")},
                     default=str))
