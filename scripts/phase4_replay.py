"""Phase 4 V03: deterministic replay of captured real-market events through the actual JINX pipeline.

    .venv/bin/python scripts/phase4_replay.py tests/fixtures/pumpapi-live-20261021.jsonl.gz:pumpapi \\
        tests/fixtures/pumpportal-live-20261020.jsonl.gz:pumpportal --seeds 1 7 20261021 \\
        --out docs/evidence/PHASE4_REPLAY_20261021.json

For each capture: one in-order replay, then one replay per seed with every 5th record resent and the delivery
shuffled. Each run goes through `JinxApp._build_core` (tests/phase4/harness.py) in a temporary directory that is
deleted afterwards. The evidence holds digests, counts and timings only (no raw market data). A capture of any
size can be given; nothing is copied.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jinx.sim import load_capture  # noqa: E402
from tests.phase4 import harness as h  # noqa: E402


def one_run(records, source, **kw) -> dict:
    d = Path(tempfile.mkdtemp(prefix="jinx-p4-replay-"))
    try:
        app = h.build_app(d)
        t0 = time.perf_counter()
        stats = h.run(app, h.deliveries(records, source, **kw))
        elapsed = time.perf_counter() - t0
        view = h.state_view(app)
        out = {"digest": h.state_digest(app), "published": stats.published, "duplicates": stats.duplicates,
               "refused_records": stats.refused_records, "handler_errors": stats.handler_errors,
               "elapsed_s": round(elapsed, 3), "tokens": len(app.core.tokens),
               "detections": len(view["early_momentum"]), "graduation_curves": len(view["graduation"]),
               "migrations": len(view["migration"]), "paper_wallet_reconciled": view["paper_wallet"]["ok"]}
        h.close(app)
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="+", help="path:source (source = pumpapi or pumpportal)")
    ap.add_argument("--seeds", nargs="*", type=int, default=[1, 7, 20261021])
    ap.add_argument("--duplicate-every", type=int, default=5)
    ap.add_argument("--out")
    args = ap.parse_args()
    report = {"what": "Phase 4 V03 deterministic replay through JinxApp._build_core (bus, dedup, durable write, "
                      "core state, every intel module, ledger, paper wallet)",
              "excluded_fields": sorted(h.VOLATILE_KEYS),
              "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
              "host": platform.node(), "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "captures": {}}
    ok = True
    for spec in args.captures:
        path, source = spec.rsplit(":", 1)
        records = load_capture(Path(path))
        base = one_run(records, source)
        runs = [{"seed": s, "duplicate_every": args.duplicate_every,
                 **one_run(records, source, shuffle_seed=s, duplicate_every=args.duplicate_every)}
                for s in args.seeds]
        same = all(r["digest"] == base["digest"] for r in runs)
        ok &= same and not base["handler_errors"]
        report["captures"][Path(path).name] = {"records": len(records), "source": source, "in_order": base,
                                               "shuffled_resent": runs, "identical_digest": same}
    report["result"] = "PASS" if ok else "FAIL"
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
