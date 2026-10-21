"""One migration, reported by two feeds, becomes ONE enriched record (owner order 2026-10-21, integration item 2).

Defect (2da8525): PumpAPI's and PumpPortal's reports of the same migration had the same MarketEvent identity
(`SIG:migration:MINT:-:-`), so the bus and the events table dropped whichever came second. When PumpPortal's
venue-only report won, PumpAPI's pool address never reached the migration module and the migration could never be
verified. Owner: "Deduplicate effects, not useful information." "A partial PumpPortal report must never prevent a
later PumpAPI report containing the pool address from confirming migration." "Do not overwrite authoritative on-chain
event times with receipt times."
"""
import asyncio
from itertools import count

from jinx.adapters.mako.normalize import normalize as pumpportal_normalize
from jinx.core.bus import EventBus, Lane
from jinx.core.events import EventKind
from jinx.core.lifecycle import Evidence, Kind, State, TokenLifecycle
from jinx.intel.migration import MigrationTracker
from jinx.intel.readings import Clock
from jinx.intel.whales import WhaleBook
from tests.pumpapi_msgs import T0_MS, amm_trade, buy, create, events, migrate

MIG_T = T0_MS + 60_000
PP_MIGRATE = {"signature": "MIGTX", "mint": "MintApump", "txType": "migrate", "pool": "pump-amm"}


def pumpportal(msg, recv_ns):
    return pumpportal_normalize(msg, recv_ns, count(9_000).__next__)


def pumpapi_migrate(**kw):
    return events(migrate(sig="MIGTX", pool_id="AMM1", t_ms=MIG_T, slot=499, **kw))


def curve():
    return [ev for m in (create(dev="DEV", slot=100), buy("W1", 1, v_quote=114.0, slot=480)) for ev in events(m)]


def pool_trade(dt_s=0.4, slot=500, pool_id="AMM1"):
    return events(amm_trade("buy", payer="S1", t_ms=MIG_T + int(dt_s * 1000), slot=slot, pool_id=pool_id))


def test_the_two_reports_have_different_identities_and_a_resend_still_deduplicates():
    [pp] = pumpportal(PP_MIGRATE, 1)
    [api] = pumpapi_migrate()
    [api_again] = pumpapi_migrate()
    assert pp.signature == api.signature == "MIGTX"
    assert pp.idempotency_key != api.idempotency_key
    assert api.idempotency_key == api_again.idempotency_key


def test_the_bus_delivers_both_reports_once_each():
    bus = EventBus()                                  # no events table since the storage repair (2026-10-21)
    got = []
    bus.subscribe(Lane.FAST, got.append)

    async def main():
        runner = asyncio.create_task(bus.run())
        [pp] = pumpportal(PP_MIGRATE, (MIG_T + 90) * 1_000_000)
        [api] = pumpapi_migrate()
        for ev in (pp, api, pp, api):
            await bus.publish(ev)
        await bus.drain()
        runner.cancel()
    asyncio.run(main())
    assert [(e.source, e.kind) for e in got] == [("mako.pumpportal", EventKind.MIGRATION),
                                                 ("pumpapi.stream", EventKind.MIGRATION)]
    assert bus.stats["duplicates"] == 2


def test_a_venue_only_report_first_never_prevents_the_pool_report_from_confirming():
    heard = []
    m = MigrationTracker(notify=heard.append)
    for ev in curve():
        m.on_event(ev)
    [pp] = pumpportal(PP_MIGRATE, (MIG_T + 90) * 1_000_000)
    [observed] = m.on_event(pp)
    assert observed.migration.pool_id is None and observed.verified is False
    assert m.on_event(pool_trade()[0]) == []                       # the pool is not known yet: held, not discarded
    [enriched, confirmed] = m.on_event(pumpapi_migrate()[0])
    assert enriched.migration.pool_id == "AMM1" and confirmed.verified is True
    record = m.evidence("MintApump").migration
    assert record.signature == "MIGTX" and record.slot == 499
    assert (record.event_ns, record.clock) == (MIG_T * 1_000_000, "provider")   # PumpAPI's time, not receipt time
    assert record.sources == ("mako.pumpportal", "pumpapi.stream")
    assert m.lifecycle("MintApump").state is State.POST_MIGRATION_MARKET
    assert [e.verified for e in heard] == [False, False, True]     # observed, enriched with the pool, confirmed


def test_a_venue_only_report_after_the_pool_report_only_adds_its_provenance():
    heard = []
    m = MigrationTracker(notify=heard.append)
    for ev in curve():
        m.on_event(ev)
    m.on_event(pumpapi_migrate()[0])
    m.on_event(pool_trade()[0])
    before = m.lifecycle("MintApump").history[:]
    [pp] = pumpportal(PP_MIGRATE, (MIG_T + 90) * 1_000_000)
    assert m.on_event(pp) == []                                     # no second effect
    record = m.evidence("MintApump").migration
    assert record.pool_id == "AMM1" and (record.event_ns, record.clock) == (MIG_T * 1_000_000, "provider")
    assert record.sources == ("pumpapi.stream", "mako.pumpportal")
    assert [r.observed_ns for r in record.reports][1] == (MIG_T + 90) * 1_000_000
    assert m.lifecycle("MintApump").history == before and len(heard) == 2


def test_the_lifecycle_keeps_the_providers_migration_time_over_a_receipt_time():
    lc = TokenLifecycle("M")
    lc.observe(Evidence(Kind.ON_CURVE, "pumpapi.stream", 10, 10, "provider", {"v_sol_lamports": 1}))
    lc.observe(Evidence(Kind.MIGRATION_SIGNAL, "mako.pumpportal", 100, 100, "receipt", {}))
    assert lc.observe(Evidence(Kind.MIGRATION_SIGNAL, "pumpapi.stream", 101, 60, "provider", {})) is None
    first = lc.fields["first_migration_signal"]
    assert (first.value, first.clock, first.source) == (60, "provider", "pumpapi.stream")
    assert [t.to_state for t in lc.history].count(State.MIGRATION_OBSERVED) == 1


def test_a_receipt_time_never_replaces_the_providers_migration_time():
    lc = TokenLifecycle("M")
    lc.observe(Evidence(Kind.MIGRATION_SIGNAL, "pumpapi.stream", 61, 60, "provider", {}))
    lc.observe(Evidence(Kind.MIGRATION_SIGNAL, "mako.pumpportal", 100, 100, "receipt", {}))
    first = lc.fields["first_migration_signal"]
    assert (first.value, first.clock) == (60, "provider")


def test_other_consumers_act_once_per_migration():
    """Both reports of one migration queue its token for whale discovery once."""
    whales = WhaleBook()
    for ev in curve():
        whales.on_event(ev)
    [pp] = pumpportal(PP_MIGRATE, 1)
    whales.on_event(pp)
    queued = whales.graduated()
    whales.on_event(pumpapi_migrate()[0])
    assert whales.graduated() == queued and list(queued) == ["MintApump"]
    assert whales.stats["graduated_queued"] == 1


def test_gmgn_reports_only_enrich_tokens_jinx_already_tracks():
    m = MigrationTracker()
    assert m.record_report("NeverSeen", "gmgn.trenches", 5 * 10**9, Clock.PROVIDER, 6 * 10**9) == []
    assert not m.tracks("NeverSeen") and m.stats["provider_reports_untracked"] == 1


def test_a_record_without_a_pool_asks_for_it_and_gmgns_migrated_pool_lets_it_verify():
    asked = []
    m = MigrationTracker(request_pool=asked.append)
    for ev in curve():
        m.on_event(ev)
    [pp] = pumpportal(PP_MIGRATE, (MIG_T + 90) * 1_000_000)
    m.on_event(pp)
    assert asked == ["MintApump"]
    m.on_event(pool_trade()[0])                                     # held: no pool named yet
    gmgn_t = (MIG_T // 1000) * 1_000_000_000                       # GMGN's migrated_timestamp is whole seconds
    [enriched, confirmed] = m.record_report("MintApump", "gmgn.getTokenInfo", gmgn_t, Clock.PROVIDER,
                                            (MIG_T + 2_000) * 1_000_000, pool="AMM1")
    assert enriched.migration.pool_id == "AMM1" and confirmed.verified is True
    record = m.evidence("MintApump").migration
    assert (record.event_ns, record.clock, record.source) == (gmgn_t, "provider", "gmgn.getTokenInfo")
    m.on_event(pumpapi_migrate()[0])                                # the transaction report itself, later
    record = m.evidence("MintApump").migration
    assert (record.event_ns, record.source, record.slot) == (MIG_T * 1_000_000, "pumpapi.stream", 499)
    assert record.sources == ("mako.pumpportal", "gmgn.getTokenInfo", "pumpapi.stream")


def test_a_whole_second_provider_time_never_replaces_the_transaction_reports_time():
    m = MigrationTracker()
    for ev in curve():
        m.on_event(ev)
    m.on_event(pumpapi_migrate()[0])
    assert m.record_report("MintApump", "gmgn.trenches", (MIG_T // 1000) * 10**9, Clock.PROVIDER,
                           (MIG_T + 3_000) * 1_000_000, venue_name="pump_amm") == []
    record = m.evidence("MintApump").migration
    assert (record.event_ns, record.source) == (MIG_T * 1_000_000, "pumpapi.stream")


def test_gmgns_near_completion_status_is_the_lifecycles_approaching_graduation():
    """Owner order item 6: near graduation comes from the provider's verified status, not a JINX number."""
    m = MigrationTracker()
    for ev in curve():
        m.on_event(ev)
    m.record_provider_status("MintApump", "new_creation", "gmgn.trenches", (MIG_T - 9_000) * 1_000_000, 0.4)
    assert m.lifecycle("MintApump").state is State.ON_BONDING_CURVE
    m.record_provider_status("MintApump", "near_completion", "gmgn.trenches", (MIG_T - 5_000) * 1_000_000, 0.98)
    life = m.lifecycle("MintApump")
    assert life.state is State.APPROACHING_GRADUATION
    assert (life.fields["category"].value, life.fields["progress"].value) == ("near_completion", 0.98)
    m.record_provider_status("NeverSeen", "near_completion", "gmgn.trenches", 1, 0.9)
    assert not m.tracks("NeverSeen")
    m.on_event(pumpapi_migrate()[0])
    assert m.lifecycle("MintApump").state is State.MIGRATION_OBSERVED
