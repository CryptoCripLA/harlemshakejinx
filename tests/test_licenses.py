"""Code translated from an upstream ships with that upstream's license and a change notice (Codex round 1, #7)."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINS = json.loads((ROOT / "UPSTREAM_PINS.json").read_text())["repos"]
TRANSLATED = {"d3ad": ROOT / "jinx" / "paper.py", "gmgn": ROOT / "jinx" / "adapters" / "gmgn" / "bridge.mjs"}


def test_translated_upstreams_ship_their_exact_license_file():
    for name in TRANSLATED:
        lic = ROOT / "third_party" / name / "LICENSE"
        assert lic.exists(), f"missing {lic}"
        assert hashlib.sha256(lic.read_bytes()).hexdigest() == PINS[name]["license_sha256"]


def test_every_file_with_translated_code_carries_a_notice_and_is_listed():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    for name, path in TRANSLATED.items():
        text = path.read_text()
        assert f"SRC: {'d3ad' if name == 'd3ad' else 'gmgn-skills'}" in text
        assert "Portions translated from" in text, f"{path.name} lacks a change notice"
        assert str(path.relative_to(ROOT)) in notices


def test_no_other_jinx_file_claims_upstream_source_without_a_notice():
    for path in (ROOT / "jinx").rglob("*"):
        if not (path.is_file() and path.suffix in (".py", ".mjs")):
            continue
        text = path.read_text()
        if "# SRC:" in text or "// SRC:" in text:
            assert path in TRANSLATED.values(), f"{path} contains translated code but is not in the notice list"


def test_docker_image_includes_the_notices_and_licenses():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "THIRD_PARTY_NOTICES.md" in dockerfile and "third_party" in dockerfile
