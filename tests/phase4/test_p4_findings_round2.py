"""Defects found by the Codex Phase 4 review, round 2 (docs/audits/phase4-codex-20261024-round2.md), pinned as strict
xfail tests in the files' owners' names (docs/AGENT_COORDINATION.md). Each turns red (XPASS) when its fix lands, so
the owner removes the marker in the fixing commit. Phase 4 does not edit these jinx/ files."""
import dataclasses
import json

import pytest

from jinx.adapters.rugcheck.client import interpret
from jinx.core.events import EventKind, MarketEvent, Side
from jinx.intel.safety import assess
from jinx.intel.whales import WhaleBook
from tests.conftest import make_trade
from tests.phase4 import harness as h

STORAGE = "storage session local_f5744230"
LEAD = "lead session local_b73d1072"
RC = json.loads((h.FIXTURES / "rugcheck_report_sample_20261021.json").read_text())["report"]
SEC = json.loads((h.FIXTURES / "gmgn_security_sample_20261021.json").read_text())["security"]
S = 1_000_000_000


# ---- P4-F13: malformed RugCheck authority values read as revoked -----------------------------------------------------

@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F13 (Codex round 2, finding 1): jinx/adapters/rugcheck/client.py "
                   f"interpret() tests mintAuthority/freezeAuthority by truthiness, so an empty string, false, 0, [] or {{}} "
                   f"reads as 'no authority' and, with rugged false, the token PASSES; RugCheck's documented 'revoked' "
                   f"value is null. Owner: {LEAD}.")
@pytest.mark.parametrize("bad", ["", False, 0, [], {}])
def test_an_unreadable_rugcheck_authority_value_is_never_a_pass(bad):
    for field in ("mintAuthority", "freezeAuthority"):
        report = interpret({**RC, field: bad})
        assert report["verdict"] != "PASS", (field, bad)
        assert assess("M", SEC, 0, report).verdict.value != "PASS", (field, bad)


# ---- P4-F8b: whale discovery keeps whichever migration report arrived first ------------------------------------------

def migration(source: str, recv_s: int, block_time: int | None = None) -> MarketEvent:
    return MarketEvent(kind=EventKind.MIGRATION, source=source, mint="GradMintpump", signature=f"M-{source}",
                       recv_ns=recv_s * S, seq=recv_s, block_time=block_time)


@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F8b (Codex round 2, finding 2): jinx/intel/whales.py keeps the FIRST "
                   f"migration report's time and clock for a token; a receipt-timed report that arrives before the "
                   f"chain-timed one fixes the wrong graduation time, so which wallets count as pre-graduation buyers "
                   f"depends on arrival order. Owner: {STORAGE}.")
def test_the_graduation_time_used_for_discovery_does_not_depend_on_which_report_arrived_first():
    receipt = migration("mako.pumpportal", recv_s=200)                 # receipt clock only
    chain = migration("test.chain", recv_s=210, block_time=100)        # the same migration, on the chain clock
    first, second = WhaleBook(), WhaleBook()
    for ev in (receipt, chain):
        first.on_event(ev)
    for ev in (chain, receipt):
        second.on_event(ev)
    assert first._graduated["GradMintpump"] == second._graduated["GradMintpump"]
    assert first._graduated["GradMintpump"][0] == 100 * S          # the best clock wins, whichever came first


# ---- P4-F14: the dropped-event path ignores the whale token cap -------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=f"FINDING P4-F14 (Codex round 2, finding 3): jinx/intel/whales.py on_dropped() "
                   f"inserts into _tracked_buys without the max_tokens cap, the clock or expiry; it runs whenever the "
                   f"background lane is full, i.e. exactly under load. Owner: {STORAGE}.")
def test_dropped_events_respect_the_whale_token_cap():
    book = WhaleBook(max_tokens=2)
    book.track("W", "test", {"why": "labelled test fixture"})
    for i in range(10_000):
        book.on_dropped(dataclasses.replace(make_trade(mint=f"DropMint{i}", trader="W", side=Side.BUY,
                                                       sig=f"D{i}"), slot=1))
    assert len(book._tracked_buys) <= 2
