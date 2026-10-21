"""Migration evidence (M1) on real migrations captured live from the free PumpAPI stream."""
from pathlib import Path

from jinx.core.lifecycle import State
from jinx.intel.replay import replay_events
from jinx.sim import capture_events, load_capture


MIG_FIXTURE = Path(__file__).parent / "fixtures" / "pumpapi-migrations-live-20261021.jsonl.gz"


def test_live_migrations_are_verified_on_the_pool_they_announced():
    """Live capture (scripts/intel_live_check.py on jinx-nyc): for each migration, the mint's last curve records, the
    migrate record and its first PumpSwap records. Checked against an independent scan of the raw records, under
    in-order, shuffled and duplicated delivery."""
    records = load_capture(MIG_FIXTURE)
    migrates = {r["raw"]["mint"]: r["raw"] for r in records if r["raw"]["action"] == "migrate"}
    assert len(migrates) >= 3
    expected = {}
    for i, r in enumerate(records):
        raw = r["raw"]
        mig = migrates.get(raw["mint"])
        if mig and raw["pool"] == "pump-amm" and raw["action"] in ("buy", "sell") \
                and raw["poolId"] == mig["poolId"] and raw["block"] >= mig["block"]:
            key = (raw["timestamp"], 2 * i + 1)
            if raw["mint"] not in expected or key < expected[raw["mint"]][0]:
                expected[raw["mint"]] = (key, raw["signature"])
    assert expected
    for kw in ({}, {"shuffle_seed": 2}, {"shuffle_seed": 9, "duplicate_every": 3}):
        run = replay_events(capture_events(records, source="pumpapi", **kw))
        for mint, mig in migrates.items():
            ev = run.migration.evidence(mint)
            assert (ev.migration.signature, ev.migration.pool_id, ev.migration.slot) == \
                (mig["signature"], mig["poolId"], mig["block"])
            life = run.migration.lifecycle(mint)
            if mint in expected:
                assert ev.verified and ev.first_verified_liquidity["signature"] == expected[mint][1]
                assert life.state is State.POST_MIGRATION_MARKET and life.post_pool == mig["poolId"]
            else:
                assert not ev.verified and life.state is State.MIGRATION_OBSERVED
