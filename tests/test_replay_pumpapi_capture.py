"""PumpAPI capture replayed through the Phase 1 engine (owner directive 2026-10-20, item 6).

The fixture was recorded live on jinx-nyc by scripts/capture_pumpapi.py through the real PumpApiFeed: every
bonding-curve event, every migration, and PumpSwap trades of mints first seen on the curve, as compact records with
JINX's receipt times. Unlike the free PumpPortal capture (creations only), it carries every buy and sell with the
fee payer, which is what CHAD's Early Momentum needs."""
from pathlib import Path

import pytest

from jinx.core.events import EventKind
from jinx.sim import load_capture, replay

FIXTURE = Path(__file__).parent / "fixtures" / "pumpapi-live-20261021.jsonl.gz"
RECORDS = load_capture(FIXTURE)


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    return replay(RECORDS, tmp_path_factory.mktemp("base") / "r.db", source="pumpapi")


def test_fixture_is_a_real_live_capture_with_trades_and_fee_payers():
    assert len(RECORDS) >= 500
    raws = [r["raw"] for r in RECORDS]
    assert all(r["signature"] and r["mint"] and r["txSigner"] and r["block"] for r in raws)
    assert sum(r["action"] == "buy" for r in raws) >= 100 and sum(r["action"] == "sell" for r in raws) >= 50
    recv = [r["recv_ns"] for r in RECORDS]
    assert recv == sorted(recv)


def test_every_record_is_ingested_and_applied_once(baseline):
    assert baseline.malformed == 0 and baseline.duplicates == 0 and baseline.unapplied_after == 0
    expected = sum(1 + (r["raw"]["action"] == "create" and r["raw"].get("initialBuy", 0) > 0) for r in RECORDS)
    assert baseline.events_admitted == expected


def test_duplicates_reordering_and_restart_give_identical_state(baseline, tmp_path):
    assert replay(RECORDS, tmp_path / "d.db", duplicate_every=4, source="pumpapi").digest == baseline.digest
    assert replay(RECORDS, tmp_path / "s.db", shuffle_seed=7, source="pumpapi").digest == baseline.digest
    assert replay(RECORDS, tmp_path / "r.db", restart_at=len(RECORDS) // 2, source="pumpapi").digest == baseline.digest


def test_the_capture_contains_three_distinct_fee_payers_buying_one_curve_mint_within_30_seconds():
    """Data sufficiency for CHAD's Early Momentum input (not a trading rule): real buys, real fee payers, real times."""
    from collections import defaultdict
    buys = defaultdict(list)
    for r in RECORDS:
        raw = r["raw"]
        if raw["pool"] == "pump" and raw["action"] == "buy":
            buys[raw["mint"]].append((raw["timestamp"], raw["txSigner"]))
    found = 0
    for events in buys.values():
        events.sort()
        for i, (t0, _) in enumerate(events):
            payers = {p for t, p in events[i:] if t - t0 <= 30_000}
            if len(payers) >= 3:
                found += 1
                break
    assert found >= 1


def test_registries_count_the_fee_payers_as_traders(tmp_path):
    from jinx.sim import _Pipeline, _feed, ReplayResult
    import asyncio
    pipe = _Pipeline(tmp_path / "x.db")
    asyncio.run(_feed(pipe, list(enumerate(RECORDS)), ReplayResult(), source="pumpapi"))
    curve_payers = defaultdict_set()
    for r in RECORDS:
        raw = r["raw"]
        trades = raw["action"] in ("buy", "sell") or (raw["action"] == "create" and raw.get("initialBuy", 0) > 0)
        if trades:   # the capture holds SOL-quoted trades only (the feed skips the rest)
            curve_payers[raw["mint"]].add(raw["txSigner"])
    busiest = max(curve_payers, key=lambda m: len(curve_payers[m]))
    assert pipe.core.tokens.get(busiest).unique_traders == len(curve_payers[busiest])
    pipe.store.close()


def defaultdict_set():
    from collections import defaultdict
    return defaultdict(set)


def test_migrations_in_the_capture_are_stored_as_migration_events(tmp_path):
    if not any(r["raw"]["action"] == "migrate" for r in RECORDS):
        pytest.skip("no migration happened during this capture")
    from jinx.store import Store
    replay(RECORDS, tmp_path / "m.db", source="pumpapi")
    s = Store(tmp_path / "m.db")
    kinds = {r["kind"] for r in s.conn.execute("SELECT kind FROM events")}
    assert EventKind.MIGRATION.value in kinds
    s.close()
