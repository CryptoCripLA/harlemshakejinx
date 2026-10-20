# Integration map: GMGN is the trading platform, JINX is the intelligence

Owner directive, 2026-10-20: "GMGN IS OUR TRADING PLATFORM. JINX IS THE INTELLIGENCE." JINX hunts, qualifies, sizes and decides when to exit (HEAR CHATTER → SIZE → FIRE → EXIT). GMGN executes, holds the wallet and reports confirmed results. Sources: GMGN's pinned `gmgn-skills@402671e` (code and SKILL docs), docs.gmgn.ai/index/gmgn-agent-api (read 2026-10-20) and pumpportal.fun/fees (read 2026-10-20).

Updated 2026-10-20 for the owner's GMGN mandate (`docs/OWNER_GMGN_MANDATE_20261020.md`): GMGN is the **primary** source for token intelligence, wallet intelligence, migration monitoring and execution. The live key is verified, and the plan is measured as **Plus** (`docs/GMGN_CAPABILITY_INVENTORY.md`).

## 1. Who owns what

| Function | Owner | How | Phase |
|---|---|---|---|
| Hunting intelligence (CHAD's ten roles as background capabilities on one path) | **JINX** | `jinx/intel/*` reads GMGN data first, then the live Pump.fun feed where it is earlier, then Mako/D3AD-derived parsers | 2 |
| Token lifecycle and migration (launch → curve → graduation → migration → post-migration market) | **JINX state machine, GMGN primary evidence** | `jinx/core/lifecycle.py` (interface built in Phase 1). Fed by GMGN Trenches (`new_creation` / `near_completion` / `completed`) and token info (`migrated_pool`, `migrated_timestamp`, `launchpad_progress`), plus the Pump.fun stream's creations and reserves. Mapping: `docs/GMGN_ADAPTER_CONTRACT.md` §5 | 1 (interface) / 2 (feeds) |
| Request pacing to the GMGN plan | **JINX** | `jinx/adapters/gmgn/budget.py`, Plus 20 weight/s | 1 |
| Entry qualification, confluence, sizing (quarter-Kelly), exit decisions (Confluence Collapse, Negative Divergence partials) | **JINX** | one decision path into the single order gateway (`jinx/execution.py`) | 3 |
| Order construction, routing, signing of the on-chain transaction, submission, landing | **GMGN** | `swap` (`POST /v1/trade/swap`) from the GMGN hosted wallet; JINX signs only the API request (Ed25519 request key) | 3 |
| Order status and fill amounts | **GMGN is the source of truth** for GMGN-placed orders | `queryOrder`: `pending → processed → confirmed` or `failed` / `expired`; filled amounts in `report.input_amount` / `report.output_amount` only once confirmed | 3 |
| Percentage sells | **GMGN mechanism, JINX decision** | `swap --percent N` (JINX picks N, e.g. CHAD's 30/40/50/60 % Negative Divergence exits) | 3 |
| Fixed and trailing stop/take-profit orders | **GMGN mechanism**, used only for CHAD's configurable stop loss / trailing moonbag once the owner sets the values | GMGN condition orders (`loss_stop`, `loss_stop_trace`, `profit_stop`, `profit_stop_trace`) | 3 |
| Wallet custody and keys | **GMGN hosted wallet** ("private keys are not stored locally"); the API key binds to that wallet | trades require API key + the request-signing key on jinx-nyc + the trusted IP | 3 |
| JINX's own decision records and reconciliation | **JINX ledger** (`jinx/ledger.py`), reconciled *from* GMGN's confirmed order reports | one position per decision; slot limit; realized net SOL | 1 (built) / 3 (GMGN reconciliation) |
| Wallet-following copy trading | **GMGN native copy trading**, configured by the owner on GMGN | not rebuilt in JINX | — |
| D3AD-E | **Isolated pinned donor** for pieces GMGN does not provide | curve math (already used for paper fills); transaction/instruction parsing if a raw chain feed is ever adopted. Its sender, tip and confirmation stack are **not** wired while GMGN executes | 1 (curve math) |
| Mako | **Intelligence-side donor** | live PumpPortal stream (built), safety/rug/on-chain readers, DexScreener client, wallet discovery mechanics | 1 (stream) / 2 |
| DataNomad | **Reference only** (no license) | deployer-history and anti-lookahead methodology | 2 |

## 2. Rules that prevent two execution controllers

1. **One live executor.** When live trading is armed, the only live route is GMGN `swap` through JINX's single gateway. D3AD's sender is not wired. JINX never signs or sends a Solana transaction itself.
2. **One exit mechanism per trigger.** A position's exits come either from JINX's score-driven decisions or from GMGN condition orders that JINX itself placed for CHAD's configurable stop/trailing values, never both for the same trigger. Before JINX sends a score-driven sell, it cancels that position's open GMGN condition orders (`cancelStrategyOrder`) and confirms the cancel, so no sell is duplicated.
3. **GMGN confirmation is truth.** JINX records `order_id` with its intent. A position opens or closes in the ledger only from a `confirmed` GMGN report, with the actual input/output amounts. `failed` and `expired` are recorded with their cost. Nothing is assumed filled.
4. **Automation opt-in mirrors GMGN's own gate.** GMGN's CLI refuses swaps unless the operator sets `GMGN_ALLOW_AUTOMATED_TRADES=1` and passes `--yes` (`src/confirm.ts`). JINX calls the API client directly, bypassing that CLI gate. So JINX must enforce an equivalent owner opt-in: live swaps stay disabled until the owner's one-time live authorization is recorded. There is no per-trade prompt after that, per the owner contract.
5. **Separate wallets.** GMGN native copy trading should use a different GMGN wallet from JINX. Otherwise its positions would land in JINX's reconciliation and slot count. → owner decision B-14.
6. **Read paths stay read-only.** Phase 1's GMGN bridge refuses `swap`, `multiSwap`, `createStrategyOrder`, `cancelStrategyOrder` and `createToken` (tested).

## 3. Runtime

GMGN provides an API and a hosted wallet. Its documentation describes agents running on the customer's own machine and calling the API (`~/.config/gmgn/.env` on that machine). Nothing in GMGN's docs offers to host or run a customer's own code. So JINX needs its own runtime.
- **Minimum existing environment: jinx-nyc.** It already runs the engine, holds the GMGN request key, and its outbound IP 45.77.159.191 is the IP given to GMGN's trusted list.
- **Railway** is only needed if jinx-nyc proves unsuitable. Railway would also need its paid static outbound IPs (Pro plan) added to GMGN's trusted list. Not provisioned (B-10).

## 4. Can GMGN alone feed CHAD's triggers?

GMGN's API is request/response only (no websocket or stream in the client). Its rate limit is a leaky bucket per plan: Free 5/5, Plus 20/20, Pro 50/50 weight per second (rate/capacity). Each route has a weight, e.g. trenches 2, token security 1, token top traders 5, smart money 1, follow-wallet 10.

The owner's plan is **Plus** (measured 2026-10-20), so 1-second candles are not available. Freshness measured at 19:16 UTC (newest item's age when the response arrived; one sample):

| Data | Newest age |
|---|---|
| Trenches `new_creation` | 3.8 s |
| Market signals | 2.0 s |
| Smart Money | 4.2 s |
| KOL | 5.4 s |
| 1-minute candles | ~10 s |

Request latency through the persistent bridge was 95–451 ms per call.

| CHAD trigger / input | Best GMGN source | What GMGN gives | Enough? | Minimum extra |
|---|---|---|---|---|
| **Early Momentum**: "3 buys · 3 unique wallets · 30-second window", "3 distinct fee payers", live curve SOL, peak SOL gate | trenches (new_creation, w2), token info `launchpad_progress` (w1), top traders (w5), 1-second candles (Pro only) | polled aggregates (swaps, holders, progress); no per-buy stream with fee-payer wallet and time | **No** | A per-trade stream. The cheapest is PumpPortal's trade stream through the Mako code already built: every pump.fun buy/sell with trader wallet, SOL, tokens and curve reserves. Cost: API key funded with ≥ 0.02 SOL, then 0.01 SOL per 10,000 trades streamed (pumpportal.fun/fees). B-01 |
| **Sybil funder** ("3+ wallets share the same SOL funder") | holder analysis "linked funding" (funded within 60 s) via holders (w5) | holder-level funding links, polled | Partly (post-hoc, polled) | Per-buyer funder lookup on the 3 trigger wallets (RPC) in Phase 2 |
| **Whale Layer 1** (tracked wallet buys) | track smartmoney / kol (w1), follow-wallet (w10) | recent trades of GMGN-tagged and followed wallets, polled | Usable at polling cadence; latency set by GMGN indexing | Push alternative: PumpPortal `subscribeAccountTrade` (same key as above) |
| **Whale discovery** (4 h cycle, 40 % win rate, 50/25/25 confidence, 14-day half-life) | wallet stats / profits (w3), activity (w3) | win rate, P&L, activity history | **Yes** (slow path) | — |
| **Insider clusters** (3 tracked wallets, same token) | built from Layer 1 events | — | as good as Layer 1 | — |
| **Graduation** (curve progress → 0–20 pts) | token info `launchpad_progress` (0–1), trenches near_completion | progress, polled | Usable polled | CHAD names Helius gRPC. The PumpPortal trade stream above also carries reserves on every trade. Using it instead of Helius needs the owner's OK (B-07) |
| **Migration** (mandate §3) | trenches `completed` (`exchange: pump_amm`, `pool_address`, `complete_timestamp`, `complete_cost_time`); token info `migrated_pool`, `migrated_timestamp` | explicit migration fields, polled; no callback | **Yes**, at polling cadence plus GMGN indexing delay | — |
| **Momentum** (15 s velocity / volume acceleration, Negative Divergence) | 1-minute candles (w2); 1-second candles Pro only | candles | Partly; CHAD names DexScreener | Mako's DexScreener client (free) at CHAD's 15 s cycle |
| **Safety** (RugCheck, honeypot, mint/freeze) | token security (w1): honeypot, renounced, rug ratio, wash trading | security fields | Mostly; CHAD names RugCheck (not in Mako or GMGN) | RugCheck API if the owner wants it literally |
| **Dev reputation** | created tokens (w2), gmgn-dev-score method | launch history | **Yes** (slow path) | — |
| **X Scout** | none (GMGN exposes social links, not X analysis data) | — | **No** | X data access (B-05) |
| **TG Scout** | none | — | **No** | Telegram access + group list (B-06) |

Conclusion: GMGN covers the slow-path intelligence (safety, dev history, wallet scoring and discovery, smart-money tracking, graduation progress) and all execution. It does **not** deliver individual-wallet buy events fast enough for CHAD's Early Momentum trigger. The current free PumpPortal feed (token creations only) **cannot** detect three-wallet momentum: it contains no external-wallet trades. The minimum additional feed is PumpPortal's paid trade stream (B-01), which plugs into the Mako stream JINX already runs.

## 5. Latency labels

- Phase 1's sub-millisecond figures are **local event processing inside JINX** (feed message received → stored and applied). They are not order execution latency.
- GMGN states "end-to-end latency under 0.3 seconds" for orders. That is GMGN's claim, unmeasured by JINX. Phase 3 measures decision → GMGN submit → `confirmed` per order, separately from detection.

## 6. Dependency map

Each JINX capability with the source that feeds it. GMGN is listed first wherever it supplies the data.

```text
GMGN (pinned client via bridge, Plus budget)
  ├─ Trenches new_creation / near_completion / completed ──► token registry + lifecycle (CREATED … MIGRATION_CONFIRMED)
  ├─ token info / security / pool / holders / traders ──────► safety evidence, dev evidence, liquidity, lifecycle corroboration
  ├─ market signals, trending 1m, 1m candles ───────────────► momentum context (never treated as sub-second events)
  ├─ Smart Money / KOL / followed-wallet trades ────────────► whale Layer 1 (tagged wallets), polled
  ├─ wallet stats / profits / activity, created tokens ─────► whale discovery and confidence inputs, dev reputation
  ├─ quote, gas, SOL/USD ────────────────────────────────────► sizing inputs (Phase 3)
  └─ swap, condition orders, queryOrder, holdings ──────────► GMGN-first execution and reconciliation (Phase 3; refused in Phases 1–2)
Mako PumpPortalStream (built)
  ├─ creations (free) ───────────────────────────────────────► token registry + lifecycle CREATED, peak virtual SOL
  └─ every trade with wallet and reserves (needs B-01) ─────► Early Momentum (3 buys · 3 fee payers · 30 s), curve reserves
D3AD-E (pinned)  ── curve math ────────────────────────────► paper fills (built); sender unwired while GMGN executes
DataNomad (reference only) ── deployer-history method ───► dev reputation methodology (Phase 2), no code copied
JINX core ── bus, registries, lifecycle, ledger, store, modes, recovery, budget ──► one decision path → one gateway
```

## 7. Four-phase integration map (revised for GMGN, phase count unchanged)

| Phase | GMGN-first content (owner mandate §9) | State |
|---|---|---|
| 1. Foundation and GMGN integration readiness | Foundation as built. Additions: authenticated read-only checks, capability inventory, adapter contract, read allowlist derived from the pinned client, Plus budget, signing-key policy, secrets handling, migration lifecycle interface, this map | Built; independent review pending |
| 2. CHAD hunting intelligence and migration | GMGN new-token discovery; migration lifecycle tracking; smart-money and wallet tracking; token security; developer and insider evidence; curve observations and reserve peaks; Early Momentum (needs B-01); whale clustering; freshness; momentum and Negative Divergence evidence; X/TG where authorized (B-05, B-06); unified confluence state | Not started; waits for Phase 1 approval |
| 3. Size, GMGN execution, exits and P&L | quarter-Kelly (B-03 values); balance and exposure checks; GMGN quotes; GMGN order gateway behind the paper/live gate; order status; reconciliation; Confluence Collapse; Negative Divergence partials; owner-set protective orders; migration-aware exits; cost-adjusted P&L | Not started |
| 4. Hardening, real-time paper tests, audit | freshness, migration, latency, rate limits, outages, recovery, isolation, credentials, no unauthorized live execution; panel review | Not started |

