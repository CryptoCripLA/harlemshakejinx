"""DataNomad research adapter. The pinned upstream has no license file, so its
code may be read for methodology but never loaded into or shipped with JINX."""
from __future__ import annotations

from pathlib import Path


class LicenseNotGranted(PermissionError):
    pass


class DataNomadResearchAdapter:
    name = "datanomad"

    def __init__(self, upstream_root: Path) -> None:
        self.root = Path(upstream_root)

    def health(self) -> dict:
        return {"adapter": self.name, "pinned": (self.root / ".git").exists(), "license": "NONE",
                "reuse": "reference-only"}

    def load_code(self, module: str):
        raise LicenseNotGranted(f"cannot load {module}: no license at the pinned commit; reference only")
