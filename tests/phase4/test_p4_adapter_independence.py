"""V07: independence of the four upstream adapters and of every consumer of the shared event stream, at runtime.

CLAUDE.md isolation rule: "A failure in GMGN must not corrupt Mako's storage. A failure in market-data ingestion must
not generate a false trade. A failure in transaction dispatch must not produce a fabricated position. A failure in
research processing must not halt the entire execution engine." The static isolation tests (no adapter imports another
or reads another's upstream tree; core never imports an adapter) are tests/test_adapters.py and stay in force; this
file adds the runtime checks on the real composition root with a live capture:

- each module that consumes the event stream is made to fail on every event in turn; every other module's state must
  be byte-identical to the healthy run, no position or fill may appear, and the paper wallet must reconcile;
- GMGN, RugCheck and the D3AD/DataNomad adapters fail; the market view built from the feeds is unchanged;
- the pinned upstream trees are unmodified after all of it (scripts/upstream_sync.py --verify)."""
import subprocess
import sys

import pytest

from jinx.intel.bundles import BundleTracker
from jinx.intel.early_momentum import EarlyMomentum
from jinx.intel.graduation import GraduationTracker
from jinx.intel.migration import MigrationTracker
from jinx.intel.whales import WhaleBook
from jinx.sim import load_capture
from tests.phase4 import harness as h

LIVE = load_capture(h.FIXTURES / "pumpapi-live-20261021.jsonl.gz")
MIGR = load_capture(h.FIXTURES / "pumpapi-migrations-live-20261021.jsonl.gz")
RECORDS = LIVE + MIGR

# module -> (class whose event handler is broken, the state_view parts that module alone owns)
CONSUMERS = {
    "early_momentum": (EarlyMomentum, {"early_momentum", "reserve_gates"}),
    "graduation": (GraduationTracker, {"graduation"}),
    "migration": (MigrationTracker, {"migration"}),
    "bundles": (BundleTracker, set()),
    "whales": (WhaleBook, set()),
}


@pytest.fixture(scope="module")
def healthy(tmp_path_factory):
    app = h.build_app(tmp_path_factory.mktemp("healthy"))
    h.run(app, h.deliveries(RECORDS, "pumpapi"))
    view = h.state_view(app)
    h.close(app)
    return view


@pytest.mark.parametrize("module", CONSUMERS)
def test_a_module_failing_on_every_event_corrupts_no_other_module_and_creates_no_trade(tmp_path, monkeypatch,
                                                                                       healthy, module):
    cls, owned = CONSUMERS[module]

    def broken(self, ev):
        raise RuntimeError(f"phase4 injected failure in {module}")
    monkeypatch.setattr(cls, "on_event", broken)
    app = h.build_app(tmp_path)
    st = h.run(app, h.deliveries(RECORDS, "pumpapi"))
    view = h.state_view(app)
    assert sum(st.handler_errors.values()) > 0                      # the failure was counted, not hidden
    for part in healthy:
        if part not in owned:
            assert view[part] == healthy[part], f"{module} failing changed {part}"
    assert view["ledger"] == [] and view["paper_wallet"]["ok"] is True
    h.close(app)


def test_a_market_store_write_failure_creates_no_event_and_no_trade(tmp_path):
    """A failed durable write is retried by the feeds (tests/test_pumpapi_adapter.py); the bus itself must not admit
    the event or tell any module about it."""
    import asyncio
    app = h.build_app(tmp_path)
    real = app.market.record_event
    calls = {"n": 0}

    def failing(ev):
        calls["n"] += 1
        raise OSError("disk full (injected)")
    app.bus._persist = failing
    [first] = h.deliveries(LIVE[:1], "pumpapi")

    async def go():
        runner = asyncio.create_task(app.bus.run())
        try:
            with pytest.raises(OSError):
                await app.bus.publish(first[0])
            await app.bus.drain()
        finally:
            runner.cancel()
    h.loop_of(app).run_until_complete(go())
    assert calls["n"] == 1 and len(app.core.tokens) == 0 and app.early.stats["events"] == 0
    assert app.ledger.all() == []
    app.bus._persist = real
    st = h.run(app, [[first[0]]])                                    # the resend is accepted once storage is back
    assert st.published == 1
    h.close(app)


@pytest.mark.parametrize("mode", ["down", "garbage", "timeout"])
def test_gmgn_and_rugcheck_failing_leave_the_market_view_exactly_as_the_feeds_built_it(tmp_path, healthy, mode):
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn(mode=mode), rugcheck=h.FakeRugCheck(mode=mode))
    h.run(app, h.deliveries(RECORDS, "pumpapi"), settle_s=1.0)
    view = h.state_view(app)
    for part in ("tokens", "wallets", "early_momentum", "reserve_gates", "graduation"):
        assert view[part] == healthy[part], part
    assert view["ledger"] == []
    h.close(app)


def test_the_execution_and_research_donors_cannot_act_and_their_failure_stops_nothing(tmp_path):
    from jinx.adapters.d3ad.adapter import D3adExecutionAdapter
    from jinx.adapters.datanomad.adapter import DataNomadResearchAdapter, LicenseNotGranted
    from jinx.execution import LiveExecutionUnavailable
    d3ad = D3adExecutionAdapter(tmp_path / "missing-d3ad")
    nomad = DataNomadResearchAdapter(tmp_path / "missing-datanomad")
    assert d3ad.health()["pinned"] is False and nomad.health()["pinned"] is False
    with pytest.raises(LiveExecutionUnavailable):
        d3ad.submit({"phase4": "an order must never leave through the donor"})
    with pytest.raises(LicenseNotGranted):
        nomad.load_code("anything")
    app = h.build_app(tmp_path / "app")
    app.d3ad, app.datanomad = d3ad, nomad
    st = h.run(app, h.deliveries(LIVE[:200], "pumpapi"))
    assert st.published > 0 and st.handler_errors == {} and app.ledger.all() == []
    h.close(app)


def test_the_pinned_upstream_trees_are_unmodified_after_the_runs():
    root = h.FIXTURES.parent.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "upstream_sync.py"), "--verify"], cwd=root,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("gmgn", "mako", "d3ad", "datanomad"):
        dirty = subprocess.run(["git", "-C", str(root / "upstream" / name), "status", "--porcelain",
                                "--untracked-files=no"], capture_output=True, text=True).stdout
        assert dirty == "", f"upstream/{name} has local modifications: {dirty}"
