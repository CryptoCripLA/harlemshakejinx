# Phase 1 independent review: Codex (round 1)

- Reviewer: OpenAI Codex CLI 0.160.0 (logged in with ChatGPT) on the owner's Windows PC, read-only sandbox.
- Target: harlemshakejinx `1ad0d65`, a clean GitHub clone plus the pinned upstreams.
- Date: 2026-10-20.
- The prompt is in `scripts/codex_phase_review.sh`. The reviewer's output follows unedited.

---

VERDICT: FAIL

1. **HIGH — Negative amounts manufacture paper capital and inventory.**  
   `jinx/paper.py:130–144`, `jinx/paper.py:153–160`, `jinx/ledger.py:164–180`; intents and fills lack amount validation. Reproduced: buying with a **−40 SOL budget** increases the 2 SOL wallet to **41.999995 SOL** and opens a position with negative cost. Selling **−1,000 tokens** increases inventory by 1,000.  
   **Fix:** Validate positive integer budgets and sell amounts before reserving or changing state; validate fills and fee ranges, and add database constraints and regression cases.

2. **HIGH — Retrying a failed entry falsely reports successful execution.**  
   `jinx/execution.py:55–56` treats every existing position except `PENDING_OPEN` as executed, including `FAILED`. Reproduced: an entry returns `NO_QUOTE`; retrying it returns `ok=True, ALREADY_EXECUTED`, although the position remains `FAILED` with **zero fills**. This also affects reservations failed by recovery at `jinx/recovery.py:39–40`.  
   **Fix:** Persist and return the original intent outcome; report execution success only when supported by fills. Reject reuse with changed intent parameters.

3. **HIGH — Exit ownership and fill identity can produce successful responses without selling.**  
   `jinx/ledger.py:142–143` accepts any competing exit while `PENDING_CLOSE`; `jinx/execution.py:65–68` then executes the caller’s intent. Meanwhile, fill IDs are globally unique (`jinx/store.py:104–105`), and duplicate IDs silently return from `jinx/ledger.py:162–163`. Reproduced: close position 1 using exit `X`, open position 2, then exit position 2 using `X`: the gateway reports success, but all inventory remains and position 2 becomes `PENDING_CLOSE`.  
   **Fix:** Bind each durable exit intent to its position and parameters, reject conflicting ownership, and distinguish a valid replay from an ID collision.

4. **HIGH — A transient persistence failure permanently loses a live event.**  
   `jinx/core/bus.py:77–83` raises when storage fails; `jinx/adapters/mako/feed.py:61–65` lets that exception escape. The pinned dispatcher catches it, logs it, and continues (`upstream/mako/src/pumpportal.py:459–475`). Nothing retries the lost message. Reproduced with the pinned dispatcher: the first event’s write fails once, the next event persists, and the first is absent from storage and recovery. `tests/test_bus.py:180` only tests an explicitly resent event.  
   **Fix:** Retain and retry the current message with backpressure until durable admission succeeds, or halt ingestion visibly pending recovery.

5. **MEDIUM — Large GMGN responses permanently kill the response reader.**  
   `jinx/adapters/gmgn/adapter.py:55–58` uses the default subprocess stream limit; `_read_stdout()` at lines 107–121 does not handle oversized lines. The bridge emits the entire result as one line (`jinx/adapters/gmgn/bridge.mjs:52`). A reproduced 70 KB result raises `ValueError`, terminates the reader, and leaves the request future unresolved. Subsequent calls time out despite Node still running.  
   **Fix:** Define a bounded response-size contract, configure appropriate framing/limits, and fail pending requests and restart the bridge when its reader fails.

6. **MEDIUM — Recovery retries can stop permanently while health remains “ok.”**  
   `jinx/app.py:102–111` has no exception handling around `core.apply_pending()`. One failed retry terminates the status task; no supervisor restarts it. Separately, `jinx/health.py:36–41` ignores unapplied events and handler/persistence failures when choosing status. A connected feed can therefore appear healthy while state remains incomplete.  
   **Fix:** Keep the retry loop alive across failures, supervise runtime tasks, and reflect unapplied-event backlog and failed core processing in health.

7. **MEDIUM — D3AD’s translated code ships without its documented license packaging.**  
   `jinx/paper.py:35–68` contains explicitly attributed D3AD translations. However, `Dockerfile:10` fetches only GMGN and Mako, then copies their trees and JINX into the image at lines 18–21. No D3AD license is included. This contradicts `docs/LICENSES.md:14`; its row at line 9 also incorrectly says Phase 1 “only pins and isolates” D3AD.  
   **Fix:** Include the Apache-2.0 license and translation notices in the repository and image; correct the reuse inventory.

8. **MEDIUM — Eleven-skill verification is marked PASSED without reviewable invocation evidence.**  
   `BUILD_STATUS.md:13` and `docs/SOURCE_FIDELITY_MATRIX.md:13` claim all eleven were loaded, but provide no session artifact showing the required invocation results. The checked-in proof ledger still says actual invocation is untested (`docs/PHASE1_PROOF_LEDGER.md:30–36`). File discovery cannot substantiate the acceptance requirement at `CLAUDE.md:452`.  
   **Fix:** Attach the invocation/results evidence and reconcile the proof ledger, or mark this requirement unverified.

9. **LOW — Pin verification falsely rejects clean Windows checkouts.**  
   `scripts/upstream_sync.py:70` hashes working-tree license bytes. Here, `--verify` rejects GMGN, Mako, and D3AD because checkout conversion produced CRLF. All three committed license blobs match their recorded hashes exactly.  
   **Fix:** Hash canonical Git blobs and check working-tree integrity separately, or enforce LF checkout for verified files.

Verification: **19 event/bus tests passed**; additional failures above were reproduced in memory. The full suite was not rerun because it writes files.

The cited D3AD math and GMGN `SRC` translations match their pinned expressions, subject to the explicitly documented reserve-cap omission. I found no additional active strategy thresholds, competing upstream controller, or real-money submission route in the wired Phase 1 runtime. No direct secret leak was confirmed. Paper results and processing measurements are appropriately labeled as simulation/local overhead; those claims do not establish trading performance or execution latency.