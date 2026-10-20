# Owner directives 2026-10-20: GMGN plan active, and the GMGN intelligence & migration integration mandate

Verbatim copy of the owner's message of 2026-10-20, kept as the canonical source for `CLAUDE.md`. The GMGN API key the owner pasted in the same message is replaced by `gmgn_<REDACTED>` (see `docs/SECRETS.md` §4). Nothing else is changed.

Later owner decisions the same day narrow parts of it (recorded in `CLAUDE.md`):
- the plan is Plus, not Pro ("its not pro its plus who cares claude use what we have available maximize jinx with what gmgn gives us");
- the pasted key stays in use ("use the fucking key claude");
- the signing key may load in paper runs ("its ok for paper we need real data i dont care if it loaded whatever").

---

## Message 1

CLAUDE — GMGN PRO IS ACTIVE. PROCEED.
The owner has upgraded to GMGN Pro.
Update `CLAUDE.md` and `BUILD_STATUS.md` immediately.

1. Verify authenticated Pro API access and available quotas.
2. Integrate GMGN Trenches new-token and migration intelligence.
3. Connect Smart Money, KOL, wallet tracking, token security, developer history and market momentum.
4. Preserve our existing Pump.fun fast stream where it provides earlier event-level information.
5. Make GMGN the primary supported execution provider; do not duplicate its functionality unnecessarily.
6. Measure actual data freshness and latency. Do not assume advertised performance is guaranteed.
7. Preserve the completed Phase 1 foundation and all four upstream-source isolation boundaries.
8. Finish the pending Phase 1 verification and independent review, then present the Phase 2 gate.

Do not start over. Do not invent trading logic. Do not add build phases.
CHAD is the blueprint. GMGN provides intelligence and trading capabilities. JINX makes the decisions.

---

## Message 2

# JINX — GMGN INTELLIGENCE & MIGRATION INTEGRATION MANDATE
## FOUR PHASES ONLY | CHAD MASTER BLUEPRINT | ENTER EARLY, EXIT SMARTER

# READ THIS FIRST — ARCHITECTURAL UPDATE

Claude, we now have access to GMGN's official API.

This is a major addition to JINX's approved engineering resources.

**DO NOT RESTART THE PROJECT. DO NOT DISCARD PHASE 1. DO NOT INTRODUCE ADDITIONAL BUILD PHASES.**

Repository:

`https://github.com/CryptoCripLA/harlemshakejinx`

Preserve the existing four-phase plan and the Phase 1 implementation, including the previously reported 117 tests, live Pump.fun capture, replay engine, event bus, registries, position ledger, paper wallet, and source isolation.

The reported Phase 1 evidence remains subject to independent verification.

GMGN must now become the PRIMARY supported source for token intelligence, smart-money analysis, migration monitoring, wallet analysis and trade execution.

The other three upstream repositories remain isolated implementation donors where GMGN does not already supply the necessary capabilities.

**CHAD REMAINS THE MASTER TRADING ARCHITECTURE.**

We are building one autonomous decision engine.

Its entire operation is:

**HEAR CHATTER → SIZE → FIRE → EXIT.**

No ten separate bot runtimes.

No seventeen competing entry paths.

No redesigning the trading strategy.

No redundant execution infrastructure.

No fake performance claims.

Every engineering decision must support earlier qualified detection, faster execution, intelligent exits or correctly measured realized profit.

# 1. IMMEDIATE SECURITY AND API INTEGRATION

A GMGN API credential was supplied in conversation.

Treat that credential as exposed. Do not copy it into source code, prompts, logs, tests, commits, issue comments or documentation.

The owner must rotate it. Configure the replacement using protected environment secrets.

Expected environment variable:

`GMGN_API_KEY`

Never print the credential.

Start with authenticated, read-only API verification.

Verify:

- Key authentication succeeds.
- Solana access is available.
- The account's enabled plan and quotas.
- Supported token endpoints.
- Trenches and migration data access.
- Wallet-tracking access.
- Smart-money data availability.
- Market-signal access.
- Quote access without trade submission.
- Actual response timestamps and freshness.
- Supported automated execution controls.

Document every successful and failed capability with redacted evidence.

Do not assume an API key alone authorizes or enables trade signing. GMGN's documented order workflows may also require a bound wallet and private-key-backed local signing.

Paper testing must not load any live signing key.

# 2. GMGN IS NOW THE PRIMARY DATA INTEGRATION

Use the actual cloned and pinned GMGN client.

Preserve its authentication, schemas, error handling, request signing and rate-limit behavior.

Inspect the pinned source and current official documentation for:

`src/client/OpenApiClient.ts`

`src/commands/token.ts`

`src/commands/market.ts`

`src/commands/portfolio.ts`

`src/commands/track.ts`

`src/commands/swap.ts`

And the relevant CLI and Skills documentation.

Do not invent endpoints.

Do not scrape GMGN's website when an official API or CLI route exists.

Do not replace GMGN's actual source-code implementation with hand-written HTTP calls unless the original source is technically unsuitable and the deviation is documented and tested.

## REQUIRED GMGN CAPABILITIES

### A. Trench new-token detection

Integrate GMGN's Trenches API.

Support the documented lifecycle categories:

- `new_creation`
- `near_completion`
- `completed`

Observe newly created tokens, bonding-curve progress and migration completion.

Capture the earliest timestamp actually available from the provider.

### B. Token intelligence

Read available token fields for:

- Contract and pool identity.
- Creation timestamp.
- Price and liquidity.
- Market capitalization.
- Bonding-curve status.
- Rug and honeypot evidence.
- Holder concentration.
- Rat trader ratio.
- Bundled-trader exposure.
- Suspected insider holdings.
- Fresh-wallet participation.
- Sniper-wallet participation.
- Developer-associated activity.
- Smart-money and KOL participation.
- Trading volume and transaction activity.

Use the exact provider field names and semantics from the verified API schema.

### C. Market signals and momentum

Integrate:

- GMGN trending data.
- One-minute ranking windows.
- Market token signals.
- Price and volume observations.
- Smart-money purchases.
- Large-buy observations where supported.
- Historical candles for context.

A one-minute ranking window is not equivalent to an event arriving within one second.

Do not use one-minute trending rankings as proof of subsecond launch detection.

### D. Wallet tracking

Integrate:

- Followed-wallet activity.
- Smart Money trades.
- KOL trades.
- Wallet holdings.
- Wallet realized P&L.
- Trading history and win-rate evidence.
- Developer-created tokens.

Distinguish the actual trader's profit from profit that JINX could have captured after latency, slippage and fees.

### E. Execution

Prefer GMGN-supported:

- Read-only order quotes.
- Market orders.
- Limit orders.
- Strategy orders.
- Take-profit and stop-loss orders.
- Percentage-based exits.
- Order-status queries.
- Wallet balance and holding verification.

Do not implement a competing D3AD-E live execution sender unless testing shows that GMGN cannot support a documented CHAD requirement.

GMGN handles supported routing and transaction services.

JINX owns entry qualification, sizing decisions, exit decisions, position-state reconciliation and execution intent.

# 3. MIGRATION INTELLIGENCE IS A CORE JINX CAPABILITY

**THIS IS A PRIMARY REQUIREMENT, NOT A SIDE FEATURE.**

JINX must understand the entire supported token lifecycle from launch to bonding-curve graduation and post-migration trading.

Implement one migration-aware token state machine.

```text
NEWLY DETECTED
      |
      v
ON BONDING CURVE
      |
      v
APPROACHING GRADUATION
      |
      v
MIGRATION OBSERVED
      |
      v
MIGRATION CONFIRMED
      |
      v
POST-MIGRATION MARKET
```

Maintain exceptional states for unknown, stale, failed or conflicting observations.

## 3.1 Token lifecycle state

Track:

- Token mint.
- Launchpad identity.
- Creation time.
- Current bonding-curve status.
- Current graduation progress.
- Highest observed virtual SOL reserves.
- GMGN Trenches category.
- First observed migration signal.
- Confirmed migration status.
- Pre-migration pool identity.
- Post-migration pool identity.
- Last valid price and liquidity observation.
- Source and time of every state change.

Never mistake a temporary missing token from a query result for a confirmed migration.

Never infer successful graduation solely because a token's price rose.

## 3.2 Migration discovery

Use GMGN's documented Trenches categories to detect transitions:

`new_creation → near_completion → completed`

Where GMGN provides a migration-status field or other explicit state evidence, preserve its precise semantics.

Corroborate the transition with the applicable supported pool and bonding-curve information.

Do not manufacture a stream, webhook or migration callback that the documented API does not actually provide.

If GMGN exposes migration states only through queries, measure the detection delay and rate-limit constraints.

## 3.3 Pre-migration intelligence

Before a token migrates, JINX must collect the available evidence needed for CHAD's early-entry logic:

- Unique buying wallets.
- Real trading activity.
- Bonding-curve reserves.
- Historical reserve peak.
- Wallet concentration.
- Smart-money participation.
- Developer history.
- Bundled and insider exposure.
- Safety status.
- Momentum observations.
- Signal freshness.

Implement CHAD's documented three-unique-wallet, three-buy, thirty-second Early Momentum pattern.

Retain the unresolved CHAD reserve-gate conflict rather than silently selecting a threshold.

## 3.4 Migration-event handling

When a migration is detected:

- Update the token state immediately.
- Publish one deduplicated migration event.
- Preserve the complete pre-migration history.
- Update the supported liquidity/pool identity.
- Request a fresh market observation.
- Refresh the execution-route and quote information.
- Reevaluate existing positions against their approved exit rules.
- Reconcile any pending orders.

Do not interpret migration itself as an automatic buy signal or a guaranteed positive price event.

Do not duplicate a purchase because both the pre-migration and post-migration detectors report the same token.

## 3.5 Post-migration intelligence

Continue monitoring migrated tokens.

Retain the original entry, whale, developer and momentum evidence.

Track the available new-pool liquidity and price observations.

Apply CHAD's normal position-management behavior across migration.

Do not automatically liquidate or abandon positions simply because the execution venue changed.

Do not assume a pre-migration quote remains executable afterward.

## 3.6 Migration simulation

Paper-test all relevant transitions:

- Normal successful migration.
- Repeated migration notification.
- Missing migration notification.
- Delayed provider observation.
- Sudden liquidity relocation.
- Changing pool identifiers.
- A pending buy during migration.
- A pending sell during migration.
- Migration followed by rapid decline.
- Migration followed by further price appreciation.
- Failed quote or unavailable exit route.

Reconcile balances and position ownership through every case.

**PROOF REQUIRED:** migration-state correctness, decision timestamps, quote freshness, duplicate prevention, sell-path continuity, and cost-adjusted paper outcomes.

# 4. THE CHAD HUNTING LOGIC — NO INVENTED STRATEGY

CHAD supplies the behavioral requirements.

Build the following inside ONE intelligence engine.

## Early opportunity detection

Use GMGN token-discovery, migration and wallet data together with the already working event stream.

Detect CHAD's specified Early Momentum pattern:

- Three qualifying buys.
- Three unique fee-paying wallets.
- Thirty-second window.
- Required safety checks.
- Required bonding-curve qualification.
- Peak reserve history.
- Common-funder evidence.

Do not create a trade from three arbitrary GMGN ranking records.

If the provider lacks raw wallet-event identity and timestamps, preserve the existing suitable event stream or identify the missing event feed.

## Whale intelligence

Use GMGN Wallet Tracking and Portfolio APIs as primary inputs.

Preserve CHAD's requirements:

- Profitable-wallet discovery every four hours.
- At least 40% historical win rate for the specified promotion condition.
- Second-degree co-buy relationships.
- Fifteen manually pinned wallets.
- Wallet score components of 50/25/25.
- Fourteen-day recency half-life.
- Three-wallet clustering.

GMGN's existing wallet-score skill may help evaluate historical track records and copy-tradeability.

Do not substitute the skill's separate proprietary scoring formula for CHAD's documented wallet-confidence policy.

## Safety intelligence

Use GMGN's available token security, rug, wash-trading, insider, bundler and holder data.

Combine independent approved evidence without introducing an invented scoring formula.

Fail closed when mandatory evidence is unknown.

The objective is to reject unsafe entries early without burdening the fast path with unnecessary duplicate data calls.

## Momentum intelligence

Use available market observations to compute CHAD's documented price velocity, volume acceleration and Negative Divergence evidence.

Preserve the fifteen-second monitoring cadence.

Keep all observations timestamped.

Do not use late-arriving minute candles as if they were live transaction events.

## Developer intelligence

Use GMGN creator-token history and supported wallet analysis.

Reuse legally available DataNomad historical methods where appropriate.

Maintain strict decision-time boundaries.

## Confluence

Maintain one authoritative signal state.

Every evidence item must include:

- Token identifier.
- Signal type.
- Source.
- Observation timestamp.
- Event timestamp, when supplied.
- Freshness.
- Evidence payload.
- Blocking status.
- Applied score component, if explicitly defined.

No undocumented weights.

No invented entry thresholds.

No fabricated confidence values.

# 5. SIZE — PROTECT CAPITAL BEFORE SUBMITTING

Implement CHAD's quarter-Kelly mechanics with its documented per-user, per-score-bucket calibration.

Where Bayesian priors, buckets or sizing caps are still unresolved, fail closed on dependent live sizing decisions.

Before execution:

- Read actual available wallet balance.
- Account for pending commitments.
- Respect configured position and exposure limits.
- Reserve expected transaction costs.
- Reject insufficient funds.
- Prevent duplicate buy intents.
- Do not treat a pending order as settled capital.

Do not hardcode a one-position limit unless that is explicitly present in the owner's approved configuration.

Do not confuse attractive wallet-reported P&L with realistically copyable net P&L.

# 6. FIRE — GMGN-FIRST EXECUTION

There must be ONE JINX execution coordinator.

The intended path is:

```text
CHAD ELIGIBILITY CONFIRMED
          |
          v
RISK / SIZE APPROVED
          |
          v
GMGN FRESH ORDER QUOTE
          |
          v
JINX EXECUTION GATE
          |
          v
GMGN ORDER SUBMISSION
          |
          v
CONFIRM ORDER RESULT
          |
          v
RECONCILE HOLDINGS
```

Keep the already established paper and live execution boundaries.

GMGN orders must use the real documented API and official signing requirements.

Do not submit live orders simply because the API key passes a read-only request.

No trade-by-trade approval prompts after the owner has explicitly activated automated trading and set the necessary operating permissions.

Until that activation, all trading remains paper-only.

GMGN's published execution speed is not guaranteed.

Measure:

- Signal observed to qualification.
- Qualification to quote.
- Quote to submission.
- Submission to confirmation.
- Confirmation to reconciled position.

Never describe a successful API request as a confirmed fill.

# 7. EXIT — THIS IS AS IMPORTANT AS ENTERING EARLY

JINX must continuously protect open positions.

GMGN provides supported sell and protective-order mechanics.

CHAD supplies the exit-decision logic.

Do not delegate CHAD's confluence decisions to an unrelated default GMGN TP/SL policy.

## Confluence Collapse

Evaluate open positions every ten seconds.

```text
IF position_age >= 600 seconds
AND current_confluence < entry_threshold * 0.40
THEN full exit
```

## Negative Divergence

Only when the required divergence signal exists and the position is in profit:

```text
ENTRY SCORE >= 75  → 30% partial exit
ENTRY SCORE 65–74  → 40% partial exit
ENTRY SCORE 60–64  → 50% partial exit
ENTRY SCORE < 60   → 60% partial exit
```

Prevent duplicate exits from one signal occurrence.

## Protective execution

Implement owner-configured stop-loss and trailing protection using appropriate supported GMGN mechanisms.

Reconcile hosted protective orders with JINX-generated discretionary exits.

A GMGN protective order and a JINX exit must not independently oversell the same position.

Before a full discretionary exit, account for outstanding GMGN conditional orders, their cancellation status and any concurrent fills.

If status is uncertain, preserve the uncertain order state and reconcile before placing conflicting additional orders.

Continue tracking positions during and after migration.

## Real profit accounting

Calculate realized P&L using actual fills and total costs.

Paper results must remain explicitly labeled simulated.

Report:

- Entry and exit quantities.
- Execution prices.
- Fees and tips.
- Slippage.
- Net SOL received.
- Realized net P&L.
- Open-position exposure.
- Unfilled or failed order costs.
- Exit reason.
- Detection and execution latency.

**Entering early does not matter if the system cannot exit intelligently and realize the gain.**

# 8. UPSTREAM SOURCE ISOLATION

Retain all four existing pinned sources.

| Source | Responsibility |
|---|---|
| GMGN | Primary data, token lifecycle, wallet intelligence and supported execution |
| Mako | Event infrastructure, safety integration, storage and recovery |
| D3AD-E | Isolated low-latency capabilities or fallback mechanics only when justified |
| DataNomad | Licensed research methods and historical validation references |

No direct cross-imports among upstream repositories.

No shared writable upstream state.

No competing position ledgers.

No competing live order senders.

No upstream strategy may silently execute.

Every source communicates through JINX-owned adapters.

Preserve the current verified code. Do not replace working Phase 1 mechanisms simply because GMGN provides overlapping data.

Use GMGN where it improves coverage, completeness or measured execution capability.

# 9. THE EXISTING FOUR BUILD PHASES — UNCHANGED IN COUNT

## PHASE 1 — FOUNDATION & GMGN INTEGRATION READINESS

Phase 1 has already been reported as implemented and pushed.

**DO NOT REBUILD IT FROM SCRATCH.**

Preserve the existing source code, tests and evidence.

Before final Phase 1 approval, incorporate this GMGN API update into:

- `CLAUDE.md`
- GMGN adapter contract.
- Source-fidelity matrix.
- Integration dependency map.
- API capability inventory.
- Migration lifecycle interface.
- Secrets-handling documentation.
- GMGN-first execution ownership plan.

Run authenticated read-only capability checks using the rotated, securely configured credential when available.

Do not write Phase 2 signal logic prematurely.

Complete the pending independent review and report any Phase 1 defects.

**STOP AT THE PHASE 1 REVIEW GATE.**

## PHASE 2 — CHAD HUNTING INTELLIGENCE & MIGRATION

This is the main intelligence build.

Implement:

- GMGN new-token discovery.
- GMGN migration lifecycle tracking.
- GMGN smart-money and wallet tracking.
- Token security evidence.
- Developer and insider intelligence.
- Bonding-curve observations.
- Historical reserve peaks.
- Early Momentum detection.
- Whale clustering.
- Signal freshness.
- Market momentum.
- Negative Divergence evidence.
- X and Telegram signals where authorized sources are available.
- Unified CHAD confluence state.

Use live accessible market observations and reproducible recorded replay.

Test migration transitions, stale signals, duplicate wallet events, delayed provider responses and failed safety gates.

Measure detection latency from the real provider data.

**STOP AT THE PHASE 2 REVIEW GATE.**

## PHASE 3 — SIZE, GMGN EXECUTION, EXITS & P&L

Build the complete paper-mode lifecycle using the GMGN execution interfaces.

Implement:

- CHAD quarter-Kelly sizing.
- Exposure and available-balance checks.
- GMGN read-only quotes.
- GMGN order gateway behind the paper/live gate.
- Order-status lifecycle.
- Position reconciliation.
- CHAD Confluence Collapse.
- Negative Divergence partial exits.
- Owner-configured protective orders.
- Migration-aware exit handling.
- Full cost-adjusted P&L accounting.

Run continuous paper simulations using current market observations.

Reproduce historical scenarios with documented execution assumptions.

Ensure no paper test sends a real order.

**STOP AT THE PHASE 3 REVIEW GATE.**

## PHASE 4 — HARDENING, REAL-TIME PAPER TESTS & AUDIT

Test the full JINX system.

Verify:

- GMGN response accuracy and freshness.
- Migration detection and state transitions.
- Fast-path decision latency.
- Wallet-tracking signal freshness.
- Safety rejections.
- Concurrent token events.
- Duplicate trade protection.
- Order timeout and recovery behavior.
- GMGN rate-limit handling.
- API outages.
- Missing price and pool data.
- Position reconciliation.
- Protective exit behavior.
- Paper P&L after modeled costs.
- Restart and failover.
- No-lookahead historical validation.
- Isolation of all four upstream code sources.
- Credentials and permissions.
- No unauthorized live execution.

Submit code and test evidence to the independent review panel, including Codex when authenticated.

Record the panel verdict.

**STOP AT THE PHASE 4 REVIEW GATE.**

No additional build phases.

# 10. PERFORMANCE AND LATENCY REQUIREMENTS

JINX must be designed to react within seconds wherever the data feed, eligibility checks and supported execution infrastructure make that possible.

Do not claim an unverified zero-latency system.

Prioritize:

- Event-driven observations where genuinely available.
- Bounded queues.
- Deduplicated wallet events.
- Fresh cached safety evidence.
- Immediate fast-lane signal publication.
- No synchronous background intelligence blocking the hot path.
- Rate-limit-aware GMGN requests.
- No redundant API calls.
- No synchronous dashboard work during trade decisions.
- Minimal order-path processing overhead.
- Immediate eligible exit evaluation.

When GMGN cannot supply a necessary event quickly enough, document the precise limitation and preserve the appropriate existing stream.

Do not purchase additional data feeds or construct another low-latency execution network without demonstrating the missing functionality.

# 11. BUILD VISIBILITY AND ACCOUNTABILITY

Update `CLAUDE.md` with this directive.

Preserve the previously required eleven-skill inventory and invocation evidence.

Before implementation work, present the revised four-phase integration map.

Before each phase, display its actual implementation checklist.

During coding, update:

`docs/BUILD_STATUS.md`

Each verified component must link to its code and test results.

At the end of each phase provide:

- Changed files.
- Reused upstream functions.
- Commit SHA.
- Passing and failing tests.
- Live-data observations.
- Paper-simulation results.
- Timing measurements.
- Discovered defects and fixes.
- Unresolved CHAD parameters.
- Independent review findings.

No progress theater.

No fabricated metrics.

No claiming a trading edge from marketing material alone.

# FINAL ENGINEERING COMMAND

**GMGN IS NOW THE PRIMARY PLATFORM INTEGRATION.**

**CHAD IS STILL THE MASTER INTELLIGENCE AND TRADING BLUEPRINT.**

**JINX IS THE SINGLE DECISION ENGINE.**

GMGN migration intelligence must be wired into JINX's token lifecycle and existing fast event stream.

Do not start over.

Do not weaken the existing four-phase delivery requirements.

Do not invent new strategies or arbitrary scoring logic.

Preserve the working foundation.

Implement GMGN's actual available capabilities.

Validate the earliest detectable signals.

Protect positions across migration.

Measure the full execution lifecycle.

Correct defects during paper simulation, not after real-money activation.

**HEAR CHATTER. SIZE. FIRE. EXIT.**

**ENTER EARLY. EXIT SMARTER. REALIZED NET PROFIT IS THE OBJECTIVE.**

**FOUR PHASES. ONE ENGINE. NO DRIFT. NO DUPLICATE EXECUTION.**

HEAR → SIZE → FIRE → EXIT.

---

## Appendix: GMGN website text pasted with the directive

These are GMGN's own marketing claims, unmeasured by JINX. They are not requirements.

```text
gmgn_<REDACTED>  Core Capabilities
Token Analysis
Multi-dimensional data including rat trader ratio, bundle ratio, and DEV tracking.
Real-time Trending
Track multi-chain momentum with 1m granularity, scanning capital sentiment.
Trench New Token
Second-level monitoring for new listings with advanced metric filtering.
Wallet Tracking
Real-time tracking of KOLs and Smart Money with millisecond response.
0-Latency Trading
Direct RPC routing for 0-latency trades. Supports TP/SL and limit orders.
Wallet P&L Analysis
Decode elite traders' tracks and win rates to capture Smart Money alpha.
Quick Start
Quickstart Guide
Create your API Key and send your first request within 2 minutes.
GitHub
Visit the open-source repo for the latest Skills, examples, and documentation.
Telegram
Join the GMGN API dev community. Collaborate and sha key is gmgn_<REDACTED>
```
