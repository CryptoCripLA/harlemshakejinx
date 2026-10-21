"""JINX command line.

    python -m jinx run [--seconds N] [--capture data/captures/live.jsonl.gz]
    python -m jinx replay CAPTURE [--db PATH] [--shuffle-seed N] [--duplicate-every N]
                                  [--restart-at N] [--accounting-exercise]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from jinx import config


ENV_FILES = (Path("/etc/jinx/jinx.env"), Path.home() / ".config" / "jinx" / "jinx.env")


def load_env_file() -> Path | None:
    """Service credentials live in a private env file outside the repository (docs/SECRETS.md): JINX_ENV_FILE, else
    /etc/jinx/jinx.env (the execution server), else ~/.config/jinx/jinx.env. Variables already set win."""
    from dotenv import load_dotenv
    import os
    for path in ([Path(os.environ["JINX_ENV_FILE"])] if os.environ.get("JINX_ENV_FILE") else list(ENV_FILES)):
        if path.is_file():
            load_dotenv(path, override=False)
            return path
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jinx")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--seconds", type=float)
    r.add_argument("--capture", type=Path)
    r.add_argument("--data")
    r.add_argument("--port", type=int)
    rp = sub.add_parser("replay")
    rp.add_argument("capture", type=Path)
    rp.add_argument("--db", type=Path)
    rp.add_argument("--shuffle-seed", type=int)
    rp.add_argument("--duplicate-every", type=int)
    rp.add_argument("--restart-at", type=int)
    rp.add_argument("--accounting-exercise", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.cmd == "run":
        from jinx.app import run
        load_env_file()
        final = asyncio.run(run(config.load_settings(args.data, args.port), args.seconds, args.capture))
        print(json.dumps(final, default=str, indent=2))
        return 0 if final["status"] != "failed" else 1

    from jinx.sim import load_capture, replay
    records = load_capture(args.capture)
    with tempfile.TemporaryDirectory(prefix="jinx-replay-") as scratch:   # removed afterwards unless --db is given
        db = args.db or Path(scratch) / "replay.db"
        result = replay(records, db, shuffle_seed=args.shuffle_seed, duplicate_every=args.duplicate_every,
                        restart_at=args.restart_at, accounting_exercise=args.accounting_exercise)
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
