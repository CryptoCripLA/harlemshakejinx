"""BigMac's exit port: Ms-PL (not MIT), the original file and license vendored at the pinned commit."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = json.loads((ROOT / "third_party" / "bigmac" / "SOURCE.json").read_text())
PORT = ROOT / "jinx" / "exits" / "bigmac_exit.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_vendored_license_and_file_match_the_pinned_upstream():
    assert SRC["commit"] == "829f251d4e371102b406176c5bd459ebf4ac87ac"
    assert _sha(ROOT / SRC["license_file"]) == SRC["license_sha256"]
    for rel, digest in SRC["files"].items():
        assert _sha(ROOT / "third_party" / "bigmac" / rel) == digest
    assert (ROOT / SRC["license_file"]).read_text().startswith("Microsoft Public License (Ms-PL)")


def test_port_declares_ms_pl_not_mit_and_names_its_source():
    head = "\n".join(PORT.read_text().splitlines()[:20])
    assert "Ms-PL" in head and "MIT." not in head and "# MIT" not in head
    assert "829f251d4e371102b406176c5bd459ebf4ac87ac" in head and "third_party/bigmac/LICENSE.md" in head
    assert "Portions translated from" in head
    assert "SRC: bigmac@829f251d helpers/exit-strategy.ts:107-114" in PORT.read_text()
    assert "SRC: bigmac@829f251d helpers/exit-strategy.ts:15-47" in PORT.read_text()


def test_notices_list_the_port_and_the_timeout_exit_is_recorded_as_not_ported():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    assert "jinx/exits/bigmac_exit.py" in notices and "Ms-PL" in notices
    assert "bigmac" in (ROOT / "docs" / "LICENSES.md").read_text().lower()
    assert "waitForExitCondition" in SRC["not_ported"]
    assert "waitForExitCondition" not in PORT.read_text().split("# The ExitIntent")[1]
