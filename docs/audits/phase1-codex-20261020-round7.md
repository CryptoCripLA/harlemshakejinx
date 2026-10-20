# Phase 1 independent review: Codex (round 7)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `a2068e3`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `3001fdee8cbf247382d50e99923b4f7080127815b086459f13b951ceb6e7115f`. The output follows unedited.

---

VERDICT: FAIL

## A. Previous findings

Reviewed `a2068e3` and the complete diff from `f270bf5`. No files modified; Git status remains clean.

| Item | Status | Evidence |
|---|---|---|
| R6-1 — Binding hides historical evidence | **FIXED** for the reported collision | Separate evidence at `jinx/store.py:165–169` is checked before bindings at `jinx/ledger.py:185–189`. Actual old `NO_QUOTE` → upgrade → replay returned `ALREADY_APPLIED`; inventory stayed 750,000,000; one sell remained. |
| R6-2 — Completion claims | **PARTIAL** | Prior claims are qualified, but the replacement universal claim at `docs/evidence/PHASE1_EVIDENCE.md:152` fails for R7-1 below. |
| R6-3 — `connect_ex` bypass | **FIXED** | `tests/test_replay_live_capture.py:101–119` guards `connect_ex` and `sendto`. The live-listener regression passed: both refused, listener received nothing. |
| R5-1 — Historical replay protection | **PARTIAL** | Binding collisions are protected; historical evidence can still be omitted. R7-1, `jinx/store.py:169`. |
| R5-3 — Replay network guard | **FIXED** | Original loopback checks and the new live-listener regression passed; `tests/test_replay_live_capture.py:95–120`. |
| R4-1 — Prefixed historical IDs | **PARTIAL** | Ordinary `p1:X` is protected. An accepted prefixed ID containing NUL bypasses backfill; R7-1. |
| R4-3 — Completion claims | **PARTIAL** | `BUILD_STATUS.md:32` distinguishes independent confirmation, but still asserts all fixes complete despite R7-1. |
| R3-1 — Migrated exits execute again | **PARTIAL** | Original regressions pass; R7-1 still executes a migrated exit twice. |
| B1 — Existing database upgrades | **PARTIAL** | Schema, preservation, budget and constraint tests pass; historical identity coverage remains incomplete at `jinx/store.py:167–169`. |
| Round-1 #3 — Exit ownership/fill identity | **PARTIAL** | Modern ownership/conflict tests pass; migrated replay protection still fails through R7-1. |

All **15 binding-state × amount combinations passed**. Additional v2/v3/v4/v5 states with conflicting legacy markers remained protected across repeated opens. Newline, Unicode and repeated `:sell` suffix IDs also remained protected.

## B. Fresh findings

### HIGH — R7-1. NUL-containing historical intent IDs escape evidence backfill and sell again

**Locations:** `jinx/store.py:167–169`, `jinx/ledger.py:185–189`, `jinx/execution.py:55–59`.

`ExitIntent` accepts an ID containing NUL. The original v1 API also accepted it and generated historical fills as `<intent>:sell`. SQLite’s text `LIKE` and `length()` operations stop at embedded NUL, so the backfill neither reliably recognizes nor extracts these historical identities.

**Reproduced:** Using the existing v1 fixture constructor with intent `"p1:X\u0000Y"`:

```text
Historical fill: 'p1:X\x00Y:sell'
Inventory before upgrade: 750000000
historical_exits after upgrade: []
Replay: ExecResult(ok=True, position_id=1, reason=None)
Inventory after replay: 500000000
Sell count: 2
```

This requires no forged provenance or conflicting binding. The replay passes current intent validation, finds no historical evidence and executes another sale.

**Fix:** Derive historical identities with NUL-safe operations, such as Python string processing, and preserve or quarantine unclassifiable provenance-free sells. Rejecting NUL in future IDs alone does not repair existing databases. Add this upgrade/replay regression.

### MEDIUM — R7-2. Replacement completion claims remain broader than the implementation

**Locations:** `docs/evidence/PHASE1_EVIDENCE.md:152`, `BUILD_STATUS.md:32`.

The evidence claims **every** provenance-free sell receives historical evidence; the build status asserts all outstanding fixes are complete. R7-1 contradicts those statements.

**Failure scenario:** Phase 1 is accepted as having complete historical replay protection while an API-accepted historical ID still permits another sale.

**Fix:** Mark migration replay protection and dependent items PARTIAL until R7-1 is repaired and verified.

### Verification and remaining claims

- **144 selected tests passed**, including all 18 round-6 cases, earlier migration/ownership regressions, paper accounting, replay and licenses.
- Replay reproduced **356 events, 234 tokens, 78 wallets**, digest `389c978b95358f06…`; **122 round trips**, wallet and realized P&L both **−26,125,445 lamports**. Duplicate, shuffled and restart checks passed.
- All four upstream integrity verifications passed.
- **No reachable real-money order route or second execution controller found.** Runtime wires paper execution at `jinx/app.py:55–57`; LIVE refuses at `jinx/execution.py:68–69,85–86`; GMGN enforces its read allowlist at `jinx/adapters/gmgn/bridge.mjs:70`; D3AD submission raises at `jinx/adapters/d3ad/adapter.py:20–21`.
- **No invented active strategy values found.** Constants retain attribution at `jinx/config.py:18–28`; mechanical replay behavior is explicitly disclosed at `jinx/sim.py:9–11`.
- Tests used shared in-memory SQLite to preserve read-only operation. Disk WAL/crash behavior, Docker, rustc compilation and remote host/authentication claims were not independently rerun. Historical live timings, capacity, skill invocations and the builder’s 203-test run remain recorded evidence, not newly certified results.