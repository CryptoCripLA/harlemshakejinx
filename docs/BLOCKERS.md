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

## B-02 Early Momentum curve gate — DECIDED BY THE OWNER 2026-10-21
- Conflict: CHAD's Bot 10 says "0.5–10 SOL bonding curve gate" and also "all below the 35 SOL bonding curve threshold", and its peak example (60 → 25 SOL) cannot be virtual SOL.
- Owner decision (for the owner, in dollars: CLAUDE.md "EARLY-LAUNCH ENTRY RULE"):
  1. Current reserve: 0.5 <= verified real SOL in the curve <= 10.0 (PumpAPI `quoteInPool` on a SOL-quoted `pool: pump` curve; never virtual minus 30).
  2. Historical peak, mandatory: verified peak real SOL since creation <= 10.0. An owner-approved interpretation of CHAD's peak protection (CHAD gives no peak number). Peaks are never lowered; both virtual and real peaks are kept for analysis.
  3. The 35 SOL statement is retired as an entry condition and kept as a superseded specification (`early_momentum.SUPERSEDED`).
  4. Non-standard curves follow the same rule when their real reserve is a verified SOL amount; unverified or non-SOL curves are ineligible.
  5. Re-checked at the entry decision on the current state (`EarlyMomentum.reserve_check`, `JinxApp.entry_check`); a stale snapshot or a feed that is not live never qualifies.
  6. Pre-graduation only: a migrated pool is outside this path.
  7. Missing or incomplete history (creation not observed, an unverified reading since creation) blocks entry.
- Implementation: `jinx/intel/early_momentum.py` (`current_reserve_0_5_to_10_real_sol`, `peak_reserve_max_10_real_sol`), `jinx/intel/readings.py::verified_real_sol`. Tests: `tests/test_b02_reserve_policy.py` (the owner's 11 required cases and more).
- Cross-feed detail: when PumpPortal's copy of a creation wins the dedup race it carries no real reserve; the curve starts empty, so the creator's own buy bounds that reading. At most 10 SOL keeps the peak provable; above that, entry is blocked.
- Replay of the 41 recorded detections (evidence at the moment of detection): 14 inside the current range, **3 pass both gates**; blocked: 27 outside the current range, 10 history incomplete (launched before the short capture began), 1 already above the ceiling. Detections are not trades: no profitability is implied.

## B-03 CHAD values that are not specified (Phases 2-3)
These are left unset, and entries that depend on them stay disabled:
safety 0–100 formula and pass threshold; momentum "stall" and "sustained volume" quantities; X Scout verdict cut-offs and the @SolBotChad bonus; graduation progress → 0–20 points mapping; cluster "short window"; freshness decay function; dev-reputation formula and cut-off; confluence weights and the global score threshold; Kelly cap, payout-ratio prior and bucket edges; stop-loss and trailing values and the "high-conviction" cut-off; priority-fee escalation amounts; the shared-funder look-back (Early Momentum's peak gate is no longer open: the owner set a hard 10 real SOL peak ceiling for that path only, B-02; it is not applied to any other CHAD strategy); the whale volume-points mapping (CHAD: "More confirmed observed buys = more reliable signal"); the second-degree co-buy window; the win-rate look-back used for whale promotion (GMGN `period`, 7d used for now).

Added 2026-10-21 with the Early Momentum build. The owner settled all but the first on 2026-10-21 (integration order, items 4-8); they are kept here with the decision and where it is implemented:
- **Peak SOL gate rule.** Settled by B-02: the verified peak real SOL since creation must not exceed 10 SOL; unknown or incomplete history blocks.
- **The creator's own buy. DECIDED:** "A verified creator purchase counts as one distinct buyer under that stated rule; flag creator participation for safety analysis. Do not invent a new creator-exclusion rule." Implemented: counted, `creator_in_window` flagged (`jinx/intel/early_momentum.py`).
- **Sybil funder check. DECIDED:** "identify three buyers sharing a verified funder and preserve CHAD's emergency protection. Missing funder data must never be represented as a clean result." Implemented: three or more buyers with one GMGN-reported funder = FAIL with `emergency_exit`; any unknown funder = UNAVAILABLE, which blocks.
- **Near graduation. DECIDED:** "use verified provider statuses and existing source-derived progress evidence. Do not invent a numerical graduation threshold or 0–20 scoring formula." Implemented: GMGN Trenches' own `near_completion` category (read every 10 s by `JinxApp.poll_trenches_once`) sets `near_threshold` and the lifecycle's APPROACHING GRADUATION; progress readings stay as evidence. The 0–20 points remain unproduced: CHAD gives the range only, and the owner said not to invent the formula.
- **Large sells. DECIDED:** "record actual sell sizes and wallet identities. Do not invent a threshold or falsely label a sell as dangerous without an approved rule." Implemented: the 10 largest sells around graduation with seller, size, phase, reserve after and whether the seller is the creator; no label.
- **Holder concentration. DECIDED:** "use verified wallet balances where available. Trade observations alone are not proof of current token ownership." Implemented: GMGN top holders (balances and share of supply) requested at each migration, pool accounts kept apart from wallets; the stream's trade flow is shown separately and labelled not proof of ownership; without balances the verified part is UNAVAILABLE.

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

## B-50 Phase 4 rows that wait for Phase 3 on main (Phase 4 builder, 2026-10-21)
- Blocked: the trading half of V01 (a continuous paper-trading run), V06 (every approved exit), V15 (paper P&L after all modeled costs), V16 (adverse moves and failed exits) and V21 (CHAD vs exit/wallet variants), in `docs/PHASE4_VERIFICATION_MATRIX.md`.
- Evidence: Phase 3's size/fire/exit code (Executioner, Negative Divergence, Sybil emergency exit, BIGMAC_PAPER, monitor) is on branch `phase3/size-fire-exit`, not on `main` (checked 2026-10-21 15:05 UTC). Contabo runs release `fe01850` in mode OFF.
- Needed: Phase 3 landing on main; then the owner-set parameters Phase 3 leaves fail-closed (B-03 confluence threshold and Kelly values, B-30 BigMac paper settings). Phase 4 runs its exit, P&L and variant checks the moment Phase 3 lands, using explicitly labelled test fixtures where a value is owner-unset.
- Not blocked by this: every other Phase 4 row (replay, predicates, safety, isolation, outages, recovery, stress, orders, reconciliation, anti-lookahead, latency, audits, single controller, speed rules).

# Phase 3 blockers (p3 builder, 2026-10-21; ids B-30..B-49)

Each is a value or permission CHAD or the owner has not given. The code around it is built and tested; the missing value is fail-closed configuration, never an invented default.

## B-30 BIGMAC_PAPER settings are not set by the owner
- The owner's BigMac exit (`jinx/exits/bigmac_exit.py`) is installed, proven equal to the original TypeScript, and wired into the position monitor. Its four percentages (take profit, stop loss, trailing stop, trailing activation) have no defaults by design ("Its numerical settings must be explicitly configured for paper testing, not inherited as live trading defaults").
- Effect now: until the owner sets them, new paper positions get no BigMac exit; CHAD's mandatory exits still run. Tests and paper runs use settings labelled `TEST_FIXTURE_*`.
- To set (paper only): `python -m jinx.exits.configure --db <ledger> set-bigmac --take-profit X --stop-loss Y --trailing-stop Z --trailing-activation W --owner-order`.

## B-31 Which Kelly bucket an Early Momentum (fast-path) entry uses
- CHAD's Kelly buckets are by confluence score ("Score 60 … Score 70 … Score 80+"), but Bot 10 "Bypasses the normal scoring queue", so a fast-path entry has no score.
- Effect now: sizing refuses an unscored entry (`NO_SCORE_BUCKET`) unless the owner's Kelly settings name an `unscored_bucket`. Paper runs use a labelled fixture bucket.
- Needed: the owner's choice of bucket (and prior) for fast-path entries.

## B-32 Kelly bucket edges, bootstrap priors and cap (CHAD C13; part of B-03)
- CHAD fixes quarter-Kelly, the 20-closed-trade switch, per-bucket models and the reset on a filter change; all four are implemented (`jinx/sizing/kelly.py`). It gives estimated win rates by score ("Score 60 … 32–36 % … Score 70 … 40 % … Score 80+ … 45–52 %+") but no payout ratio, no exact edges and no cap ("Kelly sizes up to the cap").
- Effect now: no Kelly settings = no entries (`SIZING_NOT_CONFIGURED`). Settings are set with `python -m jinx.exits.configure set-kelly --file … --owner-order` (label `OWNER_CONFIG`).
- Note: with CHAD's own score-70 win rate (40 %), any payout ratio up to 1.5 gives zero or negative edge, so Kelly would not bet there.

## B-33 Re-entry while a Negative Divergence remainder is still held
- CHAD: "Re-entry allowed immediately — No exit cooldown". Implemented: no cooldown exists; after a position closes, a new detection can enter the same token at once (`test_reentry_is_allowed_immediately_after_an_exit`).
- Conflict: after a partial Negative Divergence exit the rest of the position is still open, and Phase 1's one-position-per-token rule plus the owner's slot rule refuse a second entry into the same token.
- Needed: an owner decision only if re-entry should add to a token that is still partly held.

## B-34 Paper fill delay
- A paper fill priced at the decision's reserves ignores latency. `ExecutionGateway(fill_delay_ns=…)` prices it at the first price update at least that long after the decision (event time). It is a labelled paper simulation setting; the live paper run uses 300 ms, GMGN's own stated latency ("under 0.3 seconds"), which JINX has not measured. Real latency is measured only once live orders exist (activation).

## B-35 Confluence score and global threshold (CHAD C12; part of B-03)
- The confluence board records every component with source, times and blocking status (`jinx/confluence/board.py`). Without CHAD's weights and global threshold there is no score.
- Effect now: the standard (scored) entry path refuses (`NO_CONFLUENCE_SCORE`); Confluence Collapse cannot fire on positions without an entry threshold and reports UNAVAILABLE instead of guessing. Only the fast path (Early Momentum) can enter.

## B-36 Negative Divergence has no live producer (CHAD C02; part of B-03)
- The Executioner's partial exits (30/40/50/60 % by entry score, only in profit, once per event) are built and tested (`jinx/exits/executioner.py`). The event comes from the momentum module's "price stalls on sustained volume", whose quantities CHAD does not give.
- Effect now: no Negative Divergence events occur in the running engine; fast-path positions have no entry score, so they could not size one anyway.

## B-37 Live execution through GMGN is not wired (activation requirement)
- Paper only, as ordered. The gateway refuses LIVE (`LiveExecutionUnavailable`); the bridge refuses every GMGN trading method. `jinx/execution.py::gmgn_order_outcome` maps GMGN order reports to FILLED / FAILED / UNCERTAIN and books a fill only with actual report amounts.
- GMGN's own skill page names the success status both `confirmed` and `successful`; both are accepted only together with report amounts.
- Needed for activation: the owner's live authorization and a dedicated limited-capital wallet (B-14).

## B-38 CHAD's own stop loss and trailing values (C14; part of B-03)
- CHAD: "Standard configurable stop loss on every position. High-conviction positions get a trailing stop". Values and the high-conviction cut-off are not in CHAD. The owner's BIGMAC_PAPER policy is the paper stop/take-profit/trailing exit meanwhile. GMGN condition orders are not placed, so no protective sell can be duplicated across JINX and GMGN.

## B-39 Priority fee escalation amounts (C15; part of B-03)
- Paper fills include the 5,000-lamport network fee and the curve fees; tips and priority fees are 0 and reported as such.

## B-40 BigMac: one documented arithmetic difference that JINX cannot reach
- The original computes the take-profit price before the stop check, so a NaN (or +Infinity) take-profit percent throws there; the owner's port computes it only when used. The owner ordered exactly two fixes, so this structure is kept, and `BigMacPaperConfig` refuses non-finite percents. 39 of 6,767 differential cases, all of this kind (`docs/evidence/PHASE3_BIGMAC_SOURCE_EQUIVALENCE.md`).

## B-41 Order lifecycle replication to PostgreSQL
- `order_events` (decided, quoted, submitted, filled, failed, with costs) is in the authoritative local ledger. It joins the PostgreSQL replica once the storage session's hygiene test counts replicated tables from `OUTBOX_TABLES` (requested 2026-10-21 with the exact one-line change).

## B-42 Daily loss stop (matrix E2)
- Needs owner-approved parameters; not built.
