# BUILD STATUS — harlemshakejinx

**Current stage: Phase 1 accepted by the owner (2026-10-21) after Codex round 12 PASS. Phase 2 (CHAD hunting intelligence) is in progress. Owner directives: `docs/OWNER_GMGN_MANDATE_20261020.md`, `docs/OWNER_DIRECTIVES_20261021.md`.**

How to read the numbers here: paper round trips prove **accounting consistency**, not trading performance. Sub-millisecond figures are **local event processing inside JINX**, never order-execution latency. Since review round 2 the live figure runs from feed receive to state applied, durable write included. Figures recorded earlier (0.22 ms, 0.49 ms) started *after* the durable write and understate local processing. Execution latency is measured in Phase 3 against GMGN's confirmed orders.

Status words: NOT STARTED / IN PROGRESS / TESTING / PASSED / FAILED / BLOCKED. Every PASSED row links to a test or recorded result. Evidence: `docs/evidence/PHASE1_EVIDENCE.md`. Mapping: `docs/SOURCE_FIDELITY_MATRIX.md`. Owner decisions: `docs/BLOCKERS.md`.

## Phase 1 — Foundation, source extraction and core

| Component | Status | Source | Tests | Evidence |
|---|---|---|---|---|
| Application structure | PASSED | JINX | full suite | `jinx/`, `pyproject.toml` |
| CLAUDE.md four-phase contract | PASSED | owner directive | — | `CLAUDE.md` |
| 11 required skills | PASSED | user-global + repo `.claude/skills/` | — | `docs/evidence/SKILLS_INVOCATION_20261020.md`: 11 Skill tool calls with ids, results and delivered bodies, parsed from the session transcript; the repo hook finds all 11 files on jinx-nyc |
| Four upstreams pinned | PASSED | gmgn 402671e, mako 45dadc0, d3ad a91ca33, datanomad b79c9bd | `test_upstream_pins.py` | `UPSTREAM_PINS.json`; `upstream_sync.py --verify` OK |
| License and dependency audit | PASSED | all four | `test_pins_cover_exactly_the_four_approved_repos` | `docs/LICENSES.md` (DataNomad unlicensed → reference only) |
| Source isolation (four adapters) | PASSED | GMGN client, Mako stream; D3AD/DataNomad pinned | `test_adapters.py` (35) | live GMGN-bridge kill left ingestion running |
| Typed events, dedup, idempotency | PASSED | JINX | `test_events.py`, `test_bus.py`, replay duplicate test | 113 injected duplicates refused on real data |
| Event bus (fast + background lanes) | PASSED | JINX | `test_bus.py` | no fast-lane drops; local capacity ~5,166 events/s (synthetic load) |
| Token and wallet registries | PASSED | JINX | `test_store_registry.py` (8) | shuffled real capture → identical state |
| One authoritative position ledger | PASSED | JINX; owner one-slot rule | `test_ledger.py` (15) | slot 1 default, 2 by owner order, never 3 |
| Persistent event and trade storage | PASSED | JINX (SQLite WAL) | store, ledger, recovery tests | live restart reloaded all 234 tokens |
| AUTO/COPY/OFF | PASSED | owner contract | mode tests | default OFF; OFF never blocks exits |
| GMGN-first execution ownership | PASSED (documented) | owner directives 2026-10-20 | — | `CLAUDE.md` sections, `docs/INTEGRATION_MAP.md`, matrix section C. GMGN executes; JINX decides and reconciles; D3AD sender unwired |
| GMGN authenticated read-only access | PASSED | GMGN's pinned `OpenApiClient` through JINX's bridge | `scripts/gmgn_capability_check.py` (live run 2026-10-20 19:16 UTC: 26 GMGN calls, 25 OK, 1 refused by GMGN as expected on Plus: 1 s candles are Pro only; `swap` refused locally before any request) | `docs/GMGN_CAPABILITY_INVENTORY.md`, `docs/evidence/gmgn_capability_check_20261020.json` (redacted) |
| GMGN plan and request budget | PASSED (round 10 confirmed the weights and ban handling) | plan measured **Plus**: GMGN header `x-ratelimit-limit: 20`; weights from GMGN's skill tables | `tests/test_gmgn_plus.py` (docs-derived weights, bucket burst/pacing/refill/refusal), `tests/test_gmgn_rate_limit.py` (bans pause every call until GMGN's reset) | `jinx/adapters/gmgn/budget.py`; adapter health reports plan and spend |
| GMGN read allowlist complete | PASSED | pinned client method list | `test_bridge_allows_exactly_every_non_trading_method_of_the_pinned_client` | `getTokenSignalV2` was missing and is now enabled; trading methods refused |
| Signing-key policy | PASSED | owner decision 2026-10-20 | `test_paper_bridge_does_not_receive_the_signing_key_by_default`, `test_bridge_ignores_the_signing_key_file_when_told_to`, `test_runtime_loads_the_signing_key_for_signed_reads_by_owner_decision_but_trading_stays_refused` | default excluded; runtime allows it for signed reads; trading still refused (`docs/SECRETS.md` §3) |
| Migration lifecycle interface | PASSED (interface; confirmed by round 12) | owner mandate §3 | `tests/test_lifecycle.py` (43) | `jinx/core/lifecycle.py`. Feeding it from GMGN Trenches/token info and the Pump.fun stream is Phase 2 work; the field mapping is fixed in `docs/GMGN_ADAPTER_CONTRACT.md` §5 |
| GMGN adapter contract | PASSED (documented) | pinned client, bridge, adapter | adapter, bridge and budget tests | `docs/GMGN_ADAPTER_CONTRACT.md` |
| Secrets handling | PASSED | owner mandate §1 | `tests/test_secrets.py` (3: no committable file holds a credential shape or the configured GMGN values) | `docs/SECRETS.md`; the key pasted in chat stays in use by owner decision (B-16) |
| Paper wallet and paper execution | PASSED | D3AD curve math (SRC lines) | `test_paper.py` incl. rustc differential (2,002 cases) | accounting consistency only: 122 mechanical round trips on real captured reserves reconcile to the lamport. They are not trading performance |
| Restart recovery and health | PASSED | JINX | `test_recovery.py` (9) incl. hard kill | live restart: 0 replayed, sequence resumed at 357; `/health` 200 |
| Live market ingestion | PASSED (creations only) | Mako `PumpPortalStream` | live runs | 234 creations / 15 min, 0 malformed; local receive→state p50 0.59 ms, p95 0.90 ms (re-measured after the round-2 fix, durable write included; the earlier 0.22 ms excluded the write). Not execution latency. The creation-only feed cannot detect three-wallet momentum |
| Live trades from other wallets | BLOCKED | PumpPortal needs a key funded with 0.02 SOL | — | B-01 |
| GMGN live API access | PASSED | key applied 2026-10-20; `gmgn-cli config --apply` verified | live capability check | B-08 resolved |
| Runtime host | PASSED (jinx-nyc, existing) | GMGN does not host custom code; jinx-nyc is the minimum existing environment and the GMGN-trusted IP | — | B-10. Railway not provisioned; `Dockerfile`/`railway.json` ready but unused (image not built: no Docker permission) |
| Independent Codex review (Phase 1) | PASSED (round 12) | Codex 0.160.0 on the owner's Windows PC (logged in), read-only, clean GitHub clone | Round 12 on 127d3d5 returned **VERDICT: PASS** with no CRITICAL, HIGH, MEDIUM or LOW findings, and found every current Phase 1 claim supported by code, tests or committed evidence (`docs/audits/phase1-codex-20261020-round12.md`). Rounds 1–11 returned FAIL; every finding was fixed and confirmed (`docs/audits/phase1-codex-20261020-round{1..11}.md`). Not rerun by the reviewer, and so builder evidence only: disk durability and restart tests, the compiled-bridge integration tests, authenticated GMGN calls, and the full 317-test run (it confirmed 317 collected) | Phase 1 is at the review gate; waiting for owner acceptance |

## Phases 2–4

| Phase | Status |
|---|---|
| Phase 2 — CHAD intelligence, safety and fast signals | IN PROGRESS: built and live on Contabo (paper): Early Momentum (C10), graduation readings (C05), migration (M1-M3), safety gate with GMGN + RugCheck (C01, B-19 closed), developer history (C08), bundle facts (R1), whale intelligence (C04). 62 detections screened in 3 live minutes (`docs/evidence/screening_contabo_20261021.json`). Open: B-02, B-03, B-20, momentum C02, freshness C07, X/TG scouts (B-05/B-06), Phase 2 review gate |
| Phase 3 — Size, fire, exit and realized-profit accounting | IN PROGRESS (p3 builder, branch `phase3/size-fire-exit`; map and checklist below) |
| Phase 4 — Hardening, full simulation and independent audit | NOT STARTED |

## Phase 3 — Size, fire, exit and realized-profit accounting (p3 builder, 2026-10-21)

Owner orders: CLAUDE.md § PHASE 3 and `docs/OWNER_DIRECTIVES_20261021.md` § "Storage repair, BigMac exit install, Phase 3 and Phase 4 agents" (BigMac part and §6). File ownership: `docs/AGENT_COORDINATION.md`. Skills evidence: `docs/evidence/SKILLS_INVOCATION_p3_20261021.md`. Paper only: no live signing, no live orders; GMGN trading methods stay refused by the bridge.

Implementation map (one path, one gateway, one ledger):

```text
price update (PumpAPI/PumpPortal trade, reserves) ──► PositionMonitor (jinx/exits/monitor.py, also a ≤10 s sweep)
   read open position + entry basis + persisted peak (ledger)
   1. CHAD mandatory protective exits (Sybil emergency exit; Confluence Collapse via the Executioner)
   2. else the position's paper exit policy (BIGMAC_PAPER = jinx/exits/bigmac_exit.py::evaluate_exit)
   3. persist the new peak
   4. at most ONE ExitIntent (jinx/execution.py, owner fields) ──► ExecutionGateway.submit_exit
        ──► PaperExecutor.sell (curve fees + price impact) ──► PositionLedger (remaining tokens, proceeds, P&L)
Early Momentum detection + safety ──► confluence state (jinx/confluence/) ──► sizing (jinx/sizing/, quarter-Kelly)
   ──► EntryIntent ──► ExecutionGateway.submit_entry (slot rule) ──► PaperExecutor.buy
Executioner (jinx/exits/executioner.py): every open position ≤10 s; Confluence Collapse; Negative Divergence partials
Operator view: /operator (jinx/operator_view.py)
```

| Component | Status | Source | Tests | Evidence |
|---|---|---|---|---|
| P3-01 BigMac source pinned, Ms-PL license vendored | NOT STARTED | bigmac `helpers/exit-strategy.ts` @ 829f251d | `tests/test_bigmac_license.py` | `third_party/bigmac/` |
| P3-02 BigMac exit port (owner code; Ms-PL header; integer `applyPercent`) | NOT STARTED | owner `bigmac_exit.py`; TS `evaluateExit` 15-47, `applyPercent` 107-114 | `tests/test_bigmac_differential.py` (node 22 runs the original) | — |
| P3-03 ExitIntent with the owner's fields (one class) | NOT STARTED | owner order §2 | `tests/test_p3_exit_intent.py` | — |
| P3-04 Exit ownership + persisted peak per position | NOT STARTED | owner order §3 | `tests/test_p3_monitor.py` | — |
| P3-05 Position monitor wired to the existing gateway/paper/ledger | NOT STARTED | owner order §2 | `tests/test_bigmac_integration.py` | — |
| P3-06 OFF blocks entries, protective exits continue | NOT STARTED | owner contract | `tests/test_bigmac_integration.py` | — |
| P3-07 Confluence state and entry (freshness, hard vetoes, fast path) | NOT STARTED | CHAD C12, C10 | `tests/test_p3_confluence.py` | — |
| P3-08 Quarter-Kelly sizing (per user and bucket, cold start, 20-trade recalibration, filter reset) | NOT STARTED | CHAD C13; values B-03 | `tests/test_p3_sizing.py` | — |
| P3-09 Execution lifecycle (intent idempotency, quote/submit/confirm, uncertain outcomes) | NOT STARTED | INTEGRATION_MAP §2 | `tests/test_p3_execution.py` | — |
| P3-10 The Executioner (10 s sweep, Confluence Collapse, Negative Divergence partials) | NOT STARTED | CHAD C09 | `tests/test_p3_executioner.py` | — |
| P3-11 Accounting (simulated vs actual, costs, realized vs unrealized, trade report) | NOT STARTED | CLAUDE.md Phase 3 | `tests/test_p3_accounting.py` | — |
| P3-12 Operator view | NOT STARTED | CHAD C19 | `tests/test_p3_operator_view.py` | — |
| P3-13 Phase 3 simulations (replay of real captures + live paper run) | NOT STARTED | CLAUDE.md Phase 3 | `tests/test_p3_simulation.py` | `docs/evidence/PHASE3_*` |
| P3-14 Latency: signal→decision, decision→submission, submission→fill measured separately | NOT STARTED | CLAUDE.md Phase 3 | `tests/test_p3_simulation.py` | — |
| P3-15 Independent Codex review (Phase 3) | NOT STARTED | Codex on the owner's PC | — | `docs/audits/phase3-codex-20261021-roundN.md` |

## Environment notes

- GitHub push from jinx-nyc verified 2026-10-20: `gh` 2.102.0 is logged in as CryptoCripLA (owner's fine-grained token, never committed).
- JINX's runtime is jinx-nyc (B-10). Railway is not provisioned. GMGN is the API, the execution platform and the separate hosted copy-trading service.

## GMGN Agent API authentication (jinx-nyc)

Purpose: JINX's automatic trading integration. There is no manual buy/sell workflow.

| Component | Status | Source | Tests | Evidence |
|---|---|---|---|---|
| Official `gmgn-cli` installed | PASSED | npm `gmgn-cli@1.6.7` (maintainer gmgn_infra, repo GMGNAI/gmgn-skills) | `gmgn-cli --version` → 1.6.7 | `~/.local/bin/gmgn-cli` |
| GMGN skills installed | PASSED | `npx skills add GMGNAI/gmgn-skills -g -a claude-code` (skills CLI 1.7.4) | 19 `gmgn-*` skills listed | `~/.claude/skills/gmgn-*` |
| Ed25519 request-signing key pair | PASSED | `gmgn-cli config` (official generator) | `openssl pkey` reads an ED25519 public key | `~/.config/gmgn/keypair.pem`, mode 600, dir 700. The private key never leaves jinx-nyc |
| Trusted IP for trading | PASSED (given to owner) | jinx-nyc outbound IPv4, verified by 3 services | — | 45.77.159.191 |
| API key created on gmgn.ai with this public key | PASSED | owner, 2026-10-20 (plan measured Plus) | — | key bound to 9 chains incl. Solana wallet 3CtN…HZhg |
| `gmgn-cli config --apply` + authenticated call | PASSED | `gmgn-cli config --apply` → "Configuration verified successfully"; signed `track follow-wallet` and `portfolio holdings` OK | `scripts/gmgn_capability_check.py` | `docs/GMGN_CAPABILITY_INVENTORY.md` |
| Railway secrets `GMGN_API_KEY` / `GMGN_PRIVATE_KEY` | NOT STARTED | not needed while the runtime is jinx-nyc (B-10); the bridge reads `~/.config/gmgn/.env` | — | `docs/SECRETS.md` |

Public key registered for this host (public, not a secret):

```
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAut2OCGLiTFMpgSNhpuAX0ZaNggCXGaD1svVq4+aQOeE=
-----END PUBLIC KEY-----
```

`GMGN_PRIVATE_KEY` signs API requests. It is not a Solana wallet key, but with the API key it can submit trades on the linked GMGN wallet. Keep it out of git, logs and chat.
