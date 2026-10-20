# Source-fidelity matrix (Phase 1, 2026-10-20)

Every CHAD requirement is mapped to the approved upstream code that can supply it, the JINX destination, its phase and its status. Quotes are from `docs/CHAD_BLUEPRINT.md`, a verbatim copy of the owner's CHAD.txt (sha256 match confirmed 2026-10-20). Upstream paths are relative to `upstream/<name>/` at the commits pinned in `UPSTREAM_PINS.json`.

Status values: `NOT STARTED / IN PROGRESS / TESTING / PASSED / FAILED / BLOCKED`. Where CHAD leaves a value out, the row says so and links `docs/BLOCKERS.md`. No value is invented.

## A. Phase 1 foundation (owner directive, "PHASE 1 — Required implementation")

| # | Directive item | Upstream reused | JINX file | Test | Status |
|---|---|---|---|---|---|
| F1 | Initialize application structure | — | `jinx/`, `pyproject.toml`, `requirements.txt` | full suite | PASSED |
| F2 | Update CLAUDE.md | — | `CLAUDE.md` (four-phase mandate; stale Phase 1 line corrected) | — | PASSED |
| F3 | Verify all eleven skills | — | session log, 2026-10-20 (all 11 loaded with the Skill tool; repo hook finds all 11 files on jinx-nyc) | — | PASSED |
| F4 | Clone and pin four source repos | all four | `UPSTREAM_PINS.json`, `scripts/upstream_sync.py` | `tests/test_upstream_pins.py` | PASSED |
| F5 | Audit licensing and dependencies | all four | `docs/LICENSES.md` | `test_pins_cover_exactly_the_four_approved_repos` | PASSED (DataNomad: no license, reference only) |
| F6 | Four isolated source adapters | gmgn `src/client/OpenApiClient.ts`, `signer.ts`; mako `src/pumpportal.py::PumpPortalStream`, `src/config.py`; d3ad and datanomad pinned only | `jinx/adapters/{gmgn,mako,d3ad,datanomad}/` | `tests/test_adapters.py` (isolation, fake-GMGN contract, local-websocket Mako run, crash containment) | PASSED |
| F7 | Typed normalized market events | — | `jinx/core/events.py`, `jinx/adapters/mako/normalize.py` | `tests/test_events.py`, normalizer tests | PASSED |
| F8 | Central event bus | — | `jinx/core/bus.py` | `tests/test_bus.py` | PASSED |
| F9 | Token and wallet registries | — | `jinx/registry.py` | `tests/test_store_registry.py` | PASSED |
| F10 | One authoritative position ledger | — | `jinx/ledger.py`, `jinx/ops/entry_slots.py` | `tests/test_ledger.py` | PASSED |
| F11 | Persistent event and trade storage | mako `src/db.py` approach (SQLite) informed the choice; schema is JINX's | `jinx/store.py` | store, ledger and recovery tests | PASSED |
| F12 | AUTO/COPY/OFF mode interfaces | — | `jinx/modes.py` | `test_mode_off_blocks_entries_but_never_exits`, `test_exits_still_run_when_mode_is_off` | PASSED |
| F13 | Paper wallet and paper execution | d3ad `shred-sniper/sniper/src/pumpfun.rs:432-434, 439-449, 485-486`; `position.rs:631-646` (translated with SRC lines) | `jinx/paper.py`, `jinx/execution.py` | `tests/test_paper.py` incl. `test_curve_math_matches_d3ad_source_exactly` (D3AD's Rust compiled with rustc, 2,002 cases) | PASSED |
| F14 | Event dedup and idempotency | — | `MarketEvent.idempotency_key`, `jinx/core/dedup.py`, events table unique key, fill ids, intent ids | dedup, bus, ledger, replay duplicate tests | PASSED |
| F15 | Source-fidelity matrix | — | this file | — | PASSED (this document) |
| F16 | Restart/recovery and integration health | — | `jinx/core_state.py`, `jinx/recovery.py`, `jinx/health.py`, `/health` in `jinx/app.py` | `tests/test_recovery.py` incl. hard-kill test | PASSED |

## B. CHAD capabilities (Phases 2-3)

| ID | CHAD requirement (quoted) | Upstream code that can supply it | JINX destination | Phase | Status / gap |
|---|---|---|---|---|---|
| C01 | Safety: "Runs RugCheck, honeypot detection, and mint/freeze authority analysis on every candidate. Scores every token 0–100 before any other signal fires." | mako `src/safety.py`, `src/rug_detector.py`, `src/onchain.py`; gmgn `getTokenSecurity` (`/v1/token/security`) | `jinx/intel/safety.py` | 2 | NOT STARTED. The 0–100 formula and pass threshold are not in CHAD → B-03 |
| C02 | Momentum: "Tracks DexScreener price velocity and volume acceleration every 15 seconds. Fires a Negative Divergence signal the moment price stalls on sustained volume" | mako `src/market_data.py` (DexScreener client) | `jinx/intel/momentum.py` | 2 | NOT STARTED. "stalls" and "sustained" are not quantified → B-03 |
| C03 | X Scout: "5-dimension … Social Graph (25%) … Forensic History (20%) … Engagement Authenticity (20%) … Professionalism (10%) … Narrative + Claude AI (25%)"; verdict "STRONG / WATCH / RUG_FILTER"; "RUG_FILTER tokens are blocked from execution regardless of safety score"; "direct score bonus to tokens called by @SolBotChad" | none of the four repos has an X client (gmgn has a narrative skill doc only) | `jinx/intel/x_scout.py` | 2 | BLOCKED: X data access → B-05; verdict cut-offs and bonus size not in CHAD → B-03 |
| C04 | Whale Intel: "Layer 1: passive real-time alerts when a tracked high-conviction wallet buys … Layer 2: active scan of each token's recent transactions for smart-money buyers by SOL balance"; discovery "runs every 4 hours … promotes those with 40%+ win rate"; "Second-degree network mapping"; confidence "Win rate (50 pts) + recency decay (25 pts) + trade volume (25 pts)", "Score halves every 14 days of inactivity"; "Add your own wallets — up to 15" | mako `src/wallet_discovery.py`; gmgn `getSmartMoney`, `getKol`, `getWalletStats`, `getWalletProfits`, `getWalletActivity` | `jinx/intel/whales.py` (uses `jinx/registry.py::WalletRegistry`) | 2 | NOT STARTED. Live wallet trades need a trade stream → B-01/B-07 |
| C05 | Graduation: "Monitors every pump.fun bonding curve in real time via Helius gRPC. Scores each token's progress toward graduation (0–20 pts)" | d3ad `position.rs::read_reserves` (curve account decode) | `jinx/intel/graduation.py` | 2 | BLOCKED: Helius gRPC access → B-07; progress-to-points mapping not in CHAD → B-03 |
| C06 | Cluster: "3 or more tracked profitable wallets buy the same token within a short window" | — (JINX logic over C04 wallets) | `jinx/intel/clusters.py` | 2 | NOT STARTED. "short window" not quantified → B-03 |
| C07 | Freshness: "Applies recency decay to every signal on the board … older signals are discounted automatically" | — | `jinx/intel/freshness.py` | 2 | NOT STARTED. Decay function not in CHAD → B-03 |
| C08 | Dev Intel: "Scores the token deployer's full on-chain history — prior launch success rates, rug patterns, and wallet connections. Runs on tokens already clearing the safety score threshold" | gmgn `getCreatedTokens`, `skills/gmgn-dev-score/dev_score.py`; datanomad `src/deploy_parser.py`, `src/launch_history.py` (reference only, no license) | `jinx/intel/dev_reputation.py` | 2 | NOT STARTED. Score formula and cut-off not in CHAD → B-03 |
| C09 | Executioner: "Watches the live Confluence Score for every open position every 10 seconds. Auto-exits on Confluence Collapse — when the score drops below 40% of the entry threshold"; "A 10-minute grace window applies after entry"; Negative Divergence partial exit "Score 75+ 30% … 65–74 40% … 60–64 50% … < 60 60%"; "Only fires when you're in profit"; "Re-entry allowed immediately" | — (JINX logic; executes through `jinx/execution.py`) | `jinx/exit/executioner.py` | 3 | NOT STARTED. Every number here is specified by CHAD. Phase 1 already provides partial sells (`test_partial_sell_keeps_position_open_until_inventory_is_zero`) |
| C10 | Early Momentum: "3+ unique wallets buying within 30 seconds, all below the 35 SOL bonding curve threshold" and "0.5–10 SOL bonding curve gate"; "3 distinct fee payers required"; "Bypasses the normal scoring queue"; "Full safety check still fires … in parallel"; "Peak SOL Pump-and-Dump Gate … tracks all-time high virtualSolReserves"; "If 3+ wallets share the same SOL funder → emergency exit fires instantly" | d3ad `pumpfun.rs::parse_buy` (buy decoding); bus FAST lane | `jinx/intel/early_momentum.py` | 2-3 | Partly prepared: the FAST lane and `TokenState.peak_v_sol_lamports` exist and are tested. CHAD contradicts itself on the curve gate (35 SOL vs 0.5–10 SOL) → B-02; needs a trade stream → B-01/B-07 |
| C11 | TG Scout: "2+ independent groups · 60-second window" → confluence; "3+ groups · 60s window" → strong; "CA extraction from links and raw text"; "Feeds the Confluence Engine as a weighted input" | none of the four repos has a Telegram client | `jinx/intel/tg_scout.py` | 2 | BLOCKED: Telegram access and group list → B-06; weight not in CHAD → B-03 |
| C12 | Confluence engine: "Every token clearing the global score threshold"; "8-pillar board"; "10 signal badges per token" | — | `jinx/decision/confluence.py` | 3 | NOT STARTED. Weights and global threshold not in CHAD → B-03 |
| C13 | Kelly: "Per-user, per-score-bucket Bayesian model"; "Quarter-Kelly default"; "After 20+ closed trades per score bucket, Kelly updates"; "Change your min score threshold and Kelly resets"; buckets "Score 60 … 32–36% … Score 70 … 40% … Score 80+ … 45–52%+"; "Cold-start uses conservative bootstrap priors" | — | `jinx/decision/sizing.py` | 3 | NOT STARTED. The Kelly cap, payout-ratio prior and exact bucket edges are not in CHAD → B-03 |
| C14 | "Standard configurable stop loss on every position. High-conviction positions get a trailing stop" | — | `jinx/exit/protective.py` | 3 | NOT STARTED. Values and the "high-conviction" cut-off not in CHAD → B-03 |
| C15 | "Dynamic Priority Fee Escalation … auto-escalates compute unit pricing" when signals align | d3ad `shred-sniper/sniper/src/tip.rs`, `sender.rs` | execution adapter | 3 | NOT STARTED. Escalation amounts not in CHAD → B-03 |
| C16 | Paper wallet: "2 SOL persistent paper wallet" | — | `jinx/config.py::PAPER_START_LAMPORTS`, `jinx/paper.py` | 1 | PASSED |
| C17 | Position/trade counts by product tier ("3 max open positions", "10", "Unlimited") | — | `jinx/ledger.py` slot limit | 1 | Owner's standing rule (1 slot, 2 by owner order) applies instead → B-04 |
| C18 | "RPC redundancy + auto-failover"; "Helius webhook low-latency execution" | d3ad `providers.rs`, `sender.rs`, `confirm.rs` | execution adapter | 3-4 | NOT STARTED |
| C19 | "ConfluenceRadar Live Dashboard: 10 signal badges per token with per-signal staleness" | — | operator view | 3 | NOT STARTED |

## C. Phase 1 known deviations

| Item | What and why | Effect |
|---|---|---|
| Paper fills ignore the curve's real-token cap | PumpPortal messages carry virtual reserves only; D3AD's `initial_buy_price` also caps by real token reserves | Only matters near graduation; recorded as a paper-fill limitation |
| PumpPortal events have no slot or block time | The feed does not send them; ordering uses receive time plus sequence | Final state is order-independent (shuffle tests); chain time is used whenever a later source provides it |
| Paper fills use the last observed reserves | No latency, competing-flow or slippage model yet | Phase 3 requirement ("A historical observed market price is not automatically an executable fill") |
