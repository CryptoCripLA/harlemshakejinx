"""D3AD-E execution adapter. Pinned and isolated in Phase 1; wired as the live
execution path in Phase 3. It executes JINX decisions and never sets strategy."""
from __future__ import annotations

from pathlib import Path

from jinx.execution import LiveExecutionUnavailable


class D3adExecutionAdapter:
    name = "d3ad"

    def __init__(self, upstream_root: Path) -> None:
        self.root = Path(upstream_root)

    def health(self) -> dict:
        return {"adapter": self.name, "pinned": (self.root / ".git").exists(), "integrated": False,
                "wired_in_phase": 3}

    def submit(self, order) -> None:
        raise LiveExecutionUnavailable("D3AD execution is wired in Phase 3; Phase 1 is paper only")
