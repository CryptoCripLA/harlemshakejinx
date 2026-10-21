VERDICT: FAIL

No CRITICAL or HIGH findings. Two reproducible regressions remain relative to cf4569c.

1. **MEDIUM — Wallet-cap overflow produces false zeroes in verified-holder selling evidence.**  
   Evidence: [migration.py:556](E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/migration.py:556), `:847–861`; cap changed at `:73`.

   Once 1,000 wallets are tracked, another wallet’s trades are excluded from its counters. `_sells_of()` nevertheless returns zero sells for that wallet, and `_holder_selling()` includes those zeroes under `"verified balances"` without an unavailable or incomplete marker. The sibling observed-flow completeness flag does not qualify these selling totals.

   **Reproduce:** create and migrate a token; deliver 1,026 distinct-wallet pool buys; deliver a sell by another wallet, `HOLDER`; then record verified GMGN balances naming `HOLDER`. Its sell appears in `sells_around_graduation`, but `holder_selling["verified"]["holders"][0]["after"]["sells"]` equals **0**.

   The identical sequence reports **1** at cf4569c. Bounded retention is acceptable; reporting an unavailable tally as zero is the defect. The existing verified-holder test, `tests/test_intel_migration.py:181`, does not exercise wallet overflow.

2. **LOW — Beyond-horizon resends inflate foreign/unverified pool-trade counts.**  
   Evidence: [migration.py:388](E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/migration.py:388), `:663–671`, `:300–309`, `:825–828`.

   The R9 fix lets old trades reach `_Pool.add()`. Its duplicate check searches only the retained earliest trades. A resend whose identity and retained trade have both been evicted increments `pool.count` again. `counted=False` protects wallet totals only. The published pool counts have no uncertainty marker.

   **Reproduce:** announce pool `AMM1`; deliver 1,026 chronological buys on `OTHER`, named `P0`–`P1025`; resend `P512`. `foreign_pool_trades` changes **1,026 → 1,027**, although no new transaction occurred. The identical sequence remains **1,026** at cf4569c. Without a named migration pool, the same problem affects `unverified_pool_trades`.

   `tests/test_review_phase2_intel_round9.py:30` checks retained sell/competing-list duplicates, but misses evicted trades and these counters.

**Round 9 disposition**

| Finding | Status | Evidence |
|---|---|---|
| R9-1: older trade lost as ordering evidence | **FIXED** | `migration.py:388–403` preserves ordering evidence while excluding uncertain wallet totals; `:278–309` deduplicates retained pool trades. Reproductions pass at `tests/test_review_phase2_intel_round9.py:18`, `:25`, `:30`. |
| R9-2: documented identity-window size | **FIXED** | `docs/evidence/PHASE2_MOMENTUM_MIGRATION.md:116` now states 512 identities, matching `migration.py:353`. |

The remaining requested checks found no additional defect:

| Check | Evidence and result |
|---|---|
| B-02 | Inclusive current boundaries at `early_momentum.py:482`; strictly-above peak rejection and incomplete-history blocking at `:494–509`; migrated exclusion and decision-time recheck at `:514–525`. Verified real-reserve provenance at `readings.py:97`; boundary tests at `tests/test_b02_reserve_policy.py:51`, `:59`. No virtual-reserve substitution or active retired entry rule found. |
| Migration merging and timestamps | Separate report identities at `normalize.py:98` and `events.py:81`; authoritative merging at `migration.py:576`; provider-clock ranking at `:116`. Partial-first enrichment, confirmation and timestamp tests pass. Findings above qualify duplicate accounting. |
| Other identity consumers | Migration reports do not change token/wallet statistics: `registry.py:66`, `:135`. Whale migration handling removes the buyer set once: `whales.py:129`. Store/core/recovery retain identity-based application; no additional regression found. |
| Graduation | Source-derived progress at `graduation.py:84`; provider category controls near-graduation at `:223`; default score remains unresolved at `:209`. Donor constants/formulas match the cited D3AD and Mako sections. |
| Creator and funders | Creator counted and flagged at `early_momentum.py:448`; shared-funder failure and missing-data unavailability at `:532–548`. Relevant tests pass. |
| Sells and ownership | Actual sizes/sellers and no invented classification at `migration.py:786`; verified balances separated from explicitly qualified trade flow at `:868`. Verified-holder selling still has finding 1. |
| Engine integration | All three trackers subscribe to FAST after core state at `app.py:102–115`. Shared views at `:292`, `:326`, `:516`. GMGN work is scheduled through bounded/deduplicated background tasks at `:213`; lookup/poll implementations at `:353`, `:376`, `:389`, `:445`. No order-placement or sizing connection found. |
| Memory changes | RecentIds signed-integer and 64/65-entry transition checks passed. Slotted serialization and memory-bound tests passed. Wallet overflow is explicit for observed flow, but insufficiently qualified in the verified-holder selling view. |
| Documentation/matrix | C05/C10 and R9 corrections are supported. M2 and blanket duplicate-counting claims need qualification or correction for the two findings above. |

Validation: **201 tests passed**; four temporary-directory tests were blocked by filesystem permissions. App integration collection was blocked by denied access to installed `psycopg`. In-memory capture replay processed **1,211 events**, produced **38 detections**, tracked **118 graduation mints**, and permitted **zero entries**. Both new reproductions were compared directly against cf4569c. Repository files remained unchanged.