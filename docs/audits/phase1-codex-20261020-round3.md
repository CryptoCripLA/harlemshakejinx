# Phase 1 independent review: Codex (round 3)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `e1c5b06`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- Copied from Windows by scp; sha256 of the reviewer's file: `20aead512f52b717778cccbebd63c916baa2fadcc688fbdb680d596fc5a80723`. The output follows unedited.

---

VERDICT: FAIL

Reviewed `e1c5b06`. One HIGH and two MEDIUM findings remain. No files modified; Git status remains clean.

## A. Verification of previous findings

| Finding | Status | Verification |
|---|---|---|
| **B1 — Existing database upgrades** | **PARTIAL** | `jinx/store.py:170,182,195` migrates the old schema, reconstructs entry budgets, preserves rows, and installs constraints. Reproduced successful in-memory migration of the actual v1 schema. Historical exit idempotency is not migrated: **R3-1**. |
| **B2 — Rejected oversell locks exit** | **FIXED** | `jinx/ledger.py:178,192,200` checks inventory before binding or claiming ownership. `jinx/paper.py:180` reopens definitively rejected sells. Oversell/corrected-retry and rejected-sell regressions passed. |
| **B3 — COMMIT failure wedges connection** | **FIXED** | `jinx/store.py:234` handles COMMIT failure and rolls back an outstanding transaction. Reproduced the original one-time COMMIT failure: transaction cleared; retry stored the event successfully. |
| **B4 — Concurrent GMGN restarts** | **FIXED** | `jinx/adapters/gmgn/adapter.py:37,89,113,155` binds requests/readers to generations and serializes lifecycle changes. Controlled subprocess reproduction: four concurrent calls succeeded, exactly one replacement spawned, all children retired by stop. |
| **B5 — OFF retains paid subscriptions** | **PARTIAL** | `jinx/adapters/mako/feed.py:93,100`; `jinx/app.py:68,120` gate new subscriptions and periodically remove non-held mints. Normal transition regression passed. Failed unsubscribe loses retry state: **R3-2**. |
| **B6 — Latency excludes persistence** | **PARTIAL** | `jinx/core/bus.py:75` starts timing before persistence; the slow-write regression passed. Historical figures are relabelled. Failed applies still produce misleading “state applied” samples: **R3-3**. |
| **B7 — Windows license regressions** | **FIXED** | `tests/test_licenses.py:16,28` normalizes line endings and uses POSIX paths. All five license tests passed here. |
| **Round-1 #3 — Exit ownership/fill identity** | **PARTIAL** | Fresh-database ownership, conflicting reuse, partial-exit replay, and fill-content regressions passed (`jinx/ledger.py:178,281`). Migrated historical exits can execute twice: **R3-1**. |
| **Round-1 #4 — Transient persistence loses event** | **FIXED** | `jinx/adapters/mako/feed.py:76` retains and retries admission; COMMIT recovery now works. Re-ran the dispatcher reproduction using the exact pinned `PumpPortalStream` class, loaded separately to bypass unavailable signing dependencies: passed. |
| **Round-1 #5 — Large GMGN response kills reader** | **FIXED** | `jinx/adapters/gmgn/adapter.py:95,168` configures the limit and fails the affected generation on overflow. Controlled-stream reproductions: 70 KB succeeded; 4 KB limit returned `RESPONSE_TOO_LARGE`; subsequent request recovered. |

## B. Fresh findings

### HIGH — R3-1. Migration permits historical partial exits to execute again

**Locations:** `jinx/store.py:195,205`; `jinx/ledger.py:185,190`; `jinx/paper.py:81`.

The migration copies v1 fills unchanged, including IDs such as `X:sell`. It creates no historical `exit_intents` bindings. The gateway recognizes replay only through a binding plus the new fill ID, such as `p1:X:sell`.

**Reproduced:** Migrated a v1 position with an existing 250,000,000-token partial sell, leaving 750,000,000 tokens. Retrying that same exit:

```text
submit_exit(ExitIntent("X", 1, 250000000))
→ ok=True
inventory: 750000000 → 500000000
fills: X:sell and p1:X:sell
```

The previously executed intent sells a second time. Historical intent reuse across positions also lacks the promised durable conflict protection.

**Fix:** Migrate historical exit identity and outcomes alongside fills and wallet entries. Preserve replay/conflict protection without guessing unrecoverable original parameters; explicitly reject ambiguous historical reuse. Add migration regressions with completed partial and full exits.

The blanket “all fixed” assertions in `BUILD_STATUS.md:32` and `docs/evidence/PHASE1_EVIDENCE.md:76` are therefore unsupported.

### MEDIUM — R3-2. Failed OFF unsubscribe is forgotten permanently

**Locations:** `jinx/adapters/mako/feed.py:110`; `upstream/mako/src/pumpportal.py:491`.

The feed deletes its tracked mint **before** awaiting unsubscribe. The upstream method likewise removes its tracked token before sending the message. If sending fails, neither set retains the subscription for retry.

**Reproduced:** With mint `A` subscribed and mode OFF, injected one transient unsubscribe send failure. A second `sync_subscriptions()` made no send attempt:

```text
send attempts: 1
server subscriptions: {"A"}
feed watched: []
upstream watched: set()
```

Paid streaming can continue while local health reports zero watched tokens. This contradicts `docs/evidence/PHASE1_EVIDENCE.md:95`.

**Fix:** Retain pending unsubscribe work until successful transmission, retry failures, and expose unresolved subscription state in health. Reconnect reconciliation must use the desired mode and held positions.

### MEDIUM — R3-3. Failed state applications count as successful latency samples

**Locations:** `jinx/core/bus.py:115,118,121`; `jinx/health.py:57`.

After catching a core-handler exception, the bus unconditionally records both admission-to-state and receive-to-state latency. State application has not succeeded. Later `core.apply_pending()` recovery does not correct that sample.

**Reproduced:** Persisted an event and made its fast-lane handler fail. The database contained **one unapplied event**, while receive-to-state metrics already contained **one sample**.

Persistence is now included, but recovery delays remain excluded from measurements labelled “state applied,” contradicting the metric definition in `docs/evidence/PHASE1_EVIDENCE.md:96`.

**Fix:** Record state latency only upon successful core application, including eventual recovery, or label these measurements as dispatch-attempt latency and report failures separately. Health correctly degrades for the unapplied backlog; that does not correct the sample.

### Verification and scope

- **72 selected checks passed** with storage fixtures redirected to SQLite memory and pytest caching disabled. Five feed tests could not complete normal imports because local `base58`/`solders` dependencies are missing; 24 checks were deselected.
- Targeted pinned-stream and controlled GMGN reproductions passed as described above. The compiled GMGN bridge and full disk-backed suite were not recertified.
- All four upstream pin verifications passed.
- Capture replay reproduced **356 events, 234 tokens, 78 wallets**, the documented digest, **113 duplicates refused**, and **122 round trips reconciling exactly to −26,125,445 lamports**.
- Historical live timings, host setup, skill invocations, and the recorded **165-test** run remain builder-recorded evidence, not independently rerun results here.
- No reachable real-money order submission or second execution controller found. The runtime wires only paper execution (`jinx/app.py:55`); the GMGN bridge enforces its read allowlist (`jinx/adapters/gmgn/bridge.mjs:70`); D3AD submission raises (`jinx/adapters/d3ad/adapter.py:20`).
- No invented active strategy values found. The mechanical replay budget is explicitly an accounting exercise, and the creation-only feed limitation remains accurately disclosed.