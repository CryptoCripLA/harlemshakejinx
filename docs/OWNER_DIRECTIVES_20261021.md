# Owner directives 2026-10-20 / 2026-10-21 (verbatim)

Kept as the canonical source for the `CLAUDE.md` sections added the same day. Text is unchanged.

---

## 1. pump.fun API and swap API (2026-10-20)

> implement pumpfuns api and swap api its free and has good uses we could use ontop of everything else

Notes the owner pasted with it:

> Every single trade on a coin: wallet, buy or sell, USD price, USD amount, time. This works on the launch curve and after graduation. GMGN only gives the top 100 traders ranked by profit, with no trade-by-trade list. With pump.fun's trade list we can answer "who bought before everyone else?" exactly, plus each early buyer's entry price, and whether and when they sold.
> 1-minute dollar candles, 1,000 per request. GMGN gives 100 per request. The catch: pump.fun only serves the most recent 1,000 minutes, so older history still comes from GMGN.
> Coin details: launch-curve state, graduated yes/no, creator, social links.
> PumpPortal's free live stream (a third-party feed for pump.fun): new coins appear the second they launch (tested, working). It can also stream every trade on a chosen coin, every trade by a chosen wallet, and graduations. So the terminal can see an explosive wallet buy the moment it happens instead of polling GMGN every few seconds.

## 2. Phase 2 data-source correction (2026-10-20)

> which is best for execution claude even aside from these two

> CLAUDE — PHASE 2 DATA-SOURCE CORRECTION. NO NEW BUILD PHASES.
> We are keeping our existing four-phase JINX architecture and GMGN integration.
> I found a potential FREE source for the raw events you said were blocking Early Momentum.
> TEST PUMPAPI.IO BEFORE ASKING ME TO PURCHASE PUMPPORTAL, HELIUS, YELLOWSTONE OR ANY OTHER DATA SUBSCRIPTION.
> Official documentation:
>
> * https://pumpapi.io/stream
> * https://pumpapi.io/historical-replay
> * https://pumpapi.io/fees
>
> PumpAPI advertises a free WebSocket containing token creation, buys, sells, migrations and liquidity events, plus free historical replay.
> YOUR IMMEDIATE TASK:
>
> 1. Connect to the free PumpAPI WebSocket from the existing jinx-nyc environment.
> 2. Validate transaction signatures, timestamps, token mints, wallet identities, event completeness and real-time delivery.
> 3. Determine whether the provided wallet identity represents the actual fee payer required by CHAD, rather than assuming a trader address is sufficient.
> 4. Test three distinct buyers within thirty seconds against genuine recorded events.
> 5. Test migration continuity across Pump.fun and PumpSwap.
> 6. Replay a bounded sample against the existing Phase 1 engine.
> 7. Measure event latency, connection stability, CPU load and event loss.
> 8. Keep this provider isolated behind a JINX-owned adapter.
> 9. Do not replace GMGN's token intelligence, Smart Money analysis, wallet scoring, or existing order integration.
> 10. Report PASS or FAIL with real evidence.
>
> PumpAPI permits only one stream connection per IP and sends a broad event feed. Filter and normalize efficiently without overwhelming the existing event bus.
> DO NOT replace working code or open a competing order gateway.
> If the free feed supplies the qualifying events correctly, resolve the paid-stream dependency and continue building the CHAD hunting engine.
> If the free feed is inadequate, identify the exact missing data before proposing any paid service.
> Use GMGN's available tools to investigate candidates. Use the free events to hear the market. Use CHAD to decide.
> FOUR PHASES. ONE BRAIN. NO ADDITIONAL MONTHLY INFRASTRUCTURE WITHOUT A PROVEN NEED.
> BUILD JINX'S INTELLIGENCE. ENTER EARLY. EXIT SMARTER.

## 3. Four-phase hunting and exit completeness update (2026-10-21)

> PARTLY. We have the foundation of all five systems in our four-phase plan, but several of the specific behaviors you just listed are NOT explicitly required yet.
>
> And some are important enough that I don't want Claude to overlook them.
>
> The biggest gaps are wallet copyability, migration-specific entry intelligence, automatic wallet demotion, and exits based on actual trading behavior.
> JINX — Four-phase coverage audit
> Feature	Current plan	What needs strengthening
> 1. Wallet selection & copy logic	PARTIAL
>  Phase 2 + 3	Cost-adjusted wallet profitability, holding-time analysis, copyable returns, automatic wallet demotion, tracking copied-wallet sells
> 2. Migration sniper	PARTIAL
>  Phase 2 + 3	Detect actual migration transaction or first liquidity addition, evaluate surrounding sells, holder changes and post-migration liquidity
> 3. Advanced rug filtering	PARTIAL
>  Phase 2	Deployer patterns, early holder concentration, common-funder bundles and liquidity-to-volume analysis
> 4. Intelligent exits	PARTIAL
>  Phase 3	CHAD's collapse/divergence exits are covered; new profit ladders, time stops and tightening trailing stops are not
> 5. Execution & risk	PARTIAL
>  Phase 3 + 4	Conditional priority fees, daily loss limits, tracked-wallet-relative sizing, dedicated wallet and verified execution costs
> PARTIAL means the architecture includes the capability, not that its implementation has been independently verified.
> What CHAD already gives us
> CHAD already specifies intelligent wallet discovery, the 40% historical win-rate promotion condition, wallet-confidence scoring, wallet clustering, early momentum, migration monitoring, Kelly sizing, Confluence Collapse, Negative Divergence partial exits and trailing-stop functionality.
>
> That's the foundation Claude must preserve.
>
> But your new list adds several potentially useful rules that CHAD does not define: prioritizing 1–5-minute winners, dropping wallets after an unspecified loss streak, delaying migration entries by a few slots, predefined 1.5–2x and 3x profit ladders, and hard time stops.
>
> Those are new strategy proposals, not proven CHAD source logic. They belong in research and paper testing first, not silently activated as live trading rules.
> The changes I would make without adding phases
>
> Phase 1 — Foundation
>
> Already accepted. Preserve it. No rebuilding.
>
> Phase 2 — Build the hunting intelligence
>
> Add detailed wallet profitability and holding-time analysis, copyability estimates, wallet sell tracking, performance deterioration evidence, migration transaction detection, early holder and liquidity analysis.
>
> Use GMGN's supported data and existing source code. Keep the CHAD scoring formulas intact.
>
> Phase 3 — Size, fire, exit
>
> Implement CHAD's approved entries and exits, copy-sell handling through GMGN, capital reservation, GMGN priority execution and cost-adjusted accounting.
>
> Evaluate additional profit ladders, time stops and adaptive trailing methods in paper mode before considering them as approved policies.
>
> Phase 4 — Prove the edge
>
> Replay actual market trades and test whether the new wallet-selection and exit variants improve net paper results after fees, latency and slippage, without unacceptable losses.
>
> Codex audits architecture fidelity, test correctness, reconciled fills and the execution boundaries.
>
> Send Claude this addition
> JINX — FOUR-PHASE HUNTING AND EXIT COMPLETENESS UPDATE
> Claude, preserve our existing four phases. Do not restart the project or invent additional phases.
>
> The owner requires explicit coverage of five important trading capabilities:
>
> 1. REALIZED-PROFIT WALLET INTELLIGENCE — PHASE 2
>
> Use GMGN wallet histories and the approved upstream code to calculate or retrieve:
> - Historical realized P&L and win rates.
> - Average profitable and losing hold times.
> - Average realized win and loss multiples.
> - Performance after fees where available.
> - Exposure to tokens that subsequently rugged.
> - Whether wallets sell during deteriorating volume.
> - Wallet trade sizes relative to available liquidity.
> - Estimated copyability after observation and execution delays.
> - Recent loss streaks and suspicious dumping after followed entries.
> Maintain CHAD's specified wallet-confidence structure. Do not replace it with invented weights.
>
> Implement wallet demotion evidence and paper-test possible demotion policies. Do not invent an automatic live-demotion threshold.
>
> 2. MIGRATION INTELLIGENCE — PHASE 2
>
> In addition to GMGN's lifecycle categories, investigate:
> - Actual migration transaction evidence.
> - First verified post-migration liquidity addition.
> - Pre-migration and post-migration liquidity.
> - Large sells around graduation.
> - Developer and concentrated-holder selling.
> - Holder-distribution changes.
> - Available evidence of competing early transactions.
> Use GMGN and the approved event streams.
>
> Record decision-time evidence rather than inventing a migration-buy threshold.
>
> Build a configurable, testable confirmation-delay mechanism for paper experiments, but do not add an arbitrary delay to live strategy policy.
>
> 3. ADVANCED TOKEN RISK INTELLIGENCE — PHASE 2
>
> Include:
> - Deployer launch history.
> - Early holder concentration.
> - Bundle and common-funder evidence.
> - Liquidity relative to recent volume.
> - Insider activity and large-holder distributions.
> - Mandatory CHAD safety verdicts.
> Preserve CHAD's hard vetoes. Don't invent additional live rejection thresholds.
>
> 4. INTELLIGENT EXIT CAPABILITIES — PHASE 3
>
> Implement CHAD's existing Confluence Collapse and score-adaptive Negative Divergence exits exactly.
>
> Integrate supported GMGN copy-sell and protective-order capabilities.
>
> Make the following independently testable research variants:
> - Partial profit ladders.
> - Volume-sensitive trailing exits.
> - Time-based exits.
> - Large tracked-wallet sell response.
> - Priority execution during liquidity deterioration.
> Do not replace CHAD exits or silently activate new profit targets.
>
> 5. EXECUTION AND CAPITAL PROTECTION — PHASE 3
>
> Implement configured portfolio exposure limits, available-balance reservation, actual liquidity-aware sizing, supported GMGN fee and priority mechanisms, and detailed reason-coded trade logs.
>
> Daily loss-stop policies require approved parameters. Do not invent them.
>
> A dedicated limited-capital trading wallet remains an activation requirement, not a reason to block Phase 2 intelligence.
>
> 6. PAPER PROOF — THROUGHOUT PHASES 2–4
>
> For every capability, show actual input data, output behavior, failure cases, decision latency and the corresponding test.
>
> During Phase 3 and Phase 4, compare approved CHAD behavior against experimental alternatives using historical replay and forward paper observations.
>
> Include realistic fees, execution delays, available liquidity and failed exits.
>
> Do not use historical hindsight to select an advantageous sell price.
>
> Do not claim an experimental rule improves profitability until supported by reproducible evidence.
>
> NO FIFTH PHASE. NO NEW BOT RUNTIMES. NO STRATEGY INVENTION.
>
> Update CLAUDE.md and the Phase 2/3 fidelity matrix to account for these requirements.
>
> THE GOAL: FIND WALLETS THAT REALIZE PROFITS, IDENTIFY QUALIFIED EARLY OPPORTUNITIES, FOLLOW EXECUTABLE SIGNALS, AND EXIT INTELLIGENTLY.
>
> HEAR → QUALIFY → SIZE → FIRE → EXIT.
>
> ENTER EARLY. EXIT SMARTER.
>
> The most important addition is copyability. A wallet reporting excellent returns means little if JINX arrives late, receives a worse entry, or cannot reproduce the wallet's profitable sell. That needs to be measured explicitly rather than assumed.
>
> This preserves CHAD as the blueprint while making the new ideas measurable without letting Claude quietly redesign your live strategy.
>
> This is general information only and not financial advice. For personal guidance, please talk to a licensed professional.

## Phase 2 integration order (owner, 2026-10-21, to the momentum/migration session; verbatim)

> JINX — PHASE 2 INTEGRATION ORDER
> Claude, the agent has merged Early Momentum, Graduation and Migration into main at `2da8525`. 522 tests pass.
> STOP ASKING ME TO REDECIDE B-02. WE ALREADY RESOLVED IT.
> The approved Early Momentum policy is:
> * Current bonding curve: 0.5–10 REAL SOL.
> * Historical peak: must never have exceeded 10 REAL SOL.
> * Retire the conflicting 35 SOL entry rule.
> * Apply to compatible SOL-denominated curves only.
> * Unknown or incomplete historical peak: block entry.
> * Migration trading remains a separate capability.
> Implement these rules with boundary tests. Do not substitute virtual reserves for real reserves.
> FINISH THE INTEGRATION
> 1. Wire `GraduationTracker.on_event` and `MigrationTracker.on_event` into `jinx/app.py`.
> 2. Fix the migration deduplication defect. PumpAPI and PumpPortal may report the same migration with different fields. They must enrich ONE authoritative migration record. A partial PumpPortal report must never prevent a later PumpAPI report containing the pool address from confirming migration. Deduplicate effects, not useful information.
> 3. Preserve original provider timestamps and provenance. Do not overwrite authoritative on-chain event times with receipt times.
> 4. Creator purchases: CHAD specifies three distinct fee-paying wallets. A verified creator purchase counts as one distinct buyer under that stated rule; flag creator participation for safety analysis. Do not invent a new creator-exclusion rule.
> 5. Common funders: identify three buyers sharing a verified funder and preserve CHAD's emergency protection. Missing funder data must never be represented as a clean result.
> 6. Near graduation: use verified provider statuses and existing source-derived progress evidence. Do not invent a numerical graduation threshold or 0–20 scoring formula.
> 7. Large sells: record actual sell sizes and wallet identities. Do not invent a threshold or falsely label a sell as dangerous without an approved rule.
> 8. Holder concentration: use verified wallet balances where available. Trade observations alone are not proof of current token ownership.
> 9. Run the complete test suite, real-event replay and paper-mode integration tests.
> 10. Redeploy only the verified paper-mode build to Contabo. Keep live trading OFF.
> YOUR DELIVERY — Show me: the three modules connected to the actual running engine; B-02 implemented and tested; migration deduplication fixed; real detected events flowing through shared JINX state; safety and blocked-trade behavior; full test results; measured processing delays; remaining genuine blockers; independent review verdict.
> Do not call Phase 2 complete merely because the modules exist. Do not start Phase 3 without approval. No additional infrastructure. No new phases. No competing engines. No strategy invention.
