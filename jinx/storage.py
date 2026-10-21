"""Storage footprint monitoring and the daily clean-up (owner storage requirement 2026-10-21, rules 9 and 10:
"Expire disposable session data and clear unnecessary temporary records" and "Track actual RAM, disk, PostgreSQL,
and Redis consumption. Detect abnormal growth before capacity is exhausted").

`StorageMonitor` keeps a bounded series of readings per component (RAM, disk, filesystem, PostgreSQL, Redis, object
storage). Each component has a byte budget. Status: `critical` at 95% of budget or when its recent growth rate would
reach the budget within 2 hours; `warn` at 80% or within 24 hours; otherwise `ok`. Growth is measured over at least
10 minutes of readings so a single burst is not extrapolated.

`DailyRun` says when the end-of-day clean-up is due: once per UTC day, and at start-up.
"""
from __future__ import annotations

import os
import shutil
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

MB = 1024 * 1024
WARN, CRITICAL = 0.80, 0.95
MIN_GROWTH_WINDOW_S = 600
RANK = {"ok": 0, "warn": 1, "critical": 2}


def process_rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def tree_bytes(root: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(root):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def filesystem(root: Path) -> dict:
    u = shutil.disk_usage(root)
    return {"total_bytes": u.total, "used_bytes": u.used, "free_bytes": u.free}


class StorageMonitor:
    def __init__(self, budgets_bytes: dict[str, int], window: int = 1440) -> None:
        self.budgets = dict(budgets_bytes)
        self._series: dict[str, deque[tuple[float, int]]] = {}
        self._detail: dict[str, dict] = {}
        self.window = window

    def record(self, name: str, used_bytes: int | None, now_s: float, detail: dict | None = None,
               budget_bytes: int | None = None) -> None:
        if budget_bytes is not None:
            self.budgets[name] = budget_bytes
        self._detail[name] = detail or {}
        if used_bytes is None:
            return
        self._series.setdefault(name, deque(maxlen=self.window)).append((now_s, int(used_bytes)))

    def reading(self, name: str) -> dict:
        series = self._series.get(name)
        budget = self.budgets.get(name)
        if not series:
            return {"used_bytes": None, "budget_bytes": budget, "status": "unknown", **self._detail.get(name, {})}
        t1, used = series[-1]
        growth = hours = None
        t0, first = series[0]
        if t1 - t0 >= MIN_GROWTH_WINDOW_S:
            growth = (used - first) / (t1 - t0) * 3600          # bytes per hour
            if budget and growth > 0:
                hours = max(0.0, (budget - used) / growth)
        status = "ok"
        if budget:
            share = used / budget
            if share >= CRITICAL or (hours is not None and hours < 2):
                status = "critical"
            elif share >= WARN or (hours is not None and hours < 24):
                status = "warn"
        return {"used_bytes": used, "budget_bytes": budget, "used_pct": round(100 * used / budget, 1) if budget else None,
                "growth_bytes_per_h": None if growth is None else round(growth),
                "hours_to_budget": None if hours is None else round(hours, 1), "status": status,
                **self._detail.get(name, {})}

    def report(self) -> dict:
        components = {name: self.reading(name) for name in sorted(set(self._series) | set(self._detail))}
        worst = max((RANK.get(c["status"], 0) for c in components.values()), default=0)
        return {"status": next(k for k, v in RANK.items() if v == worst), "components": components}


class DailyRun:
    """Due at start-up and then once per UTC calendar day."""

    def __init__(self) -> None:
        self.last_day: str | None = None
        self.last_report: dict | None = None

    @staticmethod
    def day(now_ns: int) -> str:
        return datetime.fromtimestamp(now_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")

    def due(self, now_ns: int) -> bool:
        return self.day(now_ns) != self.last_day

    def done(self, now_ns: int, report: dict) -> None:
        self.last_day, self.last_report = self.day(now_ns), report
