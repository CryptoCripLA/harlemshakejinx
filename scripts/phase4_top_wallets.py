"""Phase 4 research (jinx-comparison): how the wallets that actually made money on a real capture traded, measured from
the same raw stream JINX hears, set against JINX's own measured timing on the same coins.

    nice -n 19 .venv/bin/python scripts/phase4_top_wallets.py <capture.jsonl.gz> --rate 102.72 \\
        --rate-source "GMGN getGasPrice, <utc>" --out docs/evidence/PHASE4_TOP_WALLETS_<date>.json

From every bonding-curve trade in the capture (fee payer, side, SOL amount, token amount, slot, PumpAPI timestamp):
- per wallet and coin: SOL spent and received, tokens bought and sold, first buy (seconds and slots after the coin's
  creation), last sell; a round trip counts as CLOSED when at least 99 % of the tokens bought were sold inside the
  capture, and its result is SOL received minus SOL spent (curve fees are inside those amounts; network, priority and
  tip fees are not visible and not deducted);
- the top wallets by closed-trip result, their number of coins, win rate, entry delay, hold time and size;
- JINX on the same capture: when its entry coordinator found each eligible signal, measured from the coin's creation
  (the engine run is the one scripts/phase4_paper_pnl.py uses), plus the measured RugCheck indexing delay that gates a
  real safety verdict (7.9-10.2 s after creation, docs/BLOCKERS.md B-19).
Only coins whose creation is in the capture are used (entry delay needs it). This describes what others did; it is not
a strategy and no JINX rule changes because of it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from jinx.core.events import EventKind, Side  # noqa: E402
from jinx.sim import load_capture  # noqa: E402
import phase3_paper_replay as p3  # noqa: E402
from phase4_paper_pnl import mixed_deliveries  # noqa: E402

S = 1_000_000_000


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))] if v else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--rate", type=float, required=True)
    ap.add_argument("--rate-source", required=True)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    usd = (lambda lamports: None if lamports is None else round(lamports / 1e9 * a.rate, 2))
    records = load_capture(a.capture)

    created: dict[str, tuple[int, int | None, str]] = {}          # mint -> (event ns, slot, creator)
    pos = defaultdict(lambda: {"spent": 0, "got": 0, "tok_in": 0, "tok_out": 0, "first_buy": None, "first_slot": None,
                               "last_sell": None, "buys": 0, "sells": 0})
    for evs in mixed_deliveries(records):
        for ev in evs or ():
            if (ev.raw or {}).get("pool", "pump") != "pump":
                continue
            t = ev.event_time_ns
            if ev.kind is EventKind.TOKEN_CREATED and ev.source == "pumpapi.stream":
                if ev.mint not in created or t < created[ev.mint][0]:
                    created[ev.mint] = (t, ev.slot, ev.trader)
            if ev.kind is not EventKind.TRADE or not ev.trader or not ev.sol_lamports or not ev.token_amount:
                continue
            p = pos[(ev.trader, ev.mint)]
            if ev.side is Side.BUY:
                p["spent"] += ev.sol_lamports
                p["tok_in"] += ev.token_amount
                p["buys"] += 1
                if p["first_buy"] is None or t < p["first_buy"]:
                    p["first_buy"], p["first_slot"] = t, ev.slot
            else:
                p["got"] += ev.sol_lamports
                p["tok_out"] += ev.token_amount
                p["sells"] += 1
                p["last_sell"] = t if p["last_sell"] is None else max(p["last_sell"], t)

    wallets = defaultdict(lambda: {"closed": [], "open": 0, "coins": set()})
    for (w, m), p in pos.items():
        if m not in created or p["tok_in"] == 0 or p["first_buy"] is None:
            continue
        wallets[w]["coins"].add(m)
        if p["tok_out"] >= 0.99 * p["tok_in"] and p["last_sell"] is not None:
            c_t, c_slot, creator = created[m]
            wallets[w]["closed"].append({
                "mint": m, "pnl": p["got"] - p["spent"], "spent": p["spent"],
                "entry_s": (p["first_buy"] - c_t) / S,
                "entry_slots": (p["first_slot"] - c_slot) if p["first_slot"] is not None and c_slot is not None else None,
                "hold_s": (p["last_sell"] - p["first_buy"]) / S, "creator": w == creator, "buys": p["buys"]})
        else:
            wallets[w]["open"] += 1
    ranked = sorted(((w, d) for w, d in wallets.items() if d["closed"]), key=lambda kv: -sum(t["pnl"] for t in kv[1]["closed"]))
    top = []
    for w, d in ranked[:a.top]:
        trips = d["closed"]
        wins = [t for t in trips if t["pnl"] > 0]
        top.append({"wallet": w[:4] + "…" + w[-4:], "closed_trips": len(trips), "coins": len(d["coins"]),
                    "win_rate": round(len(wins) / len(trips), 2), "result_usd": usd(sum(t["pnl"] for t in trips)),
                    "median_buy_usd": usd(statistics.median(t["spent"] for t in trips)),
                    "median_entry_s_after_creation": round(statistics.median(t["entry_s"] for t in trips), 2),
                    "entries_in_creation_slot": sum(1 for t in trips if t["entry_slots"] == 0),
                    "entries_within_2_slots": sum(1 for t in trips if t["entry_slots"] is not None
                                                  and t["entry_slots"] <= 2),
                    "median_hold_s": round(statistics.median(t["hold_s"] for t in trips), 1),
                    "is_creator_on": sum(t["creator"] for t in trips)})
    all_winning = [t for _, d in ranked[:a.top] for t in d["closed"] if t["pnl"] > 0]

    # JINX on the same capture: when the entry coordinator found each eligible signal, after the coin's creation
    tmp = Path(tempfile.mkdtemp(prefix="jinx-p4-topw-"))
    try:
        app = p3.build(tmp, 0)
        clock = {"now": 0}
        jinx_delays, seen = [], set()
        original = app.entries.consider_fast_path

        def wrapped(evidence):
            d = original(evidence)
            if (d.ok or d.reason == "SLOT_LIMIT") and evidence.mint not in seen and evidence.mint in created:
                seen.add(evidence.mint)
                jinx_delays.append((max(clock["now"], evidence.event_ns) - created[evidence.mint][0]) / S)
            return d
        app.entries.consider_fast_path = wrapped

        async def drive():
            runner = asyncio.create_task(app.bus.run())
            for evs in mixed_deliveries(records):
                for ev in evs or ():
                    clock["now"] = max(clock["now"], ev.event_time_ns)
                    await app.bus.publish(ev)
                await asyncio.sleep(0)
            await app.bus.drain()
            runner.cancel()
        asyncio.run(drive())
        app.store.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    report = {
        "what": "Research comparison: how the top wallets on a real capture traded vs JINX's measured timing on the "
                "same stream. Descriptive; not a strategy; no JINX rule changes",
        "capture": a.capture.name, "records": len(records),
        "capture_span_s": round((records[-1]["recv_ns"] - records[0]["recv_ns"]) / 1e9, 1),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "usd_rate": {"usd_per_sol": a.rate, "source": a.rate_source},
        "coins_created_in_capture": len(created), "wallets_with_closed_trips": len(ranked),
        "all_wallets_closed_trip_result_usd": usd(sum(t["pnl"] for _, d in ranked for t in d["closed"])),
        "top_wallets": top,
        "top_wallets_winning_trips": {
            "n": len(all_winning),
            "entry_s_after_creation": {"p25": pct([t["entry_s"] for t in all_winning], .25),
                                       "median": pct([t["entry_s"] for t in all_winning], .5),
                                       "p75": pct([t["entry_s"] for t in all_winning], .75)},
            "hold_s": {"median": pct([t["hold_s"] for t in all_winning], .5)},
            "median_buy_usd": usd(statistics.median(t["spent"] for t in all_winning)) if all_winning else None},
        "jinx_eligible_signal_after_creation_s": {"n": len(jinx_delays), "p25": pct(jinx_delays, .25),
                                                  "median": pct(jinx_delays, .5), "p75": pct(jinx_delays, .75)},
        "rugcheck_indexing_after_creation_s": {"min": 7.9, "max": 10.2, "source": "docs/BLOCKERS.md B-19"},
        "limits": ["closed round trips inside the 45-minute capture only; open positions are not counted",
                   "network, priority and tip fees are not visible in the stream and not deducted",
                   "wallets are shown shortened; nothing here identifies a person"],
    }
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
