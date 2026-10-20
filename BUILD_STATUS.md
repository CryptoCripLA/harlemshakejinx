# BUILD STATUS — harlemshakejinx

**Current stage: Phase 1 built; owner provisionally accepted the foundation (2026-10-20). The gate stays open until the independent review passes. No Phase 2 work starts without owner approval.**

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
| GMGN-first execution ownership | PASSED (documented) | owner directive 2026-10-20 | — | `CLAUDE.md` section, `docs/INTEGRATION_MAP.md`, matrix section C. GMGN executes; JINX decides and reconciles; D3AD sender unwired |
| Paper wallet and paper execution | PASSED | D3AD curve math (SRC lines) | `test_paper.py` incl. rustc differential (2,002 cases) | accounting consistency only: 122 mechanical round trips on real captured reserves reconcile to the lamport. They are not trading performance |
| Restart recovery and health | PASSED | JINX | `test_recovery.py` (9) incl. hard kill | live restart: 0 replayed, sequence resumed at 357; `/health` 200 |
| Live market ingestion | PASSED (creations only) | Mako `PumpPortalStream` | live runs | 234 creations / 15 min, 0 malformed; local receive→state p50 0.59 ms, p95 0.90 ms (re-measured after the round-2 fix, durable write included; the earlier 0.22 ms excluded the write). Not execution latency. The creation-only feed cannot detect three-wallet momentum |
| Live trades from other wallets | BLOCKED | PumpPortal needs a key funded with 0.02 SOL | — | B-01 |
| GMGN live API access | BLOCKED | owner creating the API key | fake-server contract tests PASSED | B-08 |
| Runtime host | jinx-nyc (existing) | GMGN does not host custom code; jinx-nyc is the minimum existing environment and the GMGN-trusted IP | — | B-10. Railway not provisioned; `Dockerfile`/`railway.json` ready but unused (image not built: no Docker permission) |
| Independent Codex review | IN PROGRESS | Codex 0.160.0 on the owner's Windows PC (logged in), read-only, clean GitHub clone | Rounds 1–5 each returned FAIL, with fewer findings each round (9, 7, 3, 3+2 low, 1+1+1 low). Confirmed fixed by Codex: round-1 #1, #2, #4, #5, #6, #7, #8, #9; B2, B3, B4, B6, B7; R3-3; both round-4 Windows portability fixes. Not yet confirmed: round-1 #3, B1, B5, R3-1, R3-2, R4-1, R4-2, R4-3, and round 5's R5-1, R5-2, R5-3. The builder has fixed these at the current head, each with a failing regression test first; they await round-6 re-review. Full findings in `docs/audits/phase1-codex-20261020-round{1..5}.md` | gate stays open until a round passes |

## Phases 2–4

| Phase | Status |
|---|---|
| Phase 2 — CHAD intelligence, safety and fast signals | NOT STARTED (waits for Phase 1 approval; see blockers B-01..B-07) |
| Phase 3 — Size, fire, exit and realized-profit accounting | NOT STARTED |
| Phase 4 — Hardening, full simulation and independent audit | NOT STARTED |

## Environment notes

- GitHub push from jinx-nyc verified 2026-10-20: `gh` 2.102.0 is logged in as CryptoCripLA (owner's fine-grained token, never committed).
- Railway is the intended JINX runtime; GMGN is the API and the separate hosted copy-trading service.

## GMGN Agent API authentication (jinx-nyc)

Purpose: JINX's automatic trading integration. There is no manual buy/sell workflow.

| Component | Status | Source | Tests | Evidence |
|---|---|---|---|---|
| Official `gmgn-cli` installed | PASSED | npm `gmgn-cli@1.6.7` (maintainer gmgn_infra, repo GMGNAI/gmgn-skills) | `gmgn-cli --version` → 1.6.7 | `~/.local/bin/gmgn-cli` |
| GMGN skills installed | PASSED | `npx skills add GMGNAI/gmgn-skills -g -a claude-code` (skills CLI 1.7.4) | 19 `gmgn-*` skills listed | `~/.claude/skills/gmgn-*` |
| Ed25519 request-signing key pair | PASSED | `gmgn-cli config` (official generator) | `openssl pkey` reads an ED25519 public key | `~/.config/gmgn/keypair.pem`, mode 600, dir 700. The private key never leaves jinx-nyc |
| Trusted IP for trading | PASSED (given to owner) | jinx-nyc outbound IPv4, verified by 3 services | — | 45.77.159.191 |
| API key created on gmgn.ai with this public key | BLOCKED | owner action | — | waiting for owner |
| `gmgn-cli config --apply` + authenticated call | NOT STARTED | signed `track follow-wallet` and one read call | pending | — |
| Railway secrets `GMGN_API_KEY` / `GMGN_PRIVATE_KEY` | NOT STARTED | the bridge reads both from env | pending | — |

Public key registered for this host (public, not a secret):

```
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAut2OCGLiTFMpgSNhpuAX0ZaNggCXGaD1svVq4+aQOeE=
-----END PUBLIC KEY-----
```

`GMGN_PRIVATE_KEY` signs API requests. It is not a Solana wallet key, but with the API key it can submit trades on the linked GMGN wallet. Keep it out of git, logs and chat.
