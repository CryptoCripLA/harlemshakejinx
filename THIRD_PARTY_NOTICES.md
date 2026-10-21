# Third-party notices

JINX runs or translates code from the owner-approved upstream repositories pinned in `UPSTREAM_PINS.json`. This file lists every JINX file that contains translated upstream code, the license, and what was changed (Apache License 2.0, section 4(b)).

## D3AD-E/Solana-sniper-bot @ a91ca33695ff4eafadd14db7d139c6ec3000c89a, Apache License 2.0

License: `third_party/d3ad/LICENSE` (identical to the upstream `LICENSE`; no NOTICE file exists at this commit).

| JINX file | Translated from | Changes |
|---|---|---|
| `jinx/paper.py` | `shred-sniper/sniper/src/pumpfun.rs:432-434` (`total_fee_bps`), `:439-449` (`initial_buy_price`), `:485-486` (`plan_buy` fee back-out); `shred-sniper/sniper/src/position.rs:631-646` (`sell_proceeds`) | Rust → Python; `initial_buy_price` applied to the current reserves rather than the curve's initial reserves; the real-token cap is optional because the live feed does not carry real reserves; `sell_proceeds` also returns the fee. Equivalence is checked against the original Rust compiled from the pinned source (`tests/test_paper.py::test_curve_math_matches_d3ad_source_exactly`). |

## GMGNAI/gmgn-skills @ 402671e2ab8d1336df9e20f4fc7da123dc719e59, MIT License, Copyright (c) 2025 GMGN

License: `third_party/gmgn/LICENSE`.

| JINX file | Translated from | Changes |
|---|---|---|
| `jinx/adapters/gmgn/bridge.mjs` | `src/config.ts:62` (escaped-newline handling of the private key), `src/config.ts:67` (default API host) | Re-expressed in the bridge's own credential loader. GMGN's `OpenApiClient` itself runs unmodified, compiled from the pinned source by `scripts/build_gmgn_bridge.sh`. |

## bigmacman1129/solana-sniper-trading-mev-bot @ 829f251d4e371102b406176c5bd459ebf4ac87ac, Microsoft Public License (Ms-PL)

Owner-ordered port (2026-10-21), not one of the four donor repositories. License: `third_party/bigmac/LICENSE.md` (identical to the upstream `LICENSE.md`; the upstream carries no separate copyright line or NOTICE file). The original file is vendored unmodified as `third_party/bigmac/helpers/exit-strategy.ts`; pin and hashes in `third_party/bigmac/SOURCE.json`. Ms-PL 3(D): the source-form portion is distributed only under Ms-PL, so `jinx/exits/bigmac_exit.py` is licensed under Ms-PL (not JINX's MIT license) and says so in its header.

| JINX file | Translated from | Changes |
|---|---|---|
| `jinx/exits/bigmac_exit.py` | `helpers/exit-strategy.ts:15-47` (`evaluateExit`), `:107-114` (`applyPercent`) | TypeScript → Python (the owner's supplied port); amounts are Python ints for TypeScript bigint, with `applyPercent`'s floor-and-clamp to a whole percent and BigInt's truncating division reproduced exactly; the take-profit price is computed only when it is used (the original computes it first, so only a non-finite take-profit percent behaves differently; JINX's config refuses those); `waitForExitCondition` (`:49-105`) is not ported: no duration/timeout exit, and the peak is persisted by JINX instead of starting at zero. Equivalence is checked against the original TypeScript run by node 22 (`tests/test_bigmac_differential.py`). |

## Upstream code run unmodified

The Docker image ships `upstream/gmgn` and `upstream/mako` with their own `LICENSE` files (MIT). JINX imports and runs them without changes: Mako's `src/pumpportal.py::PumpPortalStream` and `src/config.py`, and GMGN's compiled `OpenApiClient`.

DataNomad (datanomadhq/kaggle-solana-sniper-bot) has no license at the pinned commit. No DataNomad code is copied into or shipped with JINX.
