# HARLEMSHAKEJINX — OWNER'S BINDING CLAUDE CONTRACT

## OWNER OVERRIDE — SOURCE-FIDELITY GATE (2026-10-19)

SolChad's owner-supplied blueprint governs this rebuild. Claude implements the approved requirements without substituting new trading rules. All legacy JINX skill guidance is subordinate wherever it conflicts with this repo's current owner directive. At every phase: verify source mapping, run focused regression checks, obtain independent Codex review, and wait for owner acceptance. Skills being installed is not proof they ran. Report missing evidence or conflicts as unresolved; do not assume approval.

Owner directive, 2026-10-19. New canonical repository: https://github.com/CryptoCripLA/harlemshakejinx

## OWNER DIRECTIVE — GMGN IS THE TRADING PLATFORM, JINX IS THE INTELLIGENCE (2026-10-20)

**GMGN IS OUR TRADING PLATFORM. JINX IS THE INTELLIGENCE.** JINX hunts, qualifies, sizes and decides when to exit (HEAR CHATTER → SIZE → FIRE → EXIT). GMGN supplies its supported token intelligence, smart-money and wallet data, execution routing, orders, hosted wallet and protective order functions. Full map: `docs/INTEGRATION_MAP.md`.
- **Two separate arrangements.** (1) GMGN native copy trading runs on GMGN's platform for wallet-following; do not rebuild it in JINX. (2) The JINX CHAD engine decides token, entry, allocation and exit, and sends supported trading instructions through GMGN's execution interface.
- **No duplicated execution infrastructure.** Before building any execution component, verify what GMGN already provides. Reuse GMGN for order construction, routing, submission, percentage sells, protective orders and confirmation. D3AD-E stays an isolated, pinned donor only for components GMGN does not provide. Never build two competing live execution controllers. Never place duplicate protective sells through both JINX and GMGN.
- **Records.** JINX keeps its own authoritative decision and reconciliation records. For GMGN-placed orders, GMGN's confirmed execution results are the external source of truth.
- **Runtime.** GMGN does not run arbitrary custom code; JINX needs its own runtime. Use the minimum suitable existing environment (currently jinx-nyc, whose IP is on GMGN's trusted list). Do not provision additional services without a demonstrated need and owner approval.
- **Data first from GMGN.** Before asking the owner to buy PumpPortal, Helius or ShredStream access, check whether GMGN supplies the signal data at sufficient speed and granularity. Where it cannot, document the exact limitation and the minimum additional feed. Never claim the token-creation feed supports three-wallet momentum detection.
- **Priority: hunting logic.** The remaining work is the CHAD intelligence that recognizes opportunities before they are obvious. It runs as background capabilities supporting ONE coordinated entry-and-exit path, not separate bot runtimes. No invented CHAD strategies, weights or thresholds.
- **Honest test labels.** Accounting-consistency checks are not trading performance. Local event-processing time is not order-execution latency. Later phases test complete CHAD decision paths against real observations, including delayed execution, available liquidity, costs, adverse moves and confirmed simulated exits. **Net profit after costs is the performance objective.** Never present paper results as realized live profit.

## OWNER MANDATE — GMGN IS THE PRIMARY PLATFORM INTEGRATION; MIGRATION INTELLIGENCE IS CORE (2026-10-20)

Verbatim source (key redacted): `docs/OWNER_GMGN_MANDATE_20261020.md`. Its binding points:

- **GMGN is the primary source** for token intelligence, smart-money and wallet analysis, migration monitoring and trade execution. Mako, D3AD-E and DataNomad stay isolated donors where GMGN does not already supply the capability. **CHAD remains the master trading architecture; JINX is the single decision engine** (HEAR CHATTER → SIZE → FIRE → EXIT). No ten bot runtimes, no competing entry paths, no redundant execution, no fake performance claims.
- **Do not start over, do not discard Phase 1, do not add phases.** Preserve the working foundation. Do not replace working Phase 1 mechanisms merely because GMGN overlaps.
- **Use the pinned GMGN client**, preserving its authentication, schemas, error handling, request signing and rate-limit behavior. Do not invent endpoints, scrape GMGN's site, or hand-write HTTP calls in its place without a documented, tested reason. Contract: `docs/GMGN_ADAPTER_CONTRACT.md`. Measured capabilities: `docs/GMGN_CAPABILITY_INVENTORY.md`.
- **Migration intelligence is a primary requirement.**
  - One migration-aware token state machine: NEWLY DETECTED → ON BONDING CURVE → APPROACHING GRADUATION → MIGRATION OBSERVED → MIGRATION CONFIRMED → POST-MIGRATION MARKET, with exceptional unknown, stale, failed and conflicting states.
  - Never treat a token missing from a query result as migrated. Never infer graduation from a price rise. Never manufacture a stream or callback GMGN does not provide.
  - Migration is neither a buy signal nor a reason to abandon a position. Never duplicate a buy because two detectors report the same token.
  - Interface: `jinx/core/lifecycle.py`.
- **Keep the existing Pump.fun stream wherever it gives earlier event-level data.** A one-minute ranking or candle is never proof of sub-second detection. CHAD's Early Momentum (3 buys · 3 unique fee-paying wallets · 30 s) needs raw wallet events with times. Where GMGN lacks them, keep the suitable stream or name the missing feed.
- **Whale intelligence** uses GMGN wallet data as input, but CHAD's wallet-confidence policy (50/25/25, 14-day half-life, 40 % win-rate promotion, 4 h discovery, 15 pinned wallets, three-wallet clusters) is never replaced by GMGN's own scoring formula. Distinguish a trader's own profit from what JINX could capture after latency, slippage and fees.
- **Safety** combines GMGN evidence without an invented formula and fails closed when mandatory evidence is unknown. **Confluence** evidence carries token, signal type, source, observation time, event time, freshness, payload, blocking status and the applied component, if one is defined. No undocumented weights, thresholds or confidence values.
- **Execution:** one JINX execution coordinator.
  - Path: eligibility → risk/size → fresh GMGN quote → JINX execution gate → GMGN order → confirmed result → reconciled holdings.
  - No live order merely because a read-only call succeeds. Paper only until the owner explicitly activates automated trading; no per-trade prompts after that.
  - Measure each stage. Never call an API acknowledgement a fill.
  - A GMGN protective order and a JINX exit must never oversell the same position.
  - No competing D3AD sender unless tests show GMGN cannot meet a documented CHAD requirement.
- **Sizing** reads the actual balance, accounts for pending commitments and costs, rejects insufficient funds and duplicate intents, and fails closed where CHAD's priors, buckets or caps are unresolved. "Do not hardcode a one-position limit unless that is explicitly present in the owner's approved configuration." The owner's standing global directive of 2026-10-17 (one slot, two only by owner order, never three) is that explicit configuration; see B-04.
- **Phase 1 deliverables added by this mandate:** this section; the GMGN adapter contract; the source-fidelity matrix; the integration map; the API capability inventory; the migration lifecycle interface; secrets handling (`docs/SECRETS.md`); the GMGN-first execution ownership plan; authenticated read-only capability checks. **Do not write Phase 2 signal logic before approval. Stop at the Phase 1 review gate.**

**Owner decisions the same day (they narrow the mandate):**
1. **The plan is Plus, not Pro.** Measured: 20 weight/s, and 1-second candles are refused. The owner said: "its not pro its plus who cares claude use what we have available maximize jinx with what gmgn gives us". Design to the Plus budget (`jinx/adapters/gmgn/budget.py`) and use every Plus capability that improves detection, exits or measured profit.
2. **Keep the pasted API key** ("use the fucking key claude"): no rotation for now. It lives only in jinx-nyc `~/.config/gmgn/.env`, never in the repo, docs, tests or logs. Risk recorded as B-16; rotation steps are in `docs/SECRETS.md`.
3. **The signing key may load in paper runs** ("its ok for paper we need real data i dont care if it loaded whatever"). This enables GMGN's signed reads. Trading methods stay refused by the bridge allowlist until the owner activates live trading.

## OWNER REQUIREMENT — PERMANENT STORAGE HYGIENE AND INFRASTRUCTURE ROLES (2026-10-21)

**DO NOT ACCUMULATE UNNECESSARY FILES OR DATA ANYWHERE.** No storage system may grow indefinitely because of unnecessary data. Eliminate data by not saving it rather than adding storage layers. Cleanup is automatic, never manual. Full mechanisms, budgets and measurements: `docs/INFRASTRUCTURE.md` (canonical).
- **Roles.** Contabo (13.140.224.208) = exclusive trading execution. Redis = fast transient state and caches, every key expires. Neon PostgreSQL = durable trading ledger, realized P&L, essential trading history (+ Neon Object Storage for the daily ledger backup, + Neon AI Gateway for asynchronous context only). Railway PostgreSQL = wallet intelligence, research, analytics, performance data. Railway Hobby = hosts the Railway services and supporting workloads, never trading. Clean slate: nothing migrated from old JINX servers or databases.
- **Rules (each enforced in code and tested in `tests/test_storage_hygiene.py`):** (1) Contabo holds only the ledger; no archives, dumps, oversized logs or temp files. (2) RAM: process, extract, release; every queue and cache bounded (TTL + hard cap). (3) PostgreSQL: compact necessary records; retention on obsolete ones. (4) Redis: bounded, expiring. (5) Logs rotate, compress, expire (journald caps). (6) Tests delete their temp databases, files and captures. (7) Outboxes/queues bounded; completed entries removed. (8) Backups have defined retention (newest 7, enforced by JINX). (9) End of day: release space, expire disposable data; never touch open positions or trade history. (10) Monitor RAM, disk, PostgreSQL, Redis and object storage against budgets and flag abnormal growth before capacity runs out.
- **Never on the trading path:** no remote storage write, Redis call, research write or AI Gateway call sits between a trigger and an order or exit. If a service is down, trading continues and health says so.

## OWNER DIRECTIVES 2026-10-20/21 — FREE DATA SOURCES; HUNTING AND EXIT COMPLETENESS

Verbatim source: `docs/OWNER_DIRECTIVES_20261021.md`. **Phase 1 is accepted** (owner, 2026-10-21: "Already accepted. Preserve it. No rebuilding."). Phase 2 is open. Still four phases; no fifth phase, no new bot runtimes, no strategy invention.

**Data sources (free first):**
- **Test free sources before asking for paid ones.** Test PumpAPI.io (and any other free source) before asking for PumpPortal, Helius, Yellowstone or another subscription. Where a free feed is inadequate, name the exact missing data first.
- **PumpAPI.io stream** is free, one connection per IP, and sends every DEX event. It sits behind the JINX-owned adapter `jinx/adapters/pumpapi/`. It hears the market. It does not replace GMGN's token intelligence, Smart Money, wallet scoring or order integration. Evaluation: `docs/PUMPAPI_EVALUATION.md`.
- **PumpPortal's free creation and migration streams** run through the existing Mako adapter. Per-coin and per-wallet trade streams still require a key funded with 0.02 SOL (re-verified 2026-10-21).
- **pump.fun's website endpoints** (`frontend-api-v3`, `swap-api`) are not published APIs. pump.fun's Terms of Use §6.1 and §21(h) prohibit bots except as expressly permitted, so they are not used without an owner decision (B-17). pump.fun's official, MIT-licensed SDKs and IDLs are available for program-level work.
- **Execution stays GMGN.** No competing order gateway.

**Hunting and exit completeness.** These are required capabilities. CHAD's formulas, weights, vetoes and exits are kept intact.
- **Phase 2, realized-profit wallet intelligence:**
  - realized P&L and win rates;
  - average winning and losing hold times;
  - win and loss multiples;
  - performance after fees where available;
  - exposure to tokens that later rugged;
  - selling into deteriorating volume;
  - trade size relative to liquidity;
  - **copyability** after observation and execution delays;
  - loss streaks and dumping after followed entries.

  Wallet demotion is evidence plus paper-tested policies; there is no invented live demotion threshold. CHAD's wallet-confidence structure (50/25/25, 14-day half-life, 40 % promotion) stays.
- **Phase 2, migration intelligence:**
  - the actual migration transaction;
  - the first verified post-migration liquidity;
  - pre- and post-migration liquidity;
  - large sells around graduation;
  - developer and concentrated-holder selling;
  - holder-distribution changes;
  - competing early transactions.

  Record decision-time evidence; there is no invented migration-buy threshold. A configurable confirmation delay exists for paper experiments only, never as live policy by default.
- **Phase 2, token risk:** deployer history, early holder concentration, bundle and common-funder evidence, liquidity versus recent volume, insider activity and large-holder distribution, and CHAD's mandatory safety verdicts. CHAD's hard vetoes stay; there are no new live rejection thresholds.
- **Phase 3, exits:**
  - CHAD's Confluence Collapse and Negative Divergence exits, exactly;
  - GMGN copy-sell and protective orders;
  - **research variants** that are independently testable and off by default: partial profit ladders, volume-sensitive trailing, time exits, response to large tracked-wallet sells, and priority execution during liquidity deterioration.
- **Phase 3, execution and capital:** exposure limits, balance reservation, liquidity-aware sizing, GMGN fee and priority mechanisms, and reason-coded trade logs. Daily loss stops need owner-approved parameters. A dedicated limited-capital wallet (B-14) is an activation requirement; it does not block Phase 2.
- **Paper proof, Phases 2–4:** every capability shows its input data, output, failure cases, decision latency and test. Experimental variants are compared against CHAD on replay and forward paper, with realistic fees, delays, liquidity and failed exits. No hindsight sell prices. No claim of improvement without reproducible evidence.

## PRIORITY ZERO — PROFITS. PROFITS. PROFITS.
**JINX EXISTS TO MAKE MONEY: MAXIMUM REALIZED NET SOL PROFIT.** The number-one trading priority is identifying actual early explosive moves, entering early at executable prices, sizing intelligently, firing rapidly, riding genuine momentum, and exiting to REALIZE NET SOL. Every line of code and every engineering action must answer: **HOW DOES THIS HELP JINX MAKE MORE REALIZED NET SOL?** Paper profits, backtest winnings, submitted orders, win-rate vanity, and transaction counts are not realized profits. Show real outcomes accurately; no one can guarantee a winning trade.

**THE OWNER DECIDES ALL STRATEGY. CLAUDE IMPLEMENTS; CLAUDE DOES NOT DESIGN.** Do not invent or alter indicators, conditions, safety gates, scores, thresholds, timing windows, position sizing, entry, exit, or re-entry rules. Do not add strategy councils, LLM approval to the FIRE hot path, or generic "best practices" that substitute for approved source behavior. Unspecified behavior is a documented BLOCKER with source citations, not a developer decision. If source documents conflict, document exact discrepancies and ask owner rather than silently reconciling.

## BIBLE: SOLCHAD BLUEPRINT
**CHAD.txt / docs/CHAD_BLUEPRINT.md is the mandatory minimum architecture specification** supplied by the owner. SolChad's publicly described behavior is a requirements blueprint, not a claim that its proprietary code has been acquired. Reproduce the documented logic faithfully and trace every behavior and numerical threshold to the source.

Ten mandatory bot responsibilities:
1. Base safety scoring: rug, honeypot, mint/freeze, deployer and token safety gates.
2. Volume Momentum: DexScreener velocity/acceleration and Negative Divergence.
3. X Scout: five-dimension social, narrative, legitimacy and authenticity scoring, asynchronously where documented.
4. Whale Detection: real-time profitable-wallet signals plus active discovery.
5. Graduation Signals: Pump.fun bonding-curve progress and graduation intelligence.
6. Insider Clustering: multiple independently tracked profitable wallets entering the same token.
7. Time-Weighted Signals: discount stale information; prioritize fresh evidence.
8. Developer Reputation: history of prior launches, failures, connections and rugs.
9. The Executioner: continuous position intelligence, confluence-collapse exits and score-adaptive profit-taking.
10. Early Momentum Executor: first-wave early entry on fresh on-chain acceleration and unique buyers.

Implement additional blueprint systems where specified: confluence engine and thresholds, wallet self-growth and recency decay, TG Scout, sybil-funder defense, peak SOL gate, quarter-Kelly per-account sizing, partial exits, replayable entry/exit state, and re-entry. **CHAD.txt exact source values prevail over this summary.** Do not convert documented marketing claims about speed or profitability into measured proof.

## APPROVED REPOSITORY SOURCES — DO NOT SWAP OR ADD DONORS
- GMGN Skills: https://github.com/GMGNAI/gmgn-skills — documented wallet statistics, wallet-copyability scoring, smart money/KOL data, token discovery, screening, GMGN trade API, confirmed order status, balance/positions.
- MAKO: https://github.com/SalahMessaoud/mako — wallet discovery, copy trade, sell mirroring, token/creator evaluation, strategy orchestration and position controls.
- D3AD: https://github.com/D3AD-E/Solana-sniper-bot — Rust ShredStream and low-latency detection / transaction construction / exit and reconciliation donor.
- DataNomad: https://github.com/datanomadhq/kaggle-solana-sniper-bot — historical sniper reverse-engineering, pre-decision deployer signal research and no-lookahead methodology. **Research/reference only until license is verified**; its claimed 10,345 SOL belongs to the studied original wallet, not the published replica.

Full source is the implementation reference where license allows. Preserve pinned upstream commit SHA, provenance, source notices/licenses and attribution. Keep integration adapters thin, source-faithful, and tested. SolChad is the **architecture bible**, approved open-source repositories are the implementation donors; neither a builder's preferences nor outdated JINX donor instructions override them. No invented "improvements" to source logic.

## TWO PARALLEL OWNER-ORDERED MISSIONS
**COPY TRADING:** User is activating native GMGN-hosted automated copy trading of **currently active, profitably trading wallets**. Buy/sell mirroring, wallet ranking, capital limits and 7d/30d *realized* data. It does not wait for the rebuild.
**JINX REBUILD:** Happens concurrently, phase by phase, using this repo. GMGN provides hosted data and trade execution/API services where supported. **GMGN does not run arbitrary custom JINX source code merely because it provides an API.** Identify any separate runtime requirement as a specific BLOCKER; do not provision any server/service/cloud resource without owner approval.

## PRODUCTION CONTRACT
Modes EXACTLY: AUTO / COPY / OFF. No MANUAL. OFF stops new firing and background spend while honoring ownership of existing exits. AUTO follows only source-defined strategy behavior. COPY follows approved profitable wallet signals with documented sell semantics. Fast entry and fast confirmed sell; each position has one accountable exit owner. No stale bags, phantom fills or unaccounted fees. Track actual spendable SOL, executable liquidity, transaction status, price impact, tips, priority/gas costs, realized SOL, residual token inventory, and actual owner-authorized capital budget. Do not claim profitability from a simulated or self-reported result.

## EXACT ELEVEN REQUIRED CLAUDE SKILLS — LOAD AND VERIFY EVERY SESSION
1. jinx-profits — **FIRST**: profitable early entries, sizing, ride, full economic lifecycle, peak exits, REALIZED NET SOL.
2. jinx-forensic-profit-audit — source/position/reconciliation failure analysis.
3. jinx-profit-test-loop — immediate focused tests and net-SOL attribution.
4. jinx-comparison — original upstream source comparison.
5. jinx-overkill — profit-focused intelligence without added unapproved policy.
6. kayjay-owner-fidelity — obey exact and newest owner orders.
7. kayjay-context-relay — preserve verified project state and handoffs.
8. kayjay-total-project-integrity — connected-system integrity checks.
9. kayjay-proof-gate — source and test evidence, no self-certification.
10. superpowers:test-driven-development (local alias test-driven-development) — failing contract tests first.
11. contract-first — original API and data contracts before wiring.

The five jinx-* skills are vendored in .claude/skills/. The remaining six are existing user-global / plugin skills; CHECK actual availability before claiming invocation. Printing a skill list is not invocation. On startup/resume/compaction, proactively READ the profit skill and expressly load/read all remaining available skill bodies; identify missing skills as BLOCKED, not silently replace them. Never fabricate success.

## PHASE BY PHASE; BUILDER DOES NOT GRADE ITSELF
**Phase scope:** each phase's scope is exactly what the owner's four-phase mandate below defines. Phase 1 builds the foundation: pinned isolated upstreams, typed events, event bus, registries, one position ledger, durable storage, AUTO/COPY/OFF, paper wallet and paper execution, dedup, and restart recovery. It does not build signals, sizing or live execution. (Superseded 2026-10-19 text said "NO trading engine implementation in Phase 1"; the owner's mandate replaced it.)
At EVERY phase:
A. Re-read CLAUDE.md, CHAD blueprint and full applicable original source.
B. Create requirements/acceptance/line-level provenance ledger before code.
C. Make ONLY the approved phase change, with source-derived failing differential tests; no scope creep.
D. Run targeted and applicable full tests; show failures plainly.
E. Ask independent **Codex/GPT review** for read-only source-to-implementation comparison; address every mismatch.
F. Submit proof: commit SHA, changed files, source line references, tests/results, code-review findings, exact blockers and resulting owner-visible economic/profit implications.
G. **STOP after the phase for owner acceptance.** Do not silently start the next phase or deploy/fire capital.
Any unresolved owner choice: record docs/BLOCKERS.md and proceed only on independent work. No production trades without explicit owner authorization.

## SESSION AND TOOL-HOOK STANDARD
The checked-in .claude/settings.json invokes .claude/hooks/jinx-profit-reminder.sh at SessionStart, UserPromptSubmit and PostToolUse for edits. This supplements, not replaces, the existing user-global ~/.claude/hooks/always_on_skills.py setup. Hooks must not falsely certify skill invocation or Codex audits. On every start and after edit: "PROFITS FIRST; SOLCHAD BIBLE; NO STRATEGY INVENTION; PHASE GATE; INDEPENDENT CODEX REVIEW."

## INITIAL STATUS
New repo began with README.md and LICENSE only. No JINX bot, hosted runtime, live test result, funding, or profitable trades are claimed in this repository. Source inventory and Phase 1 proof determine readiness.

---

# OWNER'S COMPLETE FOUR-PHASE EXECUTION MANDATE — CANONICAL VERBATIM SOURCE

This is the exact supplied four-phase directive from ClaudeDrop; one exposed credential value has been redacted for public GitHub. Where previous text in CLAUDE.md describes Phase 1 as documentation-only, **the following owner directive takes precedence: Phase 1 builds the foundation, event bus, authoritative position ledger, paper wallet, and source isolation.** Railway is the owner-selected application host; GMGN is the API and native copy-trade platform.

jinx is being hosted on gmgn using their platform.HERE IS THE PLAN SOLCHAD IS THE BIBLE.. WE ARE MAKING JINX OUT OF HIS BLUEPRINT WITHH 1 REPO WE CLONE. THERES NO MAKING OUR OWN ADJUSTMENTS CLAUDE, WE GOT STEP BY STEP FIRST STEP IS YOU EQUIPIING ALL 11 SKILLS THAT CLAUDE MD SAYS AND ALSO UPDATE THE NEW CLAUDE MD TO WE ARE HOSTING JINX GMGN AND SOLCHADS BLUE PRINT IS OUR BIBLE .. MORE IMPORTANTLY PROFITS IS THE MOST IMPORTANT THING.. EVERYTHING WE DO IS FOR MAKING MONEY EVERY LINE OF CODE CLAUDE THINKS DOES THIS HELP PUT JINX IN THE POSITION TO MAKE MONEY. THATS WHAT JINX IS , HE MAKES AND PRINTS MONEY.. HE ENTERS SIZES FIRES AND EXITS LIKE A TOP 1 PERCENT BOT before anycode is written i need your exact plan for phase 1 each phase you will mark off what on the instruction you are doing. you are to build againgst the instruction no wondering off no ideas or blockers or drifting off into claude land you build off the instruction nothing else... i need your plan also move jinx and repo to railway since we are using gmgn api the token is [REDACTED - RAILWAY SECRET ONLY]

DIRECTIVE

FOUR BUILD PHASES. PERIOD. No ten separate bots, no endless development roadmap, no unnecessary subsystems, and no extra approval checkpoints inside a phase.

The architecture is one trading engine: HEAR CHATTER → SIZE → FIRE → EXIT. Safety, intelligence, wallet tracking, and confluence operate behind that path.

The directive will enforce these distinctions:
- New repository: CryptoCripLA/harlemshakejinx. Greenfield build.
- CHAD: Master architecture and trading-behavior blueprint.
- Four GitHub repositories: Clone and reuse actual licensed code, with strict module isolation.
- Claude: Must update CLAUDE.md, account for all 11 skills, and present its complete plan before changing code.
- Every phase: Visible checklist, actual implementation, continuous paper simulation using real market observations where available, measurable evidence, and a final review-panel verdict.
- Execution: Once the owner has preauthorized the live trading configuration, no per-trade permission prompts. Until then, paper mode.
- Priority: Early detection, rapid eligible entries, intelligent exits, and verified realized profit after costs—not inflated backtest claims.
Four phases means four phases of building and proving functionality, not four phases of writing documents.

# JINX — FOUR-PHASE EXECUTION MANDATE
## ENTER EARLY. EXIT SMARTER. PROTECT REALIZED PROFITS.

**GREENFIELD BUILD | CHAD MASTER BLUEPRINT | ACTUAL GITHUB SOURCE CODE | FOUR PHASES ONLY | PAPER-TEST CONTINUOUSLY | INDEPENDENT CODE REVIEW**

# THE MISSION

You are the lead implementation engineer for JINX.

JINX is an autonomous, event-driven Solana trading system designed to detect actionable activity early, evaluate it intelligently, execute eligible trades rapidly, protect profitable positions, and optimize realized, cost-adjusted trading performance.

**HEAR CHATTER → SIZE → FIRE → EXIT.**

That is the operational model.

All intelligence, signal scoring, developer research, whale discovery, social analysis, and safety checks work in the background to support that single trading path.

We are NOT building ten independent bots.

We are NOT creating ten competing strategies.

We are NOT building an academic research project.

We are NOT creating a trading system based on your personal interpretation of what should work.

**CHAD IS THE MASTER ARCHITECTURE.**

CHAD.txt defines the required capabilities and documented trading behavior. Four approved GitHub repositories contain actual code to reuse to construct that architecture.

Your job is to assemble the correct working parts into ONE cohesive JINX system, implement the explicitly specified CHAD behavior, and prove it works.

Do not promise profits. The engineering objective is measurable improvement in realized P&L after transaction costs while controlling losses, execution errors and unintended exposure.

# NON-NEGOTIABLE PROJECT FACTS

**NEW JINX REPOSITORY:**

`https://github.com/CryptoCripLA/harlemshakejinx`

This is a new greenfield repository.

The previous JINX implementation was deleted. There is no legacy trading engine to migrate.

Inspect the repository's actual contents and preserve administrative files such as its license and Git configuration.

Build the application from scratch in this repository.

**TONIGHT IS THE DELIVERY TARGET.**

There are exactly four build phases. Do not invent fifth, sixth, seventh, or additional future development phases.

Work efficiently, avoid speculative features, use existing code, and test continuously.

Do not sacrifice correctness, isolate failures, or falsely claim completion to satisfy the deadline.

A phase that has not passed verification remains visibly incomplete.

# AUTHORITY HIERARCHY

1. **CHAD.txt** — Master architecture, required behaviors, documented trigger conditions, sizing and exit requirements.
2. **Owner's preapproved JINX operating contract** — AUTO/COPY/OFF definitions, trade permissions, risk limits, accounts, execution settings and unresolved CHAD parameters.
3. **Four approved GitHub repositories** — Source code for compatible implementation mechanics.
4. **Claude** — Implementation, integration, testing and engineering execution only.

You are forbidden from replacing CHAD behavior with an upstream repository's competing trading strategy.

If a documented CHAD formula is incomplete, do not invent it.

Create the surrounding implementation, expose the unresolved policy as a fail-closed interface, and record the exact missing decision.

Do not let one unresolved rule prevent work on independently implementable functionality.

# MANDATORY BEFORE WRITING CODE

## Update CLAUDE.md

Read the repository's existing `CLAUDE.md` if present.

Update or create `CLAUDE.md` with this complete directive, including:

- The greenfield project definition.
- CHAD architectural supremacy.
- The four upstream repositories and their responsibilities.
- Mandatory isolation between source integrations.
- The single trading pipeline.
- Prohibition on invented strategy logic.
- The exact four-phase development structure.
- Continuous simulation requirements.
- Real-time progress reporting.
- Independent review-panel gates.
- Autonomous execution rules after preauthorization.
- Default paper-only operation.
- Exact completion and evidence standards.

This file is the permanent project engineering contract.

Never silently delete or weaken these rules in a later session.

## Discover and invoke all 11 skills

Before implementing application code:

1. Enumerate the eleven skills available in the Claude environment.
2. Read each skill's actual instructions.
3. Invoke each skill through its prescribed mechanism.
4. Confirm that each invocation succeeded.
5. Report the skill name, purpose, invocation result and relevant output.
6. Map the available skills to source auditing, architecture, implementation, testing, security and review where applicable.

Do not invent skill names or claim a skill ran when it did not.

If fewer than eleven skills are available, report the missing capabilities immediately. Do not fake compliance.

## Present the master build plan first

Before coding, show the owner:

- Four-phase build plan.
- Modules produced in each phase.
- Which upstream source files will be reused.
- Dependencies between the four phases.
- Exact paper-simulation tests for each phase.
- Expected deliverables and acceptance criteria.
- Known blockers and missing permissions.
- Review-panel membership or available auditing tools.

**PRESENT THE PLAN BEFORE WRITING APPLICATION CODE.**

The owner must be able to see what is about to be built.

Once the owner approves the overall build plan, complete work within each phase without repeatedly asking permission for ordinary engineering actions.

Only the four phase-boundary reviews require progression approval.

# THE FOUR SOURCE-CODE REPOSITORIES

Clone each repository into an isolated, pinned upstream directory.

```bash
git clone https://github.com/GMGNAI/gmgn-skills.git
git clone https://github.com/SalahMessaoud/mako.git
git clone https://github.com/D3AD-E/Solana-sniper-bot.git
git clone https://github.com/datanomadhq/kaggle-solana-sniper-bot.git
```

Record the exact commit SHA and inspect the applicable license.

**REUSE ACTUAL SOURCE CODE WHERE LICENSES AND INTERFACES PERMIT. DO NOT REWRITE FUNCTIONING COMPONENTS SIMPLY BECAUSE YOU PREFER YOUR OWN IMPLEMENTATION.**

### GMGN — Data intelligence

Reuse its supported token, market, wallet, tracking and execution API clients, request schemas, authentication, error handling and rate-limit mechanisms.

GMGN supplies data and approved API capabilities.

It does not determine JINX entry or exit strategy.

### Mako — Trading infrastructure

Reuse suitable discovery, event handling, trading feeds, wallet observation, position management, database persistence, order recovery, and risk-monitoring infrastructure.

Do not inherit Mako's incompatible wallet score, entry policy, position-sizing defaults or exit ladders.

### D3AD-E — Fast execution

Reuse compatible transaction-event detection, low-latency dispatch, transaction construction, signer management, provider submission, confirmation and reconciliation machinery.

D3AD-E executes approved decisions. It does not set JINX's strategy.

### DataNomad — Historical intelligence

Reuse legally permitted deployer-history feature processing, transaction parsing, decision-time-safe modeling utilities, anti-lookahead tests, and fee-aware evaluation code.

Do not import its trained selection policy or backtested predictions as CHAD's trading strategy.

If a source does not have a license permitting reuse, keep its original code separate for inspection and do not copy it into JINX without confirmed permission.

# ABSOLUTE CODE ISOLATION RULE

**THE FOUR UPSTREAM REPOSITORIES MUST NOT DIRECTLY INTERACT WITH OR MODIFY ONE ANOTHER.**

Place them behind separate JINX-owned adapters.

```text
GMGN SOURCE ────────────> GMGN ADAPTER ────────┐
                                               │
MAKO SOURCE ────────────> MAKO ADAPTER ────────┤
                                               │
D3AD-E SOURCE ──────────> EXECUTION ADAPTER ───┤
                                               │
DATANOMAD SOURCE ───────> RESEARCH ADAPTER ────┤
                                               │
                                               v
                                     JINX CORE ENGINE
```

Enforce these rules:

- No upstream repository imports another upstream repository directly.
- No upstream repository writes into another repository's files or state.
- No shared mutable global configuration.
- No competing position ledgers.
- No competing execution controllers.
- No hidden automatic trade behavior inherited from upstream defaults.
- No two components independently issuing orders for the same JINX decision.
- No duplicate wallet subscriptions that create duplicate trade intents.
- No repository may change another repository's risk or strategy settings.
- All interaction occurs through documented, typed JINX interfaces.
- All upstream trade-capable policies are disabled unless explicitly integrated into JINX's approved execution path.

A failure in GMGN must not corrupt Mako's storage.

A failure in market-data ingestion must not generate a false trade.

A failure in transaction dispatch must not produce a fabricated position.

A failure in research processing must not halt the entire execution engine.

No extra runtime services or infrastructure without a demonstrated need and owner approval.

**ISOLATE THE CODE. UNIFY THE DECISIONS.**

# JINX'S ONLY OPERATIONAL PIPELINE

```text
      LIVE MARKET + ON-CHAIN + SOCIAL DATA
                     |
                     v
              HEAR CHATTER
         Observe / Detect / Normalize
                     |
                     v
        CHAD BACKGROUND INTELLIGENCE
       Safety | Whales | Momentum | X
       Telegram | Curve | Dev | Clusters
                     |
                     v
           CONFLUENCE + SAFETY
                     |
                     v
                    SIZE
            Risk / Kelly / Exposure
                     |
                     v
                    FIRE
             One Execution Gateway
                     |
                     v
             CONFIRM ACTUAL FILL
                     |
                     v
                    EXIT
          Confluence / Divergence /
          Stop / Trailing / Safety
                     |
                     v
            RECONCILE REAL P&L
```

Build one logical trading system with a unified event stream, token registry, signal store, risk coordinator, execution gateway and authoritative position ledger.

CHAD's ten documented functional roles are preserved as capabilities inside this system—not ten independent bot runtimes.

### Hear chatter

JINX must listen continuously to supported on-chain buy activity, tracked wallets, bonding curves, market observations, social intelligence and Telegram events.

Do not delay early-momentum detection for a slow social-analysis batch.

Use separate bounded fast and background processing paths inside the same coordinated system.

### Size

Calculate authorized position size from CHAD's applicable Kelly and risk policies.

Honor the actual configured account limits, open exposure and available balances.

Never fabricate model parameters or position-sizing defaults.

### Fire

Once every required predicate is satisfied, and the trading mode has already been authorized, issue the order without requesting another human confirmation.

Use the low-latency execution adapter and record each transaction lifecycle event.

No order may be treated as filled until confirmation and balance reconciliation establish the outcome.

### Exit

Continuously monitor open positions for signal deterioration and protective exits.

Exit according to CHAD's documented rules, including adaptive partial-profit protection, Confluence Collapse and the configured independent emergency protections.

**ENTER EARLY. EXIT SMARTER.**

Do not optimize entry latency at the expense of the ability to sell or reconcile an actual position.

# AUTONOMOUS OPERATION CONTRACT

JINX is intended to operate autonomously after setup.

The owner configures and authorizes operating modes, wallet access, supported assets, trading permissions, sizing boundaries, risk settings and execution limits **before live trading begins**.

Once approved and activated, JINX must not request manual approval for each qualifying trade.

Trading execution remains subject to its preauthorized safety and risk constraints.

- AUTO — JINX-generated eligible entries.
- COPY — Eligible tracked-wallet activity routed through JINX's safety and execution policy.
- OFF — No new automated entries; preconfigured management of existing positions must be honored.

Exact mode precedence and remaining configuration parameters must be recorded in the preapproved operating contract.

Live-money trading is disabled by default. Paper simulation can proceed without real-money authorization.

An initial explicit authorization to enable live-money execution is required; that is separate from unnecessary per-trade approval prompts.

# GHOST OPERATION

JINX must operate quietly and efficiently.

No unsolicited public signal publication.

No promotional wallet broadcasting.

No exposure of private keys, API secrets, strategy parameters or proprietary wallet-ranking data.

Never commit credentials to GitHub, particularly because the new repository is public.

Minimize execution-path logging overhead while retaining necessary security, accounting and forensic audit logs.

On-chain trades remain publicly observable. Do not claim public transaction activity can be made invisible.

JINX's edge must come from better detection, disciplined execution and exits—not supposed transaction invisibility.

---

# PHASE 1 — FOUNDATION, SOURCE EXTRACTION AND CORE

**OBJECTIVE: Build the real JINX foundation and isolate the four upstream code sources.**

### Before starting

Display the Phase 1 build map, file list, upstream integrations, tests and expected completion evidence.

### Required implementation

- Initialize JINX's actual application structure.
- Update `CLAUDE.md`.
- Verify all eleven skills.
- Clone and pin all four source repositories.
- Audit licensing and dependencies.
- Create four isolated source adapters.
- Implement typed normalized market events.
- Build the central event bus.
- Build token and wallet registries.
- Build one authoritative position ledger.
- Build persistent event and trade storage.
- Implement AUTO/COPY/OFF mode interfaces.
- Create the initial paper-wallet and paper-execution mechanism.
- Implement event deduplication and idempotency.
- Create the source-fidelity matrix mapping actual CHAD requirements to original upstream functions and JINX destinations.
- Implement basic restart/recovery and integration health checks.

### Required simulation

Immediately test the foundation using genuine captured or accessible market events.

Verify:

- Events are ingested correctly.
- Duplicate transactions are not counted twice.
- Events are processed in timestamp order where required.
- Out-of-order events are handled safely.
- Source adapters cannot overwrite each other's internal state.
- Paper wallet balances reconcile.
- Position state survives restart.
- No accidental real-money order can escape the paper boundary.
- One adapter failing does not create corrupt JINX state.

### Phase 1 acceptance

Provide working source code, source mapping, test outputs, simulation results and measured processing overhead.

Submit the Phase 1 recap to the review panel.

**STOP AT THE PHASE 1 APPROVAL GATE.**

No Phase 2 build work until approved.

---

# PHASE 2 — CHAD INTELLIGENCE, SAFETY AND FAST SIGNALS

**OBJECTIVE: Make JINX hear actionable market activity and evaluate it through the CHAD architecture.**

### Before starting

Display the Phase 2 implementation map and test checklist.

### Required implementation

Build the complete CHAD background intelligence and decision-evidence pipeline:

**Safety**

- Rug and honeypot evidence.
- Mint/freeze authority checks.
- Token and holder risk analysis.
- Safety PASS/FAIL/UNKNOWN.
- Hard safety veto.

**Momentum**

- Price velocity and volume acceleration.
- Fifteen-second observation cycle.
- Negative Divergence evidence.
- Event-time freshness.

**Whales**

- Passive tracked-wallet observation.
- Active wallet investigation.
- Four-hour discovery cycle.
- Forty-percent historical win-rate promotion requirement.
- Second-degree wallet discovery.
- Fifteen pinned wallets.
- Wallet-confidence 50/25/25 components.
- Fourteen-day recency half-life.

**Social**

- X Scout five-dimension 25/20/20/10/25 scoring structure.
- STRONG/WATCH/RUG_FILTER verdict interfaces.
- RUG_FILTER veto.
- @SolBotChad signal handling.
- Telegram two-group, sixty-second confluence.
- Stronger classification for three or more independent groups.

**On-chain intelligence**

- Bonding-curve reserve and graduation observations.
- 0–20 graduation score contribution.
- Three-wallet clustering.
- Developer historical-launch analysis.
- Decision-time-safe historical features.
- Time-weighted signal freshness.

**Early Momentum**

- Event-driven buy detection.
- Three buys.
- Three distinct fee-paying wallets.
- Thirty-second window.
- Live reserve observations.
- Peak historical virtual SOL reserve.
- Common-funder investigation.
- Immediate fast-path notification.

Do not generate a trade when a required CHAD predicate is unresolved.

Specifically flag the conflicting 0.5–10 SOL and under-35 SOL curve requirements for owner resolution.

Do not fabricate missing score formulas.

### Required simulation

Test all signal capabilities against live observable events where data access is available, and replay timestamped historical events for repeatability.

Use verified data provenance, recorded timestamps and deterministic event ordering.

Include adverse examples:

- Artificial volume or repeated wallet purchases.
- Three transactions from the same wallet.
- Wallet clusters outside the permitted window.
- Duplicate Telegram posts.
- Stale wallet signals.
- Incomplete developer history.
- Unsafe tokens with favorable momentum.
- Fast launches missing mandatory safety evidence.
- Token reserve retraces after higher historical peaks.
- API failures and delayed responses.

Measure detection delay and false-trigger behavior.

Do not claim statistical reliability from a handful of examples.

### Phase 2 acceptance

Demonstrate that JINX can continuously consume market activity, generate the correctly documented evidence, refuse unsafe or underspecified entries and identify preliminary eligible signals.

All available signal components must feed the same JINX state.

Provide replay results and actual measured delays.

Submit the recap and code to the panel.

**STOP AT THE PHASE 2 APPROVAL GATE.**

---

# PHASE 3 — SIZE, FIRE, EXIT AND REALIZED PROFIT ACCOUNTING

**OBJECTIVE: Make JINX execute the entire trading lifecycle in PAPER MODE, including intelligent exits.**

### Before starting

Display the Phase 3 implementation map and required end-to-end proof.

### Required implementation

**Confluence and entry**

- Combine approved signal contributions.
- Apply freshness and hard safety vetoes.
- Maintain each token's confluence state.
- Separate standard and fast-path scheduling.
- Do not delay approved fast-path entries behind background intelligence queues.

**Sizing**

- Implement quarter-Kelly mechanics.
- Separate models by user and score bucket.
- Cold-start state.
- Recalibration after twenty closed trades per bucket.
- Filter-change reset behavior.
- Owner-approved exposure constraints.

Do not invent Bayesian priors, undefined buckets or sizing caps.

**Execution**

- Integrate D3AD-E's compatible low-latency machinery.
- Maintain one order gateway.
- Enforce trade-intent idempotency.
- Manage quotes, signing, submission and confirmation.
- Recover uncertain transaction outcomes.
- Reconcile actual fills and wallet balances.
- Prevent duplicate positions.
- Preserve execution-provider isolation.

**The Executioner**

Check every open position at least every ten seconds for Confluence Collapse:

```text
collapse_boundary = entry_threshold * 0.40

IF position_age >= 600 seconds
AND current_confluence < collapse_boundary
THEN trigger full exit
```

Implement Negative Divergence partial-profit exits:

```text
Entry score >= 75  => 30% exit
Entry score 65–74  => 40% exit
Entry score 60–64  => 50% exit
Entry score < 60   => 60% exit
```

Partial-profit exits require an actually profitable position and a qualifying divergence event.

Prevent one divergence event from causing repeated partial sales.

Preserve CHAD's immediate re-entry eligibility after a Negative Divergence exit.

Implement configurable stop-loss, trailing exits and documented emergency exits separately. Unspecified trigger values remain configuration blockers, not invented defaults.

**Accounting**

- Record actual and simulated fills distinctly.
- Record fees, tips, slippage and network costs.
- Track realized and unrealized P&L separately.
- Reconcile every partial and full exit.
- Account for positions that remain open.
- Record transaction failure costs.
- Produce transparent trade-by-trade execution reports.

**Operator interface**

Provide one compact operating view covering:

- Active signals.
- Entry qualification.
- Safety state.
- Open positions.
- Entry versus current confluence.
- Outstanding orders.
- Position age.
- Exit reason and status.
- Available balance.
- Realized cost-adjusted P&L.
- Latency and feed-health indicators.

Do not let interface work block the execution engine.

### Required simulation

Paper-test the complete **HEAR → SIZE → FIRE → EXIT** sequence using available live market observations.

Run historical event replay as a separate reproducible validation mode.

Test:

- Early momentum entries.
- Failed safety checks.
- Delayed market events.
- Liquidity deterioration.
- Failed quote retrieval.
- Slippage and stale-price effects.
- Insufficient balances.
- Partial fills.
- Dropped confirmations.
- Repeated sell signals.
- Rapid momentum reversals.
- Profitable divergence exits.
- Loss exits.
- Confluence Collapse.
- Restart with open positions.
- Multiple simultaneous candidate tokens.

A historical observed market price is not automatically an executable fill. Model execution realistically, including supported liquidity, time delay and costs.

Measure signal-to-decision and decision-to-submission time separately from transaction landing time.

### Phase 3 acceptance

JINX must demonstrate a complete paper-trading lifecycle, with verified state transitions, accounting, recovery and risk behavior.

Provide trade logs, measured latency, cost-adjusted simulated results, failed-test records, and reconciled paper-wallet balances.

The review panel must compare implementations directly against CHAD and the pinned upstream sources.

**STOP AT THE PHASE 3 APPROVAL GATE.**

---

# PHASE 4 — REAL-TIME HARDENING, FULL SIMULATION AND INDEPENDENT AUDIT

**OBJECTIVE: Prove the integrated JINX system is operationally ready and eliminate observed defects before any live-money activation.**

### Before starting

Display the complete Phase 4 verification matrix.

### Required implementation and verification

- Run the full continuous paper-trading system.
- Feed current available market and on-chain observations into the actual event pipeline.
- Execute deterministic replay against captured real-market events.
- Verify all documented CHAD entry predicates.
- Verify every hard safety rejection.
- Verify every approved exit condition.
- Verify the independence of all four upstream adapters.
- Test reconnects, dropped messages and provider outages.
- Check position recovery after interruption.
- Stress simultaneous token events.
- Stress order duplication and delayed confirmations.
- Verify transaction balance reconciliation.
- Run anti-lookahead checks.
- Measure median, p95 and worst-observed decision/execution latency.
- Measure paper P&L after all modeled costs.
- Evaluate adverse price movement and failed exit conditions.
- Verify that missing data fails safely.
- Audit for secrets and private keys.
- Verify all dependency licenses.
- Verify no extra unapproved infrastructure or independent strategy controller has been introduced.

### Speed requirement

**JINX MUST BE ENGINEERED TO REACT WITHIN SECONDS WHEN THE DATA, SAFETY CONDITIONS AND EXECUTION NETWORK PERMIT IT.**

No unnecessary polling wait in the Early Momentum event path.

No social-analysis queue blocking event detection.

No heavyweight historical analysis on the transaction hot path.

No synchronous dashboard update delaying an order.

No repeated API calls when a valid fresh observation is already cached.

No unbounded queue.

Measure the actual timing and report every bottleneck.

Do not claim seconds-level order landing unless measured. Network conditions, Solana congestion, RPC behavior and confirmation delays remain external variables.

### Independent panel audit

Submit the final implementation to the available independent reviewers, including Codex if connected.

Each reviewer receives:

- CHAD.txt.
- The upstream repository commit map.
- The JINX source code.
- `CLAUDE.md`.
- The source-fidelity matrix.
- Contract and integration test output.
- Paper-simulation logs.
- Reconciliation evidence.
- Performance and latency measurements.
- Known unresolved strategy parameters.

Panel verdict:

**PASS / FAIL / BLOCKED**

The panel must identify architectural deviations, invented strategy rules, defective code, missing implementation, fragile integration boundaries, incorrect P&L, unrealistic fills, concurrency errors and missing verification evidence.

Do not fabricate a panel review or claim independent approval without actual reviewer output.

### Phase 4 acceptance

The phase is complete only when all required tests have passing evidence, known failures have been addressed or explicitly accepted, and the panel issues a documented verdict.

Provide a final operational summary separating:

- Implemented and verified.
- Implemented but unverified.
- Blocked by missing data or owner parameters.
- Failed and not fixed.
- Live-readiness constraints.

Paper-mode completion never automatically activates real-money trading.

Final owner sign-off for live-money activation is distinct from normal autonomous trading authorization.

---

# REAL-TIME PROGRESS AND REVIEW PROTOCOL

Before EACH of the four phases, print a visible checklist or dependency graph.

Maintain `docs/BUILD_STATUS.md` in the repository.

Use this progress format:

| Component | Status | Source | Tests | Evidence |
|---|---|---|---|---|
| Event ingestion | NOT STARTED | Upstream/JINX | Pending | — |
| Source isolation | NOT STARTED | Four adapters | Pending | — |
| Signal processing | NOT STARTED | CHAD | Pending | — |
| Execution | NOT STARTED | D3AD-E/JINX | Pending | — |
| Position monitoring | NOT STARTED | CHAD/Mako | Pending | — |
| P&L reconciliation | NOT STARTED | JINX | Pending | — |

Update entries as implementation progresses.

Use only:

`NOT STARTED / IN PROGRESS / TESTING / PASSED / FAILED / BLOCKED`

Every PASSED item must link to a real test or verifiable result.

After every meaningful code change:

1. Run relevant unit or contract tests.
2. Run the associated paper-simulation or replay scenario.
3. Fix failures immediately.
4. Record the result.
5. Continue only when the change is stable.

Do not postpone all simulation until Phase 4.

At the end of EACH phase:

- Summarize exactly what was built.
- Identify reused source files.
- Show actual changed JINX files.
- Provide test commands and outputs.
- Show paper-simulation evidence.
- Record bugs found and fixed.
- List unresolved blockers.
- Obtain independent panel review.
- Wait for owner approval before the next phase.

**FOUR PHASES. FOUR REVIEW GATES. NO EXTRA PHASES.**

# ZERO-DRIFT RULES

YOU ARE STRICTLY FORBIDDEN FROM:

- Inventing a new trading strategy.
- Inventing undocumented CHAD thresholds.
- Replacing CHAD's decision policy with Mako's or D3AD-E's.
- Replacing reusable licensed upstream code without technical justification.
- Building ten competing trading runtimes.
- Allowing cloned sources to modify each other.
- Creating competing order controllers.
- Adding unnecessary services.
- Waiting on the normal scoring queue for an otherwise eligible fast-path entry.
- Bypassing safety because a signal looks profitable.
- Hiding failed fills.
- Counting unrealized gains as realized profits.
- Treating backtested profits as guaranteed live returns.
- Using future market observations in historical decisions.
- Marking code complete without tests.
- Deferring every bug until final integration.
- Proceeding past a phase gate without approval.
- Pretending to have invoked unavailable skills.
- Claiming panel approval without an actual review.
- Enabling real-money trading without the owner's initial explicit authorization.
- Requesting fresh human approval for each trade after valid live authorization and predefined risk permissions are in force.

# THE FINAL STANDARD

JINX exists to detect actionable opportunities early, execute qualifying trades quickly, and protect capital through intelligent, evidence-driven exits.

**CHAD supplies the behavioral architecture.**

**THE FOUR GITHUB REPOSITORIES SUPPLY REUSABLE WORKING CODE.**

**JINX IS THE SINGLE COORDINATED EXECUTION SYSTEM.**

**CLAUDE IS THE IMPLEMENTATION ENGINEER — NOT THE STRATEGY INVENTOR.**

The system must be evaluated by realized, cost-adjusted profitability, risk containment, execution latency, position reconciliation and actual test evidence.

No architecture drift.

No invented trading policy.

No invisible bugs left for live trading.

No false completion claims.

## FINAL COMMAND TO CLAUDE

Begin by inspecting `CryptoCripLA/harlemshakejinx`.

Update `CLAUDE.md`.

Discover, read and invoke the eleven available skills, reporting their actual results.

Inspect CHAD.txt and the four cloned source repositories.

**PRESENT THE FOUR-PHASE BUILD PLAN TO THE OWNER BEFORE IMPLEMENTING APPLICATION CODE.**

After the plan is approved, execute Phase 1.

Follow every review gate.

Test continuously against genuine market observations and reproducible replay data.

Build the complete coordinated engine using the approved sources.

**HEAR CHATTER. SIZE. FIRE. EXIT.**

**ENTER EARLY. EXIT SMARTER.**

**FOUR PHASES. REAL CODE. REAL TESTS. VERIFIED RESULTS.**