"""Phase 4: prove the verification tests are not vacuous. Each mutation changes one CHAD number or rule in memory
(never on disk) and runs the tests that must catch it; a mutation the tests do not catch is a FAIL.

    .venv/bin/python scripts/phase4_mutation_check.py --out docs/evidence/PHASE4_MUTATION_20261021.json

Mutations are applied by a pytest plugin loaded with -p, which patches module attributes before the tests run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PRED = "tests/phase4/test_p4_chad_entry_predicates.py"
SAFE = "tests/phase4/test_p4_safety_rejections.py"

# (id, CHAD reference, patch code run inside the test process, tests that must fail)
MUTATIONS = [
    ("fee_payers_2", "L78 3 distinct fee payers", "import jinx.intel.early_momentum as m; m.REQUIRED_FEE_PAYERS = 2",
     f"{PRED}::test_L78_three_buys_from_one_wallet_never_count_as_three_wallets"),
    ("window_31s", "L65 30-second window", "import jinx.intel.early_momentum as m; m.WINDOW_NS = 31_000_000_000",
     f"{PRED}::test_L65_three_wallets_spread_over_more_than_30_seconds_do_not_trigger"),
    ("window_29s", "L65 30-second window (inclusive)", "import jinx.intel.early_momentum as m; m.WINDOW_NS = 29_000_000_000",
     f"{PRED}::test_L65_three_wallets_exactly_30_seconds_apart_trigger"),
    ("floor_0_1", "L68 0.5 SOL floor", "import jinx.intel.early_momentum as m; m.FLOOR_LAMPORTS = 100_000_000",
     f"{PRED}::test_L68_below_the_half_sol_floor_is_refused"),
    ("ceiling_9_9", "L69 10 SOL ceiling (inclusive)",
     "import jinx.intel.early_momentum as m; m.CEILING_LAMPORTS = 9_900_000_000",
     f"{PRED}::test_L69_exactly_ten_sol_is_inside_the_gate"),
    ("floor_0_6", "L68 0.5 SOL floor (inclusive)", "import jinx.intel.early_momentum as m; m.FLOOR_LAMPORTS = 600_000_000",
     f"{PRED}::test_L68_exactly_half_a_sol_is_inside_the_gate"),
    ("ceiling_20", "L69 10 SOL ceiling", "import jinx.intel.early_momentum as m; m.CEILING_LAMPORTS = 20_000_000_000",
     f"{PRED}::test_L69_above_the_ten_sol_ceiling_is_refused"),
    ("peak_ceiling_20", "L227 peak gate", "import jinx.intel.early_momentum as m; m.CEILING_LAMPORTS = 20_000_000_000",
     f"{PRED}::test_L227_a_token_that_ran_past_the_ceiling_and_came_back_is_refused"),
    ("sybil_4", "L81 3+ wallets share a funder", "import jinx.intel.early_momentum as m; m.SHARED_FUNDER_WALLETS = 4",
     f"{PRED}::test_L81_three_buyers_sharing_one_funder_block_and_raise_the_emergency_exit"),
    ("honeypot_ignored", "L16 honeypot detection",
     "import jinx.intel.safety as s; from jinx.intel.safety import Check, Verdict; "
     "s._honeypot = lambda sec: Check('honeypot', Verdict.PASS, None, 'mutant')",
     f"{SAFE}::test_L16_a_reported_honeypot_is_a_hard_veto"),
    ("mint_authority_ignored", "L16 mint authority",
     "import jinx.intel.safety as s; from jinx.intel.safety import Check, Verdict; o = s._renounced; "
     "s._renounced = lambda sec, n, k: Check(n, Verdict.PASS, True, 'mutant') if n == 'mint_authority' else o(sec, n, k)",
     f"{SAFE}::test_L16_a_held_mint_authority_is_a_hard_veto"),
    ("unknown_is_pass", "L16 missing evidence fails closed",
     "import jinx.intel.safety as s; from jinx.intel.safety import Verdict; o = s.assess; "
     "s.assess = lambda *a, **k: o(*a, **k).__class__(**{**o(*a, **k).__dict__, 'verdict': Verdict.PASS}) "
     "if o(*a, **k).verdict is Verdict.UNKNOWN else o(*a, **k)",
     f"{SAFE}::test_L16_no_security_data_is_unknown_and_never_eligible"),
]


def run(mid: str, patch: str, test: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="jinx-p4-mut-") as d:
        plugin = Path(d) / f"p4mut_{mid}.py"
        plugin.write_text(textwrap.dedent(f"""
            def pytest_configure(config):
                {patch}
        """))
        env = dict(os.environ, PYTHONPATH=f"{d}{os.pathsep}{ROOT}")
        r = subprocess.run([PY, "-m", "pytest", "-p", "no:cacheprovider", "-p", f"p4mut_{mid}", test],
                           cwd=ROOT, env=env, capture_output=True, text=True)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    return {"exit_code": r.returncode, "caught": r.returncode == 1, "pytest": tail}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    rows = []
    for mid, ref, patch, test in MUTATIONS:
        if args.only and mid not in args.only:
            continue
        rows.append({"mutation": mid, "chad": ref, "test": test, **run(mid, patch, test)})
    clean = subprocess.run([PY, "-m", "pytest", "-p", "no:cacheprovider", PRED, SAFE], cwd=ROOT,
                           capture_output=True, text=True)
    report = {"utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                       text=True).stdout.strip(),
              "unmutated_run": {"exit_code": clean.returncode, "pytest": clean.stdout.strip().splitlines()[-1]},
              "mutations": rows, "result": "PASS" if clean.returncode == 0 and all(r["caught"] for r in rows) else "FAIL"}
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    sys.exit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
