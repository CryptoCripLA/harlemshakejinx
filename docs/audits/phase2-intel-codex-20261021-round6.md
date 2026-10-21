VERDICT: FAIL

1. **MEDIUM — R6-1: M3 replaces an explicitly indeterminable reference with a falsely definitive later trade.**

   Evidence: `jinx/intel/migration.py:633`–`:638` installs an arriving trade after the migration time changes; `:609`–`:613` then reports `"found"` without preserving the uncertainty caused by discarded earlier trades.

   **Reproduce in memory**, using `tests/pumpapi_msgs.py` helpers, delivering every normalized event:

   - Construct `MigrationTracker(confirmation_delay_ms=5000)`; deliver `create()`. Set `T = T0_MS + 60000`.
   - Record a `gmgn.trenches` provider report at `T`, observed at `T+50`, naming `AMM1`.
   - Deliver 4,096 reserve-bearing `amm_trade` buys at `T+i` milliseconds, slots `500+i`.
   - Deliver buys at `T+6000`, slot 5000, payer `OLDREF`; then `T+7000`, slot 5001, payer `TRUEFIRST`.
   - Deliver `migrate(t_ms=T+1500, slot=499)`. The new delay boundary is `T+6500`; evidence correctly reports `"not determinable"`.
   - Deliver another buy at `T+8000`, slot 5002, payer `LATER`.

   **Observed:** `research["reference_status"] == "found"` and the reference payer is `LATER`. The actual first qualifying observed trade was `TRUEFIRST`, which has been discarded.

   A bounded structure reporting that it cannot answer is acceptable. The defect is subsequently claiming an answer it cannot establish. This can select the wrong paper reference and distort research results. M3 remains off by default; no live-order effect was established.

   The round-5 regression (`tests/test_review_phase2_intel_round5.py:37`) covers a fixed migration time, missing this enrichment sequence.

The required prior-finding dispositions are:

| Item | Status | Current evidence |
|---|---|---|
| F1 | **FIXED** | Gap histories block at `early_momentum.py:277,500,502`; receipt-only migration flow stays incomplete at `migration.py:786`. |
| F9 | **PARTIAL** | History claims are corrected; M3’s uncertainty guarantee at `docs/evidence/PHASE2_MOMENTUM_MIGRATION.md:120` remains contradicted by R6-1. |
| R2-7 | **PARTIAL** | C10 history protection is supported; M3’s first-reference claim at `docs/SOURCE_FIDELITY_MATRIX.md:72` remains overstated under R6-1. |
| R3-2 | **FIXED** | Provider-timed pre-gap events and receipt-only creations are handled at `early_momentum.py:277,293,502` and `migration.py:341,346,786`. |
| R3-4 | **PARTIAL** | References beyond retention are tracked (`migration.py:630`), but enrichment can produce a false first reference; R6-1. |
| R4-1 | **FIXED** | Reserve equality no longer establishes completeness; unconditional blocking at `early_momentum.py:502` and `migration.py:790`. |
| R4-2 | **PARTIAL** | Recalculation handles retained trades (`migration.py:603`); lost-history uncertainty does not survive subsequent arrivals; R6-1. |
| R5-1 | **FIXED** | Unsound continuity helper removed; both trackers reject the ambiguous history. Round-5 reproductions pass. |
| R5-2 | **PARTIAL** | Original cap-exhaustion reproduction passes; the new tracked-reference logic still fails after timestamp enrichment; R6-1. |

Other requested checks:

| Check | Result and file:line evidence |
|---|---|
| B-02 | Comparisons are inclusive and exact (`early_momentum.py:484`); peak rejection is strictly above the ceiling (`:496`), and peaks never decrease (`:353`). Verified real reserves require source, venue and quote asset (`readings.py:97`). Retirement metadata is at `early_momentum.py:74`; migrated tokens block at `:478,524`. Boundary tests at `test_b02_reserve_policy.py:51,59` pass; additional checks immediately below, at and above both current boundaries passed. |
| Migration merging and effects | Separate report identities at `normalize.py:109` and `events.py:100`; merging at `migration.py:542`; duplicate-report rejection at `:545`; holder requests on first observation at `:558`. An in-memory bus/store/core reproduction confirmed migration, rejected both resends, issued one holder request and one pool request, and produced three distinct notifications: observed, pool enrichment, confirmed. No handler errors. |
| Provider times and other consumers | Provider clock selection at `readings.py:48`; report ranking at `migration.py:117,131`; receipt-to-provider lifecycle upgrade at `lifecycle.py:241`. Registries ignore migration statistics (`registry.py:67,136`); whales consume candidates once (`whales.py:129`); bundles ignore migrations (`bundles.py:67`). Store/recovery identities and adapter-based replay showed no additional identity regression. |
| Graduation | Provider category determines near-graduation status (`graduation.py:191,225`). No default score mapping (`:210`). Progress readings match the cited pinned donor constants and formulas. |
| Creator and funders | Creator participation counts and is flagged (`early_momentum.py:450`). Shared verified funders fail; missing funders remain unavailable (`:539,544,546`). |
| Sells and holders | Sell sizes and identities are ranked without a danger threshold (`migration.py:520,741`). Verified balances are separate from explicitly qualified trade flow (`:402,808,823`). |
| Engine integration | All three trackers subscribe on FAST after core state (`app.py:102,107,112,115`). Evidence reaches `entry_check`, migration views and health (`:292,326,516`). GMGN calls run in background tasks with bounded admission (`:213,353,376,445`); Trenches polling is separate (`:413`). No order-placement or sizing connection was found. Local eligibility flags describe evidence. |

Validation: **191 focused tests passed**. Four tests requiring temporary directories were blocked by filesystem permissions; app integration collection was blocked by denied access to installed `psycopg`. The committed 1,200-record replay produced 38 detections and zero locally permitted entries; live-migration fixture tests also passed. R6-1 reproduced independently in memory. Repository files remained unchanged.