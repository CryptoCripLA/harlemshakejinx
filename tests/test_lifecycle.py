"""Token lifecycle interface: launch -> bonding curve -> graduation -> migration -> post-migration market.
Owner mandate 2026-10-20, section 3. Phase 1 defines the interface and its transition rules; Phase 2 feeds it
GMGN Trenches / pool data and the Pump.fun stream."""
import pytest

from jinx.core.lifecycle import Evidence, Kind, MissingEvidence, State, TokenLifecycle


def ev(kind, t, source="gmgn.trenches", **payload):
    return Evidence(kind=kind, source=source, observed_ns=t, event_ns=payload.pop("event_ns", t), payload=payload)


def test_normal_path_records_every_transition_with_source_and_time():
    lc = TokenLifecycle("MINT")
    assert lc.state is State.UNKNOWN
    lc.observe(ev(Kind.CREATED, 1, source="mako.pumpportal"))
    lc.observe(ev(Kind.ON_CURVE, 2, progress=0.2))
    lc.observe(ev(Kind.NEAR_COMPLETION, 3, progress=0.95))
    lc.observe(ev(Kind.MIGRATION_SIGNAL, 4))
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 5, post_pool="POOL2", exchange="pump_amm"))
    lc.observe(ev(Kind.POST_MIGRATION_MARKET, 6, price=1.0))
    assert [t.to_state for t in lc.history] == [State.NEWLY_DETECTED, State.ON_BONDING_CURVE,
                                                State.APPROACHING_GRADUATION, State.MIGRATION_OBSERVED,
                                                State.MIGRATION_CONFIRMED, State.POST_MIGRATION_MARKET]
    assert lc.history[0].source == "mako.pumpportal" and lc.history[-1].observed_ns == 6
    assert lc.post_pool == "POOL2"


def test_a_repeated_migration_report_produces_one_migration_transition():
    lc = TokenLifecycle("MINT")
    for t in (1, 2):
        lc.observe(ev(Kind.ON_CURVE, t))
    first = lc.observe(ev(Kind.MIGRATION_SIGNAL, 3))
    again = lc.observe(ev(Kind.MIGRATION_SIGNAL, 4, source="mako.pumpportal"))
    assert first is not None and again is None
    assert [t.to_state for t in lc.history].count(State.MIGRATION_OBSERVED) == 1


def test_migration_is_confirmed_only_with_post_migration_pool_identity():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_SIGNAL, 1))
    with pytest.raises(MissingEvidence):
        lc.observe(ev(Kind.MIGRATION_CONFIRMED, 2))   # no pool identity: not confirmed
    assert lc.state is State.MIGRATION_OBSERVED


def test_there_is_no_way_to_infer_migration_from_price_or_absence():
    kinds = {k.name for k in Kind}
    assert not any("PRICE" in k or "ABSENT" in k or "MISSING" in k for k in kinds)


def test_older_evidence_never_moves_the_state_backwards():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.NEAR_COMPLETION, 10, event_ns=10))
    assert lc.observe(ev(Kind.ON_CURVE, 11, event_ns=5)) is None   # late report of an earlier fact
    assert lc.state is State.APPROACHING_GRADUATION


def test_newer_contradicting_evidence_is_flagged_not_silently_applied():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_SIGNAL, 1))
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 2, post_pool="P"))
    t = lc.observe(ev(Kind.ON_CURVE, 3, event_ns=3))                # a curve observation after migration
    assert t is not None and t.to_state is State.CONFLICTING
    assert lc.state is State.CONFLICTING and lc.last_consistent_state is State.MIGRATION_CONFIRMED


def test_stale_is_declared_by_the_caller_with_its_own_age_limit():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.ON_CURVE, 1_000))
    assert lc.mark_stale_if_older_than(now_ns=1_500, max_age_ns=1_000) is None
    t = lc.mark_stale_if_older_than(now_ns=2_500, max_age_ns=1_000)
    assert t.to_state is State.STALE and lc.last_consistent_state is State.ON_BONDING_CURVE
    lc.observe(ev(Kind.ON_CURVE, 2_600))                             # fresh evidence recovers it
    assert lc.state is State.ON_BONDING_CURVE


def test_failed_observation_is_recorded_without_changing_the_state():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.ON_CURVE, 1))
    lc.record_failure(source="gmgn.trenches", observed_ns=2, reason="HTTP 429")
    assert lc.state is State.ON_BONDING_CURVE and lc.failures[-1].reason == "HTTP 429"


def test_peak_virtual_sol_is_kept_across_the_lifecycle():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.ON_CURVE, 1, v_sol_lamports=40))
    lc.observe(ev(Kind.ON_CURVE, 2, v_sol_lamports=60))
    lc.observe(ev(Kind.NEAR_COMPLETION, 3, v_sol_lamports=25))
    assert lc.peak_v_sol_lamports == 60
