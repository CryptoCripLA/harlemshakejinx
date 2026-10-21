VERDICT: FAIL

Reviewed `b978908ace6e76d1a719110ec1465bef5731818c` read-only. Three additional defects reproduce.

1. **HIGH — Receipt-only creations still escape reconnect history protection.**

   Evidence: `jinx/intel/early_momentum.py:275`, `:282`, `:342`, `:492`; `jinx/intel/readings.py:65`; `jinx/intel/migration.py:336`, `:339`.

   The new gap comparison works when creation carries a provider timestamp. PumpPortal creation has only receipt time. A delayed pre-gap creation received after reconnect therefore starts a supposedly complete history. The creation-reserve bound covers the initial transaction, but cannot bound subsequent unseen activity before reconnect.

   **Reproduce:** mark reconnect at T+2.5 seconds; deliver a PumpPortal creation that happened at T but arrives at T+3; deliver qualifying PumpAPI buys from A/B/C at T+4/+5/+6. `reserve_check(mint, True)["eligible"]` returns **True**. Migration evidence also reports `observed_trade_flow["complete"] == True`.

   An unseen rise above the owner’s ceiling and retrace during that interval cannot be excluded. The round-3 regression exercises a provider-timestamped creation and misses this receipt-only case.

2. **MEDIUM — Migration-time enrichment breaks the cached paper confirmation-delay reference.**

   Evidence: `jinx/intel/migration.py:584`, `:590`, `:611`, `:738`.

   `_research_reference` accepts an existing qualifying reference without checking whether an earlier retained trade became eligible after timestamp enrichment. Conversely, `_pool_trade` compares new trades against an obsolete reference before invalidating it.

   **Reproduction A:** configure a 300 ms delay. Supply GMGN migration time T+1,000 ms and its pool; deliver reserve-bearing pool trades at T+800 and T+1,400. Then enrich with PumpAPI migration time T and its slot. The due time becomes T+300, and first verified liquidity correctly identifies the T+800 trade, but the paper reference remains **T+1,400**.

   **Reproduction B:** use `max_pool_trades=2`, delay 300 ms, initial migration time T, and trades at T+100/+200/+400. Enrich migration time to T+500, then deliver a qualifying trade at T+900. Its reference should be T+900; evidence instead returns **None** because the obsolete T+400 reference prevents updating, while retention discards T+900.

   Both reproduce in memory. The original cap-only regression passes.

3. **MEDIUM — Report-cap eviction deletes unique provenance and changes first-observation timing.**

   Evidence: `jinx/intel/migration.py:167`, `:171`, `:568`, `:577`, `:581`, `:452`; `jinx/app.py:519`.

   `_make_room` protects the best-ranked report and first report naming each pool. It does not preserve unique sources or the earliest observation. Their only report can be deleted without retaining those facts separately.

   **Reproduce:** receive PumpPortal’s migration at T+90 ms, PumpAPI’s corresponding pool report at T+100 ms, then seven distinct GMGN report versions to exceed the eight-report cap.

   Observed results:

   - `migration.sources` loses `mako.pumpportal`.
   - `summary()["observed_ns"]` changes from T+90 to **T+100**.
   - Full evidence still reports first observation at **T+90**.
   - `reports_dropped` remains **0**.

   This silently erases original provenance and makes shared-state migration-delay measurements disagree with the full evidence.

The required prior-finding dispositions are:

| Item | Status | Current evidence |
|---|---|---|
| F1 | **PARTIAL** | `early_momentum.py:275,316,492` handles tracked and provider-timestamped histories; finding 1 remains. |
| F6 | **FIXED** | `migration.py:554,634`; `lifecycle.py:258` preserves pool conflicts and revokes verification. The original cap-bypass reproduction passes. |
| F9 | **PARTIAL** | Evidence claims at `docs/evidence/PHASE2_MOMENTUM_MIGRATION.md:35,92,114` remain too broad for findings 1–3. |
| R2-2 | **FIXED** | `migration.py:537`; `adapters/pumpapi/normalize.py:103` retains improved reports and refuses timestamp-less PumpAPI migrations. |
| R2-6 | **FIXED** | `replay.py:83,84,86` uses surviving final evidence for verification and timing. |
| R2-7 | **PARTIAL** | Matrix C10/M2/M3 (`SOURCE_FIDELITY_MATRIX.md:47,71,72`) require qualification for findings 1–2; provenance claims require finding 3’s qualification. |
| R3-1 | **FIXED** | `early_momentum.py:274,279`; `app.py:305,309` remembers migration before creation and excludes lifecycle-observed migrations from this entry path. App regression execution was environment-blocked. |
| R3-2 | **PARTIAL** | Provider-time reproduction passes at `early_momentum.py:275` and `migration.py:336`; receipt-only creation still fails, finding 1. |
| R3-3 | **PARTIAL** | `migration.py:540,568` fixes the original pool-enrichment and contradiction cases; its replacement eviction loses other useful facts, finding 3. |
| R3-4 | **PARTIAL** | `migration.py:608` fixes retention with an unchanged migration record; time enrichment still fails, finding 2. |
| R3-5 | **FIXED** | `replay.py:83,85` tolerates eviction and reports it separately. |
| R3-6 | **FIXED** | `replay.py:86` measures migration delay using final authoritative timing; its regression passes. |

Other requested checks:

- **B-02 arithmetic:** inclusive current bounds at `early_momentum.py:476`, strict peak-ceiling rejection at `:488`, monotonic peak at `:338`. Existing boundary tests pass; additional in-memory checks immediately below/at each current boundary also pass. `readings.py:103` requires verified real quote reserves. The retired rule is metadata only. History enforcement remains incomplete under finding 1.
- **Migration inputs and identity:** separate PumpAPI/PumpPortal inputs survive bus/store admission through `normalize.py:109` and `events.py:100`. All 11 migration-dedup checks passed, using SQLite in memory. Registries ignore migration statistics (`registry.py:67,136`), whales consume buyers using `pop` (`whales.py:129`), and bundles ignore migrations (`bundles.py:67`). No additional identity-related consumer defect was established.
- **Graduation:** `graduation.py:191,225` uses provider category without inventing a near-graduation threshold or default scoring formula. Constants and progress calculations at `:44,84` match the specified pinned donor passages.
- **Creator/funders:** creator purchases count and are flagged at `early_momentum.py:442`; shared verified funders fail with emergency protection, while unknown funders remain unavailable at `:522`.
- **Sells/holders:** actual sell sizes and sellers are retained without a danger classification (`migration.py:509,708`). Verified balances remain separate from explicitly qualified trade flow (`:395,786,801`), subject to finding 1’s completeness error.
- **Engine:** all three modules subscribe on FAST after core state (`app.py:105,110,115,118`). Shared views exist through `entry_check`, `migration_view`, and `intel_health` (`:293,326,513`). GMGN I/O runs in background tasks with bounded admission (`:213`), bounded pool retries (`:354`), and periodic Trenches polling (`:413`). I found no order-placement or sizing connection from these modules; local predicate flags are not execution authorization.
- **Documentation:** C05 and the basic migration merging, verification, selling, and holder-evidence descriptions are supported. Unconditional history, provenance, and paper-reference claims are not.

Validation: **172 focused tests passed; three fixture setups failed on temporary-directory permissions.** App imports additionally failed because access to installed `psycopg` was denied, so complete app integration and full-suite acceptance remain unverified. All additional reproductions ran in memory. Repository files were unchanged.