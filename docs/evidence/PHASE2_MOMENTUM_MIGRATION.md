# Phase 2 event intel: Early Momentum (C10), graduation (C05), migration (M1–M3)

Paper only: none of these modules places, sizes or permits an order. Status words as in `docs/BUILD_STATUS.md`.
Timings are local processing inside JINX plus the feed's own delivery lag; they are not order execution or landing.
Owner orders: `docs/OWNER_DIRECTIVES_20261021.md` (the last section is the Phase 2 integration order, verbatim).

Evidence files:
- `docs/evidence/phase2_event_intel_replay_20261021.json`: replay of the committed live capture
  (`tests/fixtures/pumpapi-live-20261021.jsonl.gz`, 1,200 curve records, 39 s), `scripts/intel_replay_report.py`.
- `docs/evidence/phase2_event_intel_live_20261021.json`: live run of the three modules on jinx-nyc against the free
  PumpAPI stream at 254434f, `scripts/intel_live_check.py` (RAM only; no wallet addresses kept).
- `tests/fixtures/pumpapi-migrations-live-20261021.jsonl.gz`: four real migrations captured live (each mint's last
  curve records, the migrate record, its first PumpSwap records).
- `tests/fixtures/gmgn_trenches_holders_sample_20261021.json`: real GMGN Trenches, token-info and top-holder
  responses (read-only, trimmed to the fields JINX reads).
- `docs/audits/phase2-intel-codex-20261021-round*.md`: independent Codex reviews.

## In the running engine (`jinx/app.py`)

All three modules are subscribed to the engine's own bus, on the fast lane, in this order after core state:
Early Momentum, graduation, migration. Each takes one event at a time, synchronously, with no I/O; GMGN lookups they
ask for run as background tasks, at most 64 at once and one per (kind, token) (`JinxApp._spawn`), never on the event
path. A shed lookup leaves its evidence UNAVAILABLE, which blocks. State is RAM only and bounded (mint cap 20,000,
idle 2 h on the receipt clock, per-token caps) and rebuilt from the feeds after a restart.

| Module | Events in | Shared state out | GMGN asked (background) |
|---|---|---|---|
| `EarlyMomentum` (C10) | curve trades, creations, migrations | `candidates` (newest 500 detections), `entry_check(mint)` | wallet funders (`getWalletStats`, one wallet per request) |
| `GraduationTracker` (C05) | curve trades, creations | `entry_check(mint)["graduation"]` | Trenches `near_completion` / `completed` every 10 s (`poll_trenches_once`) |
| `MigrationTracker` (M1–M3) | creations, curve and PumpSwap trades, migrations from both feeds | `migrations` index, `migration_view(mint)`, `entry_check(mint)["migration"]`, the token lifecycle | `migrated_pool` when no report names the pool (`getTokenInfo`), top holders once per migration (`getTokenTopHolders`) |

`intel_health()` (inside `/health`) shows every module's counters, the newest detections and migrations read from
the trackers (never a stale copy), the GMGN Trenches read, background-task counts and the fast lane's measured
admission-to-handled delay, and each module's own processing time (`handler_processing_us`). The PumpAPI feed
reports each (re)connection; the engine then marks as incomplete the history of every token tracked so far and of
every token with an event that was received, or that happened (provider time), before that moment but is processed
later (trades sent while disconnected are lost), so B-02's peak gate blocks those tokens. The comparison mixes JINX's
receipt clock with PumpAPI's clock, which differ by the ~50 ms delivery lag; the error can only block a token that
started just after a reconnect, never pass one that started before it. A creation timed only by JINX's receipt
(PumpPortal's copy, when PumpAPI never reported the creation) that arrives after a gap cannot be placed in time, and
no reserve reading can exclude an unseen buy-and-sell round trip (it restores the curve's reserves exactly), so such
a token's history is unprovable and Early Momentum blocks it. Cost: tokens whose creation PumpAPI did not report
(about 7% of creations in the PumpAPI evaluation) are not Early Momentum candidates after any reconnect. `trade_permitted` on Early Momentum evidence covers that module's own
predicates only; safety and the entry re-check are combined in `JinxApp.entry_check`, which also refuses any token the
lifecycle has seen migrate (the Early Momentum path is pre-graduation only), whichever module heard the migration
first; Early Momentum itself remembers a migration even when it arrives before the token's creation.

## C10 Early Momentum — TESTING; entries blocked until every check passes

- **Trigger (CHAD):** the earliest moment three distinct fee payers (PumpAPI `txSigner`) have bought on the curve
  within 30 s of event time, inclusive. One evidence object per token, pushed to the fast path at once.
- **B-02 (owner decision, implemented in b63794d, verified here):** current verified real SOL in the curve 0.5–10
  inclusive; the verified peak since creation never above 10; the 35 SOL statement retired; real SOL only from
  PumpAPI's `quoteInPool` on a SOL-quoted curve, never virtual minus 30; non-standard curves follow the same rule when
  their real SOL is verified; unknown or incomplete history (creation not seen, an unverified reading, a trade-feed
  gap) blocks; migrated tokens are outside this path; re-checked on the current state at the entry decision.
  Boundaries tested to the lamport: 0.49 fails, 0.50 passes, 10.00 passes, 10.01 fails; a peak of exactly 10 passes
  and one lamport more fails, even after the reserve falls back into range.
- **Creator (owner item 4):** the creator's verified buy counts as one distinct buyer and is flagged
  (`creator_in_window`).
- **Common funders (owner item 5):** three or more buyers with one GMGN-reported funder fail the sybil check with
  `emergency_exit`; any buyer without a known funder makes it UNAVAILABLE, never clean.
- **Failure cases tested:** one wallet buying 3 and 12 times; two wallets churning; a third wallet 30.001 s after the
  first; the 30.000 s boundary; sells and PumpSwap buys; a feed backlog delivering a 45 s cluster at once; a signal
  delivered 40 s late; every arrival order of four buys; a late buy inside the window; duplicate delivery; a retrace
  after a higher peak; unknown, shared and distinct funders; funding learned after detection; failing listeners;
  non-standard curves; a trade-feed gap; bounded state.
- **Measured (Early Momentum):** replay of the committed capture: 38 detections, identical to an independent scan of the raw records;
  shuffled and duplicated delivery converge. Live module run at 254434f (600 s, jinx-nyc): 176 detections, PumpAPI
  time to evidence p50 45.7 ms / p95 50.3 ms / p99 53.6 ms / max 56.6 ms, local processing p50 51 µs; none
  permitted.

## C05 Graduation — TESTING; the 0–20 points are not produced

- **Readings per curve event:** virtual and real reserves, completion, peaks, and two progress readings with their
  sources: tokens bought / 793.1M sellable (D3AD `pumpfun.rs:538-540`, buy cap at `:448`) and Mako's real SOL / 85 SOL
  (`src/onchain.py:546-550`). Live finding: 26 of 118 curve mints in the capture are not standard 30-SOL curves; their
  readings are flagged `standard_curve` False and get no progress, never a reinterpretation.
- **Near graduation (owner item 6):** GMGN Trenches' own `near_completion` category for tracked tokens, read every
  10 s, with source, read time and progress (`near_threshold`, `provider_status`; the lifecycle's APPROACHING
  GRADUATION). GMGN's category spans progress 0.51–0.98 in the recorded sample; JINX sets no number of its own and a
  token missing from a listing changes nothing. The 0–20 points stay unproduced: CHAD gives only the range and the
  owner said not to invent the formula; an owner mapping would be accepted only inside 0–20.
- **Tested:** late and duplicate readings, completion, non-standard curves, PumpPortal-only readings, provider status
  for tracked and untracked tokens, out-of-range mappings, bounded state.

## M1–M3 Migration — TESTING

- **One migration, one record (owner items 2, 3).** Root cause of the defect fixed in 3462ce2: PumpAPI's and
  PumpPortal's reports of one migration had the same event identity, so the bus and the events table kept only the
  first. PumpAPI's pool-naming report now carries the pool as its `leg` (its own identity); both reach the tracker,
  which merges every report (PumpAPI, PumpPortal, GMGN Trenches `completed`, GMGN `migrated_pool`) into one record.
  Time comes from the best clock (chain, then provider with slot, then receipt) and a receipt time never replaces a
  provider time, in the record and in the lifecycle. Effects happen once: first report, pool learned, verification
  change; a later report from the same source with better facts (a provider time, a slot) is kept. At most 8 reports
  are held per migration; at the cap the oldest report that adds nothing (neither the record's time nor the first to
  name its pool, nor a source's first report, nor the earliest observation) makes room, so a new pool, a
  contradiction, a better time, a source or the first-observation time is never lost; `reports_seen` counts every
  report received. Reports naming
  different pools are a conflict: nothing is verified, and after a confirmation the lifecycle becomes CONFLICTING
  whatever the precision of the conflicting report's time. The PumpAPI adapter refuses a migrate without its
  timestamp.
- **M1, first verified post-migration liquidity (derived, never sticky):** the earliest PumpSwap trade on the
  record's single pool that has both reserves and is provably at or after the migration (slot against slot, otherwise
  provider time against provider time), with the migration time on a provider clock. Trades are kept per pool as the
  earliest 256 by event time (not by arrival), trades with both reserves separately, so later or reserve-less trades
  never crowd out verification evidence. Later evidence that invalidates
  it revokes the verification (a lifecycle failure) until another trade qualifies. The record names the trade it was
  read after (side, size, fee payer), because a first trade can itself be a 100+ SOL buy.
- **M2:** pre-migration liquidity and peak; first and latest post-migration liquidity; the 10 largest sells of the
  token's observed life with seller, size, phase, reserve after and whether the seller is the creator, ranked
  independently of the trade caps and never labelled (owner item 7); the creator's sells before and after; holder
  concentration from GMGN top-holder balances where available, pool accounts kept apart from wallets (owner item 8),
  and separately the observed trade flow, labelled not proof of ownership, incomplete after a feed gap, with an
  at-migration view computed from trades before the migration; the first 20 pool trades with slot and time offsets.
  Duplicates: a trade delivered again within the token's identity window (the last 2 × (256 + 256) trade identities)
  is counted once; one older than that window is not counted and the token's flow is marked incomplete, so a total
  is never inflated. The seller's creator flag and phase are read when the evidence is built, so they follow facts
  that arrive later.
- **M3:** off by default; `confirmation_delay_ms` names a paper reference trade (`live_policy: False`) for the current
  record, taken from the kept trades (4,096 per pool with M3 on) and from a reference tracked as trades arrive. When
  the migration record changes once the kept set is full and none of the kept trades qualifies under the new record,
  the first qualifying trade cannot be known: `reference_status` says "not determinable", and that answer stands for
  that migration time, so a later trade is never reported in its place.
- **Measured:** live module run at 254434f (600 s): 8 migrations, 8 verified; migrate to evidence p50 45.4 ms; first
  pool trade p50 1 slot / 245 ms after the migrate (max 5 slots / 1,056 ms).

## Tests (all on main)

`tests/test_intel_early_momentum.py`, `tests/test_b02_reserve_policy.py`, `tests/test_intel_graduation.py`,
`tests/test_intel_migration.py`, `tests/test_intel_live_migrations.py` (the 4 live migrations against an independent
scan, in-order, shuffled and duplicated), `tests/test_migration_dedup.py` (the dedup defect end to end through the
real bus and events table), `tests/test_app_event_intel.py` (real captures through the engine's own bus into shared
state; a fake GMGN serving the recorded response shapes; nothing reaches the ledger),
`tests/test_review_phase2_intel_round1.py` (one reproduction per Codex round-1 finding), `tests/test_intel_replay.py`,
`tests/test_intel_readings.py`, `tests/test_lifecycle.py`.

## Independent review

Codex round 1 on 3462ce2 (`docs/audits/phase2-intel-codex-20261021-round1.md`): FAIL with 4 HIGH, 4 MEDIUM, 1 LOW.
Fixed: F1 feed gaps now make the history incomplete; F2 background lookups bounded and deduplicated; F3 lifecycle
lists bounded with totals; F4 verification requires reserves, provable order and a provider-clock migration time and
is revoked by contrary evidence; F5 shared state reads the tracker and the confirmation is published after the market
transition; F6 conflicting pools never verify; F7 duplicate trades counted once and the at-migration view converges;
F8 largest sells survive the caps; F9 this document.

Codex round 2 on 6b2ec43 (`docs/audits/phase2-intel-codex-20261021-round2.md`): FAIL with 1 HIGH, 5 MEDIUM, 1 LOW;
F2, F3, F4, F5, F8 confirmed FIXED, F1, F6, F7, F9 PARTIAL. Fixed (`tests/test_review_phase2_intel_round2.py`): R2-1 an
event received before a feed gap but processed after it marks its token; R2-2 a better report from the same source is
kept and the adapter refuses a timestamp-less migrate; R2-3 per-pool retention by event time with reserve-carrying
trades kept apart; R2-4 a conflicting pool is a lifecycle conflict whatever its time precision; R2-5 duplicates beyond
the identity window are not counted (flow marked incomplete) and sell annotations are read at build time; R2-6 the
replay report counts the final verification; R2-7 this document's claims qualified.

Codex round 3 on ae93665 (`docs/audits/phase2-intel-codex-20261021-round3.md`): FAIL with 2 HIGH, 4 MEDIUM; F7, R2-1,
R2-3, R2-4, R2-5 confirmed FIXED. Fixed (`tests/test_review_phase2_intel_round3.py`): R3-1 a migration heard before
the creation keeps the token out of Early Momentum, and `entry_check` refuses any token the lifecycle has seen
migrate; R3-2 an event that happened before a feed gap marks its token whenever it is received; R3-3 the report cap
never refuses new facts; R3-4 the M3 reference survives the retention cap; R3-5 the replay report survives tracker
eviction (counted as `evicted`); R3-6 replay delays are measured on the final record.

Codex round 4 on b978908 (`docs/audits/phase2-intel-codex-20261021-round4.md`): FAIL with 1 HIGH, 2 MEDIUM; F6, R2-2,
R2-6, R3-1, R3-5, R3-6 confirmed FIXED. Fixed (`tests/test_review_phase2_intel_round4.py`): R4-1 a receipt-only
creation after a gap is provable only by reserve continuity (both trackers); R4-2 the M3 reference is derived from the
current record with its own retention; R4-3 the report cap never folds a source's first report or the first
observation, and every report is counted.

Codex round 5 on be5eff9 (`docs/audits/phase2-intel-codex-20261021-round5.md`): FAIL with 1 HIGH, 1 MEDIUM; R3-3, R4-3
confirmed FIXED. R5-1 was right that net reserve equality cannot prove a complete history (an unseen round trip
restores it), so the continuity exception was removed: a receipt-only creation after a gap is unprovable and blocked
in both trackers. R5-2: the M3 reference is also tracked as trades arrive, with an explicit status when it cannot be
determined (`tests/test_review_phase2_intel_round5.py`).

Codex round 6 on 872be0d (`docs/audits/phase2-intel-codex-20261021-round6.md`): FAIL with 1 MEDIUM only (no HIGH; F1,
R3-2, R4-1, R5-1 confirmed FIXED): R6-1, in the off-by-default M3 variant, a later trade could replace an
undeterminable reference after the migration time changed. Fixed: "not determinable" stands for that migration time
and the reference is settled as soon as the record's time changes (`tests/test_review_phase2_intel_round6.py`).

Codex round 7 on f4496a8 (`docs/audits/phase2-intel-codex-20261021-round7.md`): FAIL with 1 MEDIUM only, again in M3
(R6-1 confirmed FIXED): with a full kept set and no kept trade qualifying under a changed record, "no qualifying trade
yet" was claimed. Fixed: a full kept set without a qualifying trade is "not determinable"
(`tests/test_review_phase2_intel_round7.py`).

**Codex round 8 on cf4569c (`docs/audits/phase2-intel-codex-20261021-round8.md`): VERDICT: PASS** — no CRITICAL, HIGH,
MEDIUM or LOW defect in scope; every earlier finding confirmed FIXED; B-02 boundaries, migration merging and identity,
graduation, creator and funders, sells and holders, engine wiring (no order path) and the documentation checked with
file:line evidence. Codex's sandbox could not run the app tests (it was denied access to the installed `psycopg`) or
four temporary-directory tests; those run in the full suite on jinx-nyc (615 passed, 1 skipped at cf4569c). The verdict
covers the reviewed code; it is not the owner's Phase 2 acceptance.

## Deployment

The Contabo paper deployment runs whatever `deploy/contabo/deploy.sh` last shipped; the revision and the live
engine measurements are recorded here once the reviewed build is deployed. Live trading is OFF.
