# Phase 1 independent review: Codex (round 4)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `631da32`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `8b002e022dec6be14f59925d5322cd3b46cc3ff3f479c1f31647878f7253ae86`. The output follows unedited.

---

VERDICT: FAIL

Reviewed `631da32` and the complete diff from `e1c5b06`. No files modified; Git status remains clean. One HIGH finding remains.

## A. Verification of previous findings

| Item | Status | Verification |
|---|---|---|
| **R3-1 — Migrated exits execute again** | **PARTIAL** | Original `X` reproduction now returns `ALREADY_APPLIED`, preserving 750,000,000 tokens. However, valid legacy IDs beginning with the position prefix bypass migration and execute again: `jinx/store.py:151`. See R4-1. |
| **R3-2 — Failed OFF unsubscribe forgotten** | **PARTIAL** | Original failure now remains pending and succeeds on retry; pending work degrades health (`jinx/adapters/mako/feed.py:118,124`; `jinx/health.py:37`). Reconciliation can subsequently unsubscribe a held mint permanently: R4-2. |
| **R3-3 — Failed applies counted as latency** | **FIXED** | `jinx/core/bus.py:119` excludes failed dispatches; `jinx/core_state.py:42` samples after successful commit; `jinx/app.py:89` uses those samples. Reproduced: failed apply leaves one unapplied event and zero samples; recovery 100 ms later produces exactly one 100 ms receive-to-state sample. |
| **B1 — Existing database upgrades** | **PARTIAL** | v1/v2 schema upgrades, row preservation, budget reconstruction and constraints pass selected regressions (`jinx/store.py:184,191,200`). Historical exit identity remains incomplete: R4-1. |
| **B5 — OFF paid subscriptions** | **PARTIAL** | Mode gating and unsubscribe retry work (`jinx/adapters/mako/feed.py:97,103`). Held-position observation is not reliably preserved: R4-2. |
| **B6 — Latency excludes persistence** | **FIXED** | Admission timing includes persistence (`jinx/core/bus.py:74`); receive timing includes eventual successful recovery (`jinx/core_state.py:42`). The slow-persistence regression passes. Historical post-write figures are identified correctly. |
| **Round-1 #3 — Exit ownership/fill identity** | **PARTIAL** | Fresh-database ownership, conflicting reuse, partial replay and fill-content regressions pass (`jinx/ledger.py:178,301`). Migration still permits duplicate execution: R4-1. |

## B. Findings by severity

### HIGH — R4-1. Migration mistakes valid legacy IDs for modern fill IDs

**Locations:** `jinx/store.py:146–151`; `jinx/ledger.py:185,202`.

The backfill excludes every sell fill beginning with `p<position_id>:`. Legacy intent IDs have no restriction preventing that prefix.

**Reproduced:** Changed the v1 fixture’s historical intent from `X` to `p1:X`, with its existing fill `p1:X:sell`. After migration and recovery:

```text
submit_exit(ExitIntent("p1:X", 1, 250000000))
→ ok=True
inventory: 750000000 → 500000000
sell fills: p1:X:sell, p1:p1:X:sell
```

The migration creates no historical binding, so the gateway executes the same intent again.

**Fix:** Identify modern fills through durable intent bindings and migration provenance rather than a string prefix. Preserve or conservatively block ambiguous historical identities. Add this regression and repair databases already marked v3.

### MEDIUM — R4-2. Pending unsubscribe can remove a newly held mint permanently

**Locations:** `jinx/adapters/mako/feed.py:108–111,121–142`.

**Reproduced sequence:**

1. OFF unsubscribe of `A` fails; `A` remains pending.
2. `A` becomes held.
3. The next unsubscribe retry fails again; reconciliation successfully subscribes held `A`, but retains its pending unsubscribe.
4. A later retry succeeds and unsubscribes `A`.
5. Subsequent syncs see `A` in `_watched` and never restore it.

Final state:

```text
server subscriptions: {}
feed watched: ["A"]
upstream watched: {}
pending unsubscribes: {}
```

Exit observation is lost without unresolved subscription work appearing in health. Reconnect reconciliation also trusts the stale `_watched` entry.

**Fix:** Reconcile pending operations against current desired subscriptions before sending them. Cancel obsolete unsubscribes when watching a mint again, serialize reconciliation, and restore desired subscriptions from confirmed connection state.

### MEDIUM — R4-3. Fix-completion claims still exceed the implementation

**Locations:** `BUILD_STATUS.md:32`; `docs/evidence/PHASE1_EVIDENCE.md:76,78,111–112`.

The documents correctly distinguish builder runs from independent verification and say re-review is pending. However, “those 3 are fixed,” “all fixed,” and “every pre-v2 sell fill” remain unsupported by R4-1 and R4-2.

**Failure scenario:** These statements could close the Phase 1 gate despite reproducible duplicate exits and missing held-mint observation.

**Fix:** Mark R3-1/B1/round-1 #3 and subscription reconciliation as partial until the above regressions pass.

### LOW — Additional Windows test-portability failures

These files are unchanged by this diff, but failed during verification:

- **`tests/test_licenses.py:45–47`:** An already-CRLF license is converted to CRCRLF, producing a false hash failure. Normalize to LF before constructing the CRLF variant.
- **`tests/test_replay_live_capture.py:82–84`:** Blocking all socket connections also blocks Windows asyncio’s internal loopback socketpair before replay starts. Initialize the loop before installing the network guard.

Neither failure demonstrates a runtime license mismatch or an escaped order.

**Verification and remaining claims:** The broader selected run produced **110 passed, 2 portability failures, 8 deselected**, using SQLite memory and disabled pytest caching. WAL-specific checks require disk-backed verification. Normal Mako imports remain blocked by missing `base58`; subscription reproductions used the exact pinned stream class without unrelated signing imports.

All four upstream verifications passed. Capture replay reproduced **356 events, 234 tokens, 78 wallets**, digest `389c978b95358f06…`, **113 duplicates refused**, and **122 round trips reconciling to −26,125,445 lamports**.

No reachable real-money order path or second execution controller found: the app wires only paper execution (`jinx/app.py:55`), GMGN enforces its read allowlist (`jinx/adapters/gmgn/bridge.mjs:70`), and D3AD submission raises (`jinx/adapters/d3ad/adapter.py:20`). No invented active strategy values found (`jinx/config.py:1`; accounting exercise explicitly disclosed in `jinx/sim.py:9`).

Historical live timings, host/authentication setup, skill invocations and the builder’s 175-test run remain recorded evidence, not independently recertified here. Docker, authenticated GMGN access and external-wallet trade coverage remain explicitly pending.