#!/usr/bin/env python3
"""Fetch the four pinned upstream repositories into upstream/<name>/ and verify them.

Usage:
    python scripts/upstream_sync.py            # sync all four
    python scripts/upstream_sync.py gmgn mako  # sync only the named repos
    python scripts/upstream_sync.py --verify   # verify without fetching

Each repo is fetched at exactly the commit recorded in UPSTREAM_PINS.json
(shallow fetch by SHA). Verification fails if HEAD differs from the pin, if
tracked files were modified, or if the license file no longer matches its
recorded hash.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINS_PATH = ROOT / "UPSTREAM_PINS.json"
UPSTREAM_DIR = ROOT / "upstream"


def load_pins() -> dict:
    return json.loads(PINS_PATH.read_text())["repos"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def sync(name: str, pin: dict) -> None:
    dest = UPSTREAM_DIR / name
    if not (dest / ".git").exists():
        dest.mkdir(parents=True, exist_ok=True)
        _git(dest, "init", "-q")
        _git(dest, "remote", "add", "origin", pin["url"])
    if _head(dest) != pin["commit"]:
        _git(dest, "fetch", "-q", "--depth", "1", "origin", pin["commit"])
        _git(dest, "checkout", "-q", "--detach", pin["commit"])


def _head(repo: Path) -> str | None:
    try:
        return _git(repo, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        return None


def verify(name: str, pin: dict) -> list[str]:
    """Return a list of problems; empty means the checkout matches its pin."""
    dest = UPSTREAM_DIR / name
    if not (dest / ".git").exists():
        return [f"{name}: not synced (missing {dest})"]
    problems = []
    head = _head(dest)
    if head != pin["commit"]:
        problems.append(f"{name}: HEAD {head} != pinned {pin['commit']}")
    if _git(dest, "status", "--porcelain", "--untracked-files=no"):
        problems.append(f"{name}: tracked upstream files were modified")
    if pin["license_file"]:
        # Hash the committed bytes, not the working copy: git may check text out with CRLF (Windows).
        blob = subprocess.run(["git", "-C", str(dest), "show", f"HEAD:{pin['license_file']}"],
                              capture_output=True)
        if blob.returncode != 0:
            problems.append(f"{name}: license file {pin['license_file']} missing")
        elif hashlib.sha256(blob.stdout).hexdigest() != pin["license_sha256"]:
            problems.append(f"{name}: license file changed from the pinned hash")
    return problems


def main(argv: list[str]) -> int:
    pins = load_pins()
    verify_only = "--verify" in argv
    names = [a for a in argv if not a.startswith("--")] or list(pins)
    unknown = [n for n in names if n not in pins]
    if unknown:
        print(f"unknown upstream(s): {unknown}; known: {list(pins)}", file=sys.stderr)
        return 2
    problems: list[str] = []
    for name in names:
        if not verify_only:
            sync(name, pins[name])
        problems += verify(name, pins[name])
    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    if not problems:
        for name in names:
            print(f"OK {name} @ {pins[name]['commit'][:12]} ({pins[name]['license']})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
