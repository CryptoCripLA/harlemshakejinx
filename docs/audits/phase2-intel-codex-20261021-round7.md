VERDICT: FAIL

1. **MEDIUM — R7-1: M3 can still select a falsely definitive later reference after migration-slot enrichment.**

   **Evidence:** `jinx/intel/migration.py:611` filters retained trades using both the delay boundary and migration ordering. However, `:620` recognizes lost history only when the last retained timestamp is **strictly before** the boundary. If retained trades reach that boundary but fail the updated slot check, `:624` reports `"no qualifying trade yet"`. A subsequent trade is then installed as `"found"` at `:646`.

   **Reproduce in memory**, using `tests/pumpapi_msgs.py`, delivering every normalized event:

   - Construct `MigrationTracker(confirmation_delay_ms=1)`; deliver `create()`. Let `T = T0_MS + 60000`.
   - Call `record_report` for `MintApump`, source `"gmgn.trenches"`, provider time `T`, observation time `T+50`, pool `"AMM1"`.
   - Deliver 4,096 reserve-bearing `amm_trade` buys: timestamp `T+i`, slot `500+i`, payer `P{i}`, for `i=0..4095`.
   - Deliver `TRUEFIRST`: timestamp `T+4100`, slot `5000`.
   - Deliver `migrate(t_ms=T+4094, slot=4999)`.
   - Deliver `LATER`: timestamp `T+4200`, slot `5001`.

   **Observed:** after enrichment, M3 reports `"no qualifying trade yet"`; after the final arrival, it reports `"found"` with payer `LATER`.

   **Expected:** `TRUEFIRST` was the first observed qualifying trade under the updated record, but retention discarded it. M3 must report that the reference is not determinable. The retained boundary trade fails the slot check and cannot establish completeness.

   This independently reproduced with a positive confirmation delay. It affects optional paper-reference selection and can distort research comparisons; no live-order effect was established. The broad guarantees at `docs/evidence/PHASE2_MOMENTUM_MIGRATION.md:120` and `docs/SOURCE_FIDELITY_MATRIX.md:72` remain overstated.

The required prior-finding dispositions are:

| Item | Status | Evidence |
|---|---|---|
| F9 | **PARTIAL** | Most claims are supported; the M3 uncertainty guarantee at `docs/evidence/PHASE2_MOMENTUM_MIGRATION.md:120` still fails under R7-1. |
| R2-7 | **PARTIAL** | C10 history protection is supported; the M3 claim at `docs/SOURCE_FIDELITY_MATRIX.md:72` remains overstated. |
| R3-4 | **PARTIAL** | Tracking preserves references beyond retention (`migration.py:647`), but slot enrichment exposes discarded qualifying history. |
| R4-2 | **PARTIAL** | Recalculation uses the current record (`migration.py:573,611`), but its uncertainty test misses R7-1 (`:620`). |
| R5-2 | **PARTIAL** | The original cap-exhaustion regression passes (`tests/test_review_phase2_intel_round5.py:37`); R7-1 remains. |
| R6-1 | **FIXED** | Established uncertainty stays sticky (`migration.py:608`), settlement runs during report enrichment (`:573`), and both original reproductions pass (`tests/test_review_phase2_intel_round6.py:17,32`). R7-1 instead fails to establish uncertainty initially. |

Other requested checks:

| Check | Result and evidence |
|---|---|
| B-02 | Exact inclusive current comparisons (`early_momentum.py:484`), strictly-above peak rejection (`:496`), monotonic peak (`:353`), incomplete-history blocking (`:498`), migrated exclusion (`:478,524`). Verified readings require the stated source, venue and quote asset (`readings.py:97`); superseded rule retained only as metadata (`early_momentum.py:74`). Boundary tests (`test_b02_reserve_policy.py:51,59`) and additional immediately-adjacent base-unit checks passed. |
| Migration merging and effects | Separate event identities (`normalize.py:109`, `events.py:100`); authoritative merging (`migration.py:542`). An independent RAM-only bus/store/core run admitted both reports, rejected both resends, confirmed migration, produced three distinct notifications, and issued one holder request and one pool request. No handler errors. |
| Times and identity consumers | Provider timestamps remain in normalized payloads and are selected by `readings.py:48` and `migration.py:117,131`. Lifecycle receipt-time replacement is at `lifecycle.py:241`. Registries ignore migration statistics (`registry.py:67,136`); whales consume migration candidates once (`whales.py:129`); bundles ignore migration events (`bundles.py:67`). No additional identity regression established. |
| Graduation | Near-graduation comes from provider category (`graduation.py:191,225`); no default points formula (`:209`). Progress calculations match the pinned donor constants and formulas. |
| Creator and funders | Creator participation counts and is flagged (`early_momentum.py:450`). Shared verified funders fail; unknown funders remain unavailable (`:539,544,546`); emergency flag is exposed at `:191`. |
| Sells and holders | Sizes and sellers are recorded without an invented danger label (`migration.py:518,750`). Verified balances (`:402`) remain separate from explicitly qualified observed trade flow (`:817,832`). |
| Engine integration | All three modules subscribe on FAST after core state (`app.py:102,107,112,115`). Shared views use `entry_check`, migration views and health (`:292,326,516`). GMGN work runs in background tasks with bounded admission (`:213,353,376,445`); Trenches polling is separate (`:413`). No order-placement or sizing connection found. |
| Documentation | C05, C10, M1 and M2 claims were supported within the checked scope. M3 remains only partially supported because of R7-1. |

Validation: **193 tests passed**. Four temporary-directory tests were blocked by filesystem permissions; app integration collection was blocked by denied access to installed `psycopg`. The committed capture replay produced **38 detections and zero locally permitted entries**. Both round-6 regressions passed; R7-1 reproduced independently. Repository files remained unchanged.