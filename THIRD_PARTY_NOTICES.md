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

## Upstream code run unmodified

The Docker image ships `upstream/gmgn` and `upstream/mako` with their own `LICENSE` files (MIT). JINX imports and runs them without changes: Mako's `src/pumpportal.py::PumpPortalStream` and `src/config.py`, and GMGN's compiled `OpenApiClient`.

DataNomad (datanomadhq/kaggle-solana-sniper-bot) has no license at the pinned commit. No DataNomad code is copied into or shipped with JINX.
