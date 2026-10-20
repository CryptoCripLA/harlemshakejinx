# Phase 1 independent review: Codex (round 8)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `7d3fe55`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20. A first round-8 attempt on f2eac2d produced no review: OpenAI's service stopped it as "flagged for possible cybersecurity risk" (see `docs/evidence/PHASE1_EVIDENCE.md` §14).
- Copied from Windows by scp; sha256 of the reviewer's file: `e7df63d87fd9c11902425638b25ff4a4401f0495a125809a5908501447ef1aec`. The output follows unedited.

---

VERDICT: FAIL

## A. Open items from rounds 1–7

Reviewed commit `7d3fe55`. No files modified; Git status remains clean.

| Item | Status | Evidence |
|---|---|---|
| R7-1 — NUL-containing historical IDs | **FIXED** | Python extraction preserves embedded NUL at `jinx/store.py:211–216`; new intents reject controls at `jinx/execution.py:28–34`. Regression tests passed in memory. |
| R7-2 — Overbroad replacement claims | **FIXED** | The specific historical-repair claims now describe tested cases and await independent confirmation: `docs/evidence/PHASE1_EVIDENCE.md:161–163`, `docs/BUILD_STATUS.md:39`. New GMGN claim problems remain below. |
| Round-1 #3 — Exit ownership/fill identity | **PARTIAL** | Text-ID replay and collision regressions pass; BLOB-valued historical fill IDs prevent startup (`jinx/store.py:214`). |
| B1 — Existing database upgrades | **PARTIAL** | Tested v1–v6 text-ID upgrades and repeated opens successfully. All six starting versions fail on a BLOB sell ID (`jinx/store.py:214`). |
| R3-1 — Migrated exits execute again | **PARTIAL** | Tested historical text IDs cannot sell again (`jinx/ledger.py:185–189`). Required BLOB migration coverage remains broken. |
| R4-1 — Prefixed historical IDs | **FIXED** | Prefix-independent extraction and evidence-before-binding checks protect `p1:X`, including NUL variants (`jinx/store.py:214`, `jinx/ledger.py:185`). |
| R4-3 — Claims exceed implementation | **PARTIAL** | Historical claims were narrowed, but new lifecycle and capability claims exceed implementation/evidence (`docs/GMGN_CAPABILITY_INVENTORY.md:72`, `docs/evidence/PHASE1_EVIDENCE.md:178–180`). |
| R5-1 — Rejected binding hides historical fill | **FIXED** | Separate historical evidence outranks rejected/conflicting bindings (`jinx/store.py:215`, `jinx/ledger.py:185–189`). All 15 binding-state/amount combinations passed. |
| R6-2 — Unsupported completion assertions | **FIXED** | Previous assertions are qualified or superseded; unresolved review items remain explicit (`docs/evidence/PHASE1_EVIDENCE.md:148–163`, `docs/BUILD_STATUS.md:39`). |

**Migration coverage**

- **Tested:** 48 version/ID combinations covering actual v1 layout and reconstructed v2–v6 layouts, followed by repeated opens. IDs included case differences, surrounding whitespace, embedded NUL, Unicode, repeated `:sell`, a 100,000-character ID, and an intent equal to an existing buy-fill ID. Exact historical amounts returned `ALREADY_APPLIED`; changed/ambiguous amounts were refused.
- **Tested:** two sells per position, positions marked OPEN/CLOSED/FAILED, and reopening the same position row. Historical sells remained protected. Whole-position replay accepted only the latest closing sell in the tested closed-position case.
- **Tested:** BLOB-valued `fill_id` in every v1–v6 starting shape: all failed migration.
- **Reasoning:** two identical TEXT fill IDs cannot coexist because of the primary key. TEXT and BLOB values containing identical bytes are distinct SQLite keys; the BLOB scan failure prevents safe handling. Case/whitespace differences remain distinct opaque identities rather than being normalized.
- **Executed:** 58 selected prior-review tests passed using shared in-memory SQLite. These checks do not certify disk WAL or crash durability.

## B. Findings by severity

### CRITICAL

None found.

### HIGH — H1. BLOB historical sell IDs prevent database startup

**Locations:** `jinx/store.py:211–216`; `jinx/ledger.py:58`.

SQLite permits BLOB values in the TEXT-affinity `fill_id` column, and `Fill` does not enforce a string type. For a provenance-free sell with `fill_id=b"X:sell"`, migration calls `bytes.endswith(":sell")` and raises:

```text
TypeError: endswith first arg must be bytes or a tuple of bytes, not str
```

**Reproduced for schema v1 through v6.** One such row prevents JINX from opening the database, including recovery and paper exits.

**Fix:** Handle stored value types explicitly. Preserve unclassifiable evidence and quarantine affected identities/positions conservatively; do not silently decode and merge distinct SQLite keys. Reject non-string new fill IDs and add v1–v6 regressions.

### HIGH — H2. Post-migration state bypasses migration confirmation

**Locations:** `jinx/core/lifecycle.py:89–90`, `:103–112`; `docs/GMGN_CAPABILITY_INVENTORY.md:72`.

Only `Kind.MIGRATION_CONFIRMED` requires a pool. From UNKNOWN, this succeeds:

```python
observe(Evidence(Kind.POST_MIGRATION_MARKET, "provider", 1, 1, {"price": 2}))
```

Result: `POST_MIGRATION_MARKET`, with `post_pool=None` and no confirmation history. This permits a price-only report to assert a state beyond confirmed migration. Even direct confirmation checks only a truthy `post_pool`, despite the documented exchange/timestamp requirements.

**Fix:** Gate post-migration transitions on validated migration evidence. Enforce the documented pool/exchange/time requirements, or explicitly define an equivalent validated evidence contract. Test direct jumps and incomplete evidence.

### MEDIUM — M1. Rejected late reports mutate accepted pool identity and freshness

**Locations:** `jinx/core/lifecycle.py:91–100`.

Payload fields and `_last_observed_ns` change **before** event ordering is checked.

**Reproduced:** confirm pool `VALID` at event time 10, then receive an ON_CURVE report at event time 5 carrying `post_pool="OLD"`. The report produces no transition, but the confirmed pool becomes `OLD`. Repeated delayed reports also refresh observation time despite supplying no newer accepted fact.

**Fix:** Check ordering before mutating accepted lifecycle data. Track receipt time separately from accepted-evidence freshness and retain provenance for accepted pool changes.

### MEDIUM — M2. Conflicting pool reports and duplicate exceptional reports are mishandled

**Locations:** `jinx/core/lifecycle.py:96–109`; `docs/GMGN_ADAPTER_CONTRACT.md:106`.

**Reproduced:**

- Confirm pool `A`, then confirm pool `B`: pool changes silently, no transition occurs, and state remains MIGRATION_CONFIRMED.
- Report ON_CURVE twice after MIGRATION_OBSERVED: two CONFLICTING transitions are appended for the identical report.

These contradict the documented conflict and duplicate guarantees.

**Fix:** Compare migration identities before treating reports as equivalent. Deduplicate exceptional reports using stable evidence identity, retaining conflicting evidence and provenance.

### MEDIUM — M3. The lifecycle interface omits mandated state and retained evidence

**Locations:** `jinx/core/lifecycle.py:21–31`, `:59–86`, `:125–126`; mandate `docs/OWNER_GMGN_MANDATE_20261020.md:255–275`.

There is no FAILED exceptional state; `record_failure()` only appends a failure record. The interface also discards payloads needed to retain launchpad identity, graduation progress, Trenches category, and last valid price/liquidity. `Transition` retains no payload.

**Failure scenario:** after receiving progress and price/liquidity evidence, callers cannot retrieve the mandate’s lifecycle record or distinguish its failed state.

**Fix:** Define the required exceptional-state behavior and retain the mandated fields with their evidence provenance. This is interface work; provider translation can remain deferred.

### MEDIUM — M4. Local pacing continues requests during server cooldown

**Locations:** `jinx/adapters/gmgn/adapter.py:135–143`; `jinx/adapters/gmgn/bridge.mjs:86–87`.

The budget models local token availability but never receives structured server reset/cooldown information. A 429 only raises `GmgnError`; subsequent callers continue.

**Reproduced:** a simulated `RATE_LIMIT_BANNED` response specifying 300 seconds was followed immediately by another request, with zero budget waiting. Shared-key usage or server throttling can therefore cause polling to extend GMGN’s documented ban repeatedly.

**Fix:** Preserve structured rate-limit metadata across the bridge and apply a shared cooldown to queued/new calls. Account for upstream retries and test concurrent callers during cooldown.

### MEDIUM — M5. Route weights do not fully match pinned documentation

**Locations:** `jinx/adapters/gmgn/budget.py:36`, `:45–49`; `tests/test_gmgn_plus.py:79–86`; `docs/GMGN_CAPABILITY_INVENTORY.md:53`.

`getCookingStatistics` is charged 10 and described as undocumented. Its pinned documentation explicitly assigns **1**: `upstream/gmgn/skills/gmgn-cooking/SKILL.md:104–107`.

Also, `getTokenKline` always costs 2, while pinned market documentation assigns Pro `1s` requests weight **3** and a separate global limit (`upstream/gmgn/skills/gmgn-market/SKILL.md:65–72`).

**Failure scenario:** cooking-stat polling unnecessarily consumes half the Plus bucket; the exposed Pro budget undercounts second-resolution candles.

**Fix:** Add the documented cooking weight and make candle cost resolution-aware. Qualify or implement the separate Pro constraint. Replace assertions encoding the incorrect weights.

### MEDIUM — M6. Migration capability claims lack the stated evidence

**Locations:** `docs/evidence/PHASE1_EVIDENCE.md:180`; `docs/GMGN_CAPABILITY_INVENTORY.md:64–72`; `scripts/gmgn_capability_check.py:115–137`.

The docs claim six-token comparisons proving curve/completed status semantics and equality of migration pools/timestamps. The committed checker reads token info for **two** tokens, summarizes field names rather than those values, and records selected migration values for only one completed Trenches item. The JSON does not establish the six-token comparison.

The inventory also claims the interface requires nonzero completion time and the migration exchange, which it does not enforce.

**Fix:** Commit redacted per-token comparison evidence and an executable check, or narrow the measured claims. Correct the interface claims and dependent PASSED rows.

### LOW

None additional.

**Checks that passed**

- The bridge allowlist equals all **30 read methods** of the pinned client. All five mutations are excluded before client invocation (`jinx/adapters/gmgn/bridge.mjs:26–34`, `:78–80`).
- Signing-key defaults and the runtime override match the recorded owner decision (`jinx/config.py:28`, `jinx/app.py:70`).
- Credential-shape scans found no matches in the current committable files or **205 blobs reachable through HEAD history**. Exact configured-secret comparison was skipped because this host has no GMGN credentials; absolute credential absence is not independently certified.
- Budget burst/refill/capacity refusal tests passed. An additional 100 concurrent acquisitions spent exactly 100 weight over four seconds after initial capacity.
- No reachable real-money order path, second execution controller, Phase 2 signal implementation, or invented active strategy value found. Runtime uses paper execution (`jinx/app.py:55–57`); LIVE refuses without an executor (`jinx/execution.py:80–81`, `:98–99`); D3AD submission raises (`jinx/adapters/d3ad/adapter.py:20–21`).

**Test limitations:** the full suite produced **91 passed, 8 failed, 13 skipped, 134 errors**. The eight failures require missing `base58`; the 134 errors were temporary-directory permission failures. Separately, **58 adapted migration/review tests passed**, and **19 selected GMGN/lifecycle/secrets tests passed, 1 skipped**. Remote authenticated checks and the builder’s recorded 246-test run were not independently rerun.