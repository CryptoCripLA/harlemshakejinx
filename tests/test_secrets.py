"""No credential may enter the public repository (owner mandate 2026-10-20 §1; docs/SECRETS.md).

Scans every file git would commit (tracked plus untracked, not ignored). Checks for credential-shaped strings,
and for the exact values configured on this host in ~/.config/gmgn/.env, without ever printing them."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "GMGN API key": re.compile(rb"gmgn_[0-9a-fA-F]{24,}"),
    "PEM private key body": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:\\n|\s)*[A-Za-z0-9+/=]{40,}"),
    "GitHub token": re.compile(rb"(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,})"),
}


def committable_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout
    return [ROOT / p.decode() for p in out.split(b"\0") if p and (ROOT / p.decode()).is_file()]


def find(patterns: dict, data: bytes) -> list[str]:
    return [name for name, rx in patterns.items() if rx.search(data)]


def test_the_scanner_recognizes_each_credential_shape():
    assert find(PATTERNS, b"key=gmgn_" + b"0123456789abcdef" * 2) == ["GMGN API key"]
    assert find(PATTERNS, b"-----BEGIN PRIVATE KEY-----\\n" + b"A" * 64) == ["PEM private key body"]
    assert find(PATTERNS, b"github_pat_" + b"x" * 40) == ["GitHub token"]
    assert find(PATTERNS, b"gmgn_<REDACTED> and -----BEGIN PRIVATE KEY-----\\nX\\n") == []


def test_no_committable_file_contains_a_credential_shape():
    hits = [(str(p.relative_to(ROOT)), name) for p in committable_files() for name in find(PATTERNS, p.read_bytes())]
    assert hits == []


def test_no_committable_file_contains_the_configured_gmgn_secrets():
    env = Path.home() / ".config" / "gmgn" / ".env"
    if not env.exists():
        pytest.skip("no GMGN credentials configured on this host")
    secrets = []
    for line in env.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("GMGN_API_KEY", "GMGN_PRIVATE_KEY") and len(value.strip()) > 16:
            value = value.strip().strip('"').strip("'")
            # the PEM body is the secret part; header lines are public boilerplate
            body = "".join(part for part in value.replace("\\n", "\n").splitlines() if "-----" not in part)
            secrets.append((key.strip(), (body or value).encode()))
    assert secrets, "credentials file has no GMGN values"
    leaked = [(str(p.relative_to(ROOT)), name) for p in committable_files() for name, s in secrets
              if s in p.read_bytes()]
    assert leaked == []   # names only: the values are never printed
