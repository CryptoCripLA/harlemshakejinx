# PumpAPI.io free Data Stream — evaluation for CHAD's Early Momentum (2026-10-20/21)

Owner directive: test the free PumpAPI.io stream before any paid feed and report PASS or FAIL with real evidence (`docs/OWNER_DIRECTIVES_20261021.md` §2). All runs were made from jinx-nyc.

**Verdict: PASS for Early Momentum's raw events, with two conditions.** The paid PumpPortal trade stream (B-01) and Helius (B-07) are not needed.
- **Condition 1 (stability):** PumpAPI connections sometimes drop or go silent. The adapter's reconnect and stall watchdog handle it, and PumpPortal's free creation and migration stream should run alongside, because the bus de-duplicates across the two.
- **Condition 2 (storage):** about 3.9 GB/day must fit on jinx-nyc before continuous running (B-18).

| # | Owner checklist item | Result | Evidence |
|---|---|---|---|
| 1 | Connect from jinx-nyc | **PASS** | `wss://stream.pumpapi.io/`, no key; one connection per IP, as documented |
| 2 | Signatures, timestamps, mints, wallet identities, completeness, real-time | **PASS** | 90,135 pump events in 5 minutes: 0 missing required fields, no malformed events. Signatures and slots verified on chain (7/7). Millisecond timestamps. Real time: see item 7 |
| 3 | Is the wallet the fee payer CHAD requires? | **PASS, with a finding** | `txSigner` is "the account that sent the transaction and paid the priorityFee" (PumpAPI docs). On chain, `txSigner` equalled the fee payer (`accountKeys[0]`) in 7/7 transactions. **Finding:** in 1,671 of ~7,300 curve buys (23 %), the fee payer is not among `tradersInvolved` (the accounts whose funds traded), e.g. relayed or bot-paid transactions. CHAD says "3 distinct fee payers", so JINX counts `txSigner`, and keeps `tradersInvolved` in every record |
| 4 | Three distinct buyers within 30 s, genuine events | **PASS** | In 5 minutes, 98 SOL-quoted curve mints reached 3 distinct fee payers within 30 s. In 30 of them the first three landed in one block or within 3 ms (bundle-like, per PumpAPI's own bundle heuristic); in 68 they were spread over time. The 1,200-record fixture contains such cases (`test_the_capture_contains_three_distinct_fee_payers_buying_one_curve_mint_within_30_seconds`) |
| 5 | Migration continuity, Pump.fun → PumpSwap | **PASS** | All 4 migrations in the 5-minute run were followed by `pump-amm` trades on the `poolId` announced in the migrate event, the first within 0–0.7 s, with curve trades before. In the 10-minute soak PumpAPI saw 13 migrations, PumpPortal 9 |
| 6 | Replay a bounded sample through the Phase 1 engine | **PASS** | `tests/test_replay_pumpapi_capture.py`: 1,200 live records (39 s), each admitted and applied exactly once. State is identical under duplicates, reordering and a mid-stream restart. Fee payers are counted as the token's traders |
| 7a | Latency | **PASS** | PumpAPI timestamp → JINX receipt: median 50–51 ms, p95 60–63 ms, p99 93 ms, max 546 ms, flat across thirty 10-s buckets (5-min run and 10-min soak). PumpAPI delivered creations before PumpPortal: median 98 ms earlier |
| 7b | CPU | **PASS** | 9.2–9.5 % of one core for the whole check (PumpAPI + PumpPortal + analysis) at ~930 messages/s. JSON parsing is limited to pump messages by a byte pre-filter |
| 7c | Trade loss | **PASS** | On 5 bonding curves, every successful transaction on chain within the captured slot range was delivered: **532 of 532** (`getSignaturesForAddress` against PumpAPI's signatures). The sixth curve in the first run was inconclusive because its listing was truncated; the method was then fixed |
| 7d | Creation loss | **PARTIAL** | Over 209 s connected: PumpAPI missed 7 of 101 creations PumpPortal saw (≈7 %); PumpPortal missed 71 of PumpAPI's 165 (13 mayhem-mode; 38 not SOL-quoted). PumpAPI covers ≈96 % of the union, PumpPortal ≈59 %. Running both free feeds covers the union; creation identities match across feeds (124/124) |
| 7e | Connection stability | **PARTIAL** | 8 sessions: 4 held for their whole window (longest 610 s), 4 were dropped by the server or went silent. The feed reconnects after a drop in ~1 s, and its stall watchdog replaces a silent connection after 5 s instead of the 40 s keepalive timeout (both tested). Each drop still loses the events sent while disconnected |
| 8 | Isolated JINX-owned adapter | **PASS** | `jinx/adapters/pumpapi/` imports no other adapter (isolation tests now discover every adapter). Compact records; follow policy; durable publish |
| 9 | GMGN not replaced | **PASS** | No GMGN capability was changed. PumpAPI hears the market; GMGN keeps intelligence, Smart Money, wallet scoring and execution |
| 10 | No competing order gateway | **PASS** | The adapter has no trading code. PumpAPI's trade API (0.25 %) is not used |

## Measurements in numbers

| Run (UTC) | Script / evidence | Window | Key results |
|---|---|---|---|
| 2026-10-20 23:57 | `scripts/pumpapi_stream_check.py` → `docs/evidence/pumpapi_stream_check_20261020.json` | 310 s | 924 msg/s, 1.41 MB/s, 0 disconnects; lag p50 51 ms / p95 63 ms / p99 93 ms; 90,135 pump events; fee payer 7/7 on chain |
| 2026-10-21 00:12 | `scripts/pumpapi_loss_check.py` → `docs/evidence/pumpapi_loss_check_20261021.json` | 150 s | 3 bonding curves 108/108, 102/102, 86/86 against the chain. The stream dropped mid-window (this script did not reconnect) |
| 2026-10-21 00:24 | `scripts/pumpapi_soak.py` → `docs/evidence/pumpapi_soak_20261021.json` | 610 s | Through JINX's PumpApiFeed: one connection held 610 s at 930 msg/s; published 42.9 events/s; 0 duplicates, 0 malformed; lag p50 50 ms / p95 60 ms; CPU 9.5 % of one core; one stall at the very end |
| 2026-10-21 00:40 | `scripts/pumpapi_creation_check.py` → `docs/evidence/pumpapi_creation_check_20261021.json` | 240 s | One drop after 50 s, reconnected; creation coverage as in row 7d |

Two more checks earlier the same session showed the same two trade curves exactly (148/148, 88/88); that output was printed, not saved.

## Load on JINX

- PumpAPI sends ~930 messages/s, of which ~62 % are not pump/pump-amm and are dropped before parsing.
- Of the pump messages, the follow policy keeps every bonding-curve event, every migration, and PumpSwap trades of mints first seen on the curve or held. That is about **43 events/s** into the bus.
- **Storage:** compact records come to ≈ 164 MB/hour, ≈ 3.9 GB/day. jinx-nyc had 3.8 GB free, so continuous running needs disk or a retention window (B-18).

## Data the free stream does not supply

- **Not SOL-quoted coins:** 91 of 255 creations in 5 minutes were paired with another pump coin, USDC, BONK and others. JINX amounts are lamports, so those trades are counted and skipped until a quote-aware design is approved.
- **Chain block time:** the timestamp is PumpAPI's observation time with millisecond precision. Ordering uses the slot.

## Execution options (owner question: "which is best for execution")

| Option | Fee (published) | Who signs | Notes |
|---|---|---|---|
| **GMGN Agent API** (integrated, the owner's execution platform) | not published in its docs; to be measured from the first confirmed order | GMGN-hosted wallet; JINX signs only the API request | Anti-MEV routing, condition orders (TP/SL/trailing), percentage sells, order status. One order path, already built and tested read-only |
| PumpAPI trade API | 0.25 % of volume | needs its own key setup (not evaluated) | Also publishes block-leader timing (`leaders`) for landing; not used |
| PumpPortal Local / Lightning | 0.5 % / 1 % | Local: you sign; Lightning: PumpPortal signs | Mako's code can call them; deliberately unwired |
| Own transactions with pump.fun's official SDKs (MIT) + own RPC / Jito | no service fee; network fees, priority fees, tips and RPC cost | a JINX-held wallet key | Most control over landing; a second order path and in-process signing, which needs owner approval |

GMGN stays the single executor as the owner directed. If Phase 3's measured GMGN cost or landing speed turns out worse, the cheapest documented alternative is PumpAPI's 0.25 %. Self-built execution has no service fee but changes order authority. Either switch is an owner decision.
