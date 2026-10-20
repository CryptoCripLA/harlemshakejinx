# Phase 1 evidence (2026-10-20, jinx-nyc)

Host `jinx-nyc` (outbound IP 45.77.159.191), Python 3.12.3, Node 22.23.3, rustc 1.99.0. All runs are paper mode. No real-money order path exists in this build.

**How to read this file.**
- The paper round trips establish **accounting consistency**: wallet, ledger and fees agree exactly on real captured prices. Entries were mechanical, not CHAD signals, so they say nothing about profitable entry or exit behavior.
- Every millisecond figure is **local event processing inside JINX**, never order-execution latency (measured from Phase 3 on against GMGN's confirmed orders). Codex round 2 (B6) found that figures recorded before its fix started *after* the durable write. They are marked below as "post-write" and understate local processing. Section 7 has the corrected measurement, from feed receive to state applied with the write included.
- The live feed carried token creations only. It contains no external-wallet trades and **cannot** support CHAD's three-wallet momentum trigger (see `docs/INTEGRATION_MAP.md` §4).

## 1. Test suite

`.venv/bin/python -m pytest` → see section 6 for the final count at the Phase 1 head.

| File | Passed | What it proves |
|---|---|---|
| `tests/test_upstream_pins.py` | 7 | Four pins, full SHAs, license hashes; checkouts match; modified files and changed licenses are detected |
| `tests/test_events.py` | 10 | Typed events, integer base units, idempotency key independent of receive time, order key, immutability, bounded dedup |
| `tests/test_bus.py` | 9 | FIFO on both lanes, dedup, durable dedup across restart, failing handler isolation, fast-lane backpressure (no drops), background drop counting, latency sampling, resend accepted after a failed write |
| `tests/test_store_registry.py` | 8 | Dedup survives restart, WAL, reserve/count/trader updates, late events never overwrite newer reserves, trade-before-creation, shuffled order → identical state, reload after restart, wallet aggregates |
| `tests/test_ledger.py` | 15 | Owner slot rule (1 by default, 2 only by owner order, never 3, env cannot change it, corrupt state fails closed), idempotent intents, no duplicate positions, failed opens release the slot, unreconciled sells hold the slot, OFF blocks entries and never exits, partial sells, realized P&L counts every fee, fills apply once, no overselling, positions survive restart |
| `tests/test_paper.py` | 15 | **D3AD differential**: D3AD's own pinned Rust (`pumpfun.rs`, `position.rs`) compiled with rustc vs JINX on 2,002 cases, identical; flat round trip loses only fees; paper wallet idempotent, never negative, never reset; gateway buy/sell; replayed intent not re-executed; no-quote and low-balance failures release the slot; realized P&L equals wallet change; exits run in OFF; LIVE refused; no network during paper execution; no signing code in core |
| `tests/test_adapters.py` | 35 | No adapter imports another or reads another's upstream; core never imports adapters; per-adapter state dirs; Mako loader leaks no globals (sys.path, sys.modules, os.environ even with a .env present); Mako's trading client never instantiated; normalizer cases and malformed rejections; Mako's own `PumpPortalStream` end-to-end over a local websocket; GMGN's own compiled client against a fake GMGN server (path, query, X-APIKEY); trading methods refused before any request; unconfigured GMGN sends nothing; bridge crash contained; bridge gets only its own variables; D3AD refuses execution; DataNomad code cannot be loaded |
| `tests/test_replay_live_capture.py` | 9 | The directive's simulation checks on the genuine 15-minute capture (section 3) |
| `tests/test_recovery.py` | 9 | Stored-but-unapplied events applied exactly once on restart; applied events not reapplied; failed apply rolls back memory too; **hard kill** (`os._exit` mid-transaction at event 7 of 10) → nothing lost, nothing double counted; sequence resumes; unfilled paper reservation released; unfinished exit returns to OPEN; wallet mismatch reported; health states; double application guarded |

Mutation checks (each change was reverted afterwards):
- Removing `+ 1` from the curve formula made `test_curve_math_matches_d3ad_source_exactly` fail with `mismatch for (64454875638, 933905737103780, 3513435062, 541978541338974, 31, 83)`.
- Adding `from jinx.adapters.gmgn.adapter import GmgnAdapter` inside the Mako adapter made `test_no_adapter_imports_another_adapter[mako]` fail.

## 2. Live runs against PumpPortal (genuine market events)

| Run | Result |
|---|---|
| 45 s smoke (`python -m jinx run --seconds 45`) | status ok; 9 creations → 16 events; no malformed or duplicate events; post-write queue→applied p50 0.49 ms, max 0.68 ms (excludes the durable write) |
| Trade subscription probe | PumpPortal replied: "'subscribeTokenTrade' and 'subscribeAccountTrade' methods are only available when connecting with an API key funded with at least 0.02 SOL." → B-01 |
| `/health` on the running engine | HTTP 200, status ok, store ok, unapplied 0; unknown path → 404 |
| GMGN bridge killed (`kill -9`) during the live run | health then showed GMGN `down`; ingestion continued (84 → 92 events); unapplied 0; no handler errors; status stayed ok |

## 3. Live capture and replay

**Capture:** `python -m jinx run --seconds 900 --capture data/captures/live-20261020.jsonl.gz`, 03:01:14–03:16:14 UTC on 2026-10-20. Committed as `tests/fixtures/pumpportal-live-20261020.jsonl.gz`.
- 234 genuine pump.fun token creations; 122 of them carried a creator initial buy. That gives 356 events.
- 0 malformed, 0 duplicates; status ok every minute; 0 unapplied at shutdown.
- Post-write queue→applied latency over 330 events: p50 0.22 ms, p95 0.36 ms, p99 0.48 ms, max 2.36 ms. This excludes the durable write; see section 7 for the corrected measurement.
- Trades by other wallets are absent because PumpPortal withholds them without a funded key (B-01).

**Replays** (`python -m jinx replay <fixture> …`; `tests/test_replay_live_capture.py`, 9 passed):

| Directive check | Replay | Result |
|---|---|---|
| Events are ingested correctly | in order | 234 records → 356 events admitted and applied; 234 tokens, 78 wallets; 0 malformed; 0 unapplied; state digest `389c978b95358f06` |
| Duplicate transactions are not counted twice | `--duplicate-every 3` (78 records sent twice) | still 356 admitted; 113 duplicate events refused; same digest |
| Timestamp order / out-of-order events handled safely | `--shuffle-seed 1` (also seeds 2, 3 in tests) | same 356 events; same digest |
| Position state survives restart | `--restart-at 117` (stop, reopen, recover, continue) | same digest; 0 unapplied |
| Paper wallet balances reconcile | `--accounting-exercise` (mechanical, not a strategy: buy 0.01 SOL at each creator-buy observation and sell at the same observation) | 122 round trips closed; realized P&L = wallet change exactly (−26,125,445 lamports); flat round-trip cost 199–204 bps, inside D3AD's own 100–400 bps expectation; wallet reconciled |
| No accidental real-money order can escape | replay with every socket connection refused, except the one asyncio makes inside `socket.socketpair()` while creating its loop (round 5, R5-3) | completes; no connection attempted. A companion test proves the guard refuses loopback application connections |

Replay latency differs by mode. The batch modes push all 356 events at once, so they report queue drain (p50 ~32 ms). The accounting mode drains after every record, so it reports per-event cost: p50 0.145 ms, p99 0.584 ms.

**Live restart** on the capture database (`python -m jinx run --seconds 40 --data data/live1`): recovery replayed 0 events (all 356 were already applied), sequence resumed after 356 (the first new event was 357), and all 234 tokens reloaded. 9 new creations arrived, giving 372 events, all applied. Status ok; wallet reconciled.

## 4. Ingest capacity (synthetic load)

`scripts/bench_ingest.py --tokens 200 --trades-per-token 100` (SYNTHETIC messages, not market data): 20,200 messages, all admitted and applied, 0 malformed, 0 duplicates, 3.91 s → **5,166 events/s (~0.19 ms per event)** through normalize → bus → durable store → registries. When the whole batch is pushed at once, queueing delay reaches p50 953 ms and max 1,848 ms. That is the backlog draining at the service rate, not per-event cost.

## 5. Upstream integrity

`python3 scripts/upstream_sync.py --verify`: gmgn @ 402671e2ab8d, mako @ 45dadc03f84d, d3ad @ a91ca33695ff, datanomad @ b79c9bd147b4. All match their pins, with no modified files and unchanged licenses.

## 6. Final state

- Full suite at the round-5 fix head: **185 passed, 0 failed, 0 skipped** (`.venv/bin/python -m pytest`). This includes the round-1 to round-5 regression files and `tests/test_licenses.py`. The rustc differential ran; it was not skipped. The full suite was run by the builder on jinx-nyc; Codex's Windows runs covered selected tests only.
- 30-second live smoke run on the fixed code: status ok, 11 events stored and applied, 0 malformed, 0 write retries, ingestion not stalled, wallet reconciled. Local processing p50 0.29 ms.
- `scripts/upstream_sync.py --verify`: all four upstreams OK, now including clean Windows CRLF checkouts.
- Independent review:
  - Round 1 (Codex, `1ad0d65`): FAIL, 9 findings, all fixed (`docs/audits/phase1-codex-20261020-round1.md`).
  - Round 2 (Codex, `69111a2`): FAIL, 7 new findings (section 7).
  - Round 3 (Codex, `e1c5b06`): FAIL, 3 findings (section 8).
  - Round 4 (Codex, `631da32`): FAIL, 1 high, 2 medium, 2 low (section 9).
  - Round 5 (Codex, `1b6d797`): FAIL, 1 high, 1 medium, 1 low; fixed by the builder, awaiting round 6 (section 10).
- Not verified, with reasons:
  - Docker image build: no Docker permission on jinx-nyc.
  - Live GMGN API calls: no API key yet (B-08).
  - Other wallets' live trades: PumpPortal key not funded (B-01).
- Nothing was deployed. No real-money order was or could be sent.

## 7. Review round 2 fixes and corrected measurements

Codex round 2 (`docs/audits/phase1-codex-20261020-round2.md`, on `69111a2`) returned FAIL. Of the round-1 findings, 6 were confirmed fixed and 3 were partial; 7 findings were new. Each item below has a failing regression test first (`tests/test_review_round2.py`, `tests/test_adapters.py`, `tests/test_licenses.py`).

| Finding | Fix |
|---|---|
| B1 HIGH: v1 databases could not upgrade | Versioned schema (`PRAGMA user_version` = 2) with a one-time, transactional v1→v2 migration that keeps every row. Budgets are rebuilt from buy fills; with no buy fill the budget stays NULL (unknown), not invented. Tested on the exact `1ad0d65` schema with an open and a failed position, and on a real copy of the live-capture database (372 events, 243 tokens kept, quick_check ok) |
| B2 HIGH: a rejected oversell locked the position | `PositionLedger.claim_exit` checks the intent, validates the amount and takes exit ownership in one transaction (`EXCEEDS_POSITION` locks nothing). Any rejected paper sell returns the position to OPEN |
| B3 MED: a failed COMMIT wedged the connection | COMMIT failures roll back, and the next write succeeds |
| B4 MED: concurrent GMGN restarts | One lock serializes start, restart and stop. Each bridge process owns its pending requests and readers. Concurrent callers trigger exactly one restart, no request is lost, and stop leaves no child behind |
| B5 MED: OFF kept paid trade subscriptions | Paid PumpPortal trade subscriptions follow the mode: OFF subscribes no new mint and drops existing ones, except mints JINX holds (exit observation). Re-synced every status tick |
| B6 MED: latency excluded the durable write | The bus now times from admission (dedup, durable write, queue, handlers). Earlier figures are relabelled "post-write". Round 3 (R3-3) found failed applies were still sampled; see section 8 |
| B7 LOW: license tests failed on Windows | Canonical LF comparison, POSIX paths, and `.gitattributes` keeping vendored licenses LF |

Live measurement after the round-2 fixes (`python -m jinx run --seconds 120`, 2026-10-20, 26 events, local processing only, all applies succeeded):
- **Receive→state:** p50 0.59 ms, p95 0.90 ms, max 0.97 ms.
- **Admission→state:** p50 0.44 ms, p95 0.67 ms.
- Status ok; 0 malformed; 0 write retries; mode OFF with no paid subscriptions.

## 8. Review round 3 fixes

Codex round 3 (`docs/audits/phase1-codex-20261020-round3.md`, on `e1c5b06`) returned FAIL. It confirmed B2, B3, B4, B7 and round-1 #4 and #5 fixed. B1, B5, B6 and round-1 #3 were partial, which led to three findings. Each fix below had a failing regression test first (`tests/test_review_round3.py`). Round 4 then confirmed R3-3 and B6 fixed and found R3-1 and R3-2 still partial (section 9).

| Finding | Fix |
|---|---|
| R3-1 HIGH: a migrated pre-v2 partial exit could sell again | Schema v3 turns pre-v2 sell fills into historical exit bindings (round 4: its id-prefix test missed legacy ids beginning `p<position>:`, see R4-1) (`exit_intents.legacy_fill_id`). A historical intent is accepted only as an unambiguous replay: same position and the same amount as its recorded fill, or a whole-position request whose fill closed the position. Anything else, including another position, is `INTENT_CONFLICT`. Its original requested amount was never stored, so it is not guessed. v2 databases from `e1c5b06` upgrade in place |
| R3-2 MED: a failed OFF unsubscribe was forgotten | Unsubscribes wait in a pending set until sent, are retried on every sync, and show in health (`pending_unsubscribes`, status degraded). Mako's reconnect hook re-syncs subscriptions with the current mode and held positions |
| R3-3 MED: failed applies counted as state latency | The bus samples latency only when every handler succeeds. Receive→state is now measured by the core state at the moment an event's state commits, so an event applied later by `apply_pending` is counted once, with its full wait |

Metric definitions after round 3 (local processing inside JINX, never execution latency):
- **receive→state:** feed receipt → the event's state committed, for applied events only.
- **admission→state:** `publish()` called → every fast-lane handler succeeded.

Live run after the round-3 fixes (`python -m jinx run --seconds 90`, 13 events, mode OFF): status ok, 0 handler errors, 0 unsent unsubscribes.
- **Receive→state:** p50 0.60 ms, p95 0.92 ms.
- **Admission→state:** p50 0.45 ms, p95 0.62 ms.

## 9. Review round 4 fixes (round 5: R4-1 and R4-2 partial, see section 10)

Codex round 4 (`docs/audits/phase1-codex-20261020-round4.md`, on `631da32`) returned FAIL. It confirmed R3-3 and B6 fixed. R3-1, R3-2, B1, B5 and round-1 #3 were partial, plus the findings below. Each fix has a failing regression test first (`tests/test_review_round4.py`). **None of these is confirmed until the next independent review.**

| Finding | Fix (builder) |
|---|---|
| R4-1 HIGH: the v3 migration took legacy ids beginning `p<position>:` for modern ones | **Partial per round 5 (R5-1); superseded by the v5 provenance rule in section 10.** Schema v4. A sell fill counts as modern only if a non-legacy exit binding produced it (same position, fill id `p<position>:<intent>:sell`). Every other sell fill becomes a historical binding under its own old id, whatever the id looks like. The backfill re-runs, idempotently, on every open, which also repairs databases an earlier build marked v3. Tests: Codex's exact `p1:X` reproduction, a repaired v3 database, and a check that modern fills are never turned into legacy bindings |
| R4-2 MED: a retried unsubscribe could drop a mint that had become held | **Partial per round 5 (R5-2); completed in section 10.** Paid subscriptions are one locked reconcile of wanted (watched creations plus held mints) against confirmed (sent on the current connection). Unsubscribes that are no longer wanted are cancelled before anything is sent. On reconnect, Mako's re-subscribed set becomes the confirmed state. Health reports `pending_unsubscribes` and `unconfirmed_subscriptions`. Test: Codex's exact 5-step sequence ends with the held mint still subscribed on the server |
| R4-3 MED: completion claims ran ahead of the code | `BUILD_STATUS.md` and this file now separate what Codex confirmed from what awaits re-review |
| LOW: Windows test portability | The license CRLF test normalizes first. The replay network guard allows loopback, which Windows asyncio uses for its internal socketpair |

## 10. Review round 5 fixes (awaiting re-review)

Codex round 5 (`docs/audits/phase1-codex-20261020-round5.md`, on `1b6d797`) returned FAIL. It confirmed both Windows portability fixes. R4-1, R4-2, R4-3, R3-1, R3-2, B1, B5 and round-1 #3 were still partial, plus the findings below. Each fix has a failing regression test first (`tests/test_review_round5.py`). **None of these is confirmed until the next independent review.** The section-9 claims that v4 repairs every escaped database and that reconnects keep held mints subscribed did not hold; they are replaced by these fixes.

| Finding | Fix (builder) |
|---|---|
| R5-1 HIGH: a binding left by a rejected attempt hid a historical fill from v4 | Schema v5. Every sell fill now records the exit intent that produced it (`fills.exit_intent_id`), in the same transaction. A sell fill without that provenance (written before v5) always gets a historical binding under its own old id, whatever other bindings exist, so replaying that id can never sell again. `claim_exit` reports `ALREADY_APPLIED` only when the fill's provenance names this intent. A fill with other or no provenance is `AMBIGUOUS_HISTORICAL_FILL`. A new intent whose fill id is already taken is refused as `FILL_ID_COLLISION` before anything is bound or locked. Tests: Codex's exact sequence (escaped v3 binding gap, rejected `X`, upgrade, replay of `p1:X`), the collision refusal, and the provenance record |
| R5-2 MED: a cancelled unsubscribe plus a clean reconnect lost a held mint | A cancelled unsubscribe re-subscribes the mint, which restores Mako's own reconnect tracking. Every reconcile checks the connection object; on any new connection, clean closes included, the confirmed set is reset to what Mako re-subscribed. Test: Codex's five-step sequence followed by a code-1000 close ends with the held mint subscribed on the new connection and in Mako's set |
| R5-3 LOW: the replay network guard allowed any loopback connection | The guard allows only the connection asyncio makes inside `socket.socketpair()`. A second test proves loopback application connections are refused |

