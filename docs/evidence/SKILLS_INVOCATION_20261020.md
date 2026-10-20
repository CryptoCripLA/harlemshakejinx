# Evidence: the eleven required skills were invoked (2026-10-20)

Source: the Claude Code session transcript on jinx-nyc, session `52e20eb1-d1a0-40a4-baee-594b4a739c94` (`~/.claude/projects/-home-atlasops-codex-worktrees-JINX-engineering-ceabc2dd3f380301-claude-md-directive-review-adbf4b/52e20eb1-d1a0-40a4-baee-594b4a739c94.jsonl`). The rows were extracted by parsing that JSONL. Each row pairs a `Skill` tool call, the tool's result, and the skill body the harness then delivered into the session: the "Base directory for this skill" message followed by the skill's own title.

| Invoked (UTC) | Skill | Tool call id | Tool result | Body delivered (UTC) | Delivered title |
|---|---|---|---|---|---|
| 00:40:26.253 | jinx-profits | toolu_01WA6SLg3wQS3Rv9traSaJgS | Launching skill: jinx-profits | 00:40:26.271 | JINX PROFITS — THE BRAIN IS THE PRODUCT |
| 00:40:26.597 | kayjay-owner-fidelity | toolu_01JNHKfzGttseNQYVbM7VvfG | Launching skill: kayjay-owner-fidelity | 00:40:26.619 | Owner Fidelity: Execute the Request, Not a Substitute |
| 00:40:26.898 | kayjay-context-relay | toolu_011PuNdLSDfVVigsLWsLCcqm | Launching skill: kayjay-context-relay | 00:40:26.920 | Context Relay: Durable State, Fresh Execution |
| 00:40:27.258 | kayjay-total-project-integrity | toolu_01HTyKJuhgv3C58dXVsixRL5 | Launching skill: kayjay-total-project-integrity | 00:40:27.294 | KAYJAY TOTAL PROJECT INTEGRITY |
| 00:40:27.506 | kayjay-proof-gate | toolu_014uWsr8RsV7mQiS3gaUQ4Mb | Launching skill: kayjay-proof-gate | 00:40:27.528 | Proof Gate: No Claim Without Evidence |
| 00:40:29.659 | jinx-forensic-profit-audit | toolu_018BKw2h1fNLKvGV48z8hdZB | Launching skill: jinx-forensic-profit-audit | 00:40:29.683 | JINX FORENSIC PROFIT AUDIT — NO INVISIBLE BLOCKERS |
| 00:40:30.010 | jinx-profit-test-loop | toolu_01U1N6MR5cN9WQhoXgoZoe8e | Launching skill: jinx-profit-test-loop | 00:40:30.025 | JINX IMMEDIATE PROFIT TEST LOOP — NEVER SAVE TESTING FOR THE END |
| 00:40:30.311 | jinx-comparison | toolu_01NpmF5rrL9kX6QBUyqjZNWB | Launching skill: jinx-comparison | 00:40:30.333 | JINX COMPARISON — COMPETE AT THE SOURCE LEVEL |
| 00:40:30.611 | jinx-overkill | toolu_01A6x9U5bTp277wWmHtn5U1G | Launching skill: jinx-overkill | 00:40:30.629 | JINX OVERKILL — OVERBUILD THE EDGE, UNDERBUILD THE CLUTTER |
| 00:40:30.967 | superpowers:test-driven-development | toolu_01Ty7ZdwptbZzsWKAfuaM7Jr | Launching skill: superpowers:test-driven-development | 00:40:30.982 | Test-Driven Development (TDD) |
| 00:40:31.201 | contract-first | toolu_01JKY2nQN1mhmYXWdMycQaeU | Launching skill: contract-first | 00:40:31.225 | Contract-First Collaboration |

Also verified on jinx-nyc the same day:
- The repo's own startup hook (`.claude/hooks/jinx-profit-reminder.sh session`) found all eleven skill files and reported none missing.
- The five repo-vendored `jinx-*` skills are byte-identical to the user-global copies.

Limits: this proves the skills were invoked and their instructions loaded into that session. It does not prove that any later session loads them; each session must invoke them again, as `CLAUDE.md` requires.
