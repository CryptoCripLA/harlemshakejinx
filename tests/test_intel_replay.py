"""Phase 2 event intel replayed on the live PumpAPI capture (tests/fixtures/pumpapi-live-20261021.jsonl.gz: 1,200
real bonding-curve events, 39 s, recorded on jinx-nyc with JINX's receipt times).

Deterministic: each record keeps its sequence number under reordering, so shuffled and duplicated deliveries must
converge on the same evidence. The Early Momentum detections are checked against an independent scan of the raw
records (PumpAPI field names, no JINX code). Detection delay is event time (PumpAPI's timestamp) to evidence: the
recorded feed delay plus the measured local processing. It is local processing, not order execution."""
from collections import defaultdict
from pathlib import Path

from jinx.intel.replay import replay_events
from jinx.sim import capture_events, load_capture

FIXTURE = Path(__file__).parent / "fixtures" / "pumpapi-live-20261021.jsonl.gz"
RECORDS = load_capture(FIXTURE)


def replay_capture(records, **kw):
    return replay_events(capture_events(records, source="pumpapi", **kw))


def independent_scan(records) -> dict[str, str]:
    """mint -> signature of the curve buy at which three distinct fee payers first bought within 30 s."""
    buys = defaultdict(list)
    for i, r in enumerate(records):
        raw = r["raw"]
        if raw["pool"] != "pump":
            continue
        if raw["action"] == "buy" or (raw["action"] == "create" and raw.get("initialBuy", 0) > 0):
            buys[raw["mint"]].append((raw["timestamp"], i, raw["txSigner"], raw["signature"]))
    found = {}
    for mint, rows in buys.items():
        rows.sort()
        for j, (tj, _, _, sig) in enumerate(rows):
            payers = {p for t, _, p, _ in rows[:j + 1] if tj - t <= 30_000}
            if len(payers) >= 3:
                found[mint] = sig
                break
    return found


def test_early_momentum_detects_exactly_what_an_independent_scan_of_the_capture_finds():
    run = replay_capture(RECORDS)
    expected = independent_scan(RECORDS)
    assert len(expected) >= 5
    assert {e.mint: e.trigger.signature for e in run.momentum} == expected
    assert all(not e.trade_permitted for e in run.momentum)          # B-02 is unresolved: nothing may trade


def test_shuffled_and_duplicated_delivery_converges_on_the_same_evidence():
    base = replay_capture(RECORDS)
    for kw in ({"shuffle_seed": 7}, {"shuffle_seed": 11, "duplicate_every": 3}):
        other = replay_capture(RECORDS, **kw)
        assert len(other.momentum) == len(base.momentum)
        assert other.final_windows() == base.final_windows()


def test_detection_delay_is_reported_as_percentiles():
    report = replay_capture(RECORDS).report()
    em = report["early_momentum"]
    assert em["detections"] >= 5
    for name in ("feed_delay_ms", "processing_us", "detection_delay_ms"):
        assert set(em[name]) == {"p50", "p95", "p99", "max", "n"}
    assert em["detection_delay_ms"]["p50"] >= em["feed_delay_ms"]["p50"]
    assert report["per_event_processing_us"]["early_momentum"]["n"] == report["events"]
