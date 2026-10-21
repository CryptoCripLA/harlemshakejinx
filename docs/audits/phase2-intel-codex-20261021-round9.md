VERDICT: FAIL

No CRITICAL or HIGH findings. One MEDIUM regression and one LOW documentation error.

1. **MEDIUM — The shorter deduplication horizon discards useful ordering evidence while continuing to claim a verified first trade.**  
   Evidence: `jinx/intel/migration.py:336`, `:370`, `:374`, `:695`, `:789`.

   The identity window now holds 512 trades. An unfamiliar trade older than its eviction floor is discarded entirely. Only trade-flow completeness is marked false; first verified liquidity and the optional M3 reference retain definitive answers.

   **Reproduce using `tests/pumpapi_msgs.py`:**
   - Create the token with `initial_buy=0`.
   - Report migration at `T0_MS + 60000`, slot 499.
   - Deliver 513 pool buys named `P0`–`P512`, with timestamps `T0_MS + 61000 + i` and slots `501 + i`.
   - Deliver `TRUE_FIRST` afterward, timestamp `T0_MS + 60500`, slot 500.

   At **449b917**, `TRUE_FIRST` is discarded. Evidence identifies `P0` as first liquidity with `verified=True` and status `"verified: first trade with reserves on the pool after the migration"`. With `confirmation_delay_ms=100`, M3 also reports `reference_status="found"` and names `P0`.

   The identical sequence at **cf4569c** correctly identifies `TRUE_FIRST`. This is a demonstrated behavioral regression. Bounded retention is acceptable, but uncertainty must reach the affected first-liquidity/reference outputs. The trade-flow flag does not qualify those outputs.

2. **LOW — The evidence document overstates the migration identity window.**  
   Evidence: `docs/evidence/PHASE2_MOMENTUM_MIGRATION.md:116`; implementation: `jinx/intel/migration.py:336`.

   The M2 section still promises the last `2 × (256 + 256)` identities. The implementation retains 512, as the document’s RAM section correctly states at `:198`.

   **Reproduce:** deliver 513 chronological trades, then resend the first. It is outside the actual identity window and marks flow incomplete, despite being inside the documented 1,024-trade window.

Other requested checks:

| Check | Result and evidence |
|---|---|
| B-02 | Inclusive current boundaries, strictly-above peak rejection, incomplete-history blocking and migrated exclusion remain intact: `early_momentum.py:469`, `:486`, `:514`; verified reserve provenance: `readings.py:97`. Boundary tests pass: `tests/test_b02_reserve_policy.py:51`, `:59`. |
| Migration reports and identity | Separate report identities and authoritative merging remain intact: `normalize.py:98`, `events.py:81`, `migration.py:554`. A RAM bus/store/core check confirmed both reports, rejected resends, preserved provider time, and produced three notifications, one holder request and one pool request. No additional identity-consumer regression found. |
| Graduation | Provider category controls near-graduation; no invented points formula: `graduation.py:209`, `:223`. Progress readings match the pinned donor formulas: `:84`. |
| Creator and funders | Creator counted and flagged: `early_momentum.py:448`; shared-funder failure and missing-data handling: `:532`. Relevant regressions pass. |
| Sells and holders | Seller/size evidence remains at `migration.py:757`; verified balances and explicitly qualified trade flow remain separate at `:831`, `:846`. Directly verified that the 1,001st wallet marks flow incomplete. |
| Engine integration | All three trackers remain on FAST after core state: `app.py:102`, `:107`, `:112`, `:115`. Shared entry, migration and health views remain connected at `:292`, `:326`, `:516`. GMGN work remains asynchronous and bounded: `:213`, `:354`, `:375`, `:413`, `:445`. No order-placement or sizing connection found. |
| Memory and serialization | Direct checks passed across the 64/65-entry set transition, eviction boundaries and signed hashes. Slotted reading/report serialization preserves fields and clock values. |
| Documentation/matrix | C05/C10 claims remain supported. M1/M3’s first-trade claims need qualification or correction for finding 1. |

Validation: **198 tests passed; four tests encountered temporary-directory permission errors.** App integration collection was blocked by denied access to installed `psycopg`. Available earlier-round regressions passed. Committed capture replay processed **1,211 events, 38 detections, 118 graduation mints and zero permitted entries**. Repository files remained unchanged.