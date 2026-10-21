# Blockers needing an owner decision or access (as of Phase 1, 2026-10-20)

Each entry: what is blocked, the evidence, what is needed, and one reversible option. Work that does not depend on a blocker continues.

## B-01 PumpPortal trade stream needs a funded API key — RESOLVED 2026-10-21 by the free PumpAPI stream (no purchase)
- Resolution: PumpAPI.io's free stream delivers every bonding-curve and PumpSwap trade with the fee payer, amounts, reserves, slot and a millisecond timestamp. PASS for Early Momentum's raw events (`docs/PUMPAPI_EVALUATION.md`). The paid PumpPortal trade stream is not needed. The history below is kept.
- Evidence: PumpPortal replied on 2026-10-20: "'subscribeTokenTrade' and 'subscribeAccountTrade' methods are only available when connecting with an API key funded with at least 0.02 SOL." Mako's `pumpportal.py` predates this and assumes free trade streams.
- Effect now: the live feed carries token creations only (each including the creator's initial buy). Health reports `trade_stream: BLOCKED`.
- GMGN checked first (owner directive 2026-10-20): GMGN's API is polled request/response with plan rate limits. It gives aggregates (swaps, holders, curve progress) and smart-money/KOL trade lists, but no per-buy stream with the fee-payer wallet and time. It cannot drive CHAD's "3 buys · 3 unique wallets · 30-second window" trigger. See `docs/INTEGRATION_MAP.md` §4.
- Re-checked with the live Plus key, 2026-10-20 19:16 UTC (`docs/GMGN_CAPABILITY_INVENTORY.md` §5):
  - Smart Money and KOL trades carry maker, transaction hash and timestamp, but only for GMGN-tagged wallets.
  - Top traders are per-wallet aggregates.
  - No route streams every buy.

  B-01 still stands.
- Needed: an owner decision to create a PumpPortal API key and fund it with at least 0.02 SOL. Usage then costs 0.01 SOL per 10,000 trades streamed (pumpportal.fun/fees, read 2026-10-20). This is spending, so it waits for the owner. It is the minimum additional feed and plugs into the Mako stream JINX already runs.
- Reversible option: set `PUMPPORTAL_API_KEY`; the feed then subscribes every new mint's trades automatically. Unset it to return to creations only.

## B-02 CHAD contradicts itself on the Early Momentum curve gate
- Evidence: CHAD Bot 10 says "all below the 35 SOL bonding curve threshold", and the same section says "0.5–10 SOL bonding curve gate … (0.5 SOL floor) … (10 SOL ceiling)".
- Needed: the owner picks which gate applies, or how the two combine.
- It is also unstated which reserve each gate reads. PumpAPI reports both on every curve trade: the virtual SOL reserve (`vQuoteInBondingCurve`, which starts at 30 SOL, so "0.5–10" can never hold for it) and the real SOL in the curve (`quoteInPool`). CHAD's peak example ("ran to 60 SOL and dumped to 25 SOL") cannot be virtual SOL either, since that never falls below 30.
- Live finding 2026-10-21 (`tests/test_intel_graduation.py`): 26 of the 118 curve mints in the live capture are not standard 30-SOL curves. Their virtual SOL is far below 30 (as low as 0.014) and their virtual-minus-real SOL gap changes trade by trade; 5 of them hold more than the standard 793.1M sellable tokens. For such a curve neither SOL reading means what it means on a standard curve. JINX flags every reading (`standard_curve`, also shown in both gate predicates) and never reinterprets it.
- Built 2026-10-21 (`jinx/intel/early_momentum.py`): both gates are separate predicates, `curve_gate_0_5_to_10_sol` and `curve_gate_below_35_sol`, status UNRESOLVED. Each records the virtual and the real reading at detection (per buy for the 35 SOL gate) and what each reading would give, without choosing. While either is UNRESOLVED the evidence's `trade_permitted` is False.

## B-03 CHAD values that are not specified (Phases 2-3)
These are left unset, and entries that depend on them stay disabled:
safety 0–100 formula and pass threshold; momentum "stall" and "sustained volume" quantities; X Scout verdict cut-offs and the @SolBotChad bonus; graduation progress → 0–20 points mapping; cluster "short window"; freshness decay function; dev-reputation formula and cut-off; confluence weights and the global score threshold; Kelly cap, payout-ratio prior and bucket edges; stop-loss and trailing values and the "high-conviction" cut-off; priority-fee escalation amounts; the shared-funder look-back; the whale volume-points mapping (CHAD: "More confirmed observed buys = more reliable signal"); the second-degree co-buy window; the win-rate look-back used for whale promotion (GMGN `period`, 7d used for now).

Added 2026-10-21 with the Early Momentum build (`jinx/intel/early_momentum.py`); each is recorded as evidence and blocks entry until decided:
- **Peak SOL gate rule.** CHAD says the gate "tracks all-time high virtualSolReserves" and "catches" a pump-and-dump retrace, but not what peak or retrace rejects a token. JINX records the peak, the current reading and the retrace from the peak (`peak_sol_gate`, UNRESOLVED). The peak is all-time only when JINX saw the creation; otherwise the predicate is UNAVAILABLE.
- **The creator's own buy.** CHAD counts "3 unique wallets buying"; it does not say whether the creator's initial buy is one of them. JINX counts it, as it is a buy by a distinct fee payer, and flags `creator_in_window`.
- **Graduation "near the threshold" and its "elevated signal weight"** (CHAD Bot 5). Neither is quantified. `jinx/intel/graduation.py` records progress and blocks on `graduation_score`; it never marks a token near graduation and never moves the lifecycle to APPROACHING GRADUATION from stream data.
- **"Large sells around graduation"** (owner directive 2026-10-21 §3, M2). No size and no time window are given. `jinx/intel/migration.py` lists the 10 largest sells among the last 256 curve trades before the migration and the first 256 pool trades after it (RAM bounds, not a rule), each with the reserve after it, and classifies none as large.
- **Holder concentration from the stream.** The trade stream shows trades, not balances: transfers are invisible. Migration evidence therefore reports concentration as observed net buys by fee payer since the creation, labelled as such, with `complete` False when the creation was not seen or the per-mint wallet cap was hit. Balance-based concentration needs GMGN holder data (`top_10_holder_rate`); no threshold is set on either.
- **Sybil funder check before entry.** CHAD names its effect only after entry ("emergency exit fires instantly"). JINX fails closed: unknown funders (`sybil_funder` UNAVAILABLE) block entry, and three or more buyers with one funder are a FAIL with `emergency_exit` set. PumpAPI carries no SOL transfers; funders arrive through `EarlyMomentum.record_funding`. Since 59b81eb `jinx/app.py` answers `request_funders` with GMGN wallet stats (`common.fund_from_address`); a wallet GMGN gives no funder for stays unknown.

## B-04 Position count: CHAD tiers vs the owner's standing slot rule
- CHAD product tiers allow 3, 10 or unlimited positions. The owner's permanent directive (2026-10-17) is one global slot, two only by owner order, never three.
- The GMGN mandate of 2026-10-20 says: "Do not hardcode a one-position limit unless that is explicitly present in the owner's approved configuration." The 2026-10-17 directive is that explicit configuration (the owner's global CLAUDE.md: "NO EXECUTION PATH MAY BYPASS THIS LIMIT"), so the limit stays. The slot count is a stored setting changed only by owner command (`jinx/ops/entry_slots.py`), not a literal in the decision code.
- Current build: the owner rule is enforced (`jinx/ledger.py`, `jinx/ops/entry_slots.py`). Changing it needs an explicit owner order.

## B-05 X Scout has no data source among the four approved repos
- Needed: X API access (or an approved X data provider), plus the Claude Haiku narrative call the owner specified.

## B-06 TG Scout has no data source among the four approved repos
- Needed: a Telegram account/API credentials for JINX and the owner's list of alpha groups.

## B-07 Helius gRPC (CHAD Bot 5 and Bot 10 "on-chain gRPC stream") — data need covered free
- 2026-10-21: the free PumpAPI stream carries bonding-curve reserves (`vQuoteInBondingCurve`, `vTokensInBondingCurve`) on every curve trade, delivered with a median 50 ms lag. Helius is not needed for the data. Replacing CHAD's named source is the owner's choice; history below.
- CHAD names Helius gRPC. GMGN gives curve progress only by polling (`launchpad_progress`). The PumpPortal trade stream (B-01) carries curve reserves on every trade, which covers the same data without Helius.
- Needed: the owner's choice between Helius (paid) as CHAD names it, or B-01's stream as the source of curve data.

## B-08 GMGN API key — RESOLVED 2026-10-20
- The owner created the key with jinx-nyc's public key and trusted IP 45.77.159.191. `gmgn-cli config --apply` reported "Configuration verified successfully".
- Authenticated read-only checks pass through JINX's own bridge (`docs/GMGN_CAPABILITY_INVENTORY.md`).

## B-09 Codex on jinx-nyc
- `codex` 0.163.0 on jinx-nyc is not logged in. The Phase 1 review instead ran on the owner's Windows PC, where Codex (0.160.0) is logged in, against a clean clone of the GitHub repo plus the pinned upstreams. For future reviews from jinx-nyc, the owner runs `codex login` there once.

## B-10 Runtime host
- GMGN does not host custom code (its docs describe agents running on the customer's machine and calling the API), so JINX needs its own runtime.
- Under the owner's "minimum suitable existing environment" rule, that is jinx-nyc. It already runs the engine and holds the GMGN request key, and 45.77.159.191 is the trusted IP given to GMGN.
- Railway is not provisioned. It would need a demonstrated need, owner approval, and paid static outbound IPs (Pro) added to GMGN's trusted list. The repo's Dockerfile and `railway.json` stay ready but unused.

## B-11 DataNomad license
- No license file at the pinned commit; README names none. Code stays reference-only unless the author grants permission.

## B-12 Live pump.fun fee rates (Phase 3)
- Paper fills use D3AD's recorded mainnet values (95 + 5 bps). Phase 3 must read the live values from the pump.fun global account.

## B-13 D3AD fast path (superseded 2026-10-20)
- GMGN executes orders, so D3AD's ShredStream-based sender is not wired and ShredStream access is not requested. D3AD stays a pinned donor for pieces GMGN does not provide.

## B-14 Separate GMGN wallets for native copy trading and JINX
- If GMGN native copy trading and JINX share one GMGN wallet, copy-trade positions land in JINX's reconciliation and use up the owner's one-slot limit.
- Needed: the owner confirms JINX gets its own GMGN wallet (the API key binds to one wallet).
- Observed 2026-10-20 (read-only, from GMGN wallet activity): the wallet bound to JINX's API key traded ENERGY at 20:15–20:23 UTC (two buys of 0.297 SOL, two sells). No JINX process was running, and the bridge refuses every trading call. So these trades came from outside JINX, through GMGN copy trading or manually. Until B-14 is decided, JINX's Phase 3 reconciliation would have to tell those positions apart from its own.

## B-15 GMGN plan tier sets JINX's polling budget — RESOLVED 2026-10-20
- GMGN rate limits per plan: Free 5/5, Plus 20/20, Pro 50/50 weight per second. 1-second candles are Pro only.
- Measured on the owner's key: **Plus**. Evidence: GMGN's response header `x-ratelimit-limit: 20` (`docs/evidence/gmgn_capability_check_20261020T1952.json`), and 1-second candles refused with `PRO_PLAN_REQUIRED`.
- The owner's message said "Pro". The owner then confirmed: "its not pro its plus who cares claude use what we have available maximize jinx with what gmgn gives us".
- JINX paces itself to Plus (`jinx/adapters/gmgn/budget.py`). Changing plans is a one-line change (`CURRENT_PLAN`).

## B-16 GMGN API key was shared in chat; owner keeps using it (risk accepted)
- Evidence: the key was pasted into the session on 2026-10-20. The mandate said to treat it as exposed and rotate it; the owner then ordered "use the fucking key claude".
- Current state:
  - The key is only in jinx-nyc `~/.config/gmgn/.env` (mode 600). It is not in the repo, docs, tests or logs; `tests/test_secrets.py` checks every committable file against the configured value.
  - Someone holding only the key can spend this account's read quota.
  - Trading also needs the request-signing key, which never left jinx-nyc, and the trusted IP.
- Reversible option: rotate at any time with the steps in `docs/SECRETS.md` §4. No code change is needed.

## B-17 pump.fun website endpoints versus pump.fun's Terms of Use
- Owner request 2026-10-20: "implement pumpfuns api and swap api". The useful data (every trade with wallet, side, USD price and amount, time; 1-minute USD candles; coin details) is served by pump.fun's website endpoints `swap-api.pump.fun/v2/coins/{mint}/trades|candles` and `frontend-api-v3.pump.fun/coins-v3/{mint}`.
  - All three answered without a login on 2026-10-20.
  - pump.fun does not publish them: its official docs (`pump-fun/pump-public-docs` @ 639b87f) cover on-chain programs, IDLs and SDKs only. The endpoint lists online were reverse-engineered by a third party.
- pump.fun's Terms of Use (updated 17 October 2026):
  - §6.1: access "through such automated means (including bots) as we expressly permit";
  - §21(h): no "bots, spiders or other automatic devices, programs, scripts … to access, obtain, copy or monitor any part of the Pump Platform … except as expressly permitted under Section 6.1";
  - §21(j): no reverse-engineering.
- So JINX does not call them automatically. The same data is available within terms:
  - PumpAPI's free stream: every curve and PumpSwap trade with fee payer, amounts, reserves, slot and time;
  - PumpAPI's free hourly replay archives for history;
  - GMGN's candles and token info;
  - pump.fun's official MIT SDKs (`@pump-fun/pump-sdk` 4.2.0, `@pump-fun/pump-swap-sdk` 2.2.0) for on-chain quotes.
- Needed: an owner decision only if pump.fun's site endpoints are still wanted despite §21(h).
- Reversible option: written permission from pump.fun, after which a read-only adapter is about a day's work.

## B-18 Storage for the PumpAPI feed — RESOLVED 2026-10-21
- Owner decisions 2026-10-21: raw PumpAPI events are processed in RAM and discarded after extraction; no raw archive anywhere; new dedicated services (Contabo execution, Neon ledger, Railway research, Redis transient); permanent storage hygiene rules.
- Implemented: the market store is in-memory only (event TTL 300 s + cap 60,000; raw payload released at apply; tokens and wallets idle 2 h + caps). Disk on the execution server holds only the ledger (172 KB measured). The earlier projection of 3.9 GB/day no longer applies: nothing from the feed is written to disk.
- Evidence: `tests/test_market_memory.py`, `tests/test_storage_hygiene.py`, `docs/INFRASTRUCTURE.md` §4.

## B-19 RugCheck source — DECISION CLOSED 2026-10-21 (owner: use the official public RugCheck API)
- Decision: the official RugCheck API, `GET https://api.rugcheck.xyz/v1/tokens/{mint}/report`, unauthenticated (published free access: one request per second). No subscription, server or separate engine.
- Runtime integration: TESTING. `jinx/adapters/rugcheck/client.py` (one worker at 1 request/s, Retry-After on 429, per-mint dedup, fresh reuse, GMGN-first priority, bounded queue and cache) feeds the RugCheck predicate of `jinx/intel/safety.py`. Every Early Momentum detection is screened at top priority (`JinxApp._screen`). Tests: `tests/test_rugcheck_adapter.py`, `tests/test_intel_safety.py`, `tests/test_candidate_screening.py`.
- Mapping (no invented thresholds): FAIL only on RugCheck's own `rugged: true` or a present mint/freeze authority (CHAD's existing hard checks); PASS on a well-formed report without them; missing, not yet indexed, 4xx/5xx, 429, timeouts and malformed bodies are UNKNOWN, never PASS.
- Measured live 2026-10-21 (`docs/evidence/rugcheck_check_20261021.json`): 16 requests, p50 85 ms, p95 684 ms, 0 rate-limited, 0 timeouts. RugCheck indexed fresh pump.fun tokens 7.9–10.2 s after creation; until then the answer is "not found" (UNKNOWN, retried every 2 s, at most 8 times).
- Exits never consult RugCheck or the safety gate (tested: the exit path imports neither).

## B-20 RugCheck "danger" findings — CLOSED 2026-10-21 (owner decision: evidence only)
- Decision: danger-level findings never block a token. Hard vetoes stay exactly the implemented ones: RugCheck's own `rugged: true`, or a mint or freeze authority still held (GMGN or RugCheck).
- Owner's reasons: mint/freeze authority and an explicit rugged flag are structural (more supply can be minted, sells can be frozen); "Creator history of rugged tokens" and "Large Amount of LP Unlocked" are statistical or situational and common in a pump.fun token's first minutes, including on tokens that later run. A blanket danger veto would make RugCheck a universal quality gate, against earliest entry and the rule against invented scoring committees. GMGN runs first and can veto; RugCheck is secondary evidence. Live data showed tokens clearing every other check while carrying danger flags; blocking them would drop candidates inside the 30 s Early Momentum window.
- Implementation: unchanged (`jinx/adapters/rugcheck/client.py::interpret`). Score, normalized score, risk findings and liquidity are logged, never thresholded; temporary failures stay uncached for re-checks. Pinned by `test_a_real_report_is_parsed_into_findings_scores_and_liquidity` and `test_rugcheck_completes_the_gate_and_a_full_pass_is_eligible`.
