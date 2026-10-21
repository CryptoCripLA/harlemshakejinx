"""Creation coverage: PumpPortal's free creation stream against ALL PumpAPI creates (any quote mint).

The soak compared PumpPortal with the creations JINX's feed publishes, which excludes coins not quoted in SOL.
This check keeps every PumpAPI pump create with its quoteMint, so a creation PumpPortal saw and PumpAPI did not
is either truly missing from PumpAPI or was only filtered by JINX. Only times both connections were up count.

    .venv/bin/python scripts/pumpapi_creation_check.py --seconds 180 --out docs/evidence/pumpapi_creation_check_20261021.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets

from jinx.adapters.pumpapi.normalize import WSOL, may_be_pump


def short(a) -> str:
    return f"{a[:4]}…{a[-4:]}" if isinstance(a, str) and len(a) > 12 else str(a)


async def capture(seconds: float):
    api, pp, gaps = {}, {}, {"pumpapi_max_gap_s": 0.0}
    connected: list = []

    async def pumpapi():
        while True:   # reconnect after a drop; only the time actually connected is compared
            start = time.time()
            try:
                async with websockets.connect("wss://stream.pumpapi.io/", open_timeout=15, max_size=None) as ws:
                    last = time.monotonic()
                    async for raw in ws:
                        now = time.monotonic()
                        gaps["pumpapi_max_gap_s"] = max(gaps["pumpapi_max_gap_s"], now - last)
                        last = now
                        b = raw if isinstance(raw, bytes) else raw.encode()
                        if b'"action":"create"' in b and may_be_pump(b):
                            e = json.loads(b)
                            if e.get("pool") == "pump":
                                api.setdefault(e["mint"], {"recv": time.time(), "quoteMint": e.get("quoteMint"),
                                                           "mayhemMode": e.get("mayhemMode")})
            except (websockets.ConnectionClosed, OSError) as e:
                gaps.setdefault("drops", []).append(f"{type(e).__name__} after {time.time() - start:.0f}s")
            finally:
                connected.append((start + 10, time.time() - 10))
            await asyncio.sleep(1)

    async def pumpportal():
        async with websockets.connect("wss://pumpportal.fun/api/data", open_timeout=15) as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            async for raw in ws:
                m = json.loads(raw)
                if m.get("txType") == "create":
                    pp.setdefault(m["mint"], {"recv": time.time()})

    results = await asyncio.gather(asyncio.wait_for(pumpapi(), seconds), asyncio.wait_for(pumpportal(), seconds),
                                   return_exceptions=True)
    errors = [type(r).__name__ for r in results if isinstance(r, BaseException) and not isinstance(r, TimeoutError)]
    return api, pp, gaps, errors, connected


def main(seconds: float, out: str) -> dict:
    t0 = time.time()
    api, pp, gaps, errors, connected = asyncio.run(capture(seconds))
    def up(t):   # inside a PumpAPI connection, 10 s from each of its ends
        return any(a <= t <= b for a, b in connected)
    pp_mid = {m: v for m, v in pp.items() if up(v["recv"])}
    api_mid = {m: v for m, v in api.items() if up(v["recv"])}
    pp_only = [m for m in pp_mid if m not in api]
    in_both = [m for m in pp_mid if m in api]
    doc = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "window_s": seconds,
           "errors": errors, **gaps,
           "pumpapi_connected_s": round(sum(max(0, b - a) for a, b in connected), 1),
           "pumpportal_creations_mid_window": len(pp_mid),
           "also_in_pumpapi": len(in_both),
           "of_which_sol_quoted": sum(1 for m in in_both if api[m]["quoteMint"] == WSOL),
           "of_which_other_quote": sum(1 for m in in_both if api[m]["quoteMint"] != WSOL),
           "pumpportal_only_missing_from_pumpapi": [short(m) for m in pp_only],
           "pumpapi_creations_mid_window": len(api_mid),
           "pumpapi_only": len([m for m in api_mid if m not in pp]),
           "pumpapi_only_quote_mix": {
               "sol": sum(1 for m, v in api_mid.items() if m not in pp and v["quoteMint"] == WSOL),
               "other": sum(1 for m, v in api_mid.items() if m not in pp and v["quoteMint"] != WSOL),
               "mayhem_mode": sum(1 for m, v in api_mid.items() if m not in pp and v["mayhemMode"])}}
    with open(out, "w") as f:
        f.write(json.dumps(doc, indent=2) + "\n")
    return doc


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=180)
    p.add_argument("--out", default="docs/evidence/pumpapi_creation_check.json")
    a = p.parse_args()
    print(json.dumps(main(a.seconds, a.out), indent=1))
