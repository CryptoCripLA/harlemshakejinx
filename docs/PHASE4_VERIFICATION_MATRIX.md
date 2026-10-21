# Phase 4 verification matrix — real-time hardening, full simulation, independent audit

Builder: Phase 4 agent (`~/work/harlemshakejinx-p4`, branch `phase4/hardening`), started 2026-10-21. Scope is exactly `CLAUDE.md` § "PHASE 4" (required implementation and verification, speed requirement, panel audit, acceptance) plus the owner's 2026-10-21 Phase 4 line ("Replay actual market trades and test whether the new wallet-selection and exit variants improve net paper results after fees, latency and slippage"). File ownership: `docs/AGENT_COORDINATION.md`. Phase 4 is read-only on `jinx/`; a defect found there goes to its owner as a failing test (strict xfail, so it turns red the moment the fix lands and the marker must go with it).

Status words only: NOT STARTED / IN PROGRESS / TESTING / PASSED / FAILED / BLOCKED. A PASSED row names the command that was run, its exit code and the evidence. Local processing time inside JINX is never called order-execution latency. Paper results are never called realized profit.

Test command for every `tests/phase4/` row: `/home/atlasops/work/harlemshakejinx/.venv/bin/python -m pytest -q tests/phase4/<file>` (exit 0 for every file below; results as of commit `856bff1` + this update). Shared harness (one interface for every builder): `tests/phase4/harness.py`. Mutation proof that the predicate and safety tests are not vacuous: `scripts/phase4_mutation_check.py` → `docs/evidence/PHASE4_MUTATION_20261021.json` (12 of 12 mutations caught).

## A. CLAUDE.md Phase 4 — required implementation and verification

| # | CLAUDE.md bullet | Test / script | Evidence | Status |
|---|---|---|---|---|
| V01 | Run the full continuous paper-trading system | `scripts/phase4_contabo_snapshot.py` (read-only) | `docs/evidence/PHASE4_CONTABO_LIVE_20261021T1520.json`: the engine on Contabo has run continuously since 07:01 UTC, 0 restarts, release `fe01850`, mode OFF, paper wallet reconciled. It hears, screens and decides; it does not trade, because Phase 3's paper loop is not on main or deployed and its parameters are owner-unset (B-03, B-30) | BLOCKED (B-50) |
| V02 | Feed current available market and on-chain observations into the actual event pipeline | same snapshot | 2.81 M PumpAPI events published (one connection for 8 h, 0 disconnects, receipt lag p50 50.9 ms / p95 78.7 ms), 7,299 Early Momentum detections, 5,958 screened, 168 of the newest 500 pass every entry check, 0 listener errors, 0 service failures. Caveat: status `degraded` because RAM is 1.34 GB against the 1 GB budget (the storage repair, owned by the storage session) | PASSED |
| V03 | Execute deterministic replay against captured real-market events | `test_p4_replay_determinism.py` (17 passed); `scripts/phase4_replay.py` | `docs/evidence/PHASE4_REPLAY_20261021.json`: 3 live captures, in-order vs 3 shuffled+resent deliveries each: identical decision-state digest | PASSED |
| V04 | Verify all documented CHAD entry predicates | `test_p4_chad_entry_predicates.py` (20 passed): L16, L41, L61, L65 (incl. exactly 30 s), L68 (incl. exactly 0.5), L69 (incl. exactly 10), L74, L78, L79, L81 (shared funder, unknown funder), L227, B-02 §5/§6/§7 | `PHASE4_MUTATION_20261021.json` | PASSED for every predicate with an implementation. X Scout RUG_FILTER (L106, B-05), TG Scout (L114, B-06), the cluster window and global confluence threshold (B-03) have no source or number: BLOCKED, and the cluster stays fail-closed (tested) |
| V05 | Verify every hard safety rejection | `test_p4_safety_rejections.py` (15 passed): honeypot, cannot-sell, mint authority, freeze authority, RugCheck rugged/mint/freeze, unreadable flags, malformed report, no data, a vetoed token staying vetoed; B-20 danger findings never block | `PHASE4_MUTATION_20261021.json` | PASSED |
| V06 | Verify every approved exit condition | `test_p4_exits.py` (to write against Phase 3 on main): Confluence Collapse, Negative Divergence 30/40/50/60, Sybil emergency, stop/trailing, BIGMAC_PAPER | Phase 3 exits exist on branch `phase3/size-fire-exit`, not on main | BLOCKED (B-50) |
| V07 | Verify the independence of all four upstream adapters | `test_p4_adapter_independence.py` (11 passed) + the static isolation tests `tests/test_adapters.py` | each intel module failing on every event leaves every other module byte-identical and creates no trade; a failed durable write admits nothing; GMGN/RugCheck failures leave the feed-built market view unchanged; D3AD/DataNomad cannot act; upstream trees verified unmodified | PASSED |
| V08 | Test reconnects, dropped messages and provider outages | `test_p4_outages.py` (15 passed, 1 strict xfail = finding P4-F1) | PumpAPI reconnect over a real websocket blocks every tracked history; unreachable PumpAPI/PumpPortal keep retrying, entries fail closed; lost messages never add a detection; GMGN and RugCheck down/timeout/garbage/rate-limited → UNKNOWN; Neon, Railway, Redis and object storage all down → paper entry and exit complete, outbox keeps rows, health reports failures | PASSED (finding P4-F1 open: top-level health stays `ok` while the PumpAPI trade feed is down) |
| V09 | Check position recovery after interruption | `test_p4_recovery.py` (4 passed): os._exit kill with an open position + pending exit + unfilled buy, and kills inside a buy or a sell fill | exit owner kept, unfilled buy released at zero cost, no phantom fill, wallet to the lamport, recovery idempotent | PASSED |
| V10 | Stress simultaneous token events | `test_p4_stress.py` (6 passed, 1 strict xfail = finding P4-F2) | 300 simultaneous launches (1,500 events) detected on their own buyers in 0.22 s locally; slot rule holds under a 50-intent burst, 16 threads and 8 processes; shed lookups leave candidates blocked | PASSED (finding P4-F2 open: a shed screening is never retried) |
| V11 | Stress order duplication and delayed confirmations | `test_p4_orders.py` (9 passed) | 20 replays of an entry or exit act once; conflicting replays refused; a buy awaiting confirmation holds its slot, holds no tokens, cannot be sold; a delayed sell keeps one exit owner; a late duplicate confirmation changes nothing; partial exits never oversell; OFF never blocks an exit | PASSED (paper; GMGN's own confirmation path arrives with Phase 3) |
| V12 | Verify transaction balance reconciliation | `test_p4_orders.py::test_every_lamport_reconciles_over_randomized_round_trips_on_real_reserves`, `test_p4_recovery.py` | 400 seeded steps on the live capture's reserves: wallet = start + entries after every step, one wallet entry per fill with the exact amount, wallet change = realized P&L − cost still held | PASSED (paper ledger; on-chain reconciliation needs live execution) |
| V13 | Run anti-lookahead checks | `test_p4_anti_lookahead.py` (5 passed) | all 38 live detections and both reserve gates equal a fresh engine fed only the records received so far; dev history counts only earlier launches; fills never re-priced; sells never at the hindsight peak | PASSED |
| V14 | Measure median, p95 and worst-observed decision/execution latency | `scripts/phase4_latency.py --speed 1` | `docs/evidence/PHASE4_LATENCY_20261021.json`: receipt → paper fill p50 1.43 ms / p95 2.19 ms / worst 2.38 ms (local, jinx-nyc); Contabo live receive → state p50 3.1 ms / p95 72 ms / worst 1,257 ms under the RAM overrun. Order landing on Solana: not measured (paper only, no GMGN order) | PASSED for local stages; landing time BLOCKED until live execution |
| V15 | Measure paper P&L after all modeled costs | `scripts/phase4_paper_pnl.py` (needs Phase 3's sizing, entries and exits on main) | — | BLOCKED (B-50) |
| V16 | Evaluate adverse price movement and failed exit conditions | `test_p4_exits.py`, `scripts/phase4_paper_pnl.py` | — | BLOCKED (B-50) |
| V17 | Verify that missing data fails safely | `test_p4_outages.py`, `test_p4_chad_entry_predicates.py` (unknown funder, no live feed, creation not observed), `test_p4_safety_rejections.py` (no data, unreadable flags) | every missing input blocks the entry; none is read as a pass | PASSED |
| V18 | Audit for secrets and private keys | `scripts/phase4_secrets_audit.py`; `test_p4_audits.py` (5 passed) | `docs/evidence/PHASE4_SECRETS_AUDIT_20261021.json`: every blob of every ref (GitHub has only `main`) and the working tree, against credential shapes, the 9 values configured on this host (names only) and ed25519-verified Solana keypairs: 0 findings | PASSED |
| V19 | Verify all dependency licenses | `scripts/phase4_license_audit.py`; `test_p4_audits.py` | `docs/evidence/PHASE4_LICENSE_AUDIT_20261021.json`: 22 Python (transitive) and 6 Node production packages, all declared; psycopg/psycopg-binary LGPL-3.0 (installed unmodified, noted); upstream license hashes match pins. `docs/LICENSES.md` was missing psycopg, psycopg-binary, redis, boto3, botocore: added | PASSED (BigMac Ms-PL to re-check when Phase 3 lands) |
| V20 | Verify no extra unapproved infrastructure or independent strategy/order controller | `test_p4_single_controller.py` (7 passed) | one order gateway, one fill writer, one ledger writer, one service unit, approved hosts and service roles only. Re-run when Phase 3 lands: its coordinator must be the one new order submitter | PASSED (on main as of 856bff1) |
| V21 | Owner 2026-10-21 "Prove the edge": wallet-selection and exit variants vs CHAD after fees, latency and slippage | `scripts/phase4_paper_pnl.py --variants` | needs Phase 3 research variants and the wallet-intelligence history | BLOCKED (B-50) |

## B. CLAUDE.md Phase 4 — speed requirement

| # | Rule | Test / script | Evidence | Status |
|---|---|---|---|---|
| S1 | No unnecessary polling wait in the Early Momentum event path | `test_p4_speed.py` | fast-lane handlers have no `await` or `sleep`; the detection is emitted inside the call that handles the third buyer; a stuck background consumer does not delay it | PASSED |
| S2 | No social-analysis queue blocking event detection | `test_p4_speed.py` | GMGN taking 2 s per call never delays the next detection (< 0.5 s each); X/TG scouts do not exist (B-05/B-06) | PASSED |
| S3 | No heavyweight historical analysis on the transaction hot path | `test_p4_speed.py` | fast-lane and order-path modules import no adapter, persistence, health, HTTP, PostgreSQL, Redis or S3 code | PASSED |
| S4 | No synchronous dashboard update delaying an order | `test_p4_speed.py` | with every health/service call hung, a paper entry and exit complete in < 0.5 s; the gateway is synchronous and awaits nothing | PASSED |
| S5 | No repeated API calls when a valid fresh observation is already cached | `test_p4_speed.py` (1 strict xfail = finding P4-F3) | a candidate is screened once while fresh (one getTokenSecurity) | FAILED (finding P4-F3: funders already known are fetched again for each new detection) |
| S6 | No unbounded queue | `test_p4_speed.py` | every asyncio queue and rolling buffer reachable from the engine is bounded (RugCheck's by admission control, max 200) | PASSED |
| S7 | Measure the actual timing and report every bottleneck | `scripts/phase4_latency.py` | ranked: (1) PumpAPI provider → receipt (capture p50 726 ms; soak p50 50 ms; Contabo p50 51 ms / p95 79 ms); (2) RugCheck indexing 7.9–10.2 s after creation gates every safety verdict; (3) Contabo receive → state p95 72 ms / worst 1.26 s under the RAM overrun; local stages ≤ 2.4 ms | PASSED |
| S8 | Do not claim seconds-level order landing unless measured | wording | no landing time claimed anywhere in Phase 4 evidence | PASSED |

## C. Panel audit and acceptance

| # | Item | How | Evidence | Status |
|---|---|---|---|---|
| P1 | Independent panel audit (Codex, read-only, own clone on the owner's Windows PC) with the reviewer inputs CLAUDE.md lists | `codex exec -s read-only` via kayjay-windows PowerShell | `docs/audits/phase4-codex-20261021-roundN.md` | NOT STARTED |
| P2 | Final operational summary (US dollars only) | § F | — | NOT STARTED |

## D. Blockers (Phase 4 range B-50..B-69; details in `docs/BLOCKERS.md`)

- **B-50** Phase 3's size/fire/exit and paper loop are not on main: V01 (trading part), V06, V15, V16, V21 wait for it, and then for owner-set exit and sizing parameters (B-03, B-30). Phase 4 pulls main and runs these the moment it lands.

## E. Findings sent to the file owners (strict xfail on main until fixed)

| Id | Severity | Where | Finding | Test | Owner |
|---|---|---|---|---|---|
| P4-F1 | MEDIUM | `jinx/health.py::build_health` | top-level `/health` stays `ok` while the PumpAPI trade feed (the one Early Momentum needs) is down; only the PumpPortal feed is judged. Entries already fail closed | `test_p4_outages.py::test_health_reports_degraded_when_the_pumpapi_trade_feed_is_down` | storage session `local_f5744230` |
| P4-F2 | MEDIUM (missed entries) | `jinx/app.py::_on_early_momentum` | a screening shed at 64 background tasks is never retried: 268 of 300 burst launches stayed unscreened after the burst | `test_p4_stress.py::test_a_candidate_shed_during_a_burst_is_screened_once_capacity_frees` | Phase 2 session `local_f5744230` |
| P4-F3 | LOW (API budget) | `jinx/app.py::_request_funders` | buyers whose funder is already known are fetched from GMGN again for every new detection | `test_p4_speed.py::test_S5_a_wallet_whose_funder_is_already_known_is_not_looked_up_again` | Phase 2 session `local_f5744230` |
| P4-F4 | LOW (docs) | `docs/LICENSES.md` | five runtime packages were unlisted | fixed by Phase 4 | — |

## F. Final operational summary

Not written yet.
