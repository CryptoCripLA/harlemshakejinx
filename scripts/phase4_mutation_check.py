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
OUT = "tests/phase4/test_p4_outages.py"
REC = "tests/phase4/test_p4_recovery.py"
ORD = "tests/phase4/test_p4_orders.py"
STR = "tests/phase4/test_p4_stress.py"
REP = "tests/phase4/test_p4_replay_determinism.py"

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
    ("freeze_authority_ignored", "L16 freeze authority",
     "import jinx.intel.safety as s; from jinx.intel.safety import Check, Verdict; o = s._renounced; "
     "s._renounced = lambda sec, n, k: Check(n, Verdict.PASS, True, 'mutant') if n == 'freeze_authority' else o(sec, n, k)",
     f"{SAFE}::test_L16_a_held_freeze_authority_is_a_hard_veto"),
    ("cannot_sell_ignored", "L16 honeypot: token cannot be sold",
     "import jinx.intel.safety as s; o = s._honeypot; s._honeypot = lambda sec: o({**sec, 'can_not_sell': 0})",
     f"{SAFE}::test_L16_a_token_that_cannot_be_sold_is_a_hard_veto"),
    ("rugcheck_rugged_ignored", "L16 RugCheck rugged (B-19)",
     "import jinx.adapters.rugcheck.client as c; o = c.interpret; c.interpret = lambda r: o({**r, 'rugged': False}) "
     "if isinstance(r, dict) and r.get('rugged') is True else o(r)",
     f"{SAFE}::test_L16_rugchecks_own_hard_findings_are_a_hard_veto[change0]"),
    ("reconnect_gap_ignored", "B-02 s7 history incomplete after a feed gap",
     "import jinx.intel.early_momentum as m; m.EarlyMomentum.mark_feed_gap = lambda self, gap_ns, reason: None",
     f"{OUT}::test_a_pumpapi_reconnect_makes_every_tracked_history_incomplete_and_blocks_entry"),
    ("migration_lifecycle_ignored", "B-02 s6 migrated token outside this path",
     "import jinx.intel.migration as m; m.MigrationTracker.lifecycle = lambda self, mint: None",
     f"{PRED}::test_B02_a_migrated_token_is_outside_this_entry_path"),
    ("decision_recheck_ignores_feed", "L79 live curve read at the decision",
     "import jinx.intel.early_momentum as m; o = m.EarlyMomentum.reserve_check; "
     "m.EarlyMomentum.reserve_check = lambda self, mint, feed_live: o(self, mint, True)",
     f"{PRED}::test_L79_without_a_live_trade_feed_the_reserve_cannot_be_confirmed"),
    ("recovery_skips_positions", "V09 restart recovery",
     "import jinx.recovery as r; from jinx.recovery import RecoveryReport; "
     "r.recover = lambda store, core, ledger, wallet, bus=None: RecoveryReport(core.apply_pending(), store.max_seq(), "
     "0, 0, len(ledger.active()), wallet.reconcile().ok)",
     f"{REC}::test_hard_kill_with_an_open_position_a_pending_exit_and_an_unfilled_buy"),
    ("wallet_entry_off_by_one", "V12 every lamport reconciles",
     "import jinx.paper as p; o = p.PaperWallet.apply; "
     "p.PaperWallet.apply = lambda self, eid, delta, reason, ts_ns=None: o(self, eid, delta - 1 if delta < 0 else delta, "
     "reason, ts_ns)",
     f"{ORD}::test_every_lamport_reconciles_over_randomized_round_trips_on_real_reserves"),
    ("two_slots_by_default", "owner rule: one slot by default",
     "import jinx.ledger as l; l.DEFAULT_SLOTS = 2",
     f"{STR}::test_a_burst_of_entry_intents_never_holds_more_positions_than_the_owner_slots[1]"),
    ("dedup_off", "V03/V11 duplicates counted once",
     "import jinx.core.dedup as d; d.Deduplicator.first_time = lambda self, key: True; "
     "import jinx.store as st; o = st.Store.record_event; st.Store.record_event = lambda self, ev: (o(self, ev), True)[1]",
     f"{REP}::test_shuffled_and_resent_delivery_gives_the_identical_state[1-pumpapi-live]"),
]


def run(mid: str, patch: str, test: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="jinx-p4-mut-") as d:
        plugin = Path(d) / f"p4mut_{mid}.py"
        plugin.write_text(textwrap.dedent(f"""
            def pytest_configure(config):
                {patch}
        """))
        env = dict(os.environ, PYTHONPATH=f"{d}{os.pathsep}{ROOT}")
        r = subprocess.run([PY, "-m", "pytest", "-p", "no:cacheprovider", "-rf", "-p", f"p4mut_{mid}", test],
                           cwd=ROOT, env=env, capture_output=True, text=True)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    # caught = the named test itself failed on an assertion (not a collection error, not another test)
    failed_line = next((l for l in r.stdout.splitlines() if l.startswith(f"FAILED {test}")), "")
    caught = r.returncode == 1 and bool(failed_line) and " error" not in tail and "Error" not in failed_line.replace(
        "AssertionError", "")
    return {"exit_code": r.returncode, "caught": caught, "pytest": tail, "failed": failed_line[:300]}


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
    targets = sorted({t.split("::")[0] for *_, t in MUTATIONS})
    clean = subprocess.run([PY, "-m", "pytest", "-p", "no:cacheprovider", *targets], cwd=ROOT,
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
