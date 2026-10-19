#!/usr/bin/env bash
set -euo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-.}"
PHASE="${1:-session}"
echo "OWNER MISSION: PROFITTTTTTS — MAXIMUM REALIZED NET SOL. EARLY EXPLOSIVE ENTRY IS PRIORITY #1. FAST FIRE, SMART SIZING, EXITS THAT REALIZE MONEY."
echo "BIBLE: $ROOT/docs/CHAD_BLUEPRINT.md — exact owner's SolChad source. DO NOT DESIGN NEW TRADING RULES."
echo "NEW CANONICAL REPO: CryptoCripLA/harlemshakejinx. Parallel GMGN native COPY TRADING continues. JINX GMGN API integration is separate from managed hosting."
echo "PHASE GATE: ONE PHASE ONLY. TEST AND SOURCE-TRACE. INDEPENDENT CODEX/GPT REVIEW. STOP FOR OWNER ACCEPTANCE."
echo "ELEVEN REQUIRED: jinx-profits; jinx-forensic-profit-audit; jinx-profit-test-loop; jinx-comparison; jinx-overkill; kayjay-owner-fidelity; kayjay-context-relay; kayjay-total-project-integrity; kayjay-proof-gate; superpowers:test-driven-development; contract-first."
if [ "$PHASE" = "session" ]; then
  for file in "$ROOT/CLAUDE.md" "$ROOT/docs/CHAD_BLUEPRINT.md" "$ROOT/.claude/skills/jinx-profits/SKILL.md"; do
    if [ -f "$file" ]; then
      printf '\n=== REQUIRED SOURCE: %s ===\n' "$file"
      cat "$file"
      printf '\n=== END SOURCE ===\n'
    else
      echo "REQUIRED SOURCE MISSING: $file — PHASE BLOCKED" >&2
    fi
  done
  for skill in jinx-forensic-profit-audit jinx-profit-test-loop jinx-comparison jinx-overkill; do
    file="$ROOT/.claude/skills/$skill/SKILL.md"
    if [ -f "$file" ]; then
      printf '\n=== SKILL: %s ===\n' "$skill"
      cat "$file"
      printf '\n=== END SKILL ===\n'
    else
      echo "MISSING REQUIRED SKILL: $file" >&2
    fi
  done
  for skill in kayjay-owner-fidelity kayjay-context-relay kayjay-total-project-integrity kayjay-proof-gate contract-first; do
    file="$HOME/.claude/skills/$skill/SKILL.md"
    if [ -f "$file" ]; then
      printf '\n=== GLOBAL SKILL: %s ===\n' "$skill"
      cat "$file"
      printf '\n=== END SKILL ===\n'
    else
      echo "MISSING REQUIRED GLOBAL SKILL: $file" >&2
    fi
  done
  found=0
  for file in "$HOME"/.claude/plugins/cache/claude-plugins-official/superpowers/*/skills/test-driven-development/SKILL.md "$HOME"/.claude/plugins/cache/superpowers-marketplace/superpowers/*/skills/test-driven-development/SKILL.md "$HOME"/.claude/skills/test-driven-development/SKILL.md; do
    if [ -f "$file" ]; then
      printf '\n=== SUPERPOWERS TDD SKILL: %s ===\n' "$file"
      cat "$file"
      printf '\n=== END SKILL ===\n'
      found=1
      break
    fi
  done
  if [ "$found" -eq 0 ]; then echo "MISSING REQUIRED SKILL: superpowers:test-driven-development" >&2; fi
fi
