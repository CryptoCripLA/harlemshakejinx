"""Regression tests for the Codex Phase 1 review, round 6 (docs/audits/phase1-codex-20261020-round6.md)."""
import socket
import sqlite3
import threading
from pathlib import Path

import pytest

from jinx.execution import ExitIntent
from jinx.store import Store
from tests.test_replay_live_capture import install_network_guard
from tests.test_review_round4 import rig, v1_with_prefixed_exit, without_outbox


def escaped_with_binding(path: Path, binding) -> None:
    """A pre-v5 database: historical p1:X sold 250M (no provenance), and an older build left `binding`
    (intent, token_amount, legacy_fill_id) for the same intent id, e.g. after a NO_QUOTE attempt."""
    v1_with_prefixed_exit(path)
    Store(path).close()
    c = sqlite3.connect(path)
    c.execute("DELETE FROM exit_intents")
    if binding is not None:
        intent, amount, legacy = binding
        c.execute("INSERT INTO exit_intents (intent_id, position_id, token_amount, created_ns, legacy_fill_id)"
                  " VALUES (?, 1, ?, 5, ?)", (intent, amount, legacy))
    without_outbox(c)
    c.execute("ALTER TABLE fills DROP COLUMN exit_intent_id")
    c.execute("DROP TABLE IF EXISTS historical_exits")
    c.execute("PRAGMA user_version = 4")
    c.commit()
    c.close()


# ---- R6-1: a binding under the historical intent id defeated the repair ------------------------
def test_no_quote_binding_under_the_historical_id_cannot_enable_a_second_sale(tmp_path):
    """Codex's exact sequence: p1:X attempted with NO_QUOTE (binding left, no legacy marker), upgrade, replay."""
    path = tmp_path / "db"
    escaped_with_binding(path, ("p1:X", 250_000_000, None))
    s = Store(path)
    ledger, gw = rig(s)
    r = gw.submit_exit(ExitIntent("p1:X", 1, token_amount=250_000_000))
    assert r.ok and r.reason == "ALREADY_APPLIED"
    assert ledger.get(1).token_amount == 750_000_000
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1
    s.close()


@pytest.mark.parametrize("binding", [
    None,
    ("p1:X", 250_000_000, None),
    ("p1:X", 100_000_000, None),
    ("p1:X", None, None),
    ("p1:X", None, "p1:X:sell"),
])
@pytest.mark.parametrize("amount", [250_000_000, 100_000_000, None])
def test_historical_sale_never_executes_twice_whatever_binding_preceded_the_upgrade(tmp_path, binding, amount):
    path = tmp_path / "db"
    escaped_with_binding(path, binding)
    s = Store(path)
    ledger, gw = rig(s)
    r = gw.submit_exit(ExitIntent("p1:X", 1, token_amount=amount))
    assert ledger.get(1).token_amount == 750_000_000
    assert s.conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1
    if amount == 250_000_000:
        assert r.ok and r.reason == "ALREADY_APPLIED"   # the exact historical replay
    else:
        assert not r.ok                                  # anything else is ambiguous and refused
    s.close()


def test_every_provenance_free_sell_is_recorded_as_historical_evidence(tmp_path):
    path = tmp_path / "db"
    escaped_with_binding(path, ("p1:X", 100_000_000, None))
    s = Store(path)
    rows = s.conn.execute("SELECT fill_id, intent_id, position_id FROM historical_exits").fetchall()
    assert [tuple(r) for r in rows] == [("p1:X:sell", "p1:X", 1)]
    s.close()


# ---- R6-3: connect_ex bypassed the replay network guard -------------------------------------
def test_network_guard_refuses_connect_ex_and_sendto_to_a_live_listener(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    addr = listener.getsockname()
    received = []

    def serve():
        listener.settimeout(1.0)
        try:
            conn, _ = listener.accept()
            received.append(conn.recv(100))
            conn.close()
        except OSError:
            pass
    t = threading.Thread(target=serve)
    t.start()
    refused = install_network_guard(monkeypatch)
    s = socket.socket()
    try:
        with pytest.raises(AssertionError):
            s.connect_ex(addr)
    finally:
        s.close()
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(AssertionError):
            u.sendto(b"x", addr)
    finally:
        u.close()
    t.join()
    listener.close()
    assert received == [] and len(refused) == 2
