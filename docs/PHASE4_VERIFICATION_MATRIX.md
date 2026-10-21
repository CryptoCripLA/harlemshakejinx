# Phase 4 verification matrix — real-time hardening, full simulation, independent audit

Builder: Phase 4 agent (`~/work/harlemshakejinx-p4`, branch `phase4/hardening`), started 2026-10-21. Scope is exactly `CLAUDE.md` § "PHASE 4" (required implementation and verification, speed requirement, panel audit, acceptance) plus the owner's 2026-10-21 Phase 4 line ("Replay actual market trades and test whether the new wallet-selection and exit variants improve net paper results after fees, latency and slippage"). File ownership: `docs/AGENT_COORDINATION.md`. Phase 4 is read-only on `jinx/`; a defect found there goes to its owner as a failing test.

Status words only: NOT STARTED / IN PROGRESS / TESTING / PASSED / FAILED / BLOCKED. A PASSED row names the command that was run, its exit code and the evidence file. Local processing time inside JINX is never called order-execution latency. Paper results are never called realized profit.

Test command for every `tests/phase4/` row: `/home/atlasops/work/harlemshakejinx/.venv/bin/python -m pytest -q tests/phase4/<file>`.

## A. CLAUDE.md Phase 4 — required implementation and verification

| # | CLAUDE.md bullet | Test / script | Evidence | Status |
|---|---|---|---|---|
| V01 | Run the full continuous paper-trading system | `scripts/phase4_soak.py` (the real `JinxApp` with live feeds, mode as deployed) | `docs/evidence/PHASE4_SOAK_*.json` | NOT STARTED |
| V02 | Feed current available market and on-chain observations into the actual event pipeline | `scripts/phase4_soak.py`, `scripts/capture_pumpapi.py` | `docs/evidence/PHASE4_SOAK_*.json` | NOT STARTED |
| V03 | Execute deterministic replay against captured real-market events | `tests/phase4/test_p4_replay_determinism.py`, `scripts/phase4_replay.py` | `docs/evidence/PHASE4_REPLAY_*.json` | NOT STARTED |
| V04 | Verify all documented CHAD entry predicates | `tests/phase4/test_p4_chad_entry_predicates.py` (one test per CHAD line) | test names cite `docs/CHAD_BLUEPRINT.md` lines | NOT STARTED |
| V05 | Verify every hard safety rejection | `tests/phase4/test_p4_safety_rejections.py` | CHAD lines cited per test | NOT STARTED |
| V06 | Verify every approved exit condition | `tests/phase4/test_p4_exits.py` (Confluence Collapse, Negative Divergence ladder, Sybil emergency exit, stop/trailing, BigMac paper) | depends on Phase 3 landing on main | NOT STARTED |
| V07 | Verify the independence of all four upstream adapters | `tests/phase4/test_p4_adapter_independence.py` | — | NOT STARTED |
| V08 | Test reconnects, dropped messages and provider outages (PumpAPI, PumpPortal, GMGN, RugCheck, Neon, Railway, Redis) | `tests/phase4/test_p4_outages.py` | — | NOT STARTED |
| V09 | Check position recovery after interruption (hard kill with open positions and pending orders) | `tests/phase4/test_p4_recovery.py` | — | NOT STARTED |
| V10 | Stress simultaneous token events | `tests/phase4/test_p4_stress.py` | — | NOT STARTED |
| V11 | Stress order duplication and delayed confirmations | `tests/phase4/test_p4_orders.py` | — | NOT STARTED |
| V12 | Verify transaction balance reconciliation | `tests/phase4/test_p4_orders.py`, `tests/phase4/test_p4_recovery.py` | — | NOT STARTED |
| V13 | Run anti-lookahead checks | `tests/phase4/test_p4_anti_lookahead.py` | — | NOT STARTED |
| V14 | Measure median, p95 and worst-observed decision/execution latency | `scripts/phase4_latency.py` | `docs/evidence/PHASE4_LATENCY_*.json` | NOT STARTED |
| V15 | Measure paper P&L after all modeled costs | `scripts/phase4_paper_pnl.py` | depends on Phase 3's paper loop on main | NOT STARTED |
| V16 | Evaluate adverse price movement and failed exit conditions | `tests/phase4/test_p4_exits.py`, `scripts/phase4_paper_pnl.py` | depends on Phase 3 | NOT STARTED |
| V17 | Verify that missing data fails safely | `tests/phase4/test_p4_missing_data.py` | — | NOT STARTED |
| V18 | Audit for secrets and private keys (tree and full git history) | `scripts/phase4_secrets_audit.py`, `tests/phase4/test_p4_audits.py` | `docs/evidence/PHASE4_SECRETS_AUDIT_*.json` | NOT STARTED |
| V19 | Verify all dependency licenses | `scripts/phase4_license_audit.py`, `tests/phase4/test_p4_audits.py` | `docs/evidence/PHASE4_LICENSE_AUDIT_*.json` | NOT STARTED |
| V20 | Verify no extra unapproved infrastructure or independent strategy/order controller | `tests/phase4/test_p4_single_controller.py` | — | NOT STARTED |
| V21 | Owner 2026-10-21 "Phase 4 — Prove the edge": replay actual trades; compare new wallet-selection and exit variants against CHAD after fees, latency and slippage | `scripts/phase4_paper_pnl.py --variants` | depends on Phase 3 research variants | NOT STARTED |

## B. CLAUDE.md Phase 4 — speed requirement

| # | Rule | Test / script | Evidence | Status |
|---|---|---|---|---|
| S1 | No unnecessary polling wait in the Early Momentum event path | `tests/phase4/test_p4_speed.py` | — | NOT STARTED |
| S2 | No social-analysis queue blocking event detection | `tests/phase4/test_p4_speed.py` | — | NOT STARTED |
| S3 | No heavyweight historical analysis on the transaction hot path | `tests/phase4/test_p4_speed.py` | — | NOT STARTED |
| S4 | No synchronous dashboard update delaying an order | `tests/phase4/test_p4_speed.py` | — | NOT STARTED |
| S5 | No repeated API calls when a valid fresh observation is already cached | `tests/phase4/test_p4_speed.py` | — | NOT STARTED |
| S6 | No unbounded queue | `tests/phase4/test_p4_speed.py` | — | NOT STARTED |
| S7 | Measure the actual timing and report every bottleneck | `scripts/phase4_latency.py` | `docs/evidence/PHASE4_LATENCY_*.json` | NOT STARTED |
| S8 | Do not claim seconds-level order landing unless measured | report wording; no GMGN order is placed in paper mode | — | NOT STARTED |

## C. Panel audit and acceptance

| # | Item | How | Evidence | Status |
|---|---|---|---|---|
| P1 | Independent panel audit (Codex, read-only, own clone on the owner's Windows PC) with the reviewer inputs CLAUDE.md lists | `codex exec -s read-only` via kayjay-windows PowerShell | `docs/audits/phase4-codex-20261021-roundN.md` | NOT STARTED |
| P2 | Final operational summary (implemented and verified / implemented but unverified / blocked / failed / live-readiness constraints), US dollars only | this file § D | — | NOT STARTED |

## D. Final operational summary

Not written yet.
