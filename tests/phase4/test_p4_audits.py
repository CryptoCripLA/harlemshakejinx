"""V18 secrets and private keys (working tree and full git history); V19 dependency licenses.

Both audits are scripts so the evidence can be regenerated at any commit (scripts/phase4_secrets_audit.py,
scripts/phase4_license_audit.py); these tests run them and pin the detectors so they cannot silently go blind."""
import json
import subprocess
import sys

import base58
from solders.keypair import Keypair

from tests.phase4 import harness as h

ROOT = h.FIXTURES.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import phase4_secrets_audit as secrets_audit  # noqa: E402


def test_the_keypair_detector_finds_a_real_solana_keypair_in_both_encodings():
    kp = bytes(Keypair())
    as_b58 = b"secret=" + base58.b58encode(kp) + b"\n"
    as_json = json.dumps(list(kp)).encode()
    assert "Solana keypair (base58)" in secrets_audit.scan(as_b58, [])
    assert "Solana keypair (JSON array)" in secrets_audit.scan(as_json, [])


def test_the_keypair_detector_ignores_transaction_signatures_of_the_same_shape():
    sigs = [json.loads(l)["raw"]["signature"] for l in __import__("gzip").open(
        h.FIXTURES / "pumpapi-live-20261021.jsonl.gz", "rt")][:200]
    assert all(86 <= len(s) <= 88 for s in sigs)
    assert secrets_audit.scan(" ".join(sigs).encode(), []) == []


def test_a_credential_inside_a_gzip_capture_is_found():
    import gzip
    secret = b"postgresql://jinx:" + b"p4-not-a-real-password" + b"@db.example/jinx"   # split: no shape in this file
    assert secrets_audit.scan(secret, []) == ["database URL with a password"]
    assert secrets_audit.scan(gzip.compress(b'{"raw": "' + secret + b'"}'), []) == [
        "database URL with a password (inside gzip)"]
    kp = bytes(Keypair())
    assert "Solana keypair (base58) (inside gzip)" in secrets_audit.scan(gzip.compress(base58.b58encode(kp)), [])


def test_a_configured_value_is_found_and_reported_by_name_only():
    found = secrets_audit.scan(b"x=" + b"p4-not-a-real-secret-value-123" + b"\n",
                               [("jinx.env:TEST_TOKEN", b"p4-not-a-real-secret-value-123")])
    assert found == ["configured value jinx.env:TEST_TOKEN"]


def test_no_secret_or_private_key_in_the_tree_or_any_commit_of_any_ref():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "phase4_secrets_audit.py")], cwd=ROOT,
                       capture_output=True, text=True)
    report = json.loads(r.stdout)
    assert r.returncode == 0 and report["findings"] == [], report["findings"]
    assert report["history_blobs"] > 100 and report["commits"] > 50


def test_every_dependency_license_is_known_and_none_is_strong_copyleft():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "phase4_license_audit.py")], cwd=ROOT,
                       capture_output=True, text=True)
    report = json.loads(r.stdout)
    assert r.returncode == 0 and report["problems"] == [], report["problems"]
    assert all(u.get("license_sha256_matches_pin", u.get("verified")) for u in report["upstreams"].values())
