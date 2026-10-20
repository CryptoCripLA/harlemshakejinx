"""Load Mako's upstream modules (unmodified) without letting them leak into the process.

Mako's src/config.py calls python-dotenv's load_dotenv() at import time, which
would copy a .env file into this process's environment, and both modules use
bare top-level names ("config", "pumpportal") that could collide with other
code. The loader neutralizes load_dotenv for the duration of the import,
restores sys.path, and re-registers the modules under private names.
"""
from __future__ import annotations

import importlib
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

_BARE = ("config", "pumpportal")
_lock = threading.Lock()
_cache: dict[Path, "MakoModules"] = {}


@dataclass(frozen=True)
class MakoModules:
    config: ModuleType
    pumpportal: ModuleType
    default_ws_url: str


def load_mako(upstream_root: Path) -> MakoModules:
    src = (Path(upstream_root) / "src").resolve()
    with _lock:
        if src in _cache:
            return _cache[src]
        import dotenv

        saved_load = dotenv.load_dotenv
        saved_path = list(sys.path)
        saved_bare = {k: sys.modules.pop(k) for k in _BARE if k in sys.modules}
        try:
            dotenv.load_dotenv = lambda *a, **k: False  # Mako config.py:8 runs load_dotenv() on import
            sys.path.insert(0, str(src))
            config = importlib.import_module("config")
            pumpportal = importlib.import_module("pumpportal")
        finally:
            dotenv.load_dotenv = saved_load
            sys.path[:] = saved_path
            for k in _BARE:
                sys.modules.pop(k, None)
            sys.modules.update(saved_bare)
        sys.modules["jinx_upstream_mako.config"] = config
        sys.modules["jinx_upstream_mako.pumpportal"] = pumpportal
        mods = MakoModules(config, pumpportal, pumpportal.PUMPPORTAL_WS_URL)
        _cache[src] = mods
        return mods
