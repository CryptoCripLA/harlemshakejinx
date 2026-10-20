# Phase 1 independent review: Codex (round 5)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `1b6d797`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `9761f23ec1a333d23fb892e63d776fc9c0c8f38ef1e0b53f313805e55ead0560`. The output follows unedited.

---

VERDICT: FAIL

Reviewed `1b6d797` and the complete diff from `631da32`. No files modified; Git status remains clean. One HIGH finding remains.

## A. Verification of requested fixes

| Item | Status | Verification |
|---|---|---|
| **R4-1 — Prefixed legacy exit IDs** | **PARTIAL** | Original `p1:X` reproduction passes: `ALREADY_APPLIED`, inventory remains 750,000,000. However, the modern-fill exclusion at `jinx/store.py:158` still misclassifies historical fills when a rejected v3 attempt left a matching binding. See R5-1. |
| **R4-2 — Pending unsubscribe drops held mint** | **PARTIAL** | The five-step sequence now preserves server subscription `A`. Cancellation at `jinx/adapters/mako/feed.py:130` leaves Mako’s tracking inconsistent, allowing a subsequent clean reconnect to lose `A` permanently. See R5-2. |
| **R4-3 — Completion claims exceed implementation** | **PARTIAL** | `BUILD_STATUS.md:32` now distinguishes independent confirmation from builder claims. Nevertheless, the repair and reconnect assertions at `docs/evidence/PHASE1_EVIDENCE.md:129` and `:130` remain unsupported by the cases below. |
| **Windows license portability** | **FIXED** | `tests/test_licenses.py:45` normalizes existing CRLF before constructing the CRLF variant. All five license tests pass here. |
| **Windows replay portability** | **FIXED** | `tests/test_replay_live_capture.py:84` permits asyncio’s loopback socketpair; the replay test passes on Windows. Its network-proof claim is now broader than the guard: R5-3. |
| **R3-1 — Migrated exits execute again** | **PARTIAL** | Original `X` regression passes. Historical replay protection at `jinx/ledger.py:188` still depends on the incomplete backfill: R5-1. |
| **R3-2 — Failed unsubscribe forgotten** | **PARTIAL** | Retry retention at `jinx/adapters/mako/feed.py:140` fixes the original reproduction. Reconnect recovery remains incomplete: R5-2. |
| **B1 — Existing database upgrades** | **PARTIAL** | Schema, row preservation, reconstructed budgets and constraints pass selected regressions (`jinx/store.py:214`). Historical exit identity is still incomplete: R5-1. |
| **B5 — OFF paid subscriptions** | **PARTIAL** | Mode gating and normal unsubscribe work (`jinx/adapters/mako/feed.py:100`, `:121`). Required held-position observation can disappear after reconnect: R5-2. |
| **Round-1 #3 — Exit ownership/fill identity** | **PARTIAL** | Fresh-database ownership, cross-position conflict, partial replay and fill-content regressions pass (`jinx/ledger.py:178`, `:301`). Migrated identity remains vulnerable: R5-1. |

## B. Findings by severity

### HIGH — R5-1. A rejected v3 exit binding can still hide a historical fill from v4

**Locations:** `jinx/store.py:158`; `jinx/ledger.py:202`, `:211`; `jinx/paper.py:182`.

A non-legacy binding does **not** prove that it produced the matching fill. `claim_exit` persists bindings before execution; rejected execution retains them. The backfill nevertheless treats binding plus matching fill name as proof of modern provenance.

**Reproduced using the escaped v3 state:**

1. Historical intent `p1:X` already sold 250,000,000 tokens, producing `p1:X:sell`; 750,000,000 remain. The v3 prefix rule omitted its binding.
2. Submit a new intent `X` for 100,000,000 tokens. It persists binding `X`, then returns `SELL_REJECTED` because its generated fill ID collides with historical `p1:X:sell`.
3. Open with v4. The backfill finds binding `X` and incorrectly excludes `p1:X:sell`. No historical `p1:X` binding is created.
4. Replay historical `p1:X` for 250,000,000.

Observed:

```text
bindings after v4: [('X', None)]
historical replay: ok=True
inventory: 750000000 → 500000000
sell fills: p1:X:sell, p1:p1:X:sell
```

The duplicate-exit failure remains in databases v4 explicitly promises to repair. The assertion at `docs/evidence/PHASE1_EVIDENCE.md:129` is therefore incomplete.

**Fix:** Preserve durable fill provenance/outcomes, or conservatively quarantine ambiguous historical-versus-modern identities during repair. A binding alone cannot establish successful execution. Add this rejected-attempt-before-upgrade regression.

### MEDIUM — R5-2. Cancelling an unsubscribe leaves reconnect tracking inconsistent

**Locations:** `jinx/adapters/mako/feed.py:130`, `:151`, `:171`; `upstream/mako/src/pumpportal.py:411`, `:435`, `:492`.

Mako removes a mint from `_watched_tokens` **before** sending its unsubscribe. When that send fails and the mint becomes held, JINX cancels the pending unsubscribe but does not restore Mako’s tracking.

The five-step sequence now ends with:

```text
server subscriptions: {'A'}
JINX confirmed: {'A'}
Mako watched: set()
pending unsubscribes: set()
```

**Reproduced continuation:** Close the WebSocket normally with code `1000`. Mako reconnects, but its reconnect callback runs only when `attempt > 0`; a normal close does not increment it. Mako therefore restores no token subscription, while JINX keeps its old `_confirmed`.

After reconnect and three further syncs:

```text
server subscriptions: set()
JINX confirmed: {'A'}
Mako watched: set()
pending_unsubscribes: 0
unconfirmed_subscriptions: 0
```

Held-mint observation is permanently absent. Subsequent creation messages can keep feed freshness healthy, so the added health counters do not expose the loss. The lock serializes JINX operations, but does not establish connection identity or repair upstream tracking.

**Fix:** Restore upstream tracking when cancelling a failed unsubscribe, and invalidate/reconcile confirmations on **every** connection replacement, including clean closes. Add the five-step sequence followed by a normal reconnect. Correct the completion claim at `docs/evidence/PHASE1_EVIDENCE.md:130` until that passes.

### LOW — R5-3. Replay evidence overstates the network guard

**Locations:** `tests/test_replay_live_capture.py:84`; `docs/evidence/PHASE1_EVIDENCE.md:56`.

The updated test permits arbitrary loopback connections, while the evidence still says socket connections are forbidden and no connection was attempted. A replay regression contacting a local HTTP proxy or order service would pass this guard.

**Fix:** Narrow the exception to asyncio initialization and prohibit application connections during replay, or describe the weaker guarantee accurately.

### Verification and remaining claims

- **126 selected tests passed**, including the original migration, subscription, replay and portability regressions. Storage used shared in-memory SQLite to preserve reopen behavior without writing files.
- Health-policy checks separately produced `degraded` for pending unsubscribes and unconfirmed subscriptions, and `ok` otherwise, with the WAL prerequisite supplied for isolation.
- All four upstream verifications passed.
- Capture replay reproduced **356 events, 234 tokens, 78 wallets**, digest `389c978b95358f06…`, **113 duplicates refused**, and **122 round trips reconciling to −26,125,445 lamports**.
- The documented **180-test builder run**, historical live timings, capacity measurement, host/authentication setup and skill invocations remain recorded evidence, not independently recertified results. Disk-backed WAL/crash checks, rustc compilation, Docker and authenticated external services were not rerun. Normal Mako import lacks local `base58`; feed checks used the exact pinned stream class with unrelated trading imports excluded.
- **No reachable real-money order path or second execution controller found.** Runtime wires paper execution (`jinx/app.py:57`); LIVE without an executor raises (`jinx/execution.py:68`); GMGN enforces its read allowlist (`jinx/adapters/gmgn/bridge.mjs:70`); D3AD submission raises (`jinx/adapters/d3ad/adapter.py:21`).
- **No invented active strategy values found.** Source-attributed settings remain in `jinx/config.py:18`; mechanical replay accounting is explicitly disclosed in `jinx/sim.py:9`. The creation-only feed, pending phases and external-service blockers remain accurately qualified.