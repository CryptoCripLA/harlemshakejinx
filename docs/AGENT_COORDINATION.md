# Agent coordination — storage repair, Phase 3, Phase 4 (owner order 2026-10-21)

Owner order (verbatim, sha256 `adc49b94…80f8`): `docs/OWNER_DIRECTIVES_20261021.md` § "Storage repair, BigMac exit install, Phase 3 and Phase 4 agents". The owner ordered one agent to finish Phase 3 and one agent for Phase 4, while the storage repair runs in the existing Phase 2. Three builders work at the same time, so every file has exactly one owner.

## Who builds what

| Builder | Session | Checkout | Branch | Scope |
|---|---|---|---|---|
| Storage repair (Phase 2) | `local_f5744230` ("JINX Phase 2: Early Momentum, graduation, migration") | `~/work/harlemshakejinx-p2` | `phase2/storage-repair` | Owner order §1–§5 and §7: bounded memory, no raw-event warehouse, memory proof, the Contabo deployment |
| Phase 3 | p3 agent | `~/work/harlemshakejinx-p3` | `phase3/size-fire-exit` | CLAUDE.md Phase 3 in full, plus the owner's BigMac exit install order |
| Phase 4 | p4 agent | `~/work/harlemshakejinx-p4` | `phase4/hardening` | CLAUDE.md Phase 4 in full |

The lead session (`local_b73d1072`) keeps `jinx/intel/safety.py`, `jinx/intel/dev_reputation.py` and `jinx/adapters/rugcheck/`.

## File ownership

Only the owner of a file edits it. If you need a change in a file you do not own, send its owner the failing test and the exact change you need.

**Storage repair:**
- `jinx/core/dedup.py`, `jinx/core/bus.py`
- `jinx/intel/{whales,bundles,early_momentum,graduation,migration,bounded,readings,replay}.py`
- `jinx/store.py`: the market-event part only (`events`, `applied`, `record_event`, `unapplied_events`, `row_to_event`, `maintain`)
- `jinx/market_memory.py`, `jinx/core_state.py`, `jinx/registry.py`, `jinx/storage.py`, `jinx/recovery.py`, `jinx/sim.py`
- `jinx/health.py`: the memory/queue diagnostics section
- tests for those files, `scripts/memory_*`, `docs/INFRASTRUCTURE.md`, `docs/evidence/STORAGE_REPAIR_*`
- `deploy/contabo/*`. **The storage session is the only Contabo deployer.** Ask it for a deploy of a main commit.

**Phase 3:**
- new packages `jinx/exits/` (including `bigmac_exit.py`), `jinx/sizing/`, `jinx/confluence/`, the position monitor, the operator view
- `jinx/paper.py`, `jinx/execution.py`, `jinx/ledger.py`, `jinx/modes.py`, `jinx/ops/entry_slots.py`
- `jinx/store.py`: position/fill/exit tables only. Add tables or columns; never remove or rewrite market-event code.
- `third_party/bigmac/`, the BigMac rows of `THIRD_PARTY_NOTICES.md` and `docs/LICENSES.md`
- Phase 3 rows of `docs/SOURCE_FIDELITY_MATRIX.md`, `docs/evidence/PHASE3_*`, `docs/audits/phase3-*`, tests `tests/test_p3_*.py` and `tests/test_bigmac_*.py`

**Phase 4:**
- `tests/phase4/`, `scripts/phase4_*`, `docs/PHASE4_VERIFICATION_MATRIX.md`, `docs/evidence/PHASE4_*`, `docs/audits/phase4-*`
- Read-only on `jinx/`: a defect found there goes to that file's owner as a failing test.
- Files nobody owns (`jinx/adapters/*` except rugcheck, `jinx/config.py`): Phase 4 may fix them.

**Shared files.** Make small hunks, append, and rebase right before landing.
- `jinx/app.py` (wiring only)
- `docs/BUILD_STATUS.md`: edit only your own rows
- `docs/BLOCKERS.md`: new ids by builder. Storage uses B-21..B-29, Phase 3 uses B-30..B-49, Phase 4 uses B-50..B-69.
- `docs/OWNER_DIRECTIVES_20261021.md`: storage session only. `CLAUDE.md`: owner-ordered edits only.

**Schema versions.** `store.py` is at `SCHEMA_VERSION = 9`. Storage takes v10. Phase 3 takes the next free number when it lands. Whoever lands second rebases and renumbers its migration to max+1, and tests the upgrade from the version on main.

## Landing on main

1. Run `git fetch origin && git rebase origin/main`.
2. Run the full suite with `/home/atlasops/work/harlemshakejinx/.venv/bin/python -m pytest -q`. It must pass.
3. Push with `git push origin HEAD:main`. Fast-forward only: never force, never push a red main.
4. Commit by slice. Never leave tracked work uncommitted at a slice boundary.

## Rules every builder follows

- **Skills: show them, not just claim them.** At session start, call the Skill tool for all 11 skills in `CLAUDE.md`. Then write `docs/evidence/SKILLS_INVOCATION_<builder>_20261021.md` from your own session transcript JSONL, in the same format as `SKILLS_INVOCATION_20261020.md`: the tool-call ids, the results and the delivered skill titles. Add one line per skill naming the concrete rule it made you apply in this work.
- **Paper only.**
  - No live signing and no live orders.
  - GMGN trading methods stay refused by the bridge.
  - The mode on Contabo stays OFF unless the storage session deploys an owner-ordered paper AUTO run.
- **No invented strategy.** Unspecified CHAD numbers (B-03) stay fail-closed configuration. Use explicitly labelled test fixtures, never production defaults.
- **Storage hygiene** (`CLAUDE.md`, `docs/INFRASTRUCTURE.md`):
  - jinx-nyc has about 3.6 GB free. Temporary databases and captures go in a temp dir and are deleted.
  - No capture over 100 MB.
  - Run long work under `nice -n 19 ionice -c3`. No artificial load.
- **Independent Codex review** runs on the owner's Windows PC through the kayjay-windows PowerShell tool.
  - Use `codex exec -s read-only` in your own clone `E:\DevCache\agent-scratch\harlemshakejinx-review-<builder>-20261021`.
  - Prompt text: `scripts/codex_phase_review.sh`. Word prompts as correctness reviews, never "try to break it": OpenAI's filter stops those.
  - Save each round to `docs/audits/<phase>-codex-20261021-roundN.md`. Fix every finding before claiming PASS.
- **Do not spawn further agents.** The owner counts usage.
- **Reports to the owner are in US dollars.** Give the rate source and time once. SOL appears only in code, tests and evidence.
- **Owner acceptance.** Do not mark a phase accepted: only the owner accepts. Report status words exactly: NOT STARTED / IN PROGRESS / TESTING / PASSED / FAILED / BLOCKED.
