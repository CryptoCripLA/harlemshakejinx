---
name: jinx-forensic-profit-audit
description: Forensic audit of JINX runtime, all sniper owners, legacy gates, positions, entry and exit routes, and failures blocking net SOL profit.
---

# JINX FORENSIC PROFIT AUDIT — NO INVISIBLE BLOCKERS

Inspect the **actual production release**, not just the checkout. Verify runtime revision, processes, deployment symlinks, effective config, all strategy producers, every queue consumer and wallet/signer. Determine authoritative trade owner and whether a parallel old bot is still active.

Trace EACH transition: discovery → signal → FIRE → sign → submit → chain confirm → wallet balance/position → live exit monitor → exit signal → submit → confirm → close → net SOL. Every guard/veto/fee floor/entry block/exit block/time cutoff/disabled mode must have a reason and owner; separate legitimate owner mode from stale engineering residue.

Reconcile every token account, pending signature, partially recognized buy, historical holding, previous week's stranded position, venue migration and sell retry. Position custody survives restart, provider faults and partial failures. Identify why the engine could produce hundreds of events while old inventory was unknown or unsold. Validate that the active wallet—not just JSON—shows what is held.

Investigate stale pricing, incorrect pool/curve routes, insufficient lamports for transaction fees, duplicate intents, unconfirmed buy treated as filled, stale queue offsets, missing position monitor, quote/route mismatch, expired instructions and suppressed exits. Identify every upstream *and downstream* blocker rather than fixing only the first.

Deliver a table by actual file/function/line + runtime owner + proof + lost-profit impact + exact fix + immediate regression test. Remove conflicting legacy strategies and needless economic vetoes, retain the execution-consistency mechanisms that enable correct buys/sells. Reconcile from chain after each change. A stuck or invisible position is never a completed trade.

