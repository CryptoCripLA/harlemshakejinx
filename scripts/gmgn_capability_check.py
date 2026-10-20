"""Authenticated, read-only GMGN capability check through JINX's own GMGN adapter (owner mandate 2026-10-20, §1).

Every call goes through GmgnAdapter -> bridge.mjs -> GMGN's pinned OpenApiClient, paced by the Plus budget.
Nothing is submitted: the bridge refuses trading methods before any request (checked here too), and the quote
call only prices an order. The report holds no credentials and no raw payloads: per call it records success or
the GMGN error code, the latency through the persistent bridge, item counts, field names, and the age of the
newest provider timestamp. Wallet addresses are shortened to first4…last4.

    PYTHONPATH=. .venv/bin/python scripts/gmgn_capability_check.py docs/evidence/gmgn_capability_check.json
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from jinx import config
from jinx.adapters.gmgn.adapter import GmgnAdapter, GmgnError

ROOT = Path(__file__).resolve().parents[1]
SOL = "So11111111111111111111111111111111111111112"
TS_FIELDS = ("timestamp", "trigger_at", "time", "created_timestamp", "open_timestamp")
TRENCH_CATEGORIES = {"new_creation", "near_completion", "completed"}


def short(addr: str) -> str:
    return f"{addr[:4]}…{addr[-4:]}" if isinstance(addr, str) and len(addr) > 12 else addr


def to_seconds(ts) -> float | None:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return ts / 1000 if ts > 1e12 else float(ts)


def rows_of(result) -> list[dict]:
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, dict):
        for key in ("list", "rank", "data", "holders", "traders"):
            inner = result.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
            if isinstance(inner, dict):
                found = rows_of(inner)
                if found:
                    return found
    return []


def summarize(result, now: float) -> dict:
    out: dict = {}
    if isinstance(result, dict) and result and set(result) <= TRENCH_CATEGORIES:
        # Trenches: one list per lifecycle category
        out["categories"] = {}
        for cat, items in result.items():
            ages = [now - s for r in items for s in [to_seconds(r.get("created_timestamp"))] if s]
            out["categories"][cat] = {"items": len(items), "newest_created_age_s": round(min(ages), 1) if ages else None,
                                      "fields": len(items[0]) if items else 0}
        return out
    rows = rows_of(result)
    out["items"] = len(rows)
    if rows:
        out["fields"] = sorted(rows[0].keys())
        for f in TS_FIELDS:
            secs = [s for r in rows for s in [to_seconds(r.get(f))] if s]
            if secs:
                out["newest_age_s"] = round(now - max(secs), 1)
                out["oldest_age_s"] = round(now - min(secs), 1)
                out["timestamp_field"] = f
                break
    elif isinstance(result, dict):
        out["fields"] = sorted(result.keys())
        out.pop("items")
    return out


async def check(adapter: GmgnAdapter, report: list, name: str, method: str, *args):
    t0 = time.perf_counter()
    entry = {"check": name, "method": method}
    try:
        result = await adapter.call(method, *args)
        entry["ok"] = True
        entry.update(summarize(result, time.time()))
    except GmgnError as e:
        entry["ok"] = False
        entry["error"] = str(e)[:200]
        result = None
    entry["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    report.append(entry)
    return result


async def migration_semantics(adapter: GmgnAdapter, trenches: dict, per_category: int = 3) -> list[dict]:
    """Per token, the lifecycle fields Trenches and token info report, side by side (values, not field names;
    pools compared, not printed). Backs the migration claims in docs/GMGN_CAPABILITY_INVENTORY.md §3."""
    rows = []
    for category in ("near_completion", "completed"):
        for item in (trenches.get(category) or [])[:per_category]:
            info = await adapter.call("getTokenInfo", "sol", item["address"])
            rows.append({
                "category": category,
                "mint": short(item["address"]),
                "trenches": {"launchpad_status": item.get("launchpad_status"), "exchange": item.get("exchange"),
                             "progress": item.get("progress"), "complete_timestamp": item.get("complete_timestamp")},
                "token_info": {"launchpad_status": info.get("launchpad_status"),
                               "launchpad_progress": info.get("launchpad_progress"),
                               "migrated_pool_present": bool(info.get("migrated_pool")),
                               "migrated_timestamp": info.get("migrated_timestamp")},
                "migrated_pool_equals_trenches_pool": bool(info.get("migrated_pool"))
                                                      and info.get("migrated_pool") == item.get("pool_address"),
                "migrated_timestamp_equals_complete_timestamp":
                    info.get("migrated_timestamp") == item.get("complete_timestamp"),
            })
    return rows


async def run(out_path: Path) -> dict:
    adapter = GmgnAdapter(ROOT / "build" / "gmgn", allow_signing_key=config.GMGN_ALLOW_SIGNING_KEY)
    report: list[dict] = []
    started = time.time()
    await adapter.start()
    try:
        health = await adapter.health()
        info = await check(adapter, report, "authentication + bound wallets", "getUserInfo")
        wallets = (info or {}).get("wallets", []) if isinstance(info, dict) else []
        sol_wallet = next((w.get("address") for w in wallets if w.get("chain") == "sol"), None)
        report[-1]["bound_chains"] = sorted({w.get("chain") for w in wallets})
        report[-1]["sol_wallet"] = short(sol_wallet) if sol_wallet else None

        trenches = await check(adapter, report, "trenches new_creation/near_completion/completed", "getTrenches",
                               "sol", ["new_creation", "near_completion", "completed"], ["Pump.fun"], 60)
        completed = (trenches or {}).get("completed") or [] if isinstance(trenches, dict) else []
        fresh = (trenches or {}).get("new_creation") or [] if isinstance(trenches, dict) else []
        migrated_mint = completed[0]["address"] if completed else None
        new_mint = fresh[0]["address"] if fresh else None
        if completed:
            c = completed[0]
            report[-1]["migration_evidence_fields"] = {
                k: (short(c[k]) if k == "pool_address" else c.get(k))
                for k in ("exchange", "launchpad", "launchpad_status", "progress", "complete_timestamp",
                          "complete_cost_time", "pool_address") if k in c}

        migration = await migration_semantics(adapter, trenches) if isinstance(trenches, dict) else []

        await check(adapter, report, "market signals (all types)", "getTokenSignalV2", "sol", [{}])
        await check(adapter, report, "trending 1m", "getTrendingSwaps", "sol", "1m")
        await check(adapter, report, "smart money trades", "getSmartMoney", "sol", 20)
        await check(adapter, report, "KOL trades", "getKol", "sol", 20)
        await check(adapter, report, "followed-wallet trades (signed)", "getFollowWallet", "sol")
        await check(adapter, report, "gas / SOL-USD price", "getGasPrice", "sol")
        now_ms = int(time.time() * 1000)
        for mint, label in ((new_mint, "new"), (migrated_mint, "migrated")):
            if not mint:
                continue
            await check(adapter, report, f"token info ({label})", "getTokenInfo", "sol", mint)
            await check(adapter, report, f"token security ({label})", "getTokenSecurity", "sol", mint)
            await check(adapter, report, f"token pool ({label})", "getTokenPoolInfo", "sol", mint)
            await check(adapter, report, f"top holders ({label})", "getTokenTopHolders", "sol", mint, {"limit": 20})
            await check(adapter, report, f"top traders ({label})", "getTokenTopTraders", "sol", mint, {"limit": 20})
            await check(adapter, report, f"1m candles ({label})", "getTokenKline", "sol", mint, "1m",
                        now_ms - 30 * 60_000, now_ms)
        if migrated_mint:
            await check(adapter, report, "1s candles (plan probe: Pro only)", "getTokenKline", "sol", migrated_mint,
                        "1s", now_ms - 60_000, now_ms)
            if sol_wallet:
                await check(adapter, report, "quote 0.001 SOL -> token (no submission)", "quoteOrder", "sol",
                            sol_wallet, SOL, migrated_mint, "1000000", 30)
        smart = await adapter.call("getSmartMoney", "sol", 1)
        rows = rows_of(smart)
        maker = rows[0].get("maker") if rows else None
        if maker:
            await check(adapter, report, "wallet stats 7d", "getWalletStats", "sol", [maker], "7d")
            await check(adapter, report, "wallet activity", "getWalletActivity", "sol", maker)
            await check(adapter, report, "created tokens (dev history)", "getCreatedTokens", "sol", maker)
        if sol_wallet:
            await check(adapter, report, "own wallet holdings (signed)", "getWalletHoldings", "sol", sol_wallet)

        # trading stays refused by the bridge, before any request is made
        refusal = None
        try:
            await adapter.call("swap", {"chain": "sol"})
            refused = False
        except GmgnError as e:
            refused = str(e).startswith("METHOD_NOT_ENABLED")
            refusal = str(e)[:120]
        report.append({"check": "swap refused by bridge", "method": "swap", "ok": refused, "error": refusal})
        final_health = await adapter.health()
    finally:
        await adapter.stop()

    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "plan_budget": {"plan": adapter.budget.plan.name, "rate_per_s": adapter.budget.plan.rate,
                        "capacity": adapter.budget.plan.capacity, **adapter.budget.stats},
        "bridge": {k: health.get(k) for k in ("bridge", "configured", "signing") if k in health},
        "bridge_after": {k: final_health.get(k) for k in ("calls", "errors", "restarts", "oversized", "rate_limited",
                                                            "paused_until")},
        # GMGN's own x-ratelimit-* headers on the last reply: the plan's limit as GMGN reports it
        "gmgn_rate_limit_headers": final_health.get("rate_limit"),
        "checks": report,
        "migration_semantics": migration,
    }
    out_path.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "evidence" / "gmgn_capability_check.json"
    doc = asyncio.run(run(out))
    for c in doc["checks"]:
        print(f"{'OK ' if c.get('ok') else 'ERR'} {c['latency_ms'] if 'latency_ms' in c else '':>8} ms  {c['check']}"
              + ("" if c.get("ok") else f"  [{c.get('error')}]"))
    print(json.dumps(doc["plan_budget"]))
