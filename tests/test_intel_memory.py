"""RAM of the Phase 2 event intel (owner storage rule 2: process, extract, release; bounded). Contabo carries ~80,000
events an hour (live /health, 2026-10-21), so what the trackers keep per event and per token must fit the 1 GB
process budget beside the market store. Measured with tracemalloc on the live capture and on one extreme token."""
import gc
import tracemalloc

from jinx.intel.early_momentum import EarlyMomentum
from jinx.intel.graduation import GraduationTracker
from jinx.intel.migration import MigrationTracker
from jinx.sim import capture_events, load_capture
from tests.pumpapi_msgs import amm_trade, buy, create, events, migrate, sell
from tests.test_intel_replay import FIXTURE

TRACKERS = (EarlyMomentum, GraduationTracker, MigrationTracker)


def retained(evs) -> int:
    gc.collect()
    tracemalloc.start()
    trackers = [cls() for cls in TRACKERS]
    for ev in evs:
        for t in trackers:
            t.on_event(ev)
    gc.collect()
    used, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return used


def test_the_trackers_keep_under_900_bytes_per_event_of_live_traffic():
    evs = [e for d in capture_events(load_capture(FIXTURE), source="pumpapi") if d for e in d]
    per_event = retained(evs) / len(evs)
    assert per_event < 900, f"{per_event:.0f} bytes retained per event"


def test_one_extremely_active_token_stays_inside_its_per_token_bound():
    msgs = [create(dev="DEV", slot=100)]
    msgs += [(buy if i % 2 else sell)(f"W{i % 3_000}", 1 + i * 0.001, v_quote=31.0 + (i % 50) * 0.1, slot=110 + i)
             for i in range(12_000)]
    msgs += [migrate(pool_id="AMM1", t_ms=1_792_540_100_000, slot=20_000)]
    msgs += [amm_trade("buy", payer=f"S{i % 3_000}", t_ms=1_792_540_100_100 + i, slot=20_001 + i, pool_id="AMM1")
             for i in range(6_000)]
    evs = [e for m in msgs for e in events(m)]
    used = retained(evs)
    assert used < 1_200_000, f"{used / 1e3:.0f} KB for one token"


def test_the_recent_identity_window_remembers_the_newest_and_reports_what_left():
    from jinx.intel.bounded import RecentIds
    ids = RecentIds(3, timed=True)
    assert ids.add(1, 10) is None and ids.add(2, 20) is None and ids.add(3, 30) is None
    assert 1 in ids and 4 not in ids and len(ids) == 3
    assert ids.add(4, 40) == 10                         # the oldest identity left; its time is returned
    assert 1 not in ids and 4 in ids and len(ids) == 3
    plain = RecentIds(2)
    assert plain.add(7) is None and plain.add(8) is None and plain.add(9) is None and 7 not in plain
