# Upstream licenses and dependency audit (Phase 1, 2026-10-20)

JINX reuses code only from upstreams whose license permits it. Upstream code stays in `upstream/<name>/` at the commit pinned in `UPSTREAM_PINS.json`. It is fetched by `scripts/upstream_sync.py` and never committed to or edited in this repo. `tests/test_upstream_pins.py` fails if a pin, an upstream file or a license file changes.

| Upstream | Commit | License | Copyright | Reuse in JINX | NOTICE |
|---|---|---|---|---|---|
| GMGNAI/gmgn-skills | `402671e2ab8d` | MIT | (c) 2025 GMGN | Code. `src/client/OpenApiClient.ts` and `signer.ts` run unmodified behind `jinx/adapters/gmgn/` | none |
| SalahMessaoud/mako | `45dadc03f84d` | MIT | (c) 2026 Mako Contributors | Code. `src/pumpportal.py::PumpPortalStream` (with `src/config.py`) runs unmodified behind `jinx/adapters/mako/` | none |
| D3AD-E/Solana-sniper-bot | `a91ca33695ff` | Apache-2.0 (root and `shred-sniper/`) | not stated beyond the license template | Code. Phase 1 translates its bonding-curve math into `jinx/paper.py` (see `THIRD_PARTY_NOTICES.md`). Its sender/confirmation stack stays pinned and unwired while GMGN executes orders | none at this commit |
| datanomadhq/kaggle-solana-sniper-bot | `b79c9bd147b4` | **None** (no license file; README names no license) | — | **Reference only.** No code copied until the author grants permission | — |

Obligations:
- MIT (GMGN, Mako): keep the copyright and permission notice with any copied or redistributed portion. The Docker image ships the upstream trees together with their `LICENSE` files. GMGN lines re-expressed in `bridge.mjs` are covered by `third_party/gmgn/LICENSE` and `THIRD_PARTY_NOTICES.md`.
- Apache-2.0 (D3AD): keep the license, state changes to any modified file, and carry any NOTICE file (none exists at this commit). Done: `third_party/d3ad/LICENSE`, the change notice in `jinx/paper.py`, and `THIRD_PARTY_NOTICES.md`. All three ship in the Docker image. Checked by `tests/test_licenses.py`.
- No license (DataNomad): all rights reserved by default. JINX may read it for methodology but must not copy or ship its code.

## JINX's own Python dependencies (`requirements.txt`)

| Package | Version | License | Why |
|---|---|---|---|
| websockets | 17.3 | BSD-3-Clause | Mako's `PumpPortalStream` |
| python-dotenv | 1.2.4 | BSD-3-Clause | imported by Mako's `config.py` (its import-time `load_dotenv()` is neutralized by the adapter) |
| solders | 0.29.0 | Apache-2.0 OR MIT | imported by Mako's `pumpportal.py` |
| base58 | 2.1.1 | MIT | imported by Mako's `pumpportal.py` |
| httpx | 0.28.1 | BSD-3-Clause | imported by Mako's `pumpportal.py` |
| psycopg | 3.2.13 | LGPL-3.0-only (weak copyleft) | Neon and Railway PostgreSQL (`jinx/persistence/`). Installed unmodified from PyPI and loaded at run time; JINX does not copy, modify or vendor it |
| psycopg-binary | 3.2.13 | LGPL-3.0-only (weak copyleft) | pulled in by `psycopg[binary]` (prebuilt libpq); same use as psycopg |
| redis | 5.2.1 | MIT | Redis Cloud transient state (`jinx/persistence/redis_state.py`) |
| boto3 | 1.35.36 | Apache-2.0 | Neon Object Storage ledger backups (`jinx/persistence/object_store.py`) |
| botocore | 1.35.99 | Apache-2.0 | used by boto3 |
| pytest (dev) | 9.1.1 | MIT | tests |

Transitive closure (Phase 4 audit, 2026-10-21, `scripts/phase4_license_audit.py`, evidence `docs/evidence/PHASE4_LICENSE_AUDIT_20261021.json`): every installed dependency declares a permissive license (MIT, BSD, Apache-2.0, PSF-2.0, MPL-2.0 for certifi) except psycopg/psycopg-binary above. `jsonalias` (pulled in by solders) declares none in its package metadata; its repository's LICENSE is MIT.

The GMGN bridge's Node dependencies come from the pinned `upstream/gmgn/package-lock.json`: commander (MIT), dotenv (BSD-2-Clause), socks (MIT), undici (MIT).
