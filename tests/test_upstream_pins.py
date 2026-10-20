"""The four upstreams are pinned, license-checked and untouched."""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("upstream_sync", ROOT / "scripts" / "upstream_sync.py")
upstream_sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream_sync)

EXPECTED = {"gmgn", "mako", "d3ad", "datanomad"}


def test_pins_cover_exactly_the_four_approved_repos():
    pins = upstream_sync.load_pins()
    assert set(pins) == EXPECTED
    for name, pin in pins.items():
        assert len(pin["commit"]) == 40, f"{name} must pin a full commit SHA"
        if pin["license"] == "NONE":
            assert pin["reuse"] == "reference-only", "unlicensed code must stay reference-only"
        else:
            assert pin["reuse"] == "code" and pin["license_sha256"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_synced_checkout_matches_its_pin(name):
    pins = upstream_sync.load_pins()
    assert upstream_sync.verify(name, pins[name]) == []


def _fake_upstream(tmp_path: Path) -> tuple[Path, dict]:
    repo = tmp_path / "upstream" / "fake"
    repo.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("init", "-q")
    (repo / "LICENSE").write_text("MIT License\n")
    (repo / "code.py").write_text("x = 1\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    pin = {"commit": sha, "license_file": "LICENSE",
           "license_sha256": hashlib.sha256(b"MIT License\n").hexdigest()}
    return repo, pin


def test_verify_detects_modified_upstream_file(tmp_path, monkeypatch):
    repo, pin = _fake_upstream(tmp_path)
    monkeypatch.setattr(upstream_sync, "UPSTREAM_DIR", tmp_path / "upstream")
    assert upstream_sync.verify("fake", pin) == []
    (repo / "code.py").write_text("x = 2\n")
    assert any("modified" in p for p in upstream_sync.verify("fake", pin))


def test_verify_detects_wrong_commit_and_changed_license(tmp_path, monkeypatch):
    repo, pin = _fake_upstream(tmp_path)
    monkeypatch.setattr(upstream_sync, "UPSTREAM_DIR", tmp_path / "upstream")
    wrong = dict(pin, commit="0" * 40)
    assert any("pinned" in p for p in upstream_sync.verify("fake", wrong))
    changed = dict(pin, license_sha256="0" * 64)
    assert any("license" in p for p in upstream_sync.verify("fake", changed))
