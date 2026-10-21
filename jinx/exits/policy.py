"""Paper exit policies assigned to a position at entry.

BIGMAC_PAPER (owner order 2026-10-21): BigMac's evaluate_exit with explicitly configured paper settings. There are no
default numbers: until the owner sets them (python -m jinx.exits.configure set-bigmac ...), the policy is inactive and
positions get no BigMac exit (blocker B-30). Tests use settings labelled TEST_FIXTURE_*. The settings are frozen into
each position at entry, so a later change never silently alters an open position. Paper only: the monitor never
applies this policy to a live position, and the gateway refuses it there too.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from jinx.store import Store

BIGMAC_PAPER = "BIGMAC_PAPER"
TEST_FIXTURE_PREFIX = "TEST_FIXTURE"
SETTING_KEY = "exit_policy.bigmac_paper"


@dataclass(frozen=True)
class BigMacPaperConfig:
    """BigMac's four percentages (exit-strategy.ts ExitStrategyConfig: takeProfitPct, stopLossPct, trailingStopPct,
    trailingStopActivationPct). Its intervalMs/durationMs belong to the timeout loop, which is not ported."""
    take_profit_pct: float
    stop_loss_pct: float
    trailing_stop_pct: float
    trailing_stop_activation_pct: float
    label: str = BIGMAC_PAPER
    set_by: str = "unknown"

    def __post_init__(self) -> None:
        for name in ("take_profit_pct", "stop_loss_pct", "trailing_stop_pct", "trailing_stop_activation_pct"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise TypeError(f"{name} must be a number, got {v!r}")
            if not math.isfinite(v):
                raise ValueError(f"{name} must be finite, got {v!r}")
        if self.label != BIGMAC_PAPER and not self.label.startswith(TEST_FIXTURE_PREFIX):
            raise ValueError(f"label must be {BIGMAC_PAPER} or start with {TEST_FIXTURE_PREFIX}, got {self.label!r}")

    def to_dict(self) -> dict:
        return {"policy": BIGMAC_PAPER, **asdict(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "BigMacPaperConfig":
        if d.get("policy") != BIGMAC_PAPER:
            raise ValueError(f"not a {BIGMAC_PAPER} config: {d.get('policy')!r}")
        return cls(d["take_profit_pct"], d["stop_loss_pct"], d["trailing_stop_pct"],
                   d["trailing_stop_activation_pct"], d["label"], d.get("set_by", "unknown"))


def save_bigmac_config(store: Store, cfg: BigMacPaperConfig) -> None:
    store.set_setting(SETTING_KEY, json.dumps(cfg.to_dict(), sort_keys=True), by=cfg.set_by)


def load_bigmac_config(store: Store) -> BigMacPaperConfig | None:
    """The configured paper settings, or None (not configured, or unreadable: fail closed, no BigMac exits)."""
    raw = store.get_setting(SETTING_KEY)
    if raw is None:
        return None
    try:
        return BigMacPaperConfig.from_dict(json.loads(raw))
    except (ValueError, TypeError, KeyError):
        return None
