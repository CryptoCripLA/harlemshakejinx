"""Speed requirement S1-S6 (CLAUDE.md Phase 4): structure that keeps the hot path fast, checked on the real engine.

S1 No unnecessary polling wait in the Early Momentum event path.
S2 No social-analysis queue blocking event detection.
S3 No heavyweight historical analysis on the transaction hot path.
S4 No synchronous dashboard update delaying an order.
S5 No repeated API calls when a valid fresh observation is already cached.
S6 No unbounded queue.
Measured timings (S7) are produced by scripts/phase4_latency.py; nothing here claims order landing time (S8)."""
import ast
import asyncio
import collections
import inspect
import sys
import time
from pathlib import Path

import pytest

from jinx.execution import EntryIntent, ExitIntent
from jinx.core.bus import Lane
from jinx.modes import Mode, set_mode
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import DISTINCT, LiveFeed, M, baseline_msgs
from tests.pumpapi_msgs import buy, create, events

ROOT = Path(__file__).resolve().parents[2]
FAST_MODULES = ["jinx/core_state.py", "jinx/registry.py", "jinx/intel/early_momentum.py", "jinx/intel/graduation.py",
                "jinx/intel/migration.py", "jinx/intel/readings.py", "jinx/intel/bounded.py"]
ORDER_PATH = ["jinx/execution.py", "jinx/paper.py", "jinx/ledger.py", "jinx/modes.py", "jinx/store.py"]


def imports(path: str) -> set[str]:
    mods = set()
    for node in ast.walk(ast.parse((ROOT / path).read_text())):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def awaits_or_sleeps(path: str) -> list[str]:
    tree = ast.parse((ROOT / path).read_text())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Await, ast.AsyncFunctionDef)):
            found.append(f"{type(node).__name__} line {node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr == "sleep":
            found.append(f"sleep line {node.lineno}")
    return found


# ---- S1 ----------------------------------------------------------------------------------------------------------------

def test_S1_every_fast_lane_handler_is_synchronous_with_no_sleep_or_await():
    for path in FAST_MODULES:
        assert awaits_or_sleeps(path) == [], path


def test_S1_the_detection_is_emitted_inside_the_call_that_handles_the_third_buyer(tmp_path):
    app = h.build_app(tmp_path)
    out = []
    for m in [create(mint="FreshMintpump", dev="DEV", v_quote=31.0), buy("X", 1, v_quote=31.5, mint="FreshMintpump")]:
        for ev in events(m):
            out += app.early.on_event(ev)
    assert out == []                                        # two fee payers: nothing yet
    [third] = events(buy("Z", 2, v_quote=32.0, mint="FreshMintpump"))
    detected = app.early.on_event(third)
    assert len(detected) == 1 and detected[0].processing_ns < 50_000_000      # same call, no queue, no wait
    h.close(app)


def test_S1_the_fast_lane_never_waits_for_the_background_lane(tmp_path):
    app = h.build_app(tmp_path)
    gate = asyncio.Event()

    async def stuck(ev):                 # a background consumer that never finishes (e.g. a slow social scorer)
        await gate.wait()
    app.bus.subscribe(Lane.BACKGROUND, stuck, name="stuck-social")

    async def go():
        runner = asyncio.create_task(app.bus.run())
        t0 = time.perf_counter()
        for m in baseline_msgs():
            for ev in events(m):
                await app.bus.publish(ev)
        while app.early.evidence(M) is None and time.perf_counter() - t0 < 5:
            await asyncio.sleep(0.001)
        took = time.perf_counter() - t0
        gate.set()
        await app.bus.drain()
        runner.cancel()
        return took
    took = h.loop_of(app).run_until_complete(go())
    assert app.early.evidence(M) is not None and took < 1.0
    h.close(app)


# ---- S2 ----------------------------------------------------------------------------------------------------------------

def test_S2_slow_safety_and_funder_lookups_never_delay_the_next_detection(tmp_path):
    slow = h.FakeGmgn(delay_s=2.0, wallet_stats=DISTINCT)       # GMGN taking 2 s per call
    app = h.build_app(tmp_path, gmgn=slow, rugcheck=h.FakeRugCheck())
    app.pumpapi = LiveFeed()

    async def go():
        runner = asyncio.create_task(app.bus.run())
        stamps = {}
        for k, mint in enumerate(("SlowAPump", "SlowBPump")):
            t0 = time.perf_counter()
            for m in [create(mint=mint, dev=f"D{k}", v_quote=31.0), buy(f"A{k}", 1, v_quote=31.5, mint=mint),
                      buy(f"B{k}", 2, v_quote=32.0, mint=mint)]:
                for ev in events(m):
                    await app.bus.publish(ev)
            while app.early.evidence(mint) is None:
                await asyncio.sleep(0.001)
            stamps[mint] = time.perf_counter() - t0
        runner.cancel()
        for t in list(app._background):
            t.cancel()
        return stamps
    stamps = h.loop_of(app).run_until_complete(go())
    assert all(v < 0.5 for v in stamps.values()), stamps     # lookups (2 s each) run beside, never in front
    h.close(app)


# ---- S3 / S4 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", FAST_MODULES + ORDER_PATH)
def test_S3_S4_no_network_research_or_dashboard_code_is_imported_on_the_hot_paths(path):
    banned = ("jinx.adapters", "jinx.persistence", "jinx.health", "jinx.app", "jinx.storage", "httpx", "psycopg",
              "redis", "boto3", "websockets", "requests", "aiohttp")
    bad = {m for m in imports(path) if m.startswith(banned)}
    assert bad == set(), f"{path} imports {bad}"


def test_S4_an_order_completes_while_every_dashboard_and_service_call_would_hang(tmp_path):
    app = h.build_app(tmp_path)
    h.run(app, [events(m) for m in baseline_msgs()])
    set_mode(app.store, Mode.AUTO, by="phase4-test")

    async def hang(*a, **k):
        await asyncio.sleep(3600)
    app.health = hang                                       # status page, Redis snapshot, research: all hung
    t0 = time.perf_counter()
    entry = app.gateway.submit_entry(EntryIntent("s4-e", M, 50_000_000))
    exit_ = app.gateway.submit_exit(ExitIntent("s4-x", entry.position_id))
    took = time.perf_counter() - t0
    assert entry.ok and exit_.ok and took < 0.5
    assert not inspect.iscoroutinefunction(app.gateway.submit_entry)       # nothing on it can be awaited
    h.close(app)


# ---- S5 ----------------------------------------------------------------------------------------------------------------

def test_S5_a_candidate_is_screened_once_while_its_safety_answer_is_fresh(tmp_path):
    gmgn = h.FakeGmgn(wallet_stats=DISTINCT)
    app = h.build_app(tmp_path, gmgn=gmgn, rugcheck=h.FakeRugCheck())
    app.pumpapi = LiveFeed()
    h.run(app, [events(m) for m in baseline_msgs()])
    h.run(app, [events(buy(w, 4 + i, v_quote=33.0 + i, mint=M)) for i, w in enumerate(("D", "E", "F"))])
    assert [c[0] for c in gmgn.calls].count("getTokenSecurity") == 1
    asyncio.run(app.safety.evaluate(M, "DEV", time.time()))                 # a later ask inside the 60 s window
    assert [c[0] for c in gmgn.calls].count("getTokenSecurity") == 1 and app.safety.stats["cache_hits"] >= 1
    h.close(app)


@pytest.mark.xfail(strict=True, reason="FINDING P4-F3 (2026-10-21): JinxApp._request_funders asks GMGN getWalletStats "
                   "for every window wallet of every detection, including wallets whose funder EarlyMomentum already "
                   "holds (record_funding cache), spending Plus-plan weight on repeat calls (speed rule S5). Owner of "
                   "jinx/app.py funder lookups: Phase 2 session local_f5744230. Remove this marker when fixed.")
def test_S5_a_wallet_whose_funder_is_already_known_is_not_looked_up_again(tmp_path):
    gmgn = h.FakeGmgn(wallet_stats=DISTINCT)
    app = h.build_app(tmp_path, gmgn=gmgn, rugcheck=h.FakeRugCheck())
    app.pumpapi = LiveFeed()
    h.run(app, [events(m) for m in baseline_msgs()])
    first = [c for c in gmgn.calls if c[0] == "getWalletStats"]
    second = [create(mint="SecondPump", dev="DEV", v_quote=31.0, t_ms=1_792_540_100_000)] + [
        buy(w, 101 + i, v_quote=31.5 + 0.5 * i, mint="SecondPump") for i, w in enumerate(("A", "B"))]
    h.run(app, [events(m) for m in second])
    again = [c for c in gmgn.calls if c[0] == "getWalletStats"][len(first):]
    looked_up = {c[1][1][0] for c in again}
    h.close(app)
    assert app.early.evidence("SecondPump") is not None
    assert looked_up & {"DEV", "A", "B"} == set(), f"funders already known were fetched again: {looked_up}"


# ---- S6 ----------------------------------------------------------------------------------------------------------------

def walk(obj, depth=0, seen=None, path="app"):
    seen = seen if seen is not None else set()
    if id(obj) in seen or depth > 4:
        return
    seen.add(id(obj))
    yield path, obj
    if isinstance(obj, (str, bytes, int, float, bool, type(None))) or inspect.ismodule(obj) or inspect.isclass(obj):
        return
    if isinstance(obj, asyncio.Queue):          # bounded by maxsize; its private waiter lists are asyncio's own
        return
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:50]:
            yield from walk(v, depth + 1, seen, f"{path}[{k!r}]")
        return
    if isinstance(obj, (list, tuple, set)):
        return
    for name in getattr(obj, "__dict__", {}):
        yield from walk(getattr(obj, name), depth + 1, seen, f"{path}.{name}")


def test_S6_every_queue_and_rolling_buffer_in_the_engine_is_bounded(tmp_path):
    from jinx.adapters.pumpapi.feed import PumpApiFeed
    from jinx.adapters.rugcheck.client import RugCheckClient
    app = h.build_app(tmp_path, gmgn=h.FakeGmgn(), rugcheck=RugCheckClient())
    app.pumpapi = PumpApiFeed(app.bus)
    # RugCheck's priority queue is bounded by admission control instead of maxsize: report() refuses with
    # "queue_full" once max_queue requests wait (tests/test_rugcheck_adapter.py pins the refusal).
    admission_bounded = {"app.rugcheck._queue": app.rugcheck.max_queue}
    asyncio.run(app.rugcheck.report("not a mint"))           # creates nothing; the queue is made lazily below
    app.rugcheck._q()
    queues = deques = 0
    for path, obj in walk(app):
        if isinstance(obj, asyncio.Queue):
            queues += 1
            bound = next((b for k, b in admission_bounded.items() if path.endswith(k.split(".", 1)[1])), 0)
            assert obj.maxsize > 0 or bound > 0, f"unbounded queue at {path}"
        if isinstance(obj, collections.deque):
            deques += 1
            assert obj.maxlen is not None, f"unbounded deque at {path}"
    assert queues >= 2 and deques >= 3                               # the walk really reached them
    assert app.MAX_BACKGROUND_TASKS > 0
    h.close(app)
