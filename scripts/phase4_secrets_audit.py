"""Phase 4 V18: secrets and private-key audit of the working tree AND every blob in the git history.

    .venv/bin/python scripts/phase4_secrets_audit.py --out docs/evidence/PHASE4_SECRETS_AUDIT_20261021.json

Checks every blob reachable from any ref (all branches, remotes, tags) plus every committable file, once each, for:
- the credential shapes of tests/test_secrets.py (GMGN key, PEM private key body, GitHub token, database URL with a
  password, Neon keys);
- the exact values configured on this host (~/.config/gmgn/.env, the JINX service env file) — reported by NAME only;
- Solana keypairs: any 87-88 character base58 string or 64-number JSON array whose second half is the ed25519 public
  key of its first half (a transaction signature has the same shape but fails this check, so fixtures do not
  false-positive).
No value is ever printed or written: findings carry the blob, the path and the kind.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_secrets import PATTERNS  # noqa: E402

B58 = re.compile(rb"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{86,88}(?![1-9A-HJ-NP-Za-km-z])")
ARR = re.compile(rb"\[\s*(?:\d{1,3}\s*,\s*){63}\d{1,3}\s*\]")
ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: bytes) -> bytes | None:
    n = 0
    for c in s:
        i = ALPHABET.find(bytes([c]))
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip(b"1"))
    return b"\0" * pad + raw


def is_solana_keypair(secret: bytes) -> bool:
    if len(secret) != 64:
        return False
    from solders.keypair import Keypair
    try:
        return bytes(Keypair.from_seed(secret[:32]).pubkey()) == secret[32:]
    except Exception:
        return False


def configured_secrets() -> list[tuple[str, bytes]]:
    out = []
    gmgn = Path.home() / ".config" / "gmgn" / ".env"
    files = [gmgn] + [Path(p) for p in (os.environ.get("JINX_ENV_FILE"), "/etc/jinx/jinx.env",
                                         str(Path.home() / ".config" / "jinx" / "jinx.env"),
                                         str(Path.home() / ".config" / "jinx" / "railway.env")) if p]
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if line.lstrip().startswith("#") or len(value) <= 16:
                continue
            if not any(t in key for t in ("SECRET", "TOKEN", "KEY", "PASSWORD", "DATABASE_URL", "REDIS_URL")):
                continue
            body = "".join(p for p in value.replace("\\n", "\n").splitlines() if "-----" not in p) or value
            out.append((f"{f.name}:{key.strip()}", body.encode()))
    return out


def scan(data: bytes, secrets) -> list[str]:
    kinds = [name for name, rx in PATTERNS.items() if rx.search(data)]
    kinds += [f"configured value {name}" for name, v in secrets if v in data]
    for m in B58.finditer(data):
        if is_solana_keypair(b58decode(m.group(0)) or b""):
            kinds.append("Solana keypair (base58)")
            break
    for m in ARR.finditer(data):
        nums = [int(x) for x in re.findall(rb"\d+", m.group(0))]
        if all(n < 256 for n in nums) and is_solana_keypair(bytes(nums)):
            kinds.append("Solana keypair (JSON array)")
            break
    return kinds


def git(*args) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    secrets = configured_secrets()
    names = {}
    for line in git("rev-list", "--all", "--objects").decode().splitlines():
        sha, _, path = line.partition(" ")
        if path:
            names.setdefault(sha, path)
    batch = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, input="\n".join(names).encode() + b"\n",
                           capture_output=True, check=True).stdout
    findings, blobs, pos = [], 0, 0
    while pos < len(batch):
        header_end = batch.index(b"\n", pos)
        sha, kind, size = batch[pos:header_end].decode().split()
        size = int(size)
        data = batch[header_end + 1: header_end + 1 + size]
        pos = header_end + 1 + size + 1
        if kind != "blob":
            continue
        blobs += 1
        for k in scan(data, secrets):
            findings.append({"where": "history", "blob": sha, "path": names[sha], "kind": k})
    tree = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
    files = 0
    for p in tree:
        f = ROOT / p.decode() if p else None
        if f is None or not f.is_file():
            continue
        files += 1
        for k in scan(f.read_bytes(), secrets):
            findings.append({"where": "working tree", "path": p.decode(), "kind": k})
    report = {"utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "commit": git("rev-parse", "HEAD").decode().strip(),
              "refs_scanned": git("for-each-ref", "--format=%(refname)").decode().split(),
              "commits": len(git("rev-list", "--all").decode().split()), "history_blobs": blobs,
              "working_tree_files": files, "configured_values_checked": [n for n, _ in secrets],
              "patterns": sorted(PATTERNS) + ["Solana keypair (base58)", "Solana keypair (JSON array)"],
              "findings": findings, "result": "PASS" if not findings else "FAIL"}
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    sys.exit(0 if not findings else 1)


if __name__ == "__main__":
    main()
