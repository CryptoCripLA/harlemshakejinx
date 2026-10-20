# Phase 1 independent review: Codex (round 6)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `f270bf5`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `575da5c41087bf15ff18340218c8771a550a20d0e9bf73bdf223032da85e19ed`. The output follows unedited.

---

VERDICT: FAIL

Reviewed `f270bf5` and the complete diff from `1b6d797`. No files modified; Git status remains clean.

## A. Verification of previous findings

| Item | Status | Verification |
|---|---|---|
| **R5-1 — Rejected binding hides historical fill** | **PARTIAL** | Exact escaped-v3 → rejected `X` → upgrade → replay sequence now returns `ALREADY_APPLIED`, preserving 750,000,000 tokens. However, an existing binding under historical intent `p1:X` defeats `INSERT OR IGNORE` at `jinx/store.py:157` and permits another sell. See R6-1. |
| **R5-2 — Cancelled unsubscribe and clean reconnect** | **FIXED** | Five-step sequence followed by code-1000 reconnect passes: held `A` remains subscribed on the new connection and in Mako’s tracking. Cancellation repairs tracking at `jinx/adapters/mako/feed.py:141`; connection replacement resets confirmations at `:122`. |
| **R5-3 — Loopback allowed by replay guard** | **PARTIAL** | Original `connect()` and `create_connection()` loopback reproductions are blocked. `connect_ex()` remains unguarded at `tests/test_replay_live_capture.py:104–106`. See R6-3. |
| **R4-1 — Prefixed historical exit IDs** | **PARTIAL** | Original regression passes; conflicting historical bindings still permit duplicate execution (`jinx/store.py:157`, `jinx/ledger.py:189`). |
| **R4-2 — Pending unsubscribe drops held mint** | **FIXED** | Cancellation, subsequent syncs and clean reconnect preserve the held subscription (`jinx/adapters/mako/feed.py:138–165`). |
| **R4-3 — Completion claims exceed implementation** | **PARTIAL** | Independent confirmation is distinguished from builder assertions, but unconditional repair and network-proof claims remain false (`BUILD_STATUS.md:32`, `docs/evidence/PHASE1_EVIDENCE.md:56,141`). |
| **R3-1 — Migrated exits execute again** | **PARTIAL** | Original historical `X` tests pass; R6-1 still duplicates a migrated exit (`jinx/ledger.py:185–213`). |
| **R3-2 — Failed unsubscribe forgotten** | **FIXED** | Failed operations remain pending, retry successfully and degrade health; cancellation/reconnect continuation also passes (`jinx/adapters/mako/feed.py:143–165`). |
| **B1 — Existing database upgrades** | **PARTIAL** | Schema upgrades, row preservation, budget reconstruction and constraints pass. Historical identity repair remains incomplete (`jinx/store.py:157,196–205`). |
| **B5 — OFF paid subscriptions** | **FIXED** | Tested mode gating, unsubscribe retry and held-position preservation across clean reconnect (`jinx/adapters/mako/feed.py:100–165`). |
| **Round-1 #3 — Exit ownership/fill identity** | **PARTIAL** | Fresh-database ownership, conflict and replay checks pass; migrated identity remains vulnerable through R6-1 (`jinx/ledger.py:178–213`). |

## B. Findings by severity

### HIGH — R6-1. An existing binding under the historical intent still defeats v5 repair

**Locations:** `jinx/store.py:157–160`; `jinx/ledger.py:185–200,209–213`; missing regression in `tests/test_review_round5.py:19–46`.

The backfill silently ignores a historical fill when `exit_intents` already contains its derived intent ID. `claim_exit` then checks only the modern generated fill ID and can execute the historical intent again.

**Reproduced through the older claim implementation:**

1. Escaped v3 state contains historical `p1:X:sell`, representing a 250,000,000-token sale; inventory is 750,000,000.
2. Before upgrading, submit historical intent `p1:X` while its quote is unavailable.
3. The attempt returns `NO_QUOTE`, leaving binding `p1:X` with `legacy_fill_id=NULL`.
4. Upgrade to v5. The historical insertion conflicts with that binding and is ignored.
5. Restore the quote and replay `p1:X` for 250,000,000.

Observed:

```text
v5 bindings: [('p1:X', None)]
replay: ok=True, reason=None
inventory: 750000000 → 500000000
sell fills: p1:X:sell, p1:p1:X:sell
```

The original rejected-`X` regression passes because `X` and historical `p1:X` occupy different binding keys. It does not cover this collision.

**Fix:** Reconcile conflicting bindings explicitly during backfill. Preserve historical evidence independently or quarantine ambiguous identities; never silently discard it. Add the rejected-`p1:X`/`NO_QUOTE` → upgrade → replay regression.

### MEDIUM — R6-2. Completion assertions remain unsupported

**Locations:** `BUILD_STATUS.md:32`; `docs/evidence/PHASE1_EVIDENCE.md:141`.

The builder asserts that all outstanding items are fixed and that every provenance-free sell “always gets a historical binding … whatever other bindings exist.” R6-1 directly contradicts both statements.

**Failure scenario:** Phase 1 could be accepted as having complete historical replay protection while an ordinary rejected attempt still permits a second sale.

**Fix:** Mark migration repair and its dependent items PARTIAL until conflicting-binding repair passes. Retain the distinction between builder test results and independent confirmation.

### LOW — R6-3. `connect_ex()` bypasses the replay network guard

**Locations:** `tests/test_replay_live_capture.py:79–106,117–129`; `docs/evidence/PHASE1_EVIDENCE.md:56,143`.

The guard patches `connect()` and `create_connection()`, but not `socket.socket.connect_ex()`.

**Reproduced under the installed guard against a local listener:**

```text
connect_ex: 0
server received: b'application request'
guard refused list: []
```

An application connection can therefore succeed and transmit data while `assert refused == []` passes. This weakens the test’s proof; it does not establish a reachable order route in the current runtime.

**Fix:** Guard `connect_ex()` with the same socketpair exception and add a successful-listener regression. Narrow the evidence wording until then.

### Validation and claim coverage

- **135 selected tests passed**, including all round-5 regressions, earlier migration/ownership/subscription cases, replay, paper accounting and licenses.
- Read-only adaptations used shared in-memory SQLite. Health-policy tests supplied the WAL prerequisite; they do **not** verify disk-backed WAL. Mako subscription tests used the exact pinned streaming class with unrelated trading imports excluded because local `base58` is missing.
- All four upstream verifications passed.
- Replay reproduced **356 events, 234 tokens, 78 wallets**, digest `389c978b95358f06…`, and **122 round trips reconciling to −26,125,445 lamports**. Duplicate, shuffled and restart replay tests passed.
- The documents appropriately qualify paper results, creation-only ingestion, processing latency, future phases and external-service blockers. Historical live timings, capacity measurements, host/authentication setup, skill invocations and the builder’s **185-test** run remain recorded evidence, not independently recertified here. Docker, rustc compilation and disk-backed crash checks were not rerun.
- **No reachable real-money order path or second execution controller found.** Runtime wires paper execution at `jinx/app.py:55–57`; unavailable LIVE execution raises at `jinx/execution.py:68,85`; GMGN enforces its read allowlist at `jinx/adapters/gmgn/bridge.mjs:70`; D3AD submission raises at `jinx/adapters/d3ad/adapter.py:21`.
- **No invented active strategy values found.** Settings retain source attribution at `jinx/config.py:18–28`; mechanical replay accounting is explicitly disclosed at `jinx/sim.py:9–11`.