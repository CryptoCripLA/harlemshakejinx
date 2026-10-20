# Phase 1 independent review: Codex (round 10)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `2bb51c3`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `e704ef2c0f228748bcf28584dd7f8c6366b96b469ce320c0172520f848544e8d`. The output follows unedited.

---

VERDICT: FAIL

Reviewed `2bb51c3` read-only. No CRITICAL or HIGH findings remain, but lifecycle metadata handling remains incomplete and some Phase 1 documentation claims are unsupported.

**A — Previous findings**

| Item | Status | Location and verification |
|---|---|---|
| R9-H1 — Mixed-clock lifecycle ordering | **PARTIAL** | `jinx/core/lifecycle.py:149`, `:197`, `:208`. Original reproduction now reaches MIGRATION_CONFIRMED: curve query at 100, confirmation received at 110 with migration time 90. Passing regression. Delayed creation metadata still fails in two reproduced sequences described below. |
| R9-M1 — Queued request during ban | **FIXED** | `jinx/adapters/gmgn/adapter.py:185`. The actual `_send` checks cooldown after `_ensure_running`, without an intervening await before writing. Queued-call regression passed and produced no stdin write. |
| R9-M2 — Body-only `reset_at` | **FIXED** | `jinx/adapters/gmgn/bridge.mjs:71`; `adapter.py:171`. Executed the actual fetch wrapper against a synthetic 429: body reset preserved in the header, body unchanged. Mocked adapter checks honored that reset and the 300-second missing-reset fallback. Full compiled-client integration unavailable locally. |
| R9-M3 — Tied closing timestamps | **FIXED** | `jinx/ledger.py:245`. In-memory execution of the historical-schema regression refused whole-position replays for both tied sells, retained exact-amount ALREADY_APPLIED results and retained exactly two sells. |
| R9-L1 — Documentation inconsistencies | **PARTIAL** | `docs/GMGN_ADAPTER_CONTRACT.md:92`, `:113`; `docs/GMGN_CAPABILITY_INVENTORY.md:31`, `:74`. Retry, FAILED, wallet amounts and principal cross-sectional descriptions corrected. Temporal wording remains at `docs/evidence/PHASE1_EVIDENCE.md:212`. Verified against code and committed JSON. |
| Round-8 M3 — FAILED and mandated lifecycle record | **PARTIAL** | `jinx/core/lifecycle.py:179`, `:223`. FAILED, provenance and normal-path record tests pass. Creation time, launchpad and pre-pool evidence can still be discarded. |
| Round-8 M4 — Server cooldown | **FIXED** | `jinx/adapters/gmgn/adapter.py:160`, `:185`; `bridge.mjs:71`. Queued-call reproduction passed; body-reset and fallback checks passed separately. Pinned documentation supports the fallback. |
| Round-1 #3 — Exit ownership/fill identity | **FIXED** | `jinx/ledger.py:185`, `:228`. Adapted prior-review ledger regressions passed, including ambiguous history, identity collisions and tied timestamps. No repeated historical sale found. |
| R4-3 — Claims exceed evidence/implementation | **PARTIAL** | `docs/BUILD_STATUS.md:30`; `docs/evidence/PHASE1_EVIDENCE.md:209`, `:212`; `docs/GMGN_ADAPTER_CONTRACT.md:137`. Remaining exceptions are detailed below. |

**B — Fresh findings**

**CRITICAL:** None found.

**HIGH:** None found.

**MEDIUM — R10-M1: Creation evidence still loses required metadata or falsely creates a migration conflict**

Locations: `jinx/core/lifecycle.py:149`, `:157`, `:197`, `:208`, `:232`; `tests/test_lifecycle.py:237`.

Two concrete reproductions:

- Accept MIGRATION_CONFIRMED with migration time 90, pool P and venue `pump_amm`. Then receive CREATED with event time 50, launchpad and pre-pool. The token becomes CONFLICTING; creation metadata is absent. `_contradicts` treats CREATED as contradictory before the creation-specific handling executes.
- Accept PumpPortal CREATED at receive/event time 100. Then receive GMGN CREATED at observation time 110 with provider creation time 50. The shared CREATED watermark rejects the GMGN report entirely, losing its launchpad and pre-pool. The stored creation time remains the receive-time approximation.

Both were executed against the current code. The added delayed-creation test covers neither sequence.

These are ordinary combinations of the two sources explicitly mapped by the adapter contract. They violate mandate §3.1 and the claim that delayed evidence can supply missing lifecycle metadata.

**Fix:** Process historical creation metadata independently of current-stage contradictions. Distinguish source/clock semantics when merging creation facts, retain provenance, and preserve the current migration stage. Add both reproductions as regressions.

**LOW — R10-L1: Evidence still implies an unobserved temporal sequence**

Location: `docs/evidence/PHASE1_EVIDENCE.md:212`.

The replacement-evidence explanation still says token info showed status “2 right after completion and 1 later.” The committed report samples different tokens once; it establishes no same-token transition.

**Failure scenario:** An implementer treats the sentence as verified lifecycle behavior.

**Fix:** Explicitly describe different tokens at different completion ages, consistent with the corrected inventory.

**LOW — R10-L2: Exact confirmation mapping omits required venue payload**

Locations: `docs/GMGN_ADAPTER_CONTRACT.md:137`, `:146`; `jinx/core/lifecycle.py:184`.

The translation table supplies only `post_pool` for MIGRATION_CONFIRMED, while validation also requires payload `exchange`.

**Failure scenario:** A Phase 2 translator implementing the table “exactly as below” produces confirmations rejected with MissingEvidence.

**Fix:** Include `exchange` in that row’s payload mapping and verify the documented mapping with a contract test.

Verification and scope:

- **42 selected tests passed; 12 skipped**, using an existing-directory fixture.
- **78 adapted prior-review tests passed** using isolated in-memory SQLite. Five additional failures involved missing `base58` or resulting runtime-health degradation.
- Unadapted ledger tests encountered Windows temp-directory denial. Compiled-bridge integration and authenticated checks could not be rerun. The builder’s **303 passed** result was not independently reproduced.
- All four upstream checkouts passed pin verification. Recorded API counts, quoted latencies, plan headers and six-token comparison values match the committed reports.
- The bridge still enables exactly **30 read methods** and refuses all five mutations. Runtime wires paper execution at `jinx/app.py:55`; LIVE refuses without an executor at `jinx/execution.py:80`, `:98`; D3AD submission refuses at `jinx/adapters/d3ad/adapter.py:20`.
- No reachable real-money order path, second execution controller, Phase 2 signal implementation or invented active strategy value found.
- Git status remained clean.