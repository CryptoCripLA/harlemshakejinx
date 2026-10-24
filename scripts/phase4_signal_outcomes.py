"""Phase 4 V21 (research comparison, paper only): what JINX's eligible signals did on real markets, and how the exit
policy changes the result. Owner order 2026-10-21 §3: time-based exits are a research variant, off by default, compared
on replay with realistic fees, delays and liquidity before any approval; no hindsight sell prices.

    nice -n 19 .venv/bin/python scripts/phase4_signal_outcomes.py <capture.jsonl.gz> --rate 102.72 \\
        --rate-source "GMGN getGasPrice, 2026-10-24T04:50:07Z" --out docs/evidence/PHASE4_SIGNAL_OUTCOMES_<date>.json

1. Signals: the capture runs through the whole engine (JinxApp bus, Early Momentum, safety screen, B-02 entry rule,
   the fast-path EntryCoordinator) exactly as scripts/phase4_paper_pnl.py does; every decision that passed every
   check (entered, or refused only because the one slot was taken) is an eligible signal.
2. Paths: each signal's coin is followed on its real bonding-curve readings from the capture.
3. Execution, never hindsight: a buy of a fixed TEST_FIXTURE budget fills on the first curve reading at least `delay`
   after the decision; an exit decided on a reading fills on the first reading at least `delay` later, or, when the
   coin has stopped trading, on the curve as it last stood (a pump.fun curve always takes a sell). Costs: curve fees
   both ways (95 + 5 bps) and the 5,000-lamport network fee both ways. Priority fees and tips are not modeled (B-39).
4. Policies (labelled; none is live policy): BIGMAC_FIXTURE = the owner's BigMac evaluate_exit with Phase 3's
   TEST_FIXTURE settings 50 / 20 / 12 / 20, exactly as the position monitor runs it today (no timeout); and the same
   plus BigMac's own `waitForExitCondition` timeout (helpers/exit-strategy.ts: exit 'timeout' after durationMs), at
   several research durations. Run two ways: every signal on its own (what the signals offer), and with the owner's
   one entry slot (what JINX would actually have done, one position at a time).
5. Diagnostics, labelled hindsight: each signal's peak value after entry (MFE) and lowest value (MAE). They show what
   the market offered; no policy may use them.

Not real, and labelled in the output: the safety verdict (PASS for every screened candidate: GMGN/RugCheck answers
were not captured, so some signals here would have been vetoed live), the buyers' funders (distinct), the budget and
the BigMac numbers (TEST_FIXTURE). SIMULATED; never realized profit.
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
import time
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from jinx import config  # noqa: E402
from jinx.core.events import EventKind  # noqa: E402
from jinx.exits.bigmac_exit import evaluate_exit  # noqa: E402
from jinx.intel.readings import standard_curve, verified_real_sol  # noqa: E402
from jinx.paper import curve_sol_for_budget, curve_tokens_out, sell_proceeds  # noqa: E402
from jinx.sim import load_capture  # noqa: E402
import phase3_paper_replay as p3  # noqa: E402
from jinx.exits.policy import BigMacPaperConfig  # noqa: E402
from phase4_paper_pnl import SWEEP_NS, mixed_deliveries  # noqa: E402

BUDGET = 100_000_000                       # TEST_FIXTURE: the Kelly fixture's 5 % of the 2 SOL paper wallet
FEE_BPS = config.PUMP_CURVE_FEES.total_bps
NET = config.NETWORK_FEE_LAMPORTS
S = 1_000_000_000
BM = p3.BIGMAC_FIXTURE                     # TEST_FIXTURE 50 / 20 / 12 / 20
TIMEOUTS_S = (None, 60, 180, 300, 600)
# Research candidates for the owner's BIGMAC_PAPER settings (B-30 is unset): Phase 3's fixture, and the same with the
# take-profit switched off (BigMac: takeProfitPct 0 disables it), with a tighter and a wider trailing stop. Labelled
# TEST_FIXTURE; none is a default and none is live.
RESEARCH_BM = {
    "fixture TP50 SL20 TS12 act20": BM,
    "TP off SL20 TS12 act20": BigMacPaperConfig(0, 20, 12, 20, label="TEST_FIXTURE_p4_tp_off", set_by="phase4 research"),
    "TP off SL30 TS25 act50": BigMacPaperConfig(0, 30, 25, 50, label="TEST_FIXTURE_p4_wide_trail",
                                                set_by="phase4 research"),
}


def signals_from_engine(records) -> tuple[list[dict], dict]:
    tmp = Path(tempfile.mkdtemp(prefix="jinx-p4-signals-"))
    try:
        app = p3.build(tmp, 0)
        clock = {"now": 0}
        seen, out = set(), []
        original = app.entries.consider_fast_path

        def wrapped(evidence):
            d = original(evidence)
            key = (evidence.mint, evidence.event_ns)
            if (d.ok or d.reason == "SLOT_LIMIT") and key not in seen:
                seen.add(key)
                out.append({"mint": evidence.mint, "signal_ns": evidence.event_ns,
                            "decided_event_ns": max(clock["now"], evidence.event_ns)})
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
            for _ in range(300):
                if not app._background:
                    break
                await asyncio.sleep(0.01)
            runner.cancel()
        asyncio.run(drive())
        stats = {"detections": app.early.stats["detections"], "entry_decisions": dict(app.entries.stats)}
        app.store.close()
        return out, stats
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def curve_paths(records) -> dict[str, tuple[list[int], list[tuple[int, int]]]]:
    """Each coin's bonding-curve readings, kept only when the reading is a verified standard pump.fun curve (virtual
    minus verified real SOL is the standard 30 SOL: jinx.intel.readings.standard_curve). Some live readings report a
    virtual size far beyond graduation with almost no real SOL; pricing a buy on them would invent huge gains, so a
    coin with no standard reading is not priced (counted as unpriced)."""
    paths = defaultdict(list)
    for evs in mixed_deliveries(records):
        for ev in evs or ():
            if ev.v_sol_lamports and ev.v_tokens and ev.kind in (EventKind.TRADE, EventKind.TOKEN_CREATED) \
                    and (ev.raw or {}).get("pool", "pump") == "pump" \
                    and standard_curve(ev.v_sol_lamports, verified_real_sol(ev)) is True:
                paths[ev.mint].append((ev.event_time_ns, ev.seq, ev.v_sol_lamports, ev.v_tokens))
    out = {}
    for mint, rows in paths.items():
        rows.sort()
        out[mint] = ([r[0] for r in rows], [(r[2], r[3]) for r in rows])
    return out


def state_at(path, t_ns: int):
    """Index and reserves of the first reading at or after t_ns, or the last reading before it when the coin
    stopped trading (the curve still stands there)."""
    times, res = path
    i = bisect_left(times, t_ns)
    if i < len(times):
        return i, times[i], res[i], True
    return len(times) - 1, t_ns, res[-1], False


def value(res, tokens: int) -> int:
    return sell_proceeds(res[0], res[1], tokens, FEE_BPS)[0] - NET


def simulate(signal: dict, path, delay_ns: int, timeout_s: int | None, horizon_ns: int, bm=None) -> dict | None:
    times, res = path
    i, t_fill, r, traded = state_at(path, signal["decided_event_ns"] + delay_ns)
    if not traded:
        return None                                     # no reading after the decision: the buy could not be priced
    tokens = curve_tokens_out(r[0], r[1], curve_sol_for_budget(BUDGET, FEE_BPS))
    cost = BUDGET + NET
    if tokens <= 0:
        return None
    basis = BUDGET                                      # what the held tokens cost (the monitor's entry basis)
    bm = bm or BM
    # HINDSIGHT diagnostic over the whole path after the fill (independent of any exit; no policy reads it)
    later = [value(res[k], tokens) for k in range(i, len(times)) if times[k] <= horizon_ns]
    mfe, mae = max(later), min(later)
    peak = 0
    exit_t, exit_res, reason = None, None, None
    deadline = t_fill + timeout_s * S if timeout_s else None
    j = i + 1
    while j < len(times) and times[j] <= horizon_ns:
        if deadline is not None and times[j] >= deadline:
            break
        v = value(res[j], tokens) + NET                 # BigMac compares sale value with the entry basis
        why, peak = evaluate_exit(v, basis, peak, bm.take_profit_pct, bm.stop_loss_pct, bm.trailing_stop_pct,
                                  bm.trailing_stop_activation_pct)
        if why is not None:
            _, exit_t, exit_res, _ = state_at(path, times[j] + delay_ns)
            reason = why.value
            break
        j += 1
    if reason is None and deadline is not None and deadline <= horizon_ns:
        _, exit_t, exit_res, _ = state_at(path, deadline + delay_ns)
        reason = "timeout"
    if reason is None:                                  # still held when the capture ends: unrealized, no hindsight
        _, _, last, _ = state_at(path, horizon_ns)
        return {"fill_ns": t_fill, "exit_ns": None, "reason": "open_at_end", "cost": cost,
                "unrealized": value(last, tokens) - cost, "realized": None, "mfe": mfe - cost, "mae": mae - cost}
    proceeds = value(exit_res, tokens)
    return {"fill_ns": t_fill, "exit_ns": exit_t, "reason": reason, "cost": cost, "realized": proceeds - cost,
            "unrealized": None, "mfe": mfe - cost, "mae": mae - cost}


def summarize(trades: list[dict], usd) -> dict:
    real = [t["realized"] for t in trades if t["realized"] is not None]
    unreal = [t["unrealized"] for t in trades if t["unrealized"] is not None]
    reasons = defaultdict(int)
    for t in trades:
        reasons[t["reason"]] += 1
    return {"trades": len(trades), "closed": len(real), "open_at_end": len(unreal),
            "wins": sum(1 for x in real if x > 0), "losses": sum(1 for x in real if x <= 0),
            "realized_usd": usd(sum(real)), "unrealized_usd": usd(sum(unreal)),
            "median_trade_usd": usd(statistics.median(real)) if real else None,
            "best_trade_usd": usd(max(real)) if real else None, "worst_trade_usd": usd(min(real)) if real else None,
            "exit_reasons": dict(reasons)}


def one_slot(signals, paths, delay_ns, timeout_s, horizon_ns, bm=None) -> list[dict]:
    """The owner's one entry slot: take the next eligible signal only once the previous position has exited."""
    free_at, out = 0, []
    for s in sorted(signals, key=lambda s: s["decided_event_ns"]):
        if s["decided_event_ns"] < free_at or s["mint"] not in paths:
            continue
        t = simulate(s, paths[s["mint"]], delay_ns, timeout_s, horizon_ns, bm)
        if t is None:
            continue
        out.append(t)
        if t["exit_ns"] is None:
            break                                       # held to the end of the capture: the slot never frees
        free_at = t["exit_ns"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--delay-ms", type=int, default=400)
    ap.add_argument("--rate", type=float, required=True)
    ap.add_argument("--rate-source", required=True)
    ap.add_argument("--ceiling-sol", type=float, default=None,
                    help="RESEARCH ONLY: replay with a different Early Momentum real-SOL ceiling than the owner's B-02 "
                         "value (10). Never changes live policy; the report labels it.")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if a.ceiling_sol is not None:
        import jinx.intel.early_momentum as em
        em.CEILING_LAMPORTS = int(a.ceiling_sol * 1_000_000_000)
    usd = (lambda lamports: None if lamports is None else round(lamports / 1e9 * a.rate, 2))
    records = load_capture(a.capture)
    signals, engine = signals_from_engine(records)
    paths = curve_paths(records)
    horizon = max(t for times, _ in paths.values() for t in times[-1:])
    delay = a.delay_ms * 1_000_000
    each = [(s, simulate(s, paths[s["mint"]], delay, None, horizon)) for s in signals if s["mint"] in paths]
    priced = [(s, t) for s, t in each if t is not None]
    mfe_x = [(t["mfe"] + t["cost"]) / t["cost"] for _, t in priced]
    report = {
        "what": "Phase 4 research comparison on real captured markets: SIMULATED paper outcomes of JINX's eligible "
                "signals under labelled exit policies; not live trading, not profit, not a live policy",
        "capture": a.capture.name, "records": len(records),
        "capture_span_s": round((records[-1]["recv_ns"] - records[0]["recv_ns"]) / 1e9, 1),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "usd_rate": {"usd_per_sol": a.rate, "source": a.rate_source},
        "budget_usd": usd(BUDGET), "delay_ms": a.delay_ms, "engine": engine,
        "entry_ceiling": {"real_sol": a.ceiling_sol if a.ceiling_sol is not None else 10.0,
                          "usd": usd(int((a.ceiling_sol if a.ceiling_sol is not None else 10.0) * 1e9)),
                          "label": "owner decision B-02 (live policy)" if a.ceiling_sol in (None, 10.0) else
                          "RESEARCH VARIANT for comparison only; live policy stays B-02 until the owner decides"},
        "stand_ins": p3.STAND_INS, "bigmac_fixture": BM.to_dict(),
        "signals": {"eligible": len(signals), "priced": len(priced),
                    "hindsight_peak_value_after_fees": {
                        "at_least_1_5x": sum(x >= 1.5 for x in mfe_x), "at_least_2x": sum(x >= 2 for x in mfe_x),
                        "at_least_5x": sum(x >= 5 for x in mfe_x), "at_least_10x": sum(x >= 10 for x in mfe_x),
                        "never_above_cost": sum(x <= 1 for x in mfe_x),
                        "median_peak_x": round(statistics.median(mfe_x), 3) if mfe_x else None,
                        "best_peak_x": round(max(mfe_x), 2) if mfe_x else None,
                        "label": "HINDSIGHT diagnostic: the best value each signal reached after a delayed buy; no "
                                 "policy can sell at it"}},
        "policies": {},
    }
    for bm_name, bm in RESEARCH_BM.items():
        for timeout_s in TIMEOUTS_S:
            name = f"BigMac {bm_name}" + (" (no timeout: as built)" if timeout_s is None else
                                          f" + BigMac timeout {timeout_s} s")
            every = [simulate(s, paths[s["mint"]], delay, timeout_s, horizon, bm) for s in signals if s["mint"] in paths]
            every = [t for t in every if t is not None]
            report["policies"][name] = {"settings": bm.to_dict(), "timeout_s": timeout_s,
                                        "every_signal_on_its_own": summarize(every, usd),
                                        "one_slot_as_jinx_trades": summarize(one_slot(signals, paths, delay, timeout_s,
                                                                                      horizon, bm), usd)}
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
