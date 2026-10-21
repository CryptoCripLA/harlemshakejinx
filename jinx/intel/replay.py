"""Replay captured events through the event-driven intel modules, deterministically, and measure them.

The caller supplies the deliveries (jinx/sim.py::capture_events normalizes a capture with the real adapter; each
record keeps its sequence numbers under reordering), so a shuffled or duplicated delivery is the same facts in
another arrival order. Timing:
- per-event processing: wall time spent in each module's on_event (perf_counter, measured now);
- detection delay: event time of the triggering trade (PumpAPI's timestamp) to evidence, i.e. the feed delay
  recorded in the capture (receipt time minus PumpAPI's timestamp) plus the measured processing.
Both are local processing inside JINX, not order execution or landing. Nothing is written to disk.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from jinx.core.events import MarketEvent
from jinx.intel.early_momentum import EarlyMomentum, EarlyMomentumEvidence
from jinx.intel.graduation import GraduationTracker


def percentiles(values) -> dict:
    v = sorted(values)
    if not v:
        return {"p50": None, "p95": None, "p99": None, "max": None, "n": 0}
    pick = (lambda q: v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))])
    return {"p50": pick(0.50), "p95": pick(0.95), "p99": pick(0.99), "max": v[-1], "n": len(v)}


@dataclass
class ReplayRun:
    momentum: list[EarlyMomentumEvidence] = field(default_factory=list)
    early_momentum: EarlyMomentum | None = None
    graduation: GraduationTracker | None = None
    curve_mints: set[str] = field(default_factory=set)
    processing_ns: dict[str, list[int]] = field(default_factory=dict)
    events: int = 0
    refused: int = 0

    def final_windows(self) -> dict[str, tuple[str, ...]]:
        out = {}
        for e in self.momentum:
            final = self.early_momentum.evidence(e.mint)
            out[e.mint] = tuple(b.signature for b in final.window)
        return out

    def graduation_latest(self) -> dict[str, tuple[str, int]]:
        """mint -> (signature of the latest curve reading, peak virtual SOL)."""
        out = {}
        for mint in self.curve_mints:
            e = self.graduation.evidence(mint)
            if e is not None:
                out[mint] = (e.latest.signature, e.peak_v_sol_lamports)
        return out

    def report(self) -> dict:
        em = self.momentum
        return {
            "events": self.events, "refused": self.refused,
            "per_event_processing_us": {name: percentiles([x / 1e3 for x in ns])
                                        for name, ns in self.processing_ns.items()},
            "early_momentum": {
                "detections": len(em),
                "feed_delay_ms": percentiles([e.feed_delay_ns / 1e6 for e in em]),
                "processing_us": percentiles([e.processing_ns / 1e3 for e in em]),
                "detection_delay_ms": percentiles([(e.feed_delay_ns + e.processing_ns) / 1e6 for e in em]),
                "trade_permitted": sum(e.trade_permitted for e in em),
                "blocking": sorted({b for e in em for b in e.blocking}),
            },
            "graduation": self._graduation_report(),
        }

    def _graduation_report(self) -> dict:
        finals = [e for e in (self.graduation.evidence(m) for m in self.curve_mints) if e is not None]
        return {"mints": len(finals),
                "not_standard_curves": sum(e.latest.standard_curve is False for e in finals),
                "complete_observed": sum(e.complete_observed for e in finals),
                "latest_progress": percentiles([e.latest.progress for e in finals if e.latest.progress is not None]),
                "late_readings": self.graduation.stats["late_readings"],
                "score_points_produced": sum(e.score_points is not None for e in finals)}


def replay_events(deliveries: Iterable[list[MarketEvent] | None]) -> ReplayRun:
    run = ReplayRun(early_momentum=EarlyMomentum(), graduation=GraduationTracker())
    modules = {"early_momentum": run.early_momentum.on_event, "graduation": run.graduation.on_event}
    run.processing_ns = {name: [] for name in modules}
    for events in deliveries:
        if events is None:
            run.refused += 1
            continue
        for ev in events:
            run.events += 1
            if ev.v_sol_lamports is not None:
                run.curve_mints.add(ev.mint)
            for name, on_event in modules.items():
                t0 = time.perf_counter_ns()
                out = on_event(ev)
                run.processing_ns[name].append(time.perf_counter_ns() - t0)
                if name == "early_momentum":
                    run.momentum.extend(out)
    return run
