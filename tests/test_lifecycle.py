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
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 2, post_pool="P", exchange="pump_amm"))
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


def test_a_failed_observation_is_an_exceptional_state_that_fresh_evidence_clears():
    """Mandate §3: exceptional states for unknown, stale, failed or conflicting observations (round 8 M3)."""
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.ON_CURVE, 1))
    t = lc.record_failure(source="gmgn.trenches", observed_ns=2, reason="HTTP 429")
    assert t.to_state is State.FAILED and lc.state is State.FAILED
    assert lc.last_consistent_state is State.ON_BONDING_CURVE and lc.failures[-1].reason == "HTTP 429"
    assert lc.record_failure(source="gmgn.trenches", observed_ns=3, reason="timeout") is None   # one transition
    assert len(lc.failures) == 2
    lc.observe(ev(Kind.ON_CURVE, 4))
    assert lc.state is State.ON_BONDING_CURVE


def test_peak_virtual_sol_is_kept_across_the_lifecycle():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.ON_CURVE, 1, v_sol_lamports=40))
    lc.observe(ev(Kind.ON_CURVE, 2, v_sol_lamports=60))
    lc.observe(ev(Kind.NEAR_COMPLETION, 3, v_sol_lamports=25))
    assert lc.peak_v_sol_lamports == 60


def test_a_late_report_never_overwrites_newer_pool_identity():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 10, event_ns=10, post_pool="NEW", pre_pool="CURVE2", exchange="pump_amm"))
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 11, event_ns=5, post_pool="OLD", pre_pool="CURVE1", exchange="pump_amm"))  # late, older
    assert lc.post_pool == "NEW" and lc.pre_pool == "CURVE2"


def test_a_late_report_still_counts_toward_the_reserve_peak():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.ON_CURVE, 10, event_ns=10, v_sol_lamports=40))
    lc.observe(ev(Kind.ON_CURVE, 11, event_ns=5, v_sol_lamports=90))   # an earlier, higher reserve reading
    assert lc.peak_v_sol_lamports == 90 and lc.state is State.ON_BONDING_CURVE


def test_fresh_evidence_consistent_with_a_confirmed_migration_recovers_from_stale():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 1_000, post_pool="P", exchange="pump_amm"))
    lc.mark_stale_if_older_than(now_ns=5_000, max_age_ns=1_000)
    t = lc.observe(ev(Kind.MIGRATION_SIGNAL, 5_100))          # a repeated migration report, fresh
    assert t is not None and t.to_state is State.MIGRATION_CONFIRMED and lc.state is State.MIGRATION_CONFIRMED
    assert [x.to_state for x in lc.history].count(State.MIGRATION_OBSERVED) == 0   # never moved backwards


def test_without_an_exceptional_state_a_repeated_earlier_stage_report_changes_nothing():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 1, post_pool="P", exchange="pump_amm"))
    assert lc.observe(ev(Kind.MIGRATION_SIGNAL, 2)) is None and lc.state is State.MIGRATION_CONFIRMED


# ---- Codex round 8 (docs/audits/phase1-codex-20261020-round8.md) ---------------------------------
def test_post_migration_market_needs_a_confirmed_migration_first():          # H2
    lc = TokenLifecycle("MINT")
    with pytest.raises(MissingEvidence):
        lc.observe(ev(Kind.POST_MIGRATION_MARKET, 1, price=2.0))
    assert lc.state is State.UNKNOWN and lc.history == []
    lc.observe(ev(Kind.MIGRATION_SIGNAL, 2))                                # observed, not confirmed
    with pytest.raises(MissingEvidence):
        lc.observe(ev(Kind.POST_MIGRATION_MARKET, 3, price=2.0))
    assert lc.state is State.MIGRATION_OBSERVED


@pytest.mark.parametrize("payload", [{"post_pool": "P"}, {"exchange": "pump_amm"}, {"post_pool": "", "exchange": "x"},
                                     {"post_pool": 7, "exchange": "pump_amm"}])
def test_confirmation_needs_pool_venue_and_time(payload):                   # H2
    lc = TokenLifecycle("MINT")
    with pytest.raises(MissingEvidence):
        lc.observe(ev(Kind.MIGRATION_CONFIRMED, 1, **payload))
    with pytest.raises(MissingEvidence):
        lc.observe(ev(Kind.MIGRATION_CONFIRMED, 1, event_ns=0, post_pool="P", exchange="pump_amm"))
    assert lc.state is State.UNKNOWN and lc.post_pool is None


def test_a_late_report_cannot_change_the_confirmed_pool_or_refresh_freshness():   # M1 (Codex reproduction)
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 1_000, event_ns=1_000, post_pool="VALID", exchange="pump_amm"))
    assert lc.observe(ev(Kind.ON_CURVE, 2_400, event_ns=500, post_pool="OLD")) is None
    assert lc.post_pool == "VALID" and lc.state is State.MIGRATION_CONFIRMED
    assert lc.last_received_ns == 2_400 and lc.last_accepted_ns == 1_000
    t = lc.mark_stale_if_older_than(now_ns=2_500, max_age_ns=1_000)       # the late report did not refresh it
    assert t is not None and t.to_state is State.STALE


def test_a_second_confirmation_naming_another_pool_is_a_conflict():         # M2
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 1, post_pool="A", exchange="pump_amm"))
    t = lc.observe(ev(Kind.MIGRATION_CONFIRMED, 2, post_pool="B", exchange="pump_amm"))
    assert t is not None and t.to_state is State.CONFLICTING
    assert lc.post_pool == "A" and [c.payload["post_pool"] for c in lc.conflicts] == ["B"]
    assert lc.observe(ev(Kind.MIGRATION_CONFIRMED, 3, post_pool="A", exchange="pump_amm")).to_state is State.MIGRATION_CONFIRMED


def test_a_repeated_contradiction_is_kept_but_makes_one_transition():      # M2
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_SIGNAL, 1))
    first = lc.observe(ev(Kind.ON_CURVE, 2))
    again = lc.observe(ev(Kind.ON_CURVE, 3))
    assert first.to_state is State.CONFLICTING and again is None
    assert [t.to_state for t in lc.history].count(State.CONFLICTING) == 1 and len(lc.conflicts) == 2


def test_post_migration_data_from_another_pool_is_a_conflict():             # M2
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 1, post_pool="A", exchange="pump_amm"))
    t = lc.observe(ev(Kind.POST_MIGRATION_MARKET, 2, pool="Z", price=1.0))
    assert t.to_state is State.CONFLICTING and lc.post_pool == "A"


def test_the_mandated_lifecycle_record_is_kept_with_provenance():           # M3 (mandate §3.1)
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.CREATED, 10, source="mako.pumpportal", launchpad="Pump.fun", pre_pool="CURVE"))
    lc.observe(ev(Kind.NEAR_COMPLETION, 20, category="near_completion", progress=0.95, v_sol_lamports=80))
    lc.observe(ev(Kind.MIGRATION_SIGNAL, 30))
    lc.observe(ev(Kind.MIGRATION_SIGNAL, 31))
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 40, post_pool="AMM", exchange="pump_amm", category="completed"))
    lc.observe(ev(Kind.POST_MIGRATION_MARKET, 50, source="gmgn.token_info", price=0.5, liquidity=1_000.0))
    lc.observe(ev(Kind.POST_MIGRATION_MARKET, 60, price=-1, liquidity="n/a"))   # not a valid price or liquidity
    f = lc.fields
    assert f["launchpad"].value == "Pump.fun" and f["launchpad"].source == "mako.pumpportal"
    assert f["created"].event_ns == 10 and f["pre_pool"].value == "CURVE"
    assert f["progress"].value == 0.95 and f["category"].value == "completed"
    assert f["first_migration_signal"].event_ns == 30            # the first one, not the repeat
    assert f["post_pool"].value == "AMM" and f["post_pool"].event_ns == 40 and f["exchange"].value == "pump_amm"
    assert (f["price"].value, f["price"].observed_ns, f["liquidity"].value) == (0.5, 50, 1_000.0)
    assert lc.peak_v_sol_lamports == 80
    assert lc.history[-1].payload["price"] == 0.5 and lc.history[0].source == "mako.pumpportal"


def test_a_creation_report_never_moves_a_token_back_to_newly_detected():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.ON_CURVE, 1))
    assert lc.observe(ev(Kind.CREATED, 2)) is None and lc.state is State.ON_BONDING_CURVE


# ---- Codex round 9 (docs/audits/phase1-codex-20261020-round9.md) ---------------------------------
def test_a_migration_indexed_after_a_newer_curve_read_is_still_recognized():   # R9-H1 (Codex reproduction)
    """Curve reads carry the read time; confirmations carry the provider's migration time. They are different
    clocks, so a confirmation is not 'late' just because a curve read happened after the migration."""
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.NEAR_COMPLETION, 100, event_ns=100))
    t = lc.observe(ev(Kind.MIGRATION_CONFIRMED, 110, event_ns=90, post_pool="P", exchange="pump_amm"))
    assert t is not None and lc.state is State.MIGRATION_CONFIRMED and lc.post_pool == "P"
    assert lc.fields["post_pool"].event_ns == 90


def test_a_migration_signal_indexed_late_is_still_recognized():                # R9-H1
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.NEAR_COMPLETION, 100, event_ns=100))
    assert lc.observe(ev(Kind.MIGRATION_SIGNAL, 110, event_ns=90)).to_state is State.MIGRATION_OBSERVED


def test_curve_data_older_than_the_migration_is_history_not_a_conflict():      # R9-H1
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.MIGRATION_CONFIRMED, 100, event_ns=90, post_pool="P", exchange="pump_amm"))
    assert lc.observe(ev(Kind.ON_CURVE, 105, event_ns=80, progress=0.5)) is None
    assert lc.state is State.MIGRATION_CONFIRMED and lc.conflicts == [] and "progress" not in lc.fields
    assert lc.observe(ev(Kind.ON_CURVE, 130, event_ns=120)).to_state is State.CONFLICTING   # newer: a conflict


def test_a_delayed_creation_report_keeps_its_metadata_without_moving_the_state():   # R9-H1 / M3
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.ON_CURVE, 100, event_ns=100))
    assert lc.observe(ev(Kind.CREATED, 105, event_ns=50, launchpad="Pump.fun", pre_pool="CURVE")) is None
    assert lc.state is State.ON_BONDING_CURVE
    assert (lc.fields["created"].event_ns, lc.fields["launchpad"].value, lc.pre_pool) == (50, "Pump.fun", "CURVE")


def test_an_older_value_never_replaces_a_newer_one_of_the_same_field():
    lc = TokenLifecycle("MINT")
    lc.observe(ev(Kind.NEAR_COMPLETION, 100, event_ns=100, progress=0.95))
    lc.observe(ev(Kind.MIGRATION_SIGNAL, 110, event_ns=90, progress=0.9))       # accepted, but its progress is older
    assert lc.fields["progress"].value == 0.95
