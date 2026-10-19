---
name: jinx-profit-test-loop
description: Red-green-fix-immediate-profit-proof cycle after every JINX change; a negative net-SOL round trip fails acceptance and triggers root-cause iteration.
---

# JINX IMMEDIATE PROFIT TEST LOOP — NEVER SAVE TESTING FOR THE END

## Every fix gets an immediate proof
0. Before editing, record the precise failure, real executable baseline and expected benefit. Find the repo's own focused/full tests and replay/ledger tooling; inspect actual field names before using them.
1. **RED:** reproduce the defect (stuck sell, mistimed entry, bad quote, weak peak exit, stale re-entry, negative-cost math) with a failing fixture/test. Include the past real incident signature/time when available.
2. **GREEN:** implement the smallest faithful repair on the active owner/path. Run the focused test immediately.
3. **TRADE ECONOMICS:** exercise entry→hold→exit→fill confirmation→ledger on an event-time replay or the owner-requested execution test. Report executable net SOL with every attributable cost and the exact source of data. For actual live execution, report chain signatures and wallet reconciliation.
4. **PROFITABILITY ASSERTION:** if the run's attributable net SOL is negative or its exit fails, report **PROFIT ACCEPTANCE FAILED**; diagnose whether signal, entry, sizing, exit, fee drag or operational blockage caused it, correct, and rerun **now**, not at the end of a long feature batch. Do not mark this iteration complete.
5. **REGRESSION:** repeat previous failure fixtures and the repo's full relevant suite before finishing the item. Account for old positions and restart effects.
6. **LOG:** persist baseline, revised result, net SOL, MFE/MAE, exit capture, fees, test/replay command, evidence paths, code SHA and remaining deficiencies. Then move to the next change.

Do not confuse a positive replay with confirmed live profitability, or a signed order with a fill. In simulated tests state *simulated*, but still make the economic result pass/fail visible. Do not hide negative trades, reset a ledger to mask drawdowns, or call a code-only pass an economic victory.

Maintain the loop after each fix; do not code for hours and finally discover another negative round-trip or stranded position.

