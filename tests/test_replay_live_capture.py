"""Phase 1 simulation on genuine captured PumpPortal events (fixture recorded live on jinx-nyc).

The fixture holds raw messages exactly as Mako's PumpPortalStream delivered them.
Without a funded PumpPortal key the live feed carries token creations only (each
with the creator's initial buy), so these runs exercise creations and creator buys.
"""
import socket
from pathlib import Path

import pytest

from jinx.sim import load_capture, replay

FIXTURE = Path(__file__).parent / "fixtures" / "pumpportal-live-20261020.jsonl.gz"
RECORDS = load_capture(FIXTURE)


def expected_events(records):
    n = 0
    for r in records:
        raw = r["raw"]
        n += 1
        if raw.get("txType") == "create" and raw.get("initialBuy", 0) > 0:
            n += 1
    return n


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    return replay(RECORDS, tmp_path_factory.mktemp("base") / "r.db")


def test_fixture_is_a_real_live_capture():
    assert len(RECORDS) >= 100
    assert all(r["raw"]["signature"] and r["raw"]["mint"] for r in RECORDS)
    recv = [r["recv_ns"] for r in RECORDS]
    assert recv == sorted(recv)  # recorded in arrival order


def test_every_record_is_ingested_and_applied(baseline):
    assert baseline.malformed == 0
    assert baseline.events_admitted == expected_events(RECORDS)
    assert baseline.duplicates == 0
    assert baseline.unapplied_after == 0
    assert baseline.tokens == len({r["raw"]["mint"] for r in RECORDS})


def test_duplicate_messages_are_counted_once(baseline, tmp_path):
    dup = replay(RECORDS, tmp_path / "r.db", duplicate_every=3)
    assert dup.events_admitted == baseline.events_admitted
    injected = sum(1 + (r["raw"].get("initialBuy", 0) > 0) for i, r in enumerate(RECORDS) if i % 3 == 0)
    assert dup.duplicates == injected
    assert dup.digest == baseline.digest


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_out_of_order_arrival_gives_identical_state(baseline, tmp_path, seed):
    shuffled = replay(RECORDS, tmp_path / "r.db", shuffle_seed=seed)
    assert shuffled.digest == baseline.digest
    assert shuffled.events_admitted == baseline.events_admitted


def test_stop_and_restart_mid_stream_gives_identical_state(baseline, tmp_path):
    restarted = replay(RECORDS, tmp_path / "r.db", restart_at=len(RECORDS) // 2)
    assert restarted.digest == baseline.digest
    assert restarted.unapplied_after == 0


def test_paper_accounting_reconciles_on_real_reserves(tmp_path):
    r = replay(RECORDS, tmp_path / "r.db", accounting_exercise=True, exit_after=0)
    acc = r.accounting
    assert acc["closed_round_trips"] >= 50
    assert acc["wallet_reconciled"] and r.wallet_reconciled
    assert acc["wallet_change_equals_realized_minus_open_cost"]
    # flat-price round trip on real reserves loses only the two fee legs (D3AD's own expectation: 100-400 bps)
    assert 100 <= acc["flat_round_trip_cost_bps"]["min"] <= acc["flat_round_trip_cost_bps"]["max"] <= 400


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def test_replay_and_paper_execution_never_touch_the_network(tmp_path, monkeypatch):
    # Loopback stays allowed: asyncio on Windows builds its internal socketpair over 127.0.0.1.
    real_connect, real_create = socket.socket.connect, socket.create_connection

    def guarded_connect(sock, address, *a, **k):
        if isinstance(address, tuple) and address[0] not in LOOPBACK:
            raise AssertionError(f"network access during paper replay: {address}")
        return real_connect(sock, address, *a, **k)

    def guarded_create(address, *a, **k):
        if address[0] not in LOOPBACK:
            raise AssertionError(f"network access during paper replay: {address}")
        return real_create(address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create)
    r = replay(RECORDS, tmp_path / "r.db", accounting_exercise=True)
    assert r.accounting["closed_round_trips"] > 0
