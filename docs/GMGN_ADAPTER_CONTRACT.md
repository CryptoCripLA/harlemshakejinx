# GMGN adapter contract

The GMGN adapter is the only JINX code that talks to GMGN. It runs GMGN's own client, unmodified, and exposes it to JINX through one narrow interface. Owner mandate 2026-10-20 §2: "Use the actual cloned and pinned GMGN client. Preserve its authentication, schemas, error handling, request signing and rate-limit behavior."

| Part | File | Tests |
|---|---|---|
| Upstream client (pinned) | `upstream/gmgn/src/client/OpenApiClient.ts` @ gmgn-skills `402671e`, compiled unmodified by `scripts/build_gmgn_bridge.sh` into `build/gmgn/dist` | `tests/test_upstream_pins.py`, `tests/test_licenses.py` |
| Bridge process | `jinx/adapters/gmgn/bridge.mjs` | `tests/test_adapters.py`, `tests/test_gmgn_plus.py` |
| Python adapter | `jinx/adapters/gmgn/adapter.py` | `tests/test_adapters.py`, `tests/test_review_round*.py`, `tests/test_gmgn_plus.py` |
| Plan budget | `jinx/adapters/gmgn/budget.py` | `tests/test_gmgn_plus.py` |
| Lifecycle interface fed by GMGN (Phase 2) | `jinx/core/lifecycle.py` | `tests/test_lifecycle.py` |

## 1. Process and credentials

- The adapter starts `node bridge.mjs` as a child process. The child receives only:
  - `PATH` and `HOME`;
  - `JINX_GMGN_BUILD`;
  - `GMGN_API_KEY` and `GMGN_PRIVATE_KEY` when set in JINX's environment;
  - `JINX_GMGN_HOST`;
  - `JINX_GMGN_NO_SIGNING_KEY=1` when the signing key is withheld.

  No other JINX environment variable reaches it.
- Without the variables, the bridge reads `~/.config/gmgn/.env`, the file `gmgn-cli config --apply` writes. Escaped newlines in the key are handled as GMGN's `src/config.ts` does.
- The bridge deletes `GMGN_DEBUG` before loading the client, because the upstream client prints full requests, API key included, when it is set.
- **Signing key.**
  - The Ed25519 request-signing key is excluded unless the adapter is built with `allow_signing_key=True`. Exclusion means the variable is removed and the bridge ignores the file's value.
  - JINX's runtime sets it from `jinx/config.py::GMGN_ALLOW_SIGNING_KEY = True`. Owner decision 2026-10-20: "its ok for paper we need real data". This lets paper runs read GMGN's signed routes: followed-wallet trades, own holdings, order status.
  - Trading stays refused by the allowlist (§3), whatever the key state.
- Secrets handling: `docs/SECRETS.md`.

## 2. Wire protocol (one JSON object per line)

```text
request : {"id": <int>, "method": "<client method>", "args": [<positional args>]}
success : {"id": <int>, "ok": true,  "result": <GMGN data, as returned by the client>}
failure : {"id": <int|null>, "ok": false, "error": {"code": "<code>", "message": "<text>"}}
```

| `error.code` | Meaning |
|---|---|
| `BAD_REQUEST` | the line was not JSON |
| `METHOD_NOT_ENABLED` | the method is not on the read allowlist; no request was made |
| `NOT_CONFIGURED` | no API key available |
| GMGN status or API code (e.g. `403` with `PRO_PLAN_REQUIRED`, `429`) | GMGN's error, passed through with its message |

`__health` returns `{"configured": bool, "signing": bool}` without calling GMGN.

## 3. Read allowlist and refused methods

Enabled (all read-only):
- `getTokenInfo`, `getTokenSecurity`, `getTokenPoolInfo`, `getTokenTopHolders`, `getTokenTopTraders`, `getTokenKline`;
- `getWalletHoldings`, `getWalletActivity`, `getWalletStats`, `getWalletProfits`, `getWalletTokenBalance`;
- `getTrenches`, `getTrendingSwaps`, `getTokenSignalV2`, `getHotSearches`, `searchMarket`, `getUserInfo`;
- `getFollowWallet`, `getFollowTokens`, `getFollowGroupNames`, `getKol`, `getSmartMoney`, `getCreatedTokens`;
- `getCalloutRecord`, `getCalloutGlobal`;
- `quoteOrder`, `queryOrder`, `getGasPrice`, `getStrategyOrders`, `getCookingStatistics`.

Refused: `swap`, `multiSwap`, `createStrategyOrder`, `cancelStrategyOrder`, `createToken`.

The enabled set must equal every method of the pinned client minus the refused set. `test_bridge_allows_exactly_every_non_trading_method_of_the_pinned_client` derives it from `OpenApiClient.ts`; that is how the missing `getTokenSignalV2` was found. Live trading in Phase 3 goes through JINX's single `ExecutionGateway` behind the owner's activation, never by widening this allowlist silently.

## 4. Python interface (`GmgnAdapter`)

```python
GmgnAdapter(build_dir, env=None, home=None, node="node", timeout_s=15.0,
            max_response_bytes=16 MiB, auto_restart=True,
            allow_signing_key=False, budget=None)

await adapter.start()
result = await adapter.call(method, *args)   # the client's positional arguments
await adapter.health()                       # dict, never raises
await adapter.stop()
```

**`call` outcomes:**

| Outcome | When |
|---|---|
| returns GMGN's `result` | success |
| `GmgnError("<code>: <message>")` | any `ok: false` reply, including `RESPONSE_TOO_LARGE` |
| `GmgnUnavailable` | bridge down and not restartable, or pipe closed |
| `asyncio.TimeoutError` | no reply within `timeout_s` |
| `PlanCapacityExceeded` | the method's weight exceeds the plan's whole bucket |

**Lifecycle:**
- Each bridge process is a generation that owns its pending requests. A dead or replaced generation fails only its own requests.
- Start, restart and stop are serialized. Concurrent callers that find the bridge dead trigger exactly one replacement.
- A crashed bridge never stops the rest of JINX: tested by killing the live bridge while ingestion ran.

**Size:** one reply line is at most 16 MiB. A larger reply fails that request with `RESPONSE_TOO_LARGE`, and the generation is replaced. The largest observed reply is Trenches at about 0.8 MB.

**Budget:**
- Before each call (not `__health`), the adapter takes the method's weight from the plan's token bucket (`budget.py`). It waits if the bucket is short, first come first served.
- Plan: Plus, 20 per second, capacity 20, measured (`docs/GMGN_CAPABILITY_INVENTORY.md`).
- Weights come from GMGN's skill rate-limit tables; undocumented routes are charged 10.

**Health:** `bridge` (`up`/`down`), `configured`, `signing`, call/error/restart/oversize counters, `plan`, and `budget` (`weight_spent`, `waited_s`, `calls`).

## 5. Lifecycle evidence mapping (interface in Phase 1, translation built in Phase 2)

`jinx/core/lifecycle.py` keeps one state machine per mint: `UNKNOWN → NEWLY_DETECTED → ON_BONDING_CURVE → APPROACHING_GRADUATION → MIGRATION_OBSERVED → MIGRATION_CONFIRMED → POST_MIGRATION_MARKET`, plus the exceptional states `STALE` and `CONFLICTING`. Failures are recorded without a state change.

The rules are tested in `tests/test_lifecycle.py`:
- every transition keeps its source, observation time and event time;
- a late report of an earlier fact never moves the state;
- a repeated report makes no second transition;
- newer evidence that contradicts a migration becomes `CONFLICTING`;
- `STALE` is declared by the caller's own age limit;
- the peak virtual SOL is kept across the lifecycle.

Phase 2 translates provider data into `Evidence` exactly as below. The categories and fields are GMGN's; JINX sets no threshold of its own.

| Provider observation | Evidence `Kind` | `event_ns` | Payload |
|---|---|---|---|
| Mako/PumpPortal `create` message | `CREATED` | receive time (the feed has no chain time) | creator, initial buy, `v_sol_lamports` |
| Trenches `new_creation` item | `CREATED` | `created_timestamp` | `pre_pool` = `pool_address`, `launchpad`, `exchange` |
| PumpPortal trade, or token info with `exchange: pump` and `launchpad_progress < 1` | `ON_CURVE` | trade receive time, or GMGN read time | `v_sol_lamports` (stream), `progress` |
| Trenches `near_completion` item | `NEAR_COMPLETION` | GMGN read time | `progress` |
| `complete_timestamp > 0`, but no post-migration pool yet | `MIGRATION_SIGNAL` | `complete_timestamp` | — |
| `migrated_pool` non-empty, `exchange: pump_amm`, `migrated_timestamp > 0` | `MIGRATION_CONFIRMED` | `migrated_timestamp` | `post_pool` = `migrated_pool` |
| price, liquidity or candles read from the post-migration pool | `POST_MIGRATION_MARKET` | provider time | price, liquidity, source route |
| GMGN error, timeout or 429 | `record_failure` | — | reason |
| token missing from a query result | nothing | — | there is deliberately no evidence kind for absence or for price |

`launchpad_status` alone never confirms a migration. GMGN's skill doc says `2 = migrated`, but on 2026-10-20 migrated tokens read `1` (inventory §3). It is recorded as payload only.

Migration handling around positions (mandate §3.4 and §3.5) is built in Phase 2 and Phase 3:
- one deduplicated migration event;
- refreshed quote and route;
- re-evaluation of open positions under their approved exit rules;
- reconciliation of pending orders;
- no automatic buy or sell on migration itself.

## 6. What the adapter never does

- It never submits, signs or cancels orders, and never creates tokens: those methods are refused.
- It never runs strategy logic. GMGN supplies data; JINX decides.
- It never writes JINX state directly. Phase 2 readers publish typed events into the bus like every other adapter.
- It never logs credentials, request headers or raw payloads containing them.
- It never makes hand-written HTTP calls in place of the pinned client.
