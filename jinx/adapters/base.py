"""Shared adapter plumbing. Adapters depend on JINX core; core never depends on adapters,
and no adapter depends on another."""
from __future__ import annotations

from pathlib import Path


def state_dir(data_root: Path, adapter_name: str) -> Path:
    """Each adapter keeps any private state in its own directory."""
    return Path(data_root) / "adapters" / adapter_name
