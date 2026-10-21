"""PumpAPI soak test through JINX's own PumpApiFeed (owner directive 2026-10-20: connection stability, event loss).

Runs the real feed (stall watchdog, reconnects, filtering, follow policy) for N seconds next to PumpPortal's free
creation + migration stream, then:
- reports the feed's connection log (duration, messages, end reason per connection) and stalls;
- compares creations and migrations seen while the PumpAPI connection was up, and checks every one-sided
  creation/migration on chain (public RPC, transaction versions up to 1);
- reports throughput, receipt lag and CPU.

    .venv/bin/python scripts/pumpapi_soak.py --seconds 600 --out docs/evidence/pumpapi_soak_20261021.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import resource
import time
import urllib.request

import websockets

from jinx.adapters.pumpapi.feed import PumpApiFeed
from jinx.adapters.pumpapi.normalize import WSOL

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


def short(a) -> str:
    return f"{a[:4]}…{a[-4:]}" if isinstance(a, str) and len(a) > 12 else str(a)


def classify(signature: str) -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                       "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 1,
                                              "commitment": "confirmed"}]}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(PUBLIC_RPC, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read())
            time.sleep(2)
            if "error" in out:
                raise RuntimeError(str(out["error"])[:80])
            tx = out.get("result")
            if not tx:
                return "not found"
            if tx["meta"].get("err") is not None:
                return "failed transaction"
            logs = " ".join(tx["meta"].get("logMessages") or [])
            for word in ("Create", "Migrate", "Buy", "Sell"):
                if f"Instruction: {word}" in logs:
                    return f"pump {word.lower()}"
            return "other"
        except Exception as e:
            last = f"rpc error: {type(e).__name__}: {str(e)[:60]}"
            time.sleep(5 * (attempt + 1))
    return last


class Sink:
    def __init__(self):
        self.keys, self.events, self._seq = set(), [], 0

    def next_seq(self):
        self._seq += 1
        return self._seq

    async def publish(self, ev):
        if ev.idempotency_key in self.keys:
            return False
        self.keys.add(ev.idempotency_key)
        if ev.kind.value in ("token_created", "migration"):
            self.events.append({"kind": ev.kind.value, "mint": ev.mint, "signature": ev.signature,
                                "recv": ev.recv_ns / 1e9, "quoteMint": ev.raw.get("quoteMint", WSOL)})
        return True


async def pumpportal(seconds: float, sink: list) -> None:
    async with asyncio.timeout(seconds):
        while True:
            try:
                async with websockets.connect("wss://pumpportal.fun/api/data", open_timeout=15) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    async for raw in ws:
                        m = json.loads(raw)
                        if m.get("txType") in ("create", "migrate"):
                            sink.append({"kind": m["txType"], "mint": m.get("mint"), "signature": m.get("signature"),
                                         "recv": time.time()})
            except (websockets.ConnectionClosed, OSError):
                await asyncio.sleep(1)


async def run(seconds: float):
    sink = Sink()
    feed = PumpApiFeed(sink)
    pp: list = []
    cpu0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.time()
    task = asyncio.create_task(feed.run())
    try:
        await pumpportal(seconds, pp)
    except TimeoutError:
        pass
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    cpu1 = resource.getrusage(resource.RUSAGE_SELF)
    return feed, sink, pp, t0, time.time() - t0, (cpu1.ru_utime - cpu0.ru_utime) + (cpu1.ru_stime - cpu0.ru_stime)


def main(seconds: float, out: str) -> dict:
    feed, sink, pp, t0, wall, cpu = asyncio.run(run(seconds))
    log = list(feed.connection_log)
    # intervals during which PumpAPI was connected (from the log), with a 10 s margin at each edge
    up, clock = [], t0
    for c in log:
        start = time.mktime(time.strptime(c["start_utc"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone if c["start_utc"] else None
        if start is not None:
            up.append((start + 10, start + c["seconds"] - 10))
    def connected(t):
        return any(a <= t <= b for a, b in up)
    api_c = {e["mint"]: e for e in sink.events if e["kind"] == "token_created"}
    api_m = {e["mint"]: e for e in sink.events if e["kind"] == "migration"}
    pp_c = {p["mint"]: p for p in pp if p["kind"] == "create"}
    pp_m = {p["mint"]: p for p in pp if p["kind"] == "migrate"}
    pp_only_c = [p for m, p in pp_c.items() if m not in api_c and connected(p["recv"])]
    api_only_c = [e for m, e in api_c.items() if m not in pp_c and connected(e["recv"]) and e["quoteMint"] == WSOL]
    pp_only_m = [p for m, p in pp_m.items() if m not in api_m and connected(p["recv"])]
    api_only_m = [e for m, e in api_m.items() if m not in pp_m and connected(e["recv"])]
    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "window_s": round(wall, 1),
        "connections": log, "stats": {k: feed.stats[k] for k in ("received", "filtered", "published", "duplicates",
                                                                  "malformed", "not_quoted_in_sol", "amm_not_followed",
                                                                  "connections", "disconnects", "stalls")},
        "messages_per_s": round(feed.stats["received"] / wall, 1), "published_per_s": round(feed.stats["published"] / wall, 1),
        "receipt_lag_ms_last_2000": feed.health()["receipt_lag_ms"], "cpu_share_of_one_core": round(cpu / wall, 3),
        "connected_share_of_window": round(sum(c["seconds"] for c in log) / wall, 3),
        "creations_while_connected": {
            "pumpapi_sol_quoted": sum(1 for e in api_c.values() if connected(e["recv"]) and e["quoteMint"] == WSOL),
            "pumpportal": sum(1 for p in pp_c.values() if connected(p["recv"])),
            "pumpportal_only": [{"mint": short(p["mint"]), "on_chain": classify(p["signature"])} for p in pp_only_c[:10]],
            "pumpportal_only_count": len(pp_only_c),
            "pumpapi_only_sol_quoted_count": len(api_only_c),
            "pumpapi_only_sample": [{"mint": short(e["mint"]), "on_chain": classify(e["signature"])} for e in api_only_c[:5]]},
        "migrations_while_connected": {
            "pumpapi": sum(1 for e in api_m.values() if connected(e["recv"])),
            "pumpportal": sum(1 for p in pp_m.values() if connected(p["recv"])),
            "pumpportal_only": [{"mint": short(p["mint"]), "on_chain": classify(p["signature"])} for p in pp_only_m[:10]],
            "pumpapi_only": [{"mint": short(e["mint"]), "on_chain": classify(e["signature"])} for e in api_only_m[:10]]},
    }
    with open(out, "w") as f:
        f.write(json.dumps(doc, indent=2) + "\n")
    return doc


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=600)
    p.add_argument("--out", default="docs/evidence/pumpapi_soak.json")
    a = p.parse_args()
    d = main(a.seconds, a.out)
    print(json.dumps({k: d[k] for k in ("window_s", "stats", "connected_share_of_window", "messages_per_s",
                                        "receipt_lag_ms_last_2000", "cpu_share_of_one_core")}))
