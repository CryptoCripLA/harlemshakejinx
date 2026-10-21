"""Replay a PumpAPI capture through the Phase 2 event intel and write the measured summary (no raw data).

    .venv/bin/python scripts/intel_replay_report.py tests/fixtures/pumpapi-live-20261021.jsonl.gz \\
        --out docs/evidence/phase2_event_intel_replay_20261021.json

Timings are local processing inside JINX plus the receipt lag recorded in the capture; not order execution.
"""
from __future__ import annotations

import argparse
import json
import platform
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jinx.intel.replay import replay_events  # noqa: E402
from jinx.sim import capture_events, load_capture  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--out")
    args = ap.parse_args()
    records = load_capture(Path(args.capture))
    run = replay_events(capture_events(records, source="pumpapi"))
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    report = {"capture": Path(args.capture).name, "records": len(records), "commit": commit,
              "host": socket.gethostname(), "python": platform.python_version(),
              "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **run.report()}
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
