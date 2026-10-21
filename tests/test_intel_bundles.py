"""R1 bundle evidence (owner completeness requirements 2026-10-21): buys that land in the same slot as the token's
creation, from PumpAPI's slot-ordered stream. Facts only (counts, signers, SOL); no rejection threshold invented."""
import gzip
import json
from pathlib import Path

from jinx.adapters.pumpapi.normalize import normalize
from jinx.core.events import EventKind, MarketEvent, Side
from jinx.intel.bundles import BundleTracker


def ev(kind, mint, slot, trader, sig, side=None, sol=0):
    return MarketEvent(kind=kind, source="t", mint=mint, signature=sig, recv_ns=slot, seq=slot, trader=trader,
                       side=side, sol_lamports=sol, token_amount=1 if side else None, slot=slot)


def test_same_slot_buys_are_counted_with_distinct_signers_and_sol():
    t = BundleTracker()
    t.on_event(ev(EventKind.TOKEN_CREATED, "M", 100, "DEV", "c"))
    t.on_event(ev(EventKind.TRADE, "M", 100, "DEV", "b0", Side.BUY, 500))       # creator's own buy, same slot
    t.on_event(ev(EventKind.TRADE, "M", 100, "W1", "b1", Side.BUY, 1_000))
    t.on_event(ev(EventKind.TRADE, "M", 100, "W1", "b2", Side.BUY, 2_000))      # same wallet again
    t.on_event(ev(EventKind.TRADE, "M", 100, "W2", "s1", Side.SELL, 9_999))     # sells are not buys
    t.on_event(ev(EventKind.TRADE, "M", 101, "W3", "late", Side.BUY, 7_000))    # next slot: not in the bundle
    b = t.evidence("M")
    assert b.create_slot == 100 and b.same_slot_buys == 3 and b.distinct_buyers == 2
    assert b.creator_bought_same_slot and b.same_slot_buy_lamports == 3_500 and b.final


def test_a_buy_seen_before_its_create_is_still_attributed():
    t = BundleTracker()
    t.on_event(ev(EventKind.TRADE, "M", 7, "W1", "b1", Side.BUY, 10))
    t.on_event(ev(EventKind.TOKEN_CREATED, "M", 7, "DEV", "c"))
    assert t.evidence("M").same_slot_buys == 1


def test_unknown_tokens_have_no_evidence_and_state_is_bounded():
    t = BundleTracker(max_tokens=10, max_pending=10)
    for i in range(50):
        t.on_event(ev(EventKind.TOKEN_CREATED, f"M{i}", i, "D", f"c{i}"))
        t.on_event(ev(EventKind.TRADE, f"X{i}", i, "W", f"p{i}", Side.BUY, 1))   # buys for mints never created
    assert t.evidence("nope") is None and len(t._tokens) <= 10 and len(t._pending) <= 10


def test_live_capture_replay_produces_consistent_bundle_facts():
    recs = [json.loads(l) for l in gzip.open(Path(__file__).parent / "fixtures" / "pumpapi-live-20261021.jsonl.gz", "rt")]
    seq = iter(range(1, 10**6))
    t = BundleTracker()
    for r in recs:
        for e in normalize(r["raw"], r["recv_ns"], seq.__next__):
            t.on_event(e)
    created = [m for m, b in t._tokens.items()]
    assert created, "the capture contains creations"
    for m in created:
        b = t.evidence(m)
        assert b.distinct_buyers <= b.same_slot_buys and b.same_slot_buy_lamports >= 0
