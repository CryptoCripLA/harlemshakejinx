#!/usr/bin/env bash
# Independent read-only Codex review of a JINX phase (the builder does not grade itself).
# Usage: scripts/codex_phase_review.sh 1
# Needs `codex login` on this host. Writes docs/audits/phase<N>-codex-<date>.md.
set -euo pipefail
PHASE="${1:?phase number}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/audits/phase${PHASE}-codex-$(date -u +%Y%m%d).md"
codex login status >/dev/null 2>&1 || { echo "codex is not logged in: run 'codex login' first" >&2; exit 2; }
codex exec --sandbox read-only -C "$ROOT" -o "$OUT" - <<PROMPT
You are an independent reviewer of the JINX repository in the current directory. You did not write this code.
Review Phase ${PHASE} only, read-only. Do not modify files.

Read: CLAUDE.md (the owner's binding contract and four-phase mandate), docs/CHAD_BLUEPRINT.md (the architecture
bible), UPSTREAM_PINS.json and the pinned upstream code under upstream/, docs/SOURCE_FIDELITY_MATRIX.md,
docs/BLOCKERS.md, docs/LICENSES.md, BUILD_STATUS.md, docs/evidence/PHASE${PHASE}_EVIDENCE.md, all of jinx/ and tests/.

Check, with file:line evidence for every finding:
1. Every Phase ${PHASE} item in CLAUDE.md's mandate: implemented, tested, and honestly reported?
2. Any invented trading rule, threshold, score, sizing default or exit rule not traceable to CHAD or an owner order?
3. Upstream isolation: does any upstream import or modify another, share global config, or run its own
   order controller? Is any upstream trade-capable path reachable?
4. Translated upstream code (SRC comments): does it match the pinned source exactly?
5. Correctness: dedup and idempotency, ordering, the position ledger and slot limit, paper accounting,
   crash recovery, concurrency. Look for bugs the tests miss.
6. Can any path send a real-money order in this phase?
7. Are any claims in BUILD_STATUS.md or the evidence file unsupported by the code or test output?

Output: a verdict line "VERDICT: PASS | FAIL | BLOCKED", then findings ordered by severity
(CRITICAL / HIGH / MEDIUM / LOW), each with file:line and the concrete failure.
PROMPT
echo "review written to $OUT"
