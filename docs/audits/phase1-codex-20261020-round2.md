# Phase 1 independent review: Codex (round 2)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `69111a2`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `14e0b2d2b9693ff2db3537fee2dfeb9284d6f4da13f372d882956de438c10c75`. The output follows unedited.

---

VERDICT: FAIL

Reviewed `69111a2`. Two HIGH findings remain. The original reproductions mostly pass, but the fixes introduce database compatibility and exit-lock failures. No files were modified; Git status remains clean.

## A. Round-1 findings

| # | Status | Code verification |
|---|---|---|
| 1. Negative amounts manufacture capital/inventory | **FIXED** | Positive integer checks in `jinx/execution.py:42–59`; fill validation in `jinx/ledger.py:57–63`; fresh-schema constraints in `jinx/store.py:89–112`. Negative-budget and negative-sell regressions pass. Existing-schema compatibility is separately broken: B1. |
| 2. Failed entry retry reports success | **FIXED** | `jinx/execution.py:73–78` returns the stored failure for `FAILED`; `jinx/ledger.py:122–126` rejects changed mint/budget. Reproduced `NO_QUOTE`, recovery-released reservations, and conflicting reuse: none falsely reports execution. |
| 3. Exit ownership and fill identity | **PARTIAL** | Durable exit binding at `jinx/ledger.py:165–174`, competing-owner rejection at `:185–188`, and fill-content comparison at `:250–258` fix the original reproductions. Reusing `X` across positions now returns `INTENT_CONFLICT`. However, a rejected sell can permanently lock exit ownership: B2. |
| 4. Transient persistence failure loses an event | **PARTIAL** | `jinx/adapters/mako/feed.py:71–87` retains and retries the current event. Using the exact pinned `PumpPortalStream` dispatcher, a one-time write failure preserved both `FIRST` and `NEXT`. But a COMMIT failure leaves the underlying store permanently unable to retry: B3. |
| 5. Large GMGN response kills reader | **PARTIAL** | `jinx/adapters/gmgn/adapter.py:64–66,133–144` increases the stream limit and fails pending requests on overflow. Reproduced: 70 KB succeeds at the default limit; a 4 KB limit produces `RESPONSE_TOO_LARGE` and retires the process. The new restart path is unsafe under concurrent calls: B4. |
| 6. Recovery retry loop dies while health stays OK | **FIXED** | Exceptions are contained per tick at `jinx/app.py:104–112`; supervision runs at `:125–134`; unapplied backlog and ingestion stalls degrade health at `jinx/health.py:36–45`. The failing-tick and crashed-feed reproductions pass. |
| 7. Missing D3AD license packaging | **FIXED** | Attribution/change notice at `jinx/paper.py:14`; inventory in `THIRD_PARTY_NOTICES.md:5`; Docker copies notices and licenses at `Dockerfile:20–21`. Both committed vendored license blobs match the pinned hashes. Docker packaging was inspected, not built. |
| 8. Eleven-skill claim lacks invocation evidence | **FIXED** | `docs/evidence/SKILLS_INVOCATION_20261020.md:3–19` now records eleven call IDs, results, and delivered-body timestamps/titles. `docs/PHASE1_PROOF_LEDGER.md:36` reconciles the prior claim. This supports the identified session; the underlying remote transcript was not independently accessible. |
| 9. Windows CRLF breaks pin verification | **FIXED** | `scripts/upstream_sync.py:70–78` hashes committed license blobs and separately checks tracked changes. Re-ran `--verify`: all four upstreams pass on this Windows checkout. New license-test portability failures are B7. |

## B. New findings

### HIGH — B1. Existing Phase 1 databases cannot upgrade

**Location:** `jinx/store.py:86–103,147`; `jinx/ledger.py:122,271–275`.

The fixes require `positions.entry_budget_lamports`, but startup only executes `CREATE TABLE IF NOT EXISTS`. Existing tables never receive the column or new constraints.

**Reproduced:** Created the `1ad0d65` schema in memory, then applied the current schema exactly as startup does:

- Entry reservation raises `OperationalError: no such column: entry_budget_lamports`.
- An existing active position makes ledger loading raise `TypeError: Position.__init__() missing ... entry_budget_lamports`, preventing startup recovery.

**Fix:** Add a versioned, transactional migration before loading ledger state. Preserve existing positions/fills and explicitly reconcile historical intent budgets that cannot be reconstructed faithfully. Test upgrading an actual round-1 schema with active positions.

### HIGH — B2. Rejected oversell leaves the position unable to exit

**Location:** `jinx/execution.py:87–97`; `jinx/paper.py:167–176`; `jinx/ledger.py:208–210`.

The gateway binds the intent and commits `PENDING_CLOSE` before validating the requested amount against inventory. The ledger then rejects an oversell, but nothing releases the exit lock.

**Reproduced:** Bought a position holding `30,939,636,459` units, then requested `30,939,636,460`:

- Sell raises `ValueError`.
- Position remains `PENDING_CLOSE`.
- Correcting the same intent returns `INTENT_CONFLICT`.
- A new full-exit intent returns `EXIT_IN_PROGRESS`.

Inventory remains exposed until recovery or explicit intervention.

**Fix:** Validate the amount before binding/taking ownership, atomically with the reservation. A definitively rejected paper sell must leave the position open and retryable. Add gateway-level oversized-sell and corrected-retry coverage.

### MEDIUM — B3. A transient COMMIT failure permanently wedges persistence

**Location:** `jinx/store.py:159–168`.

`COMMIT` runs outside the exception/rollback handler. When it fails while SQLite retains the transaction, `_depth` is already zero but the connection remains inside a transaction.

**Reproduced:** Injected one `sqlite3.OperationalError` at COMMIT. Every subsequent persistence attempt failed with:

`cannot start a transaction within a transaction`

The feed’s new retry loop therefore retries indefinitely after the original fault has cleared.

**Fix:** Include COMMIT in transaction failure handling. Roll back when appropriate, restore consistent transaction bookkeeping, and retire/reopen an unusable connection. Verify that a one-time COMMIT failure permits a later durable retry.

### MEDIUM — B4. Concurrent GMGN restarts spawn competing children and lose requests

**Location:** `jinx/adapters/gmgn/adapter.py:62–67,74–76,119–126`.

Restart has no synchronization. Two callers observing a dead bridge can both launch replacements. Each overwrites `_proc` and `_tasks`; reader tasks then obtain the shared process reference.

**Reproduced with controlled subprocess doubles:** Two simultaneous calls after bridge death spawned two live children. One request timed out, one succeeded, and `stop()` left one child alive.

Concurrent health requests can reach the same race when they encounter a failed reader/process transition.

**Fix:** Serialize start/restart/stop, recheck process health after acquiring the lock, and bind reader tasks to their specific process. Resolve old-generation pending requests before replacing it. Test simultaneous recovery and cleanup.

### MEDIUM — B5. OFF still enables paid background trade subscriptions

**Location:** `jinx/adapters/mako/feed.py:39,68–69,89–98`; `jinx/app.py:64–66`.

Supplying `PUMPPORTAL_API_KEY` automatically enables trade subscriptions regardless of operating mode. Neither startup nor subsequent subscription handling checks OFF.

**Reproduced:** With the store’s default mode `OFF`, a configured-key feed subscribed to a newly created mint. Changing mode also provides no unsubscribe mechanism. Under the documented paid-stream contract, background spending continues despite `CLAUDE.md:55`.

**Fix:** Connect paid subscription lifecycle to mode changes. OFF must stop background subscriptions/spending while preserving the observation and exit responsibilities explicitly required for existing positions.

### MEDIUM — B6. Reported receive-to-state latency excludes persistence

**Location:** `jinx/core/bus.py:79–89,118`; `docs/evidence/PHASE1_EVIDENCE.md:7`; `BUILD_STATUS.md:5`.

The timer starts **after** normalization, deduplication, and synchronous persistence. Nevertheless, the evidence describes these figures as “feed message received → stored and applied.”

**Reproduced:** Added a 100 ms persistence delay. Actual publish-to-state elapsed time was approximately **100.58 ms**; the reported latency was **0.21 ms**.

The figures are correctly distinguished from execution latency, but still understate their claimed local processing interval.

**Fix:** Either measure from receipt through successful state application, or rename existing measurements as post-persistence queue/handler latency and correct the historical evidence descriptions.

### LOW — B7. New license regressions fail on clean Windows checkouts

**Location:** `tests/test_licenses.py:15,24`.

Two tests repeat platform assumptions:

- Hashing working-tree license bytes rejects CRLF conversion.
- `str(path.relative_to(ROOT))` produces backslashes, while the notice uses forward slashes.

**Reproduced:** Both tests fail here despite matching committed license blobs and present attribution.

**Fix:** Compare canonical Git bytes or normalized license text, and use `.as_posix()` for document paths.

The wired Phase 1 runtime contains no reachable real-money order submission or second live execution controller. I found no invented active strategy thresholds. Paper accounting is appropriately labeled, and the creation-only feed is explicitly documented as insufficient for three-wallet momentum.

Verification included passing in-memory gateway/core regressions, status-loop/supervisor reproductions, upstream verification, and targeted adapter checks. The full disk-backed suite was not re-certified: file-writing tests were excluded, Mako’s full import lacks local `base58`, and the compiled GMGN bridge is unavailable.