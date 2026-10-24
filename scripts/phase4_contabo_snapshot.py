"""Phase 4 V01/V02: a READ-ONLY snapshot of the engine running on Contabo (no deploy, no restart, no write).

    .venv/bin/python scripts/phase4_contabo_snapshot.py --out docs/evidence/PHASE4_CONTABO_LIVE_<utc>.json

Reads `systemctl` state, the release symlink and `/health` over SSH with the deploy key, and keeps a summary: service
uptime and restarts, mode, slots, paper wallet, feed connections, event counts, Early Momentum detections and the
entry-check summary, background lookups, local latency, storage budget status and service failures. Wallet and mint
lists are dropped. The storage session is the only Contabo deployer; this script never changes anything there.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOST = os.environ.get("JINX_CONTABO", "ubuntu@13.140.224.208")
KEY = os.environ.get("JINX_SSH_KEY", str(Path.home() / ".ssh" / "jinx_contabo"))
REMOTE = ("systemctl is-active jinx; systemctl show jinx -p ActiveEnterTimestamp -p NRestarts --value; "
          "readlink /opt/jinx/current; echo; curl -s --max-time 10 localhost:8080/health")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    r = subprocess.run(["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, REMOTE],
                       capture_output=True, text=True, timeout=60)
    lines = r.stdout.splitlines()
    active, restarts, since, release = lines[0], lines[1], lines[2], lines[3]
    h = json.loads(lines[-1])
    i = h.get("intel", {})
    em = i.get("early_momentum", {})
    keep = ("connected", "received", "published", "duplicates", "malformed", "ingest_stalled", "connections",
            "disconnects", "stalls", "receipt_lag_ms")
    snap = {
        "read_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": "Contabo vmi3672752 (13.140.224.208)", "how": "read-only: ssh + systemctl + GET /health",
        "service": {"active": active, "restarts": int(restarts), "active_since": since, "release": release},
        "status": h.get("status"), "mode": h.get("mode"), "execution_mode": h.get("execution_mode"),
        "slots": h.get("slots"), "paper_wallet": h.get("paper_wallet"),
        "feeds": {a["adapter"]: {k: a.get(k) for k in keep if k in a}
                  for a in h.get("adapters", []) if a.get("adapter") in ("mako", "pumpapi.stream", "rugcheck")},
        "receive_to_state_ms_local": h.get("receive_to_state_ms"),
        "admission_to_state_ms_local": h.get("admission_to_state_ms"),
        "market_memory": {k: v for k, v in (h.get("market_memory") or {}).items() if not isinstance(v, (list, dict))},
        "early_momentum": {k: em.get(k) for k in ("events", "curve_buys", "detections", "listener_errors",
                                                  "candidates", "detection_delay_ms", "entry_summary", "screening")
                           if k in em},
        "safety": i.get("safety"), "background": i.get("background"),
        "trading": {k: v for k, v in (h.get("trading") or {}).items() if k not in ("latest_entries",)},
        "storage": {"status": (h.get("storage") or {}).get("status"),
                    "components": {k: {f: c.get(f) for f in ("used_bytes", "budget_bytes", "status",
                                                               "growth_bytes_per_h")}
                                   for k, c in (h.get("storage") or {}).get("components", {}).items()}},
        "services_failures": {k: v.get("failures") for k, v in (h.get("services") or {}).items()},
        "bus": {k: v for k, v in (h.get("bus") or {}).items() if not isinstance(v, (dict, list))},
    }
    if isinstance(snap["trading"].get("monitor"), dict):
        snap["trading"]["monitor"] = {k: v for k, v in snap["trading"]["monitor"].items() if k != "latest"}
    text = json.dumps(snap, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    sys.exit(0 if r.returncode == 0 else 1)


if __name__ == "__main__":
    main()
