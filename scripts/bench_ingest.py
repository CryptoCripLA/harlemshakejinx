#!/usr/bin/env python3
"""Ingest overhead under load: synthetic PumpPortal-shaped messages through the real pipeline.

The messages are SYNTHETIC (generated here, not market data). They only measure
how fast normalize -> bus -> durable store -> registries runs on this machine,
which matters once the trade stream is enabled. Live-capture replays measure
the same path with real data.

    .venv/bin/python scripts/bench_ingest.py --tokens 200 --trades-per-token 100
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jinx.sim import replay  # noqa: E402


def synthetic(tokens: int, trades: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    recs, t = [], 1_800_000_000_000_000_000
    for i in range(tokens):
        mint = f"SYNTH{i:05d}pump"
        vsol, vtok = 30.0, 1_073_000_000.0
        t += 1_000_000
        recs.append({"recv_ns": t, "raw": {"signature": f"C{i}", "mint": mint, "traderPublicKey": f"DEV{i}",
                                           "txType": "create", "initialBuy": 0, "solAmount": 0, "pool": "pump",
                                           "vSolInBondingCurve": vsol, "vTokensInBondingCurve": vtok}})
        for j in range(trades):
            sol = round(rng.uniform(0.01, 2.0), 6)
            buy = rng.random() < 0.6
            k = vsol * vtok
            vsol = vsol + sol if buy else max(vsol - sol, 1.0)
            tok = abs(vtok - k / vsol)
            vtok = k / vsol
            t += 1_000_000
            recs.append({"recv_ns": t, "raw": {"signature": f"T{i}-{j}", "mint": mint,
                                               "traderPublicKey": f"W{rng.randint(0, 5000)}",
                                               "txType": "buy" if buy else "sell", "solAmount": sol,
                                               "tokenAmount": tok, "pool": "pump", "vSolInBondingCurve": vsol,
                                               "vTokensInBondingCurve": vtok}})
    return recs


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tokens", type=int, default=200)
    p.add_argument("--trades-per-token", type=int, default=100)
    a = p.parse_args()
    recs = synthetic(a.tokens, a.trades_per_token)
    db = Path(tempfile.mkdtemp(prefix="jinx-bench-")) / "bench.db"
    r = replay(recs, db)
    out = asdict(r)
    out["synthetic"] = True
    out["events_per_second"] = round(r.events_admitted / r.elapsed_s, 1)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
