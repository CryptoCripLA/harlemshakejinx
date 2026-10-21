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
    assert all(not e.trade_permitted for e in run.momentum)          # offline replay: funders unknown, nothing may trade


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


def test_graduation_reads_every_curve_mint_and_converges_under_reordering():
    base = replay_capture(RECORDS)
    curve_mints = {r["raw"]["mint"] for r in RECORDS if r["raw"]["pool"] == "pump"}
    latest = base.graduation_latest()
    assert set(latest) == curve_mints
    for m in curve_mints:
        reading = base.graduation.evidence(m).latest
        assert (reading.progress is not None) is reading.standard_curve
    for kw in ({"shuffle_seed": 3}, {"shuffle_seed": 5, "duplicate_every": 4}):
        assert replay_capture(RECORDS, **kw).graduation_latest() == latest
    report = base.report()
    assert report["graduation"]["mints"] == len(curve_mints) and report["graduation"]["not_standard_curves"] == 26
    assert report["per_event_processing_us"]["graduation"]["n"] == report["events"]


def test_the_lifecycle_feed_follows_every_curve_mint_and_infers_no_migration():
    """The capture holds no migration, so none may appear: graduation is never inferred from prices or progress."""
    from jinx.core.lifecycle import State
    run = replay_capture(RECORDS)
    curve_mints = {r["raw"]["mint"] for r in RECORDS if r["raw"]["pool"] == "pump"}
    states = {run.migration.lifecycle(m).state for m in curve_mints}
    assert states <= {State.NEWLY_DETECTED, State.ON_BONDING_CURVE} and State.ON_BONDING_CURVE in states
    assert all(run.migration.evidence(m) is None for m in curve_mints)
    report = run.report()
    assert report["migration"]["migrations"] == 0
    assert report["per_event_processing_us"]["migration"]["n"] == report["events"]
