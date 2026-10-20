# Upstream licenses and dependency audit (Phase 1, 2026-10-20)

JINX reuses code only from upstreams whose license permits it. Upstream code stays in `upstream/<name>/` at the commit pinned in `UPSTREAM_PINS.json`. It is fetched by `scripts/upstream_sync.py` and never committed to or edited in this repo. `tests/test_upstream_pins.py` fails if a pin, an upstream file or a license file changes.

| Upstream | Commit | License | Copyright | Reuse in JINX | NOTICE |
|---|---|---|---|---|---|
| GMGNAI/gmgn-skills | `402671e2ab8d` | MIT | (c) 2025 GMGN | Code. `src/client/OpenApiClient.ts` and `signer.ts` run unmodified behind `jinx/adapters/gmgn/` | none |
| SalahMessaoud/mako | `45dadc03f84d` | MIT | (c) 2026 Mako Contributors | Code. `src/pumpportal.py::PumpPortalStream` (with `src/config.py`) runs unmodified behind `jinx/adapters/mako/` | none |
| D3AD-E/Solana-sniper-bot | `a91ca33695ff` | Apache-2.0 (root and `shred-sniper/`) | not stated beyond the license template | Code, Phase 3 (execution). Phase 1 only pins and isolates it | none at this commit |
| datanomadhq/kaggle-solana-sniper-bot | `b79c9bd147b4` | **None** (no license file; README names no license) | — | **Reference only.** No code copied until the author grants permission | — |

Obligations:
- MIT (GMGN, Mako): keep the copyright and permission notice with any copied or redistributed portion. The Docker image ships the upstream trees together with their `LICENSE` files.
- Apache-2.0 (D3AD): keep the license, state changes to any modified file, and carry any NOTICE file (none exists at this commit).
- No license (DataNomad): all rights reserved by default. JINX may read it for methodology but must not copy or ship its code.

## JINX's own Python dependencies (`requirements.txt`)

| Package | Version | License | Why |
|---|---|---|---|
| websockets | 17.3 | BSD-3-Clause | Mako's `PumpPortalStream` |
| python-dotenv | 1.2.4 | BSD-3-Clause | imported by Mako's `config.py` (its import-time `load_dotenv()` is neutralized by the adapter) |
| solders | 0.29.0 | Apache-2.0 OR MIT | imported by Mako's `pumpportal.py` |
| base58 | 2.1.1 | MIT | imported by Mako's `pumpportal.py` |
| httpx | 0.28.1 | BSD-3-Clause | imported by Mako's `pumpportal.py` |
| pytest (dev) | 9.1.1 | MIT | tests |

The GMGN bridge's Node dependencies come from the pinned `upstream/gmgn/package-lock.json`: commander (MIT), dotenv (BSD-2-Clause), socks (MIT), undici (MIT).
