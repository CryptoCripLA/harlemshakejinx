VERDICT: FAIL

Reviewed `ae93665ece82f7f49e84a290b52abc3238c038de` read-only. The original regressions pass where executable, but six additional failures reproduce.

1. **HIGH — A migration received before creation is forgotten by Early Momentum.**  
   Evidence: [early_momentum.py:272](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/early_momentum.py:272), [app.py:304](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/app.py:304).

   The migration handler marks only an existing mint. If migration arrives first, subsequent delayed creation and curve events create an unmigrated history. `entry_check` displays the MigrationTracker’s migration evidence but does not use it to exclude this entry path.

   **Reproduce:** deliver migration at event time T+60 first, then creation at T and qualifying buys from A/B/C at T+1/+2/+3. `reserve_check(mint, True)["eligible"]` returns `True`. With distinct verified funders and an eligible safety result, the actual `entry_check` method returns `all_checks_pass=True` while its lifecycle is `MIGRATION_OBSERVED`. No order was placed during reproduction.

2. **HIGH — A delayed creation received after reconnect escapes gap protection.**  
   Evidence: [early_momentum.py:280](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/early_momentum.py:280), [early_momentum.py:493](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/early_momentum.py:493), [migration.py:334](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/migration.py:334).

   The watermark checks receipt time exclusively. A creation whose provider timestamp predates the reconnect is considered complete if received afterward, although intervening trades may have been missed. Migration trade-flow completeness has the same omission.

   **Reproduce:** mark reconnect at T+2.5 seconds; deliver a creation timestamped T with receipt time T+3; deliver three qualifying buyers at T+4/+5/+6. The reserve check returns `eligible=True`. The queued-before-gap regression passes because it exercises a different receipt ordering.

3. **MEDIUM — The report cap silently discards authoritative enrichment and contradictions.**  
   Evidence: [migration.py:535](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/migration.py:535), [migration.py:538](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/migration.py:538).

   Distinct report versions consume the eight-report allowance. Once full, every subsequent report is ignored, regardless of whether it first supplies the pool or contradicts a verified destination. There is no completeness warning.

   **Reproduce:** confirm pool A, then add seven distinct provider reports with different event timestamps. Submit a ninth report naming pool B. The record still names only A, remains `verified=True`, and stays `POST_MIGRATION_MARKET`. Alternatively, fill eight reports without a pool, then deliver the pool-naming PumpAPI migration and matching trade: confirmation never occurs.

4. **MEDIUM — Pool retention loses the paper confirmation-delay reference.**  
   Evidence: [migration.py:278](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/migration.py:278), [migration.py:699](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/migration.py:699).

   `_Pool.full` retains only the earliest capped trades. M3 searches that list exclusively, so once it fills before the configured delay, subsequent qualifying reference trades are permanently discarded.

   **Reproduce with defaults:** configure a 1,000 ms paper delay; deliver migration, 256 reserve-bearing pool trades at offsets 1–256 ms, then another at 1,100 ms. Migration verifies and `latest_post_liquidity` names the last trade, but `research["reference_trade"]` remains `None`.

   The smaller equivalent uses `max_pool_trades=2`, delay 300 ms, and trades at 100, 200 and 400 ms.

5. **MEDIUM — Replay reporting crashes after normal tracker eviction.**  
   Evidence: [replay.py:81](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/replay.py:81), [bounded.py:36](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/bounded.py:36).

   Historical notification mints outlive tracker entries. The new final-evidence comprehension assumes every lookup returns evidence.

   **Reproduce:** replay a migration for A, followed by creation of B three hours later. A expires under the normal two-hour retention. `run.report()` raises `AttributeError: 'NoneType' object has no attribute 'verified'`. Cap eviction produces the same failure.

6. **MEDIUM — Replay migration-delay measurements still use superseded receipt-clock evidence.**  
   Evidence: [replay.py:78](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/replay.py:78), [replay.py:85](/E:/DevCache/agent-scratch/harlemshakejinx-review-20261020/jinx/intel/replay.py:85).

   Verification totals use final evidence, but signal-delay samples use the first historical notification. A receipt-only PumpPortal report therefore contributes essentially processing time, even after PumpAPI supplies the provider timestamp.

   **Reproduce:** receive PumpPortal’s report at T+90 ms, then PumpAPI’s same migration with provider timestamp T at receipt T+100 ms; deliver its verifying pool trade. Final authoritative evidence shows 90 ms from provider time to first observation. The replay reported approximately 2 ms—the first notification’s processing time—rather than that delay plus processing.

The required prior-finding dispositions are:

| Item | Status | Evidence |
|---|---|---|
| F1 | **PARTIAL** | Existing and queued histories are invalidated at `early_momentum.py:280,316`; finding 2 remains. |
| F6 | **PARTIAL** | Conflict handling works at `migration.py:553` and `lifecycle.py:258`; the report cap bypasses it in finding 3. |
| F7 | **FIXED** | `migration.py:348–360` prevents duplicate accounting beyond the identity window and marks flow incomplete; `:676` derives seller annotations from current facts. |
| F9 | **PARTIAL** | Documentation was updated, but unconditional completeness claims remain unsupported by findings 1–6. |
| R2-1 | **FIXED** | The exact queued-before-gap case is handled at `early_momentum.py:280` and `migration.py:334`; regressions at `test_review_phase2_intel_round2.py:35,49` pass. Finding 2 is a different arrival case. |
| R2-2 | **PARTIAL** | Report equality includes improved facts at `migration.py:535`; missing migration timestamps are rejected at `normalize.py:103`. Later enrichment is still discarded after the cap, finding 3. |
| R2-3 | **FIXED** | Separate reserve-bearing retention at `migration.py:275` fixes both original cases; regressions at `test_review_phase2_intel_round2.py:85,97` pass. Finding 4 identifies a separate M3 retention failure. |
| R2-4 | **FIXED** | `lifecycle.py:258` checks conflicting destinations regardless of timestamp precision; both regressions at `test_review_phase2_intel_round2.py:106,115` pass. |
| R2-5 | **FIXED** | `migration.py:351,676`; regressions at `test_review_phase2_intel_round2.py:125,138` pass. |
| R2-6 | **PARTIAL** | `replay.py:83` correctly excludes revoked verification; final-evidence reporting still fails findings 5–6. |
| R2-7 | **PARTIAL** | `PHASE2_MOMENTUM_MIGRATION.md:48,83,108,132` and matrix C10/M1/M3 need qualification for the reproduced failures. |

Other requested checks:

- **Reserve arithmetic is correct:** inclusive current comparison at `early_momentum.py:477`, strictly-over-ceiling peak rejection at `:489`, and monotonic peak accumulation at `:339`. Boundary tests are at `test_b02_reserve_policy.py:51,61`; the peak test includes the smallest base-unit excess. `readings.py:103` requires explicitly verified real reserves; virtual reserves are not substituted. The retired entry rule is metadata only. History and migrated-token enforcement still fail findings 1–2.
- **Graduation follows provider status:** `graduation.py:191,225` uses GMGN’s category. No numerical near-graduation threshold or default points formula is invented. Progress constants and calculations at `:44–46,84–96` match the requested pinned donor passages.
- **Creator and funders:** creator participation is flagged at `early_momentum.py:443`; shared verified funders fail and missing funders remain unavailable at `:523–544`. Corresponding tests pass.
- **Selling and holders:** sell sizes, identities and creator flags are retained without a danger classification at `migration.py:509,676`. Verified holder evidence is separate from explicitly labelled trade flow at `:390,740–762`.
- **Engine wiring exists:** `app.py:105–118` subscribes all three modules on FAST after core state. Shared views exist at `:291,319,509`. GMGN lookups run in background tasks with bounded admission at `:212`; per-handler timing samples are bounded at `:199`. I found no order-placement or sizing connection from these modules.
- **Identity consumers:** separate migration reports survive bus/store admission; registries ignore migration statistics (`registry.py:67,136`), whales consume buyers using `pop` (`whales.py:129`), and bundles ignore migrations (`bundles.py:70`). No additional identity-related consumer defect was established.
- **Documentation:** C05 and the basic M2 evidence descriptions are supported. C10’s history guarantees, M1’s unconditional enrichment/conflict guarantees, M3’s reference-selection claim, and replay timing claims require the qualifications above.

Validation: **164 focused tests passed**, plus the bus/store migration regression passed using SQLite in memory. All new reproductions ran in memory. App-test collection was blocked by denied access to installed `psycopg`; the filesystem-backed migration test encountered a temporary-directory permission error. Full-suite and deployed-engine acceptance remain unverified. Repository files were unchanged.