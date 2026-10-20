# Phase 1 independent review: Codex (round 11)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `39525dd`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `031b08cd7dc45011a7caa6c3f3413ca09b97930615c7f3f18ab3495870f0a83a`. The output follows unedited.

---

VERDICT: FAIL

No CRITICAL or HIGH findings found at `39525dd`. The requested reproductions pass, but two lifecycle behaviors contradict the Phase 1 claims.

**A — Previous findings**

| Item | Status | Location and verification |
|---|---|---|
| R10-M1 — Creation metadata | **FIXED** | `jinx/core/lifecycle.py:153`, `:191`, `:247`. Reran both reproductions independently: confirmation at migration time 90 followed by creation time 50 preserves MIGRATION_CONFIRMED, launchpad and pre-pool without conflict; provider creation time 50 replaces receipt-time 100 and retains metadata. Regressions at `tests/test_lifecycle.py:254`, `:262` pass. |
| R10-L1 — Unobserved temporal sequence | **FIXED** | `docs/evidence/PHASE1_EVIDENCE.md:212` explicitly describes different tokens read once and disclaims a same-token sequence. Checked against the six committed observations. |
| R10-L2 — Missing confirmation venue | **FIXED** | `docs/GMGN_ADAPTER_CONTRACT.md:138` now supplies `exchange`. The mapping contract test at `tests/test_lifecycle.py:279` passes. |
| R9-H1 — Mixed-clock ordering | **FIXED** | `jinx/core/lifecycle.py:158`, `:210`. Reran curve observation at 100 followed by confirmation received at 110 with migration time 90: reaches MIGRATION_CONFIRMED with pool P. Delayed-creation regressions also pass. |
| Round-8 M3 — FAILED and mandated record | **PARTIAL** | Record/provenance and ordinary recovery tests pass (`tests/test_lifecycle.py:79`, `:187`). Creation handling at `jinx/core/lifecycle.py:193` now bypasses recovery from FAILED; reproduced below. |
| R9-L1 — Documentation inconsistencies | **FIXED** | Prior discrepancies corrected at `docs/GMGN_ADAPTER_CONTRACT.md:92`, `:113`, `:138`; `docs/GMGN_CAPABILITY_INVENTORY.md:31`, `:74`; evidence `:212`. Compared with implementation and committed reports. New discrepancies are identified below. |
| R4-3 — Claims exceed implementation | **PARTIAL** | `docs/BUILD_STATUS.md:30`, evidence `:209`, contract `:113`, inventory `:77`. Two lifecycle claims remain broader than enforced behavior. |

**B — Fresh review**

**CRITICAL:** None found.

**HIGH:** None found.

**MEDIUM — R11-M1: Successful creation observations cannot recover FAILED or STALE**

Locations: `jinx/core/lifecycle.py:191`, `:193`; `docs/GMGN_ADAPTER_CONTRACT.md:113`; `docs/evidence/PHASE1_EVIDENCE.md:209`.

Executed sequence:

1. CREATED received at 100, provider creation time 50.
2. Polling failure received at 110 → FAILED.
3. Successful CREATED observation received at 120, creation time 50.

Result: state remains **FAILED**, and `last_accepted_ns` remains **100**. The creation-specific early return bypasses exceptional-state recovery. The equivalent STALE sequence has the same problem.

This contradicts the documented recovery on the next accepted consistent observation and regresses recovery for tokens still in NEWLY_DETECTED.

**Fix:** Distinguish historical metadata enrichment from a successful current creation observation. Allow the latter to refresh observation freshness and recover an exceptional NEWLY_DETECTED token without regressing later lifecycle stages. Add FAILED and STALE regressions.

**MEDIUM — R11-M2: Receipt time is accepted as provider migration time**

Locations: `jinx/core/lifecycle.py:72`, `:200`, `:203`; `docs/GMGN_CAPABILITY_INVENTORY.md:77`; `docs/GMGN_ADAPTER_CONTRACT.md:146`.

Executed MIGRATION_CONFIRMED with pool P, venue `pump_amm`, positive `event_ns`, and **`clock="receipt"`**. It reaches MIGRATION_CONFIRMED and records receipt-clock pool provenance.

A caller lacking provider migration time can therefore substitute receipt time and pass validation, despite the claim that the interface enforces provider migration time.

**Fix:** Validate the clock discriminator and require `clock="provider"` for confirmation, alongside pool, venue and positive time. Test receipt-clock and invalid-clock rejection before any state or metadata mutation.

**LOW:** None additional.

Verification and scope:

- **73 targeted tests passed; 12 skipped; 5 deselected.** This includes all lifecycle regressions and the documentation mapping test.
- **59 additional tests passed with an in-memory store fixture.** This does not verify disk durability or restart behavior.
- Initial runs encountered Windows temp-directory denial and default-encoding failure; UTF-8 resolved the encoding issue. Missing `base58` prevented feed integration checks. In-memory health checks also cannot satisfy the WAL requirement.
- Compiled GMGN bridge integration and authenticated checks were not rerun. The builder’s **307 passed** remains recorded evidence, not an independently reproduced result.
- All four upstream pins verify. Recorded API counts, latencies, plan headers and six-token values support the capability summaries. The exceptions to lifecycle claims are above.
- Runtime wires paper execution at `jinx/app.py:55`; LIVE refuses without an executor at `jinx/execution.py:76`, `:98`. The bridge enables exactly 30 read methods and excludes all five mutations; D3AD submission refuses at `jinx/adapters/d3ad/adapter.py:20`.
- No reachable real-money order path, second execution controller, Phase 2 signal implementation or invented active strategy value found.
- Repository remained unchanged; Git status is clean.