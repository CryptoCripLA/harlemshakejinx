"""Storage repair (owner order 2026-10-21 §1-§3) for the intelligence trackers: every per-token collection has an
explicit expiry and a finite capacity, no tracker keeps lists of full MarketEvent objects or unrelated buyer
archives, held tokens are never evicted to meet a cache limit, and incomplete history is never presented as
clean."""
from __future__ import annotations

from jinx.core.events import EventKind, MarketEvent, Side
from jinx.intel.bundles import BundleTracker

S = 1_000_000_000


def ev(kind, mint, slot, trader, sig, side=None, sol=0, recv_ns=None, raw=None):
    return MarketEvent(kind=kind, source="t", mint=mint, signature=sig, recv_ns=recv_ns if recv_ns is not None else slot,
                       seq=slot, trader=trader, side=side, sol_lamports=sol, token_amount=1 if side else None,
                       slot=slot, raw=raw or {})


def _holds_market_events(obj, seen=None) -> bool:
    seen = seen if seen is not None else set()
    if id(obj) in seen:
        return False
    seen.add(id(obj))
    if isinstance(obj, MarketEvent):
        return True
    if isinstance(obj, dict):
        return any(_holds_market_events(v, seen) for v in list(obj.values()) + list(obj.keys()))
    if isinstance(obj, (list, tuple, set, frozenset)):
        return any(_holds_market_events(v, seen) for v in obj)
    slots = getattr(type(obj), "__slots__", ())
    d = getattr(obj, "__dict__", {})
    return any(_holds_market_events(getattr(obj, s, None), seen) for s in slots) or \
        any(_holds_market_events(v, seen) for v in d.values())


# ---- bundles -----------------------------------------------------------------------------------------------------
def test_bundles_never_keep_full_market_events_for_unknown_tokens():
    t = BundleTracker()
    for i in range(20):
        t.on_event(ev(EventKind.TRADE, "M", 7, f"W{i}", f"b{i}", Side.BUY, 10))   # buys before their create
    assert not _holds_market_events(t._pending)
    t.on_event(ev(EventKind.TOKEN_CREATED, "M", 7, "DEV", "c"))
    b = t.evidence("M")
    assert b.same_slot_buys == 20 and b.distinct_buyers == 20
    assert not _holds_market_events(t)


def test_bundle_pending_groups_expire_on_the_receipt_clock():
    t = BundleTracker()
    t.on_event(ev(EventKind.TRADE, "X", 7, "W", "b", Side.BUY, 10, recv_ns=100 * S))
    t.on_event(ev(EventKind.TOKEN_CREATED, "Y", 9, "DEV", "c", recv_ns=100 * S + t.pending_ttl_ns + 1))
    assert len(t._pending) == 0 and t.stats["pending_expired"] == 1


def test_bundle_tokens_expire_when_idle_but_a_held_token_is_kept():
    t = BundleTracker()
    t.on_event(ev(EventKind.TOKEN_CREATED, "HELD", 1, "D", "c1", recv_ns=1 * S))
    t.on_event(ev(EventKind.TOKEN_CREATED, "IDLE", 1, "D", "c2", recv_ns=1 * S))
    t.sweep(1 * S + t.idle_ttl_ns + 1, frozenset({"HELD"}))
    assert t.evidence("HELD") is not None and t.evidence("IDLE") is None


def test_bundle_evidence_is_marked_incomplete_when_the_bus_dropped_one_of_its_events():
    t = BundleTracker()
    t.on_event(ev(EventKind.TOKEN_CREATED, "M", 7, "DEV", "c"))
    t.on_dropped(ev(EventKind.TRADE, "M", 7, "W", "lost", Side.BUY, 10))
    b = t.evidence("M")
    assert b.complete is False and "dropped" in b.incomplete_reason


def test_a_drop_before_the_create_still_makes_the_evidence_incomplete():
    t = BundleTracker()
    t.on_dropped(ev(EventKind.TRADE, "M", 7, "W", "lost", Side.BUY, 10))
    t.on_event(ev(EventKind.TOKEN_CREATED, "M", 7, "DEV", "c"))
    assert t.evidence("M").complete is False


def test_bundle_buyer_set_is_capped_and_says_so():
    t = BundleTracker()
    t.on_event(ev(EventKind.TOKEN_CREATED, "M", 7, "DEV", "c"))
    for i in range(t.max_buyers + 5):
        t.on_event(ev(EventKind.TRADE, "M", 7, f"W{i}", f"b{i}", Side.BUY, 1))
    b = t.evidence("M")
    assert b.same_slot_buys == t.max_buyers + 5            # counts stay exact
    assert len(b.buyers) == t.max_buyers and b.buyers_capped


# ---- whales -----------------------------------------------------------------------------------------------------
from jinx.intel.whales import WhaleBook  # noqa: E402


def wtrade(mint, trader, t_s, side=Side.BUY, kind=EventKind.TRADE):
    return MarketEvent(kind=kind, source="pumpapi", mint=mint, signature=f"{mint}{trader}{t_s}{side}", recv_ns=t_s * S,
                       seq=t_s, trader=trader, side=side if kind is EventKind.TRADE else None, sol_lamports=1,
                       token_amount=1 if kind is EventKind.TRADE else None, raw={"pool": "pump"})


def test_whales_keep_nothing_about_untracked_buyers():
    book = WhaleBook()
    for i in range(2_000):
        book.on_event(wtrade(f"M{i % 300}", f"W{i}", 1_000 + i))
    assert len(book._tracked_buys) == 0 and len(book._candidates) == 0 and len(book.alerts) == 0


def test_whale_co_buy_evidence_expires_but_a_held_tokens_is_kept():
    book = WhaleBook()
    book.track("A", source="promoted")
    book.on_event(wtrade("HELD", "A", 1_000))
    book.on_event(wtrade("GONE", "A", 1_000))
    book.sweep((1_000 + 1) * S + book.co_buy_ttl_ns, frozenset({"HELD"}))
    assert book.cluster_facts("HELD")["tracked_buyers"] == 1
    assert book.cluster_facts("GONE")["tracked_buyers"] == 0


def test_a_dropped_tracked_trade_makes_cluster_facts_incomplete():
    book = WhaleBook()
    book.track("A", source="promoted")
    book.on_dropped(wtrade("M", "A", 1_000))
    f = book.cluster_facts("M")
    assert f["complete"] is False and "dropped" in f["incomplete_reason"]


def test_a_dropped_migration_still_queues_discovery():
    book = WhaleBook()
    book.on_dropped(wtrade("M", None, 1_000, kind=EventKind.MIGRATION))
    assert set(book.graduated()) == {"M"}


# ---- early momentum ---------------------------------------------------------------------------------------------
from jinx.intel import early_momentum as em_mod  # noqa: E402
from jinx.intel.early_momentum import EarlyMomentum, Funding, Status as EmStatus  # noqa: E402
from tests.pumpapi_msgs import T0_MS, buy, create, events  # noqa: E402


def em_feed(em, msgs):
    out = []
    for m in msgs:
        for e in events(m):
            out += em.on_event(e)
    return out


def at_s(t_s):
    return (T0_MS + int(t_s * 1000)) * 1_000_000


def test_early_momentum_defaults_drop_the_20000_token_two_hour_model():
    assert em_mod.MAX_MINTS <= 12_000 and em_mod.IDLE_TTL_S <= 1_800
    assert em_mod.RETAIN_NS == em_mod.WINDOW_NS + em_mod.LATENESS_ALLOWANCE_NS


def test_an_idle_token_keeps_only_its_compact_record_after_the_buy_window():
    em = EarlyMomentum()
    em_feed(em, [create(initial_buy=0.0, v_quote=31.0), buy("A", 1), buy("B", 2)])
    st = em._mints.get("MintApump")
    assert st.active is not None and len(st.active.buys) == 2
    em.sweep(at_s(3) + em_mod.RETAIN_NS, frozenset())            # last receipt was at 2.05 s
    assert st.active is None                                     # buy list and identities released
    check = em.reserve_check("MintApump", feed_live=True)
    assert check["peak"]["status"] == "PASS" and check["current"]["status"] == "PASS"   # reserve and peak kept


def test_a_token_evicted_while_idle_is_never_eligible_on_revival():
    """Owner order §1: "If peak history becomes incomplete, never mark that token eligible by assuming the missing
    history was safe"."""
    em = EarlyMomentum(idle_ttl_s=60)
    em_feed(em, [create(initial_buy=0.0, v_quote=31.0), buy("A", 1)])
    em.sweep(at_s(1) + 61 * 1_000_000_000, frozenset())
    assert em.tracked_mints() == 0
    em_feed(em, [buy("B", 200), buy("C", 201), buy("D", 202)])  # revives: three new fee payers
    ev = em.evidence("MintApump")
    peak = next(p for p in ev.predicates if p.name == em_mod.PEAK_GATE)
    assert peak.status is EmStatus.UNAVAILABLE and "creation not observed" in peak.detail["reason"]
    assert ev.trade_permitted is False


def test_a_held_token_is_never_evicted():
    em = EarlyMomentum(idle_ttl_s=60, max_mints=2)
    em_feed(em, [create(initial_buy=0.0, v_quote=31.0, mint="HELD"), buy("A", 1, mint="HELD")])
    em.sweep(at_s(1), frozenset({"HELD"}))
    for i in range(5):
        em_feed(em, [buy("X", 500 + i, mint=f"M{i}")])
    em.sweep(at_s(10_000), frozenset({"HELD"}))
    assert em._mints.get("HELD") is not None and em._mints.get("HELD").since_creation


def test_funding_is_kept_only_for_wallets_of_tracked_detections():
    em = EarlyMomentum(idle_ttl_s=60)
    em_feed(em, [create(initial_buy=0.0, v_quote=31.0), buy("A", 1), buy("B", 2), buy("C", 3)])
    for w in ("A", "B", "C", "UNRELATED"):
        em.record_funding(Funding(w, "F" + w, "test", 1))
    em.sweep(at_s(3), frozenset())
    assert set(em._funding) == {"A", "B", "C"}                  # unrelated funding released
    em.sweep(at_s(3) + 61 * 1_000_000_000, frozenset())         # the detection's token went idle and left
    assert em._funding == {} or len(em._funding) == 0
    assert em.known_funder("A") is None


def test_unverified_readings_are_a_count_and_the_latest_signatures_only():
    em = EarlyMomentum()
    st = em_mod._Mint()
    for i in range(50):
        st.note_unverified(f"sig{i}")
    assert st.unverified_n == 50 and len(st.unverified) <= em_mod.UNVERIFIED_KEPT


# ---- migration --------------------------------------------------------------------------------------------------
from jinx.intel.migration import MigrationTracker  # noqa: E402
from tests.pumpapi_msgs import migrate, sell  # noqa: E402


def test_a_creators_sell_delivered_before_its_creation_is_still_in_the_developer_tally():
    """Out-of-order delivery (owner order §4): pre-migration trades are tallied only for the creator, so a creator
    trade that arrives before the creation names the creator is buffered (bounded) and tallied when it does."""
    m = MigrationTracker()
    msgs = [sell("DEV", 2, v_quote=30.5, slot=102, sol=0.5), create(dev="DEV", slot=100, initial_buy=0.0),
            migrate(pool_id="AMM1", t_ms=T0_MS + 60_000, slot=499)]
    for msg in msgs:
        for e in events(msg):
            m.on_event(e)
    dev = m.evidence("MintApump").developer_selling
    assert dev["status"] == "observed" and dev["before"]["sells"] == 1
    assert m._tokens.get("MintApump").pre_creation is None               # released once the creator is known


def test_non_creator_pre_migration_trades_leave_no_per_wallet_tally():
    m = MigrationTracker()
    for msg in [create(dev="DEV", slot=100, initial_buy=0.0)] + [buy(f"W{i}", i * 0.1) for i in range(200)]:
        for e in events(msg):
            m.on_event(e)
    tok = m._tokens.get("MintApump")
    assert set(tok.flow) <= {"DEV"} and len(tok.seen) <= 64
