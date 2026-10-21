"""V03: deterministic replay of captured real-market events through the actual JINX pipeline.

The real composition root (`JinxApp._build_core`: bus with dedup and durable write, core state, every intel module on
its own lane, ledger, paper wallet) consumes three captures recorded live on jinx-nyc. The same facts delivered again,
in another order, with resends, or across a restart of the market store, must give the identical decision-relevant
state (tests/phase4/harness.py::state_view; excluded receipt/wall-clock fields are listed in VOLATILE_KEYS)."""
from pathlib import Path

import pytest

from jinx.sim import load_capture
from tests.phase4 import harness as h

FIX = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURES = {
    "pumpapi-live": (load_capture(FIX / "pumpapi-live-20261021.jsonl.gz"), "pumpapi"),
    "pumpapi-migrations": (load_capture(FIX / "pumpapi-migrations-live-20261021.jsonl.gz"), "pumpapi"),
    "pumpportal-live": (load_capture(FIX / "pumpportal-live-20261020.jsonl.gz"), "pumpportal"),
}


def replay(tmp_path, name, **kw):
    records, source = CAPTURES[name]
    app = h.build_app(tmp_path)
    stats = h.run(app, h.deliveries(records, source, **kw))
    view, digest = h.state_view(app), h.state_digest(app)
    h.close(app)
    return stats, view, digest


@pytest.fixture(scope="module")
def baselines(tmp_path_factory):
    return {n: replay(tmp_path_factory.mktemp(n), n) for n in CAPTURES}


@pytest.mark.parametrize("name", CAPTURES)
def test_every_capture_is_ingested_by_every_module_without_a_handler_error(baselines, name):
    stats, view, _ = baselines[name]
    assert stats.handler_errors == {}
    assert stats.published > 0 and stats.duplicates == 0
    assert view["paper_wallet"]["ok"] is True


def test_the_live_capture_drives_detections_reserve_gates_and_graduation(baselines):
    _, view, _ = baselines["pumpapi-live"]
    assert len(view["early_momentum"]) == 38                     # same count as the Phase 2 offline replay
    assert view["reserve_gates"] and view["graduation"]


def test_the_migration_capture_drives_the_migration_tracker(baselines):
    _, view, _ = baselines["pumpapi-migrations"]
    assert view["migration"]


@pytest.mark.parametrize("name", CAPTURES)
def test_the_same_capture_replayed_again_gives_the_identical_digest(baselines, tmp_path, name):
    assert replay(tmp_path, name)[2] == baselines[name][2]


@pytest.mark.parametrize("name", CAPTURES)
@pytest.mark.parametrize("seed", [1, 7, 20261021])
def test_shuffled_and_resent_delivery_gives_the_identical_digest(baselines, tmp_path, name, seed):
    stats, view, digest = replay(tmp_path, name, shuffle_seed=seed, duplicate_every=5)
    base_stats, base_view, base_digest = baselines[name]
    assert stats.duplicates > 0 and stats.published == base_stats.published
    for part in base_view:                                     # name the module that diverged, if any
        assert view[part] == base_view[part], part
    assert digest == base_digest
