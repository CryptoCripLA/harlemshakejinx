"""Source equivalence: the Python BigMac port against the ORIGINAL TypeScript, run by node 22 on identical inputs.

The two functions are extracted verbatim from the vendored file by line range (third_party/bigmac/SOURCE.json) and
hash-checked against the pinned upstream before they run. Node 22 strips the TypeScript types itself, so nothing
in the original is rewritten. Every case compares the decision (exit reason or none), the returned peak, and
whether an error is raised (BigInt() refuses NaN and Infinity).
"""
import hashlib
import json
import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from jinx.exits.bigmac_exit import ExitReason, apply_percent, evaluate_exit

ROOT = Path(__file__).resolve().parent.parent
SOURCE = json.loads((ROOT / "third_party" / "bigmac" / "SOURCE.json").read_text())
TS_FILE = ROOT / "third_party" / "bigmac" / "helpers" / "exit-strategy.ts"
NODE = shutil.which("node") or "/usr/bin/node"

HARNESS = r"""
const enc = (x) => (typeof x === 'string' && ['NaN', 'Infinity', '-Infinity'].includes(x)) ? Number(x) : x;
const lines = require('fs').readFileSync(0, 'utf8').trim().split('\n');
const out = [];
for (const line of lines) {
  const c = JSON.parse(line);
  try {
    if (c.fn === 'apply') {
      out.push({ value: applyPercent(BigInt(c.amount), enc(c.percent), c.add).toString() });
    } else {
      const r = evaluateExit({ current: BigInt(c.current), entry: BigInt(c.entry), peak: BigInt(c.peak),
        takeProfitPct: enc(c.tp), stopLossPct: enc(c.sl), trailingStopPct: enc(c.ts),
        trailingStopActivationPct: enc(c.tsa) });
      out.push({ reason: r.reason ?? null, peak: r.peak.toString() });
    }
  } catch (e) {
    out.push({ error: e.name });
  }
}
process.stdout.write(out.map((o) => JSON.stringify(o)).join('\n'));
"""


def _extract() -> str:
    raw = TS_FILE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE["files"]["helpers/exit-strategy.ts"], "vendored file changed"
    text = raw.decode().split("\n")
    parts = []
    for name in ("evaluateExit", "applyPercent"):
        a, b = SOURCE["ported"][name]["lines"]
        block = "\n".join(text[a - 1:b]) + "\n"
        assert hashlib.sha256(block.encode()).hexdigest() == SOURCE["ported"][name]["sha256"], f"{name} lines moved"
        parts.append(block)
    return "\n".join(parts)


def _run_ts(tmp_path, cases):
    # `require` is used by the harness; a .cts module keeps CommonJS while node strips the types
    src = tmp_path / "bigmac_exit_original.cts"
    src.write_text(_extract().replace("export function", "function") + HARNESS)
    res = subprocess.run([NODE, str(src)], input="\n".join(json.dumps(c) for c in cases), capture_output=True,
                         text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    return [json.loads(x) for x in res.stdout.split("\n")]


def _num(x):
    return float(x) if isinstance(x, str) else x


def _py(c):
    try:
        if c["fn"] == "apply":
            return {"value": str(apply_percent(int(c["amount"]), _num(c["percent"]), c["add"]))}
        reason, peak = evaluate_exit(int(c["current"]), int(c["entry"]), int(c["peak"]), _num(c["tp"]),
                                     _num(c["sl"]), _num(c["ts"]), _num(c["tsa"]))
        return {"reason": reason.value if reason else None, "peak": str(peak)}
    except (ValueError, OverflowError):
        return {"error": "RangeError"}


SPECIALS = ["NaN", "Infinity", "-Infinity"]
PERCENTS = [0, 0.0, -0.0, 5e-324, 0.5, 0.999999, 1, 1.0000001, 1.5, 9.99, 10, 12, 19.999, 20, 33.3, 50, 99.9, 100,
            100.5, 150, 1000, 1e15, 1e21, 2.0 ** 53 + 2, 1e308, -0.5, -1, -100, -1e300] + SPECIALS
AMOUNTS = [0, 1, 99, 100, 101, 12_345, 1_000_000_000, 2 ** 63, 10 ** 40, -1, -150, -(10 ** 30)]


def _apply_cases():
    return [{"fn": "apply", "amount": str(a), "percent": p, "add": add}
            for a in AMOUNTS for p in PERCENTS for add in (True, False)]


def _boundary_cases():
    """Exact stop, take-profit, activation and trail-floor prices, one below and one above, and several conditions
    true at once. Prices are computed with the TypeScript formula (integer percent, truncating division)."""
    cases = []

    def ev(cur, entry, peak, tp, sl, ts, tsa):
        cases.append({"fn": "eval", "current": str(cur), "entry": str(entry), "peak": str(peak),
                      "tp": tp, "sl": sl, "ts": ts, "tsa": tsa})

    for entry in (1, 99, 100, 101, 7_777, 1_000_000_000, 123_456_789_012_345, 10 ** 30):
        for sl in (0, 0.5, 1, 10, 33.3, 99, 100, 150):
            stop = entry - entry * max(0, math.floor(sl)) // 100
            stop = stop if entry > entry * max(0, math.floor(sl)) // 100 else 0
            for cur in (stop - 1, stop, stop + 1):
                ev(cur, entry, 0, 0, sl, 0, 0)
        for tp in (0.5, 1, 9.9, 10, 50, 100, 250):
            take = entry * (100 + max(0, math.floor(tp))) // 100
            for cur in (take - 1, take, take + 1):
                ev(cur, entry, 0, tp, 99, 0, 0)
        for tsa in (0, 0.5, 10, 20, 99.9):
            act = entry * (100 + max(0, math.floor(tsa))) // 100
            for ts in (0.5, 1, 12, 50, 100):
                for peak in (act - 1, act, act + 1, act * 3):
                    floor = peak - peak * max(0, math.floor(ts)) // 100 if peak > peak * max(0, math.floor(ts)) // 100 else 0
                    for cur in (floor - 1, floor, floor + 1):
                        ev(cur, entry, peak, 0, 99, ts, tsa)
    # several conditions true at once: the decision order SL -> TP -> trailing decides
    ev(100, 100, 0, 0.5, 0, 0, 0)          # SL (= entry) and TP (= entry) both hit: stop_loss
    ev(140, 100, 300, 10, 50, 50, 20)      # TP (110) and trailing (floor 150) both hit: take_profit
    ev(40, 100, 1000, 0, 50, 90, 20)       # SL (50) and trailing (floor 100) both hit: stop_loss
    ev(40, 100, 1000, 10, 50, 90, 20)      # all three true at once: stop_loss
    ev(150, 100, 1000, 0, 0, 90, 20)       # SL (100) false, TP off, trailing (floor 100) false: none
    ev(500, 100, 200, 0, 50, 10, 20)       # current above the old peak: peak moves to current, no exit
    ev(0, 0, 0, 10, 10, 10, 10)            # all-zero prices: stop_loss (0 <= 0)
    return cases


def _random_cases(n=4_000, seed=20261021):
    rng = random.Random(seed)
    pct = lambda: rng.choice([rng.uniform(-50, 300), float(rng.randint(-5, 300)), rng.choice(PERCENTS)])
    cases = []
    for _ in range(n):
        entry = rng.choice([rng.randint(0, 10 ** 12), rng.randint(0, 2 ** 70), rng.randint(-10 ** 6, 10 ** 6)])
        cur = rng.choice([entry + rng.randint(-entry or -1, 3 * abs(entry) + 1), rng.randint(0, 2 ** 64)])
        peak = rng.choice([0, max(cur, entry), cur + rng.randint(0, 10 ** 9), rng.randint(0, 2 ** 64)])
        cases.append({"fn": "eval", "current": str(cur), "entry": str(entry), "peak": str(peak),
                      "tp": pct(), "sl": pct(), "ts": pct(), "tsa": pct()})
    return cases


def _documented_divergence(c) -> bool:
    """The one known difference, kept because the owner ordered exactly two fixes: TypeScript computes the
    take-profit price before the stop-loss check (exit-strategy.ts:25), the owner's port only when tp > 0 and after
    the stop check. So a NaN take-profit, or +Infinity when the stop fires first, throws in TypeScript and not in
    the port. Unreachable through JINX: BigMacPaperConfig refuses every non-finite percent
    (test_paper_config_refuses_non_finite_percents)."""
    return c["fn"] == "eval" and c["tp"] in ("NaN", "Infinity")


@pytest.mark.skipif(not Path(NODE).exists(), reason="node 22 is required for the source-equivalence test")
def test_apply_percent_equals_the_original_typescript(tmp_path):
    cases = _apply_cases()
    ts = _run_ts(tmp_path, cases)
    py = [_py(c) for c in cases]
    bad = [(c, t, p) for c, t, p in zip(cases, ts, py) if t != p]
    assert not bad, bad[:5]
    assert len(cases) >= 600


@pytest.mark.skipif(not Path(NODE).exists(), reason="node 22 is required for the source-equivalence test")
def test_evaluate_exit_decisions_and_peaks_equal_the_original_typescript(tmp_path):
    cases = _boundary_cases() + _random_cases()
    ts = _run_ts(tmp_path, cases)
    py = [_py(c) for c in cases]
    bad = [(c, t, p) for c, t, p in zip(cases, ts, py) if t != p and not _documented_divergence(c)]
    assert not bad, bad[:5]
    reasons = {t.get("reason") for t in ts}
    assert {"stop_loss", "take_profit", "trailing_stop", None} <= reasons  # every outcome was exercised
    divergent = [(c, t, p) for c, t, p in zip(cases, ts, py) if t != p]
    assert all(t == {"error": "RangeError"} and "error" not in p for _c, t, p in divergent)


@pytest.mark.skipif(not Path(NODE).exists(), reason="node 22 is required for the source-equivalence test")
def test_decision_order_with_several_conditions_true(tmp_path):
    cases = _boundary_cases()[-7:]
    ts = _run_ts(tmp_path, cases)
    assert [t["reason"] for t in ts] == ["stop_loss", "take_profit", "stop_loss", "stop_loss", None, None,
                                         "stop_loss"]
    assert [_py(c)["reason"] for c in cases] == [t["reason"] for t in ts]
    assert ts[5]["peak"] == "500"


def test_port_uses_integer_amounts_only():
    with pytest.raises(TypeError):
        apply_percent(1.5, 10, True)
    with pytest.raises(TypeError):
        evaluate_exit(100, 100.0, 0, 0, 10, 0, 0)
    assert evaluate_exit(10 ** 40, 10 ** 40, 0, 0, 10, 0, 0) == (None, 10 ** 40)
    assert evaluate_exit(90, 100, 0, 0, 10, 0, 0) == (ExitReason.STOP_LOSS, 90)
