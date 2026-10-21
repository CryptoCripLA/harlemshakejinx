# Phase 2 event intel: Early Momentum (C10), graduation (C05), migration (M1–M3)

Built 2026-10-21 on branch `phase2/momentum-migration`, merged to `main` slice by slice. Paper only: none of these
modules places, sizes or permits an order. Status words as in `docs/BUILD_STATUS.md`. Timings are local processing
inside JINX plus the feed's own delivery lag; they are not order execution or landing.

Evidence files:
- `docs/evidence/phase2_event_intel_replay_20261021.json`: replay of the committed live capture
  (`tests/fixtures/pumpapi-live-20261021.jsonl.gz`, 1,200 curve records, 39 s), `scripts/intel_replay_report.py`.
- `docs/evidence/phase2_event_intel_live_20261021.json`: live run on jinx-nyc against the free PumpAPI stream,
  `scripts/intel_live_check.py` (RAM only; the summary keeps no wallet addresses).
- `tests/fixtures/pumpapi-migrations-live-20261021.jsonl.gz`: from an earlier live run the same morning, for each
  migration the mint's last curve records, the migrate record and its first PumpSwap records.

## Wiring contract (for `jinx/app.py`, owned by the other engineer; Early Momentum wired in 59b81eb, graduation and migration not yet)

All three take one `MarketEvent` at a time, synchronously, with no I/O; state is RAM only, bounded (mint cap
20,000, idle 2 h on the events' receipt clock, per-mint caps) and rebuilt from the feeds after a restart.

| Module | Call | Returns / pushes | Lane |
|---|---|---|---|
| `jinx/intel/early_momentum.py::EarlyMomentum` | `on_event(ev)`; `evidence(mint)`; `record_funding(Funding)` | one `EarlyMomentumEvidence` per mint at detection, also pushed to `notify(evidence)` at once; `request_funders(mint, wallets)` asks for the buyers' funders; a funding result that changes the sybil check is pushed again | FAST |
| `jinx/intel/graduation.py::GraduationTracker` | `on_event(ev)`; `evidence(mint)` | `GraduationEvidence` per curve event | either |
| `jinx/intel/migration.py::MigrationTracker` | `on_event(ev)`; `evidence(mint)`; `lifecycle(mint)` | `MigrationEvidence` when the migration is observed and when it is verified, also pushed to `notify`; the mint's `TokenLifecycle` | either |

`notify` and `request_funders` failures are counted (`stats["listener_errors"]`) and never stop detection. Every
evidence object has `to_dict()` with the confluence fields the GMGN mandate lists: token, signal type, sources,
observation time, event time and clock, freshness, payload and blocking status.

One wiring fact to know: PumpAPI and PumpPortal report the same migration transaction with the same
`MarketEvent.idempotency_key`, so the bus delivers whichever arrives first. PumpAPI's carries the new pool and
usually arrives first; when PumpPortal's wins, the migration stays observed but unverifiable (it names the venue,
not the pool), and the evidence says so.

## C10 Early Momentum — TESTING (detection evidence); entry BLOCKED by B-02

- **Input:** PumpAPI curve trades (fee payer = `txSigner`, PumpAPI millisecond time, slot, virtual and real
  reserves) and creations from both feeds.
- **Output:** the earliest moment three distinct fee payers have bought on the curve within 30 s of event time
  (inclusive), as one `EarlyMomentumEvidence`: the window's buys, the trigger, creator flag, live and all-time-peak
  reserves, the retrace from the peak, and six predicates: the window (PASS), the live reserve read, both curve gates
  (UNRESOLVED, B-02), the peak gate (UNRESOLVED, B-02/B-03; UNAVAILABLE without the creation) and the sybil funder
  check (UNAVAILABLE for a buyer without a known funder; FAIL with `emergency_exit` when three or more buyers share
  one). `trade_permitted` is False while any predicate is not PASS.
- **Failure cases tested:** one wallet buying 3 and 12 times; two wallets churning; a third wallet 30.001 s after
  the first; the 30.000 s boundary; sells and PumpSwap buys; a feed backlog delivering a 45 s cluster at once; a
  signal delivered 40 s late; every arrival order of four buys; a late buy inside the window; duplicate delivery; a
  reserve retrace after a higher peak; a late reserve report; unknown, shared and distinct funders; funding learned
  after detection; failing listeners; non-standard curves; bounded state.
- **Tests:** `tests/test_intel_early_momentum.py` (28), `tests/test_intel_replay.py`, `tests/test_intel_readings.py`.
  Five targeted mutants (exclusive boundary, counting buys instead of wallets, arrival time instead of event time,
  no peak, no late merge) each fail at least one test.
- **Replay (committed capture):** 38 detections, identical to an independent scan of the raw records; shuffled and
  duplicated delivery converge. Detection delay p50 672 ms / p95 1,459 ms / p99 1,538 ms, almost all of it the
  receipt lag recorded during that capture (190–1,690 ms); local processing p50 9.7 µs / p99 27 µs.
- **Live:** 176 detections in 600 s, detection delay p50 45.7 ms / p95 50.3 ms / p99 53.6 ms (live section).

## C05 Graduation — TESTING (readings); 0–20 score BLOCKED by B-03

- **Input:** every curve trade and creation; PumpAPI gives virtual and real reserves, PumpPortal virtual only.
- **Output:** `GraduationEvidence` with the latest reading (newest by event time), peak virtual SOL, peak progress,
  completion, and two progress readings with provenance: tokens bought / 793.1M sellable (D3AD `pumpfun.rs:538-540`,
  buy cap at `:448`) and Mako's real SOL / 85 SOL (`src/onchain.py:546-550`). No points (CHAD gives the 0–20 range
  only); an owner mapping is accepted only inside 0–20.
- **Live finding:** 26 of 118 curve mints in the committed capture are not standard 30-SOL curves (virtual SOL as
  low as 0.014, a virtual-minus-real gap that moves trade by trade, 5 with more than 793.1M sellable tokens).
  Readings are flagged `standard_curve` and get no progress; nothing is derived or reinterpreted. Recorded under B-02,
  because neither SOL reading means the same on such a curve.
- **Failure cases tested:** late readings, sells after a peak, completion, PumpPortal-only readings, non-curve
  events, out-of-range owner mappings, bounded state.
- **Tests:** `tests/test_intel_graduation.py` (13), `tests/test_intel_replay.py` (all 118 curve mints read;
  reordering converges). Local processing p50 6 µs / p99 18 µs per event on the replay.

## M1–M3 Migration — TESTING

- **Input:** PumpAPI `migrate` (signature, slot, PumpAPI time, new pool), PumpSwap trades on any pool (pool id and
  reserves after the trade), curve trades, creations; PumpPortal migrations (venue only).
- **Output (M1):** the migration transaction (MIGRATION OBSERVED) and the first verified post-migration liquidity:
  the earliest PumpSwap trade on the announced pool at or after the migration slot, with its reserves and its slot
  and time offsets (MIGRATION CONFIRMED on the migration's own time, then POST-MIGRATION MARKET). Trades on any other
  pool never confirm and never contradict.
- **Output (M2):** pre-migration liquidity and peak; first and latest post-migration liquidity; the 10 largest
  sells around graduation with the reserve after each ("large" is B-03); the creator's sells before and after; the
  top observed net buyers at the migration and their sells after it; the observed holder distribution at the
  migration and now; the first 20 pool trades (competing early transactions). Holder figures are observed trades by
  fee payer, not balances, and say whether they are complete.
- **Output (M3):** off by default; with `confirmation_delay_ms` the evidence names the first pool trade at or after
  migration + delay as a paper reference (`live_policy: False`).
- **Never:** a migration from a price rise, a complete curve or a missing token.
- **Failure cases tested:** trades on another pool; a pool trade delivered before its migrate; a pool trade before
  the migration slot; PumpPortal's venue-only report; a repeated migrate; out-of-order pool trades; a complete curve
  without a migration; failing listeners; bounded state.
- **Tests:** `tests/test_intel_migration.py` (20), `tests/test_intel_live_migrations.py` (the 4 live migrations
  against an independent scan under in-order, shuffled and duplicated delivery), `tests/test_intel_replay.py` (no migration inferred on the 39 s
  capture, which holds none). Five targeted mutants (any pool confirms, venue-only confirms, no held trades, no slot
  check, no migration snapshot) each fail a test.

## Live run (jinx-nyc, 2026-10-21 04:19:53 UTC, 600 s, commit 254434f, no code changes)

`docs/evidence/phase2_event_intel_live_20261021.json`, `scripts/intel_live_check.py`. One PumpAPI connection for the
whole window: 386,018 messages received, 28,183 events published to the three modules, 0 malformed, 0 duplicates,
0 disconnects, 0 stalls. PumpAPI timestamp to JINX receipt: p50 45.1 ms, p95 49.2 ms.

| Capability | Result | Detection delay (PumpAPI time → evidence) | Local processing per event |
|---|---|---|---|
| Early Momentum | 176 detections; `trade_permitted` 0 (B-02 unresolved) | p50 45.7 ms, p95 50.3 ms, p99 53.6 ms, max 56.6 ms (n 176) | p50 26 µs, p95 72 µs, p99 98 µs, max 4.3 ms |
| Graduation | 21,491 curve readings on 634 mints, 0 late | — (evidence on every curve event) | p50 25 µs, p95 44 µs, p99 59 µs, max 0.7 ms |
| Migration | 8 migrations, 8 verified on the announced pool, 0 trades on other pools | signal p50 45.4 ms, max 47.0 ms (n 8) | p50 58 µs, p95 91 µs, p99 115 µs, max 8.6 ms |

Detection delay is almost entirely the feed's delivery lag; JINX's own processing at detection was p50 51 µs, max
115 µs. The per-event maxima are single events on a shared host with the process under `nice` (cause not isolated).
176 detections and 8 migrations in ten minutes are a sample, not a statistical result.

What the live data showed:
- **Early Momentum:** the creator's own buy was one of the three wallets in 120 of 176 detections (B-03 asks whether
  it should count). 71 of 176 triggered on a curve that is not a standard 30-SOL curve, where B-02's SOL gates
  cannot mean the same thing. 122 of 176 had their creation observed, so their peak was all-time.
- **Migration (M1):** the first trade on the announced pool came 1 slot (p50) and 245 ms (p50) after the migrate,
  at most 5 slots and 1,056 ms. Every pool trade in the window was on the announced pool.
- **Migration (M2):** all 8 left the curve holding 85.005 SOL. The first pool trade's reserve-after was p50 67.3 SOL
  and up to 238.4 SOL, because a first trade can itself be a buy of well over 100 SOL in the migration slot.
- **Fixture:** an earlier 600 s run at 04:04 UTC, on the code before 254434f's last fixes, captured 4 migrations
  (3 standard curves holding 85.005 SOL; one non-standard curve holding 3.44 SOL whose pool opened with 3.43 SOL; two
  first pool trades of 139.6 and 169 SOL in the migration slot, one on a token whose creator bought the whole curve in
  its creation transaction). They are `tests/fixtures/pumpapi-migrations-live-20261021.jsonl.gz` (244 records),
  replayed by `tests/test_intel_live_migrations.py`. That run's numbers were superseded by the run above.

## Blockers left (owner decisions; nothing here is invented)

- **B-02:** which curve gate applies (0.5–10 SOL or under 35 SOL) and which reserve it reads. Until decided no Early
  Momentum evidence permits a trade.
- **B-03 (added 2026-10-21):** the peak-gate rule; whether the creator's buy counts; the sybil check before entry;
  graduation's "near the threshold" and elevated weight; the 0–20 mapping (already listed); "large sells" around
  graduation; holder concentration from trades versus balances.
- **Funders:** PumpAPI carries no transfers. Since 59b81eb the app answers `request_funders` with GMGN wallet stats
  (`common.fund_from_address`); a buyer GMGN has no funder for keeps the sybil check UNAVAILABLE (blocking).
