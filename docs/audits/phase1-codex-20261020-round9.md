# Phase 1 independent review: Codex (round 9)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `c8c6e48`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `de8cc8a27488d91af704a449fbc2320d4a37eeaaaa3c108cf95fc2da9f4169cd`. The output follows unedited.

---

VERDICT: FAIL

Reviewed `c8c6e48` read-only. No files modified. One HIGH defect remains; several correctness and documentation claims remain unsupported.

**A — Round-8 findings and outstanding items**

| Item | Status | Verification and location |
|---|---|---|
| H1 — Non-text historical fill IDs prevent startup | **FIXED** | `jinx/store.py:222` preserves BLOB/NULL evidence separately; `jinx/ledger.py:185` refuses affected exits. Tested actual v1–v6 schemas, repeated opens, BLOB/NULL and mixed SQLite identities. Replay-reporting ambiguity remains separately below. |
| H2 — Post-migration state bypasses confirmation | **FIXED** | `jinx/core/lifecycle.py:179` requires pool, venue and positive migration time; post-migration data requires prior confirmation. Lifecycle regressions passed. |
| M1 — Late reports mutate pool/freshness | **FIXED** | `jinx/core/lifecycle.py:141` rejects older reports before recording fields or accepted freshness. Both regressions passed. The ordering rule introduces the HIGH defect below. |
| M2 — Pool conflicts/repeated exceptional transitions | **FIXED** | `jinx/core/lifecycle.py:189` detects conflicting pools; `:198` retains reports without repeated CONFLICTING transitions. Regressions passed. |
| M3 — Missing FAILED state and lifecycle record | **PARTIAL** | FAILED and provenance-bearing fields exist at `jinx/core/lifecycle.py:38`, `:118`, `:172`, `:204`. Normal-path tests passed, but delayed creation evidence is discarded before its required fields are retained (`:141`). |
| M4 — Requests continue during server cooldown | **PARTIAL** | Structured errors and pause checks exist at `jinx/adapters/gmgn/adapter.py:160`. Reproduced a queued send after the ban was registered; body-only reset times are also lost. See MEDIUM findings. |
| M5 — Incorrect route weights | **FIXED** | `jinx/adapters/gmgn/budget.py:44`, `:52`: cooking statistics costs 1; 1-second candles cost 3. Pinned-document comparison passed with UTF-8 enabled. Separate Pro global limit is documented at `:47`. |
| M6 — Missing six-token comparison evidence | **FIXED** | `scripts/gmgn_capability_check.py:96` records comparisons; `docs/evidence/gmgn_capability_check_20261020T2001.json:992` contains six rows. Values support the inventory’s cross-sectional table. They do not establish a same-token time sequence. |
| Round-1 #3 — Exit ownership/fill identity | **PARTIAL** | Duplicate-sale protections passed at `jinx/ledger.py:185`; ambiguous whole-position replay can still be reported as successful at `:244`. |
| B1 — Existing database upgrades | **FIXED** | `jinx/store.py:201`, `:212`: tested actual v1–v6 layouts, including BLOB/NULL IDs, upgrade to v8 and repeated opens. |
| R3-1 — Migrated exits execute again | **FIXED** | `jinx/ledger.py:185`, `:187`: classified history prevents re-execution; unclassified history refuses exits. Tests found no repeated historical sale. |
| R4-3 — Claims exceed implementation/evidence | **PARTIAL** | Cooldown guarantees, lifecycle contract text and some measured claims remain inconsistent or unsupported. Details below. |

For H1, **216 schema/ID/status combinations passed**, each opened twice. Coverage included case, whitespace, embedded NUL, Unicode, repeated `:sell`, a 100,000-character ID, an intent matching a buy-fill ID, BLOBs and NULLs, across OPEN/CLOSED/FAILED positions. Additional checks covered mixed TEXT/BLOB keys, multiple NULL keys, multiple historical sells and reopened positions.

Exact text replays returned `ALREADY_APPLIED`; changed/ambiguous requests were refused except for the timestamp-tie defect below. BLOB/NULL histories returned `UNCLASSIFIED_HISTORY`, without decoding or merging identities. These were in-memory checks, not disk-WAL or crash-durability certification.

**B — Fresh findings**

**CRITICAL**

None found.

**HIGH — R9-H1: Valid migration evidence can be permanently rejected**

Locations: `jinx/core/lifecycle.py:141`; `docs/GMGN_ADAPTER_CONTRACT.md:124`, `:127`.

The contract timestamps curve queries with read time, but confirmations with the provider’s migration time. One global event-time watermark compares these different facts.

**Reproduced:** accept NEAR_COMPLETION at event time 100, then receive a confirmation at observation time 110 with migration time 90, a valid pool and venue. Confirmation is discarded; state remains APPROACHING_GRADUATION and `post_pool` remains absent. Every subsequent confirmation carrying that same migration timestamp is also discarded.

This occurs when indexing delays expose completion after a newer curve query. It prevents the mandated lifecycle from recognizing an established migration. Delayed CREATED evidence similarly loses creation time, launchpad and pre-pool metadata.

**Fix:** distinguish observation freshness from fact timestamps. Apply ordering per fact/source and permit validated forward migration evidence despite an older migration timestamp. Retain historical metadata without regressing current state. Add mixed query-time/provider-time regressions.

**MEDIUM — R9-M1: A queued request can send after cooldown begins**

Locations: `jinx/adapters/gmgn/adapter.py:162`, `:183`, `:189`.

The final ban check precedes `await self._ensure_running()`. A caller waiting there can resume after another response sets `paused_until`, then write its request without another check.

**Reproduced using the actual `_send`:** one request waited in `_ensure_running`; another registered a ban until time 400. Releasing the waiting request caused a stdin write while `paused_until == 400`.

The new concurrency test starts callers after the ban already exists, so it misses this interleaving.

**Fix:** recheck cooldown after acquiring the generation and immediately before writing, with no intervening await. Test callers already queued when the ban arrives. Revise the unconditional guarantees in `docs/evidence/PHASE1_EVIDENCE.md:210` and `docs/GMGN_ADAPTER_CONTRACT.md:84`.

**MEDIUM — R9-M2: Documented body-only ban reset times are discarded**

Locations: `jinx/adapters/gmgn/bridge.mjs:98`; `jinx/adapters/gmgn/adapter.py:169`; `upstream/gmgn/src/client/OpenApiClient.ts:734`, `:756`.

Pinned GMGN documentation explicitly permits a response-body `reset_at` (`upstream/gmgn/skills/gmgn-token/SKILL.md:73`). The client extracts only the reset header; the bridge forwards only that extracted value.

**Failure scenario:** GMGN returns a 300-second ban in JSON without the header. JINX loses the supplied reset and substitutes five seconds, then resumes calls during the active ban. Those requests can extend it.

**Fix:** preserve documented body reset metadata through a tested compatibility layer while retaining the pinned client. Do not treat the five-second ban-extension increment as proof that an unknown cooldown lasts five seconds. Add a body-only-reset regression.

**MEDIUM — R9-M3: Timestamp ties falsely identify an unambiguous closing replay**

Location: `jinx/ledger.py:244`.

Whole-position historical replays are accepted whenever their fill timestamp equals `MAX(ts_ns)`.

**Reproduced:** two historical partial sells of five tokens share a timestamp and together close a ten-token position. Replaying either intent with `token_amount=None` returns `ALREADY_APPLIED`. Both cannot be uniquely established as the closing sell.

No duplicate sale occurred, but the success result asserts certainty the stored evidence lacks.

**Fix:** refuse whole-position replay when the closing fill cannot be uniquely established. Do not invent ordering from tied timestamps. Preserve exact-amount replay behavior.

**LOW — R9-L1: Documentation still contradicts code or exceeds recorded evidence**

Locations:

- `docs/GMGN_ADAPTER_CONTRACT.md:100` says the client retries short bans. The adapter disables that retry, and the documented missing-`await` issue prevents it in the pinned implementation.
- `docs/GMGN_ADAPTER_CONTRACT.md:108` says failures do not change state and omits FAILED; `jinx/core/lifecycle.py:172` enters FAILED.
- `docs/SOURCE_FIDELITY_MATRIX.md:69` says status was read “2, then 1.” The committed evidence samples different tokens once; it establishes no same-token transition.
- `docs/GMGN_CAPABILITY_INVENTORY.md:31` gives exact wallet balances and dollar valuation absent from the committed capability reports. Those reports retain field names and holding counts, not these values.

**Failure scenario:** a reviewer or implementer relies on incorrect failure/retry behavior or treats an unrecorded measurement as verified.

**Fix:** update the contract to implemented behavior, describe the migration sample as cross-sectional, and either commit redacted balance evidence or remove/qualify those numbers.

**Verification and scope**

- **68 adapted prior-review/migration tests passed.**
- **36 selected lifecycle/GMGN/secrets tests passed; 11 skipped**, using UTF-8 and an existing-directory fixture.
- Unadapted full suite: **109 passed, 9 failed, 22 skipped, 155 errors**. Limitations included missing `base58`, temp-directory permissions and Windows decoding. The compiled GMGN bridge was unavailable; authenticated checks and the builder’s `295 passed` run were not independently reproduced.
- The allowlist still matches all **30 pinned read methods** and excludes all five mutations (`jinx/adapters/gmgn/bridge.mjs:22`, `:86`).
- No reachable real-money order path, second execution controller, Phase 2 signal logic or invented active strategy value found. Runtime wires paper execution (`jinx/app.py:55`); LIVE refuses without an executor (`jinx/execution.py:80`, `:98`); D3AD submission remains disabled (`jinx/adapters/d3ad/adapter.py:20`).
- Git status remained clean.