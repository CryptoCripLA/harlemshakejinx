VERDICT: FAIL

Reviewed `be5eff9` read-only. Two reproducible defects remain.

1. **HIGH — R5-1: Net reserve accounting cannot prove complete history.**

   Evidence: `jinx/intel/readings.py:119,128`; `jinx/intel/early_momentum.py:294,507`; `jinx/intel/migration.py:764,769`.

   `reserves_continuous` checks net token movement. Unseen buys followed by sells can restore the same reserves, including after an unseen peak above the owner’s ceiling. Equality therefore cannot establish that every trade was observed.

   **Reproduce:** use the round-4 helpers:
   
   - Mark reconnect at `GAP_NS`.
   - Deliver `pumpportal_creation((T0_MS + 3000) * 1000000)`.
   - Deliver `consistent_buys([31.0, 32.0, 35.0])`.
   - Withhold an earlier buy/sell round trip whose token deltas cancel—for example, curve movement `30 → 50 → 30` before reconnect.
   
   Observed: `reserve_check("MintApump", True)["eligible"] == True`. Feed the same observed history into MigrationTracker, followed by migration: `observed_trade_flow["complete"] == True`.

   Those outputs are identical with or without the hidden round trip. The history is unprovable, and the hidden peak violates B-02. The new positive regression at `tests/test_review_phase2_intel_round4.py:53` explicitly accepts this ambiguous history.

2. **MEDIUM — R5-2: M3 still discards qualifying reference trades after its enlarged cap fills.**

   Evidence: `jinx/intel/migration.py:281,282,325,326,599,604`.

   M3 now retains the earliest 4,096 trades per pool. Once those are all before the configured delay, every subsequent qualifying trade is discarded from `pool.full`; `_research_reference` searches only that list.

   **Reproduce:**
   
   - Construct `MigrationTracker(confirmation_delay_ms=5000)`.
   - Deliver creation and migration at `MIG_T`, migration slot 499.
   - Deliver 4,096 reserve-bearing trades on the named pool at `MIG_T + i` milliseconds, with increasing slots.
   - Deliver another reserve-bearing trade at `MIG_T + 6000`, slot 5000.
   
   Observed: migration remains verified, but `research["reference_trade"] is None`. Expected: the last trade is the first qualifying paper reference.

   Recomputing the reference fixes the stale-cache cases, but increasing retention only moves the failure boundary.

The required prior-finding dispositions are:

| Item | Status | Evidence |
|---|---|---|
| F1 | **PARTIAL** | Known gaps block, but the continuity exception admits unprovable histories: `early_momentum.py:505,507`; R5-1. |
| F9 | **PARTIAL** | Documentation still claims reserve equality proves every trade was seen and M3 references survive retention: `docs/evidence/PHASE2_MOMENTUM_MIGRATION.md:40,121`. Both reproductions contradict those claims. |
| R2-7 | **PARTIAL** | Matrix C10’s incomplete-history guarantee and M3’s first-reference guarantee remain overstated: `docs/SOURCE_FIDELITY_MATRIX.md:47,72`; R5-1/R5-2. |
| R3-2 | **PARTIAL** | Provider-timestamped pre-gap creation is handled; receipt-only history remains unsafe: `early_momentum.py:278,298,507`; `migration.py:341,344,769`. |
| R3-3 | **FIXED** | Report replacement preserves pool facts and admits enrichment in the prior reproductions: `migration.py:551,579,592`; regressions at `tests/test_review_phase2_intel_round3.py:80,93` pass. |
| R3-4 | **PARTIAL** | Reference retention fails beyond the new cap: `migration.py:282,326,604`; R5-2. |
| R4-1 | **PARTIAL** | Detects nonzero missing token movement, but cannot detect cancelling unseen trades: `readings.py:128`; R5-1. |
| R4-2 | **PARTIAL** | Timestamp-enrichment reproductions pass because the reference is derived afresh (`migration.py:599`); cap exhaustion still loses it, R5-2. |
| R4-3 | **FIXED** | Earliest observation and each source’s first report are protected at `migration.py:584,589,592`; the regression at `tests/test_review_phase2_intel_round4.py:99` passes. |

Other requested checks:

- **B-02 arithmetic is exact.** Current comparisons are inclusive at `early_momentum.py:489`; peak rejection is strictly above the ceiling at `:501`; observed peaks never decrease at `:357`. Boundary tests are at `tests/test_b02_reserve_policy.py:49,59`. Additional in-memory checks immediately below, at, and above both current boundaries passed. Verified real reserves require the correct source, venue and quote asset (`readings.py:100`); the retired rule is metadata (`early_momentum.py:74`). History protection remains defective under R5-1.
- **Migration merging and identity work in the tested cases.** PumpAPI’s pool leg separates its report from PumpPortal’s (`normalize.py:109`; `events.py:100`). Reports merge at `migration.py:544`; holder requests occur on first observation at `:563`. An in-memory bus/store/core reproduction delivered both reports, confirmed migration, rejected two resends, and recorded no handler errors.
- **Other consumers:** token/wallet registries ignore migration statistics (`registry.py:67,136`); whales consume migration candidates once (`whales.py:129`); bundles ignore migrations (`bundles.py:67`). Core application and recovery use stored identities (`core_state.py:30`; `recovery.py:31`). Capture replay normalizes through the adapters (`sim.py:115`). No additional identity-related defect was established.
- **Timestamps and graduation:** report clock ranking preserves provider time over receipt time (`migration.py:117,131`); lifecycle upgrades receipt-time migration evidence (`lifecycle.py:241`). Near graduation uses provider category, with no default numerical threshold or score mapping (`graduation.py:191,210,225`). Progress references match the pinned donor passages.
- **Creator, funders, sells and holders:** creator participation counts and is flagged (`early_momentum.py:455`); shared verified funders fail, unknown funders remain unavailable (`:541,551,553`). Sell sizes and sellers are retained without a danger threshold (`migration.py:515,709`). Verified holder balances and explicitly qualified trade flow remain separate (`:398,798,812`), subject to R5-1’s false completeness flag.
- **Engine integration:** Early Momentum, Graduation and Migration subscribe on FAST after core state (`app.py:102,107,112,115`). Shared views read tracker evidence through `entry_check`, `migration_view` and `intel_health` (`:292,326,516`). GMGN calls run in background tasks with bounded concurrent admission and bounded lookup attempts (`:198,213,353,376,445`); Trenches polling runs separately (`:413`). No order-placement or sizing connection was found. Local eligibility flags are evidence, not execution authorization.

Validation: **188 focused tests passed; four fixture setups were blocked by temporary-directory permissions.** App integration collection was separately blocked by denied access to installed `psycopg`. Both findings reproduced in memory. Repository files remained unchanged.