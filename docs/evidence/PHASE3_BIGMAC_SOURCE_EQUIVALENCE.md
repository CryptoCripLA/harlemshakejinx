# BigMac exit port: source equivalence (2026-10-21)

Original: `bigmacman1129/solana-sniper-trading-mev-bot` @ `829f251d4e371102b406176c5bd459ebf4ac87ac` (`git ls-remote` HEAD and main, 2026-10-21), `helpers/exit-strategy.ts`, Ms-PL. Vendored byte-for-byte in `third_party/bigmac/` (sha256 matches raw.githubusercontent.com at that commit). Port: `jinx/exits/bigmac_exit.py`.

Method (`tests/test_bigmac_differential.py`): the original `evaluateExit` (lines 15-47) and `applyPercent` (lines 107-114) are cut from the vendored file by line range, hash-checked against `third_party/bigmac/SOURCE.json`, and run unmodified by node v22.23.3 (which strips the TypeScript types itself). The Python port gets identical inputs. Compared: the decision (stop_loss / take_profit / trailing_stop / none), the returned peak, and whether an error is raised.

## RED: the owner's port as supplied (before the two fixes)

```
owner verbatim port, applyPercent: 578 of 696 finite cases differ from the TypeScript; decision (reason) differs in 0
  input {'fn': 'apply', 'amount': '0', 'percent': 5e-324, 'add': True} 
   TS  {'value': '0'} 
   PY  {'value': '0E-353'}
  input {'fn': 'apply', 'amount': '0', 'percent': 5e-324, 'add': False} 
   TS  {'value': '0'} 
   PY  {'value': '0E-353'}
  input {'fn': 'apply', 'amount': '0', 'percent': 0.5, 'add': True} 
   TS  {'value': '0'} 
   PY  {'value': '0.000'}
owner verbatim port, evaluateExit: 969 of 6304 finite cases differ from the TypeScript; decision (reason) differs in 969
  input {'fn': 'eval', 'current': '1', 'entry': '1', 'peak': '0', 'tp': 0, 'sl': 0.5, 'ts': 0, 'tsa': 0} 
   TS  {'reason': 'stop_loss', 'peak': '1'} 
   PY  {'reason': None, 'peak': '1'}
  input {'fn': 'eval', 'current': '1', 'entry': '1', 'peak': '0', 'tp': 0, 'sl': 1, 'ts': 0, 'tsa': 0} 
   TS  {'reason': 'stop_loss', 'peak': '1'} 
   PY  {'reason': None, 'peak': '1'}
  input {'fn': 'eval', 'current': '1', 'entry': '1', 'peak': '0', 'tp': 0, 'sl': 10, 'ts': 0, 'tsa': 0} 
   TS  {'reason': 'stop_loss', 'peak': '1'} 
   PY  {'reason': None, 'peak': '1'}
```

The supplied `apply_percent` used fractional decimal percentages. The original floors the percent to a whole number, clamps it at zero, and divides with BigInt's truncating integer division. With the supplied version, JINX would have made a different sell decision than BigMac's code in 969 of 6,304 finite cases: for example a 0.5 % stop, which BigMac treats as 0 % (sell at the entry price), never fired.

## GREEN: after the two ordered fixes (Ms-PL header; integer `applyPercent`)

```
applyPercent: 768 cases, identical 768, different 0 (all documented non-finite take-profit: True); TS outcomes {None: 720, 'error': 48}
evaluateExit: 6767 cases, identical 6728, different 39 (all documented non-finite take-profit: True); TS outcomes {'stop_loss': 1304, None: 2148, 'take_profit': 1979, 'trailing_stop': 1162, 'error': 174}
```

- `applyPercent`: identical on all 768 cases (12 amounts including 0, negatives and 10^40; 32 percents including fractions, negatives, -0, 5e-324, 1e308, NaN, ±Infinity; add and subtract). NaN and +Infinity raise like `BigInt()`; -Infinity clamps to 0 like `Math.max(0, -Infinity)`.
- `evaluateExit`: identical decisions and peaks on 6,728 of 6,767 cases: exact stop, take-profit, activation and trail-floor prices with one below and one above, several conditions true at once, huge integers, and 4,000 seeded random cases. All four outcomes occur (stop_loss 1,304, take_profit 1,979, trailing_stop 1,162, none 2,148).
- The 39 differing cases are all the one documented difference: a NaN take-profit percent (or +Infinity when the stop fires first). The original computes the take-profit price before the stop check (exit-strategy.ts:25) and throws; the owner's port computes it only when it is used. The owner ordered exactly two fixes, so this structure is kept. JINX's BigMac settings refuse every non-finite percent, so the case cannot occur in JINX.
- Decision order with several conditions true: SL before TP before trailing (`test_decision_order_with_several_conditions_true`).

## Not ported

`waitForExitCondition` (lines 49-105): its duration/'timeout' exit is not installed (owner order §6: no five-minute forced exit), and its peak, which restarts at 0 on every call, is replaced by a peak persisted in the ledger (`PositionLedger.raise_peak`, survives restart: `tests/test_p3_exit_intent.py::test_peak_is_persisted_and_survives_restart`).

## Units used by JINX (same as BigMac's pump.fun path, bot.ts:1008-1023)

- entry = the SOL the held tokens cost (BigMac: the SOL spent on the buy); after a partial sell, the cost of the tokens still held.
- current = the SOL a sell of all held tokens would return on the curve now, net of the curve fee (BigMac: `computeSolOutForTokens`, 1 % fee; JINX: `paper.sell_proceeds` at the configured 95 + 5 bps).
- On any exit reason BigMac's pump.fun path sells the full held amount (bot.ts:838-866), so a BigMac exit intent sells 100 %.
