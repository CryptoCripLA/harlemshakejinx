# GMGN API capability inventory (Plus plan, measured 2026-10-20)

Owner mandate 2026-10-20 §1: verify authenticated read-only access and document every capability, successful or failed, with redacted evidence.

**How this was measured**
- `scripts/gmgn_capability_check.py` runs every call through JINX's own path: `GmgnAdapter` → `bridge.mjs` → GMGN's pinned, unmodified `OpenApiClient` (gmgn-skills@402671e), paced by the Plus budget.
- Run: 2026-10-20 19:16:06 UTC from jinx-nyc (45.77.159.191, the trusted IP). Raw output: `docs/evidence/gmgn_capability_check_20261020.json`.
- The output holds no credentials and no raw payloads. It records per call: success or GMGN's error, latency, item counts, provider field names, and the age of the newest provider timestamp. Wallet addresses are shortened to first4…last4.
- Nothing was submitted. The quote call only prices an order, and the bridge refused `swap` before any request was made (last row of the evidence).

Reproduce:

```bash
PYTHONPATH=. .venv/bin/python scripts/gmgn_capability_check.py docs/evidence/gmgn_capability_check_20261020.json
```

## 1. Access verification (mandate §1 checklist)

| Mandate item | Result | Evidence |
|---|---|---|
| Key authentication succeeds | **PASSED** | `getUserInfo` OK (450.8 ms). `gmgn-cli config --apply` reported "Configuration verified successfully". The key lives only in `~/.config/gmgn/.env` (mode 600); see `docs/SECRETS.md` |
| Solana access | **PASSED** | 9 chains bound to the key, `sol` among them; Solana wallet 3CtN…HZhg |
| Enabled plan and quotas | **Plus**: 20 weight/s refill, burst capacity 20 | Response header `x-ratelimit-limit: 20`, measured with gmgn-cli on 2026-10-20. 1-second candles refused: `403 PRO_PLAN_REQUIRED "1s kline is available to Pro users only"` (reproduced in this run). The owner's message said "Pro"; measurement shows Plus, and the owner confirmed: "its not pro its plus … use what we have available" |
| Supported token endpoints | **PASSED** | info, security, pool, top holders, top traders and 1-minute candles all OK, for a fresh token and for a migrated one |
| Trenches and migration data | **PASSED** | `getTrenches` returned 60 items in each of `new_creation`, `near_completion` and `completed`, with explicit migration fields (§3) |
| Wallet tracking | **PASSED** | `getFollowWallet` (signed route) OK, 10 trades. The newest was 48.8 min old, because the account follows few wallets |
| Smart-money data | **PASSED** | `getSmartMoney` and `getKol` OK, 20 trades each, with maker wallet, transaction hash and timestamp |
| Market signals | **PASSED** | `getTokenSignalV2` OK, 50 signals spanning 49 s; `getTrendingSwaps` 1m OK |
| Quote without submission | **PASSED** | `quoteOrder` 0.001 SOL → token. Fields: `input_amount`, `output_amount`, `min_output_amount`, `slippage`, `tx`. The unsigned transaction is returned; nothing is sent |
| Response timestamps and freshness | **MEASURED** | §4 |
| Wallet holdings / balances | **PASSED** | `getWalletHoldings` (signed) OK, 4 holdings. Balance of the bound Solana wallet at 19:04 UTC: 0.8186 SOL + 2.06 USDC ≈ **$88.70** (SOL at $105.84 from GMGN `getGasPrice.native_token_usd_price`, read 19:04 UTC) |
| Automated execution controls | **DOCUMENTED, NOT EXERCISED** | §6 |

## 2. Endpoint inventory (pinned client; weights from GMGN's skill rate-limit tables)

| Client method | Route | Auth | Weight | JINX bridge | Measured |
|---|---|---|---|---|---|
| `getTrenches` | `POST /v1/trenches` | API key | 2 | enabled | OK 346 ms |
| `getTokenSignalV2` | `POST /v1/market/token_signal` | API key | 1 | enabled (was missing before this change; allowlist now derived from the client and tested) | OK 138 ms |
| `getTrendingSwaps` | `GET /v1/market/rank` | API key | 3 | enabled | OK 134 ms |
| `getSmartMoney` / `getKol` | track routes | API key | 1 / 1 | enabled | OK 129 / 138 ms |
| `getFollowWallet` | `GET /v1/trade/follow_wallet` | **signed** | 10 | enabled | OK 299 ms |
| `getTokenInfo` / `getTokenSecurity` / `getTokenPoolInfo` | token routes | API key | 1 / 1 / 1 | enabled | OK 103–136 ms |
| `getTokenTopHolders` / `getTokenTopTraders` | token routes | API key | 5 / 5 | enabled | OK 151–205 ms |
| `getTokenKline` | `GET /v1/market/token_kline` | API key | 2 | enabled | 1m OK 114–126 ms; 1s **refused on Plus** |
| `getWalletStats` / `getWalletActivity` / `getWalletProfits` | user routes | API key | 3 each | enabled | stats 100 ms, activity 246 ms |
| `getCreatedTokens` | dev history | API key | 2 | enabled | OK 105 ms |
| `getWalletHoldings` | `GET /v1/user/wallet_holdings` | **signed** | 2 | enabled | OK 118 ms |
| `getUserInfo` | `GET /v1/user/info` | API key | 2 | enabled | OK 451 ms |
| `getGasPrice` | gas + SOL/USD | API key | 1 | enabled | OK 131 ms |
| `quoteOrder` | `GET /v1/trade/quote` | API key | 10 | enabled | OK 157 ms |
| `queryOrder`, `getStrategyOrders` | order status | **signed** | 5 / 1 | enabled (read) | not called (no orders exist) |
| `getHotSearches`, `searchMarket`, `getFollowTokens`, `getFollowGroupNames`, `getWalletTokenBalance`, `getCalloutRecord`, `getCalloutGlobal`, `getCookingStatistics` | various | API key / signed | 3,1,3,1,2,3,3, undocumented (budgeted at 10) | enabled | not exercised in this run |
| `swap`, `multiSwap`, `createStrategyOrder`, `cancelStrategyOrder`, `createToken` | trade routes | **signed** | 10,10,5,2,— | **refused** (`METHOD_NOT_ENABLED`) | refusal verified, no request made |

Latency above is through the persistent bridge (Node process already running), from jinx-nyc. The earlier one-shot `gmgn-cli` timings (339–668 ms, 1,414 ms for pool) include Node start-up on each call. Neither is order execution latency.

## 3. Migration evidence actually supplied

Trenches categories match GMGN's documented lifecycle `new_creation → near_completion → completed`. Observed fields:

| Field | Route | Observed on curve (`near_completion`) | Observed after migration (`completed`) |
|---|---|---|---|
| `exchange` | trenches | `pump` | `pump_amm` |
| `progress` / `launchpad_progress` | trenches / token info | 0.93–0.95 | 1 |
| `launchpad_status` | trenches and token info | 0 | 1 |
| `complete_timestamp` / `migrated_timestamp` | trenches / token info | 0 | equal, e.g. 1792523765 in both |
| `pool_address` | trenches | the bonding-curve pool (pre-migration identity) | the new AMM pool |
| `migrated_pool` | token info | empty | equals trenches `pool_address` |
| `complete_cost_time` | trenches (`completed` only) | — | seconds from creation to completion, e.g. 1094 |

**Documented vs observed discrepancy:** `skills/gmgn-token/SKILL.md:166` documents `launchpad_status` as `0` not opened, `1` live, `2` migrated. On 2026-10-20, curve tokens read `0` and migrated tokens read `1` in both routes (6 tokens checked). Phase 2 must therefore not confirm a migration from `launchpad_status` alone. JINX's lifecycle interface confirms a migration only with a post-migration pool identity (`migrated_pool` / `pool_address` with `exchange: pump_amm`) and a non-zero completion timestamp. See `docs/GMGN_ADAPTER_CONTRACT.md` §5.

GMGN provides migration state only through queries; there is no stream, webhook or callback in the client. Detection delay is therefore bounded by the polling interval plus GMGN's indexing delay. In this run, the newest `completed` item had been created 220.8 s earlier; its completion age is not separately reported by this check. Phase 2 measures the delay from `complete_timestamp` to JINX observation as a distribution.

## 4. Freshness (single run, 19:16:06 UTC)

"Age" is the local clock minus the newest provider timestamp at the moment the response arrived. It combines GMGN's indexing delay, the response path and any clock offset. It is a snapshot of how stale the newest item was, not JINX's detection latency for any one event. Phase 2 measures distributions.

| Data | Newest item age | Span of the returned items | Timestamp field |
|---|---|---|---|
| Trenches `new_creation` | 3.8 s | 60 items | `created_timestamp` |
| Trenches `near_completion` | 25.8 s (creation time) | 60 items | `created_timestamp` |
| Trenches `completed` | 220.8 s (creation time) | 60 items | `created_timestamp` |
| Market signals | 2.0 s | 2.0–49.0 s | `trigger_at` |
| Smart Money trades | 4.2 s | 4.2–15.2 s | `timestamp` |
| KOL trades | 5.4 s | 5.4–71.4 s | `timestamp` |
| Trending 1m ranking | 3.1 s (newest pool opening) | 3.1 s–47.9 min | `open_timestamp` |
| 1-minute candles | 9.6–10.5 s (last candle) | — | `time` |
| Followed wallets | 48.8 min | 48.8–74.5 min | `timestamp` |

An earlier gmgn-cli probe the same day measured similar values: `new_creation` 18.5 s, Smart Money 15.5 s, KOL 20.5 s.

## 5. What the Plus plan does not provide

- **1-second candles**: Pro only (`PRO_PLAN_REQUIRED`). 1-minute candles are available. A one-minute ranking or candle is not a sub-second event and is never used as one.
- **No push stream**: every route is request/response. Polling cadence is limited by the budget (§7).
- **No per-buy stream for arbitrary wallets.** Smart Money and KOL trades carry `maker`, `transaction_hash` and `timestamp`, but only for wallets GMGN has tagged. Top traders are per-wallet aggregates (`buy_tx_count_cur`, `created_at`, `last_active_timestamp`), not individual buys with times. CHAD's Early Momentum trigger ("3 buys · 3 unique wallets · 30-second window", "3 distinct fee payers") needs every buy with its fee payer and time. GMGN Plus does not supply that; the PumpPortal trade stream (B-01) still does.

## 6. Execution controls in GMGN (documented, not exercised in Phase 1)

- **Order routes** (all signed: API key + Ed25519 request key on jinx-nyc + trusted IP 45.77.159.191):
  - `swap` (`POST /v1/trade/swap`), including percentage sells;
  - `multiSwap`;
  - `queryOrder` (`pending → processed → confirmed | failed | expired`);
  - condition orders: `createStrategyOrder` / `getStrategyOrders` / `cancelStrategyOrder`, types `loss_stop`, `loss_stop_trace`, `profit_stop`, `profit_stop_trace`.
- **Wallet:** GMGN hosts the wallet bound to the key ("private keys are not stored locally"). The request-signing key is not a Solana wallet key, but together with the API key it can trade that wallet.
- **GMGN's own CLI gate:** `GMGN_ALLOW_AUTOMATED_TRADES=1` plus `--yes`. JINX calls the client directly and so bypasses that gate. JINX therefore keeps its own gate:
  - the bridge refuses every trading method (tested);
  - `ExecutionGateway` LIVE raises `LiveExecutionUnavailable`;
  - live trading needs the owner's explicit one-time activation (Phase 3).

## 7. Plus budget

GMGN's limit is a leaky bucket per API key: 20 weight refilled per second, at most 20 banked. Requests over the limit return 429. Repeated requests during a cooldown extend the ban by 5 s each, up to 5 minutes. JINX paces itself with `jinx/adapters/gmgn/budget.py`:
- per-route weights come from the skill docs;
- undocumented routes are charged 10;
- a call heavier than the whole bucket is refused locally;
- the adapter's health reports weight spent and time waited.

This run spent 84 weight over 28 calls with no waiting.

Budget arithmetic (cost per second of polling one route once per second): trenches 2, signals 1, Smart Money 1, KOL 1, trending 3, follow-wallet 10. For example, trenches + signals + Smart Money + KOL every second costs 5/s and leaves 15/s for per-token lookups (info/security/pool cost 1 each, holders/traders 5 each). The actual allocation is a Phase 2 plan item for owner approval, not a Phase 1 decision.
