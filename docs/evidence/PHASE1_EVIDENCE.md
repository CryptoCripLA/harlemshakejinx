# Phase 1 evidence (2026-10-20, jinx-nyc)

Host `jinx-nyc` (outbound IP 45.77.159.191), Python 3.12.3, Node 22.23.3, rustc 1.99.0. All runs are paper mode. No real-money order path exists in this build.

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
| 45 s smoke (`python -m jinx run --seconds 45`) | status ok; 9 creations → 16 events; no malformed or duplicate events; ingest→state p50 0.49 ms, max 0.68 ms |
| Trade subscription probe | PumpPortal replied: "'subscribeTokenTrade' and 'subscribeAccountTrade' methods are only available when connecting with an API key funded with at least 0.02 SOL." → B-01 |
| `/health` on the running engine | HTTP 200, status ok, store ok, unapplied 0; unknown path → 404 |
| GMGN bridge killed (`kill -9`) during the live run | health then showed GMGN `down`; ingestion continued (84 → 92 events); unapplied 0; no handler errors; status stayed ok |

## 3. Live capture and replay

**Capture:** `python -m jinx run --seconds 900 --capture data/captures/live-20261020.jsonl.gz`, 03:01:14–03:16:14 UTC on 2026-10-20. Committed as `tests/fixtures/pumpportal-live-20261020.jsonl.gz`.
- 234 genuine pump.fun token creations; 122 of them carried a creator initial buy. That gives 356 events.
- 0 malformed, 0 duplicates; status ok every minute; 0 unapplied at shutdown.
- Live ingest→state latency over 330 events: p50 0.22 ms, p95 0.36 ms, p99 0.48 ms, max 2.36 ms.
- Trades by other wallets are absent because PumpPortal withholds them without a funded key (B-01).

**Replays** (`python -m jinx replay <fixture> …`; `tests/test_replay_live_capture.py`, 9 passed):

| Directive check | Replay | Result |
|---|---|---|
| Events are ingested correctly | in order | 234 records → 356 events admitted and applied; 234 tokens, 78 wallets; 0 malformed; 0 unapplied; state digest `389c978b95358f06` |
| Duplicate transactions are not counted twice | `--duplicate-every 3` (78 records sent twice) | still 356 admitted; 113 duplicate events refused; same digest |
| Timestamp order / out-of-order events handled safely | `--shuffle-seed 1` (also seeds 2, 3 in tests) | same 356 events; same digest |
| Position state survives restart | `--restart-at 117` (stop, reopen, recover, continue) | same digest; 0 unapplied |
| Paper wallet balances reconcile | `--accounting-exercise` (mechanical, not a strategy: buy 0.01 SOL at each creator-buy observation and sell at the same observation) | 122 round trips closed; realized P&L = wallet change exactly (−26,125,445 lamports); flat round-trip cost 199–204 bps, inside D3AD's own 100–400 bps expectation; wallet reconciled |
| No accidental real-money order can escape | replay with socket connections forbidden | completes; no connection attempted |

Replay latency differs by mode. The batch modes push all 356 events at once, so they report queue drain (p50 ~32 ms). The accounting mode drains after every record, so it reports per-event cost: p50 0.145 ms, p99 0.584 ms.

**Live restart** on the capture database (`python -m jinx run --seconds 40 --data data/live1`): recovery replayed 0 events (all 356 were already applied), sequence resumed after 356 (the first new event was 357), and all 234 tokens reloaded. 9 new creations arrived, giving 372 events, all applied. Status ok; wallet reconciled.

## 4. Ingest capacity (synthetic load)

`scripts/bench_ingest.py --tokens 200 --trades-per-token 100` (SYNTHETIC messages, not market data): 20,200 messages, all admitted and applied, 0 malformed, 0 duplicates, 3.91 s → **5,166 events/s (~0.19 ms per event)** through normalize → bus → durable store → registries. When the whole batch is pushed at once, queueing delay reaches p50 953 ms and max 1,848 ms. That is the backlog draining at the service rate, not per-event cost.

## 5. Upstream integrity

`python3 scripts/upstream_sync.py --verify`: gmgn @ 402671e2ab8d, mako @ 45dadc03f84d, d3ad @ a91ca33695ff, datanomad @ b79c9bd147b4. All match their pins, with no modified files and unchanged licenses.

## 6. Final state

- Full suite at the commit that adds this section: **117 passed, 0 failed, 0 skipped** (`.venv/bin/python -m pytest`, 5.25 s). The rustc differential ran; it was not skipped.
- `scripts/upstream_sync.py --verify`: all four upstreams OK.
- Not verified, with reasons:
  - Docker image build: atlasops has no Docker socket permission on jinx-nyc.
  - Railway deploy: not authorized (B-10).
  - Live GMGN API calls: no API key yet (B-08).
  - Codex review: not logged in (B-09).
  - Other wallets' live trades: PumpPortal key not funded (B-01).
- Nothing was deployed. No real-money order was or could be sent.
