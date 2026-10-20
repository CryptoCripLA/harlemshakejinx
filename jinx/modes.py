"""Operating modes, exactly as the owner defined them: AUTO / COPY / OFF.

AUTO: JINX-generated eligible entries.
COPY: eligible tracked-wallet activity routed through JINX's safety and execution policy.
OFF:  no new automated entries; existing positions are still managed and exited.

The mode lives in the store, defaults to OFF, and any unreadable value is
treated as OFF (fail closed). Execution is PAPER unless the owner has armed
live trading; Phase 1 has no live execution path at all.
"""
from __future__ import annotations

from enum import StrEnum

from jinx.store import Store

MODE_KEY = "mode"
EXECUTION_KEY = "execution_mode"


class Mode(StrEnum):
    OFF = "OFF"
    AUTO = "AUTO"
    COPY = "COPY"


class ExecutionMode(StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"


def get_mode(store: Store) -> Mode:
    try:
        return Mode(store.get_setting(MODE_KEY) or Mode.OFF)
    except ValueError:
        return Mode.OFF


def set_mode(store: Store, mode: Mode, by: str = "owner") -> None:
    store.set_setting(MODE_KEY, Mode(mode).value, by=by)


def entries_allowed(mode: Mode) -> bool:
    return mode in (Mode.AUTO, Mode.COPY)


def get_execution_mode(store: Store) -> ExecutionMode:
    try:
        return ExecutionMode(store.get_setting(EXECUTION_KEY) or ExecutionMode.PAPER)
    except ValueError:
        return ExecutionMode.PAPER
