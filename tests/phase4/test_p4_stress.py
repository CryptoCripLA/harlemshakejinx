"""V10 stress simultaneous token events; the owner's entry-slot invariant under concurrent pressure.

Owner global directive (2026-10-17): "ONE GLOBAL BUY/POSITION SLOT. OWNER-APPROVED MAXIMUM: TWO ... NO EXECUTION PATH
MAY BYPASS THIS LIMIT." The engine is one asyncio process, but the slot must also hold if two processes or threads
ever share the ledger (a restart overlap, an operator tool), because the reservation is taken inside a SQLite
`BEGIN IMMEDIATE` transaction. Many simultaneous launches must each be detected on their own facts, with no
cross-token contamination, and background lookups beyond the engineering cap must leave evidence UNAVAILABLE (blocked),
never wrong. Synthetic launches are built with the live PumpAPI field layout (tests/pumpapi_msgs.py); counts are
engineering stress sizes, not trading values."""
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from jinx.execution import EntryIntent
from jinx.ledger import PositionLedger
from jinx.modes import Mode, set_mode
from jinx.ops.entry_slots import set_entry_slots
from jinx.store import Store
from tests.phase4 import harness as h
from tests.phase4.test_p4_chad_entry_predicates import LiveFeed
from tests.pumpapi_msgs import T0_MS, create, events, trade

ROOT = h.FIXTURES.parent.parent
N = 300


def launches(n: int) -> list:
    """n launches created in the same second, their three buyers interleaved across all of them."""
    msgs = [create(mint=f"Stress{i:04d}pump", dev=f"DEV{i}", t_ms=T0_MS, v_quote=31.0) for i in range(n)]
    for k, w in enumerate(("A", "B", "C")):
        for i in range(n):
            msgs.append(trade("buy", mint=f"Stress{i:04d}pump", payer=f"{w}{i}", t_ms=T0_MS + 1000 * (k + 1),
                              v_quote=31.5 + 0.5 * k))
    return msgs


def test_simultaneous_launches_are_each_detected_once_on_their_own_buyers(tmp_path):
    app = h.build_app(tmp_path)
    t0 = time.perf_counter()
    st = h.run(app, [events(m) for m in launches(N)])
    elapsed = time.perf_counter() - t0
    assert st.handler_errors == {} and st.published == N * 5                  # create + creator buy + 3 buys
    assert app.early.stats["detections"] == N
    for i in range(N):
        ev = app.early.evidence(f"Stress{i:04d}pump")
        assert ev is not None and set(ev.fee_payers) <= {f"DEV{i}", f"A{i}", f"B{i}", f"C{i}"}
        assert all(b.signature for b in ev.window)
    assert elapsed < 30, f"{N} launches took {elapsed:.1f}s"                  # engineering bound, local processing
    assert app.ledger.all() == []
    h.close(app)


def test_background_lookups_beyond_the_cap_are_shed_and_their_candidates_stay_blocked(tmp_path):
    slow = h.FakeGmgn(delay_s=0.5)                       # GMGN slower than the launch rate
    app = h.build_app(tmp_path, gmgn=slow, rugcheck=h.FakeRugCheck())
    app.pumpapi = LiveFeed()
    st = h.run(app, [events(m) for m in launches(N)], settle_s=0.1)
    assert st.handler_errors == {}
    assert app.background_stats["shed"] > 0 and len(app._background) <= app.MAX_BACKGROUND_TASKS
    unscreened = [m for m, c in app.candidates.items() if not c.get("safety")]
    assert unscreened and all(not app.entry_check(m)["all_checks_pass"] for m in unscreened)
    assert app.ledger.all() == []
    h.close(app)


@pytest.mark.parametrize("slots", [1, 2])
def test_a_burst_of_entry_intents_never_holds_more_positions_than_the_owner_slots(tmp_path, slots):
    app = h.build_app(tmp_path)
    h.run(app, [events(m) for m in launches(50)])
    set_mode(app.store, Mode.AUTO, by="phase4-test")
    if slots == 2:
        set_entry_slots(app.store, 2, owner_order=True)        # labelled test fixture: the owner's command path
    results = [app.gateway.submit_entry(EntryIntent(f"burst-{i}", f"Stress{i:04d}pump", 10_000_000,
                                                    origin="phase4-test")) for i in range(50)]
    assert sum(r.ok for r in results) == slots and app.ledger.active_slots() == slots
    assert {r.reason for r in results if not r.ok} == {"SLOT_LIMIT"}
    with pytest.raises(ValueError):
        set_entry_slots(app.store, 3, owner_order=True)        # never three
    h.close(app)


def test_threads_sharing_one_ledger_cannot_take_more_than_one_slot(tmp_path):
    store = Store(tmp_path / "jinx.db")
    set_mode(store, Mode.AUTO, by="phase4-test")
    ledger = PositionLedger(store)
    barrier, results = threading.Barrier(16), []

    def take(i):
        barrier.wait()
        results.append(ledger.reserve_entry(f"thread-{i}", f"ThreadMint{i}", 10_000_000))
    threads = [threading.Thread(target=take, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r.ok for r in results) == 1 and ledger.active_slots() == 1
    store.close()


def test_separate_processes_sharing_one_ledger_cannot_take_more_than_one_slot(tmp_path):
    db = tmp_path / "jinx.db"
    s = Store(db)
    set_mode(s, Mode.AUTO, by="phase4-test")
    s.close()
    go = tmp_path / "go"
    child = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from jinx.ledger import PositionLedger
        from jinx.store import Store
        s = Store({str(db)!r})
        ledger = PositionLedger(s)
        while not Path({str(go)!r}).exists():
            time.sleep(0.001)
        r = ledger.reserve_entry("proc-%d" % os.getpid(), "ProcMint%d" % os.getpid(), 10_000_000)
        print("OK" if r.ok else r.reason)
        s.close()
    """)
    procs = [subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(8)]
    time.sleep(1.0)
    go.write_text("go")
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1][-300:] for o in outs]
    answers = [o[0].strip() for o in outs]
    assert answers.count("OK") == 1 and set(answers) - {"OK"} == {"SLOT_LIMIT"}
    s = Store(db)
    assert PositionLedger(s).active_slots() == 1
    s.close()


@pytest.mark.xfail(strict=True, reason="FINDING P4-F2 (2026-10-21): JinxApp._on_early_momentum marks a candidate "
                   "'screening' before _spawn, and a lookup shed at MAX_BACKGROUND_TASKS is never retried, so a valid "
                   "launch heard during a burst stays unscreened (blocked) for good: fail-closed, but a missed entry. "
                   "Owner of jinx/app.py screening: Phase 2 session local_f5744230. Remove this marker when fixed.")
def test_a_candidate_shed_during_a_burst_is_screened_once_capacity_frees(tmp_path):
    slow = h.FakeGmgn(delay_s=0.2)
    app = h.build_app(tmp_path, gmgn=slow, rugcheck=h.FakeRugCheck())
    app.pumpapi = LiveFeed()
    h.run(app, [events(m) for m in launches(N)], settle_s=0.05, cancel_background=False)
    assert app.background_stats["shed"] > 0
    h.run(app, [], settle_s=10.0)                       # the burst is over; capacity is free again
    unscreened = [m for m, c in app.candidates.items() if not c.get("safety")]
    h.close(app)
    assert unscreened == []
