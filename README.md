# harlemshakejinx

JINX: one coordinated Solana trading engine built to the CHAD blueprint, **HEAR CHATTER → SIZE → FIRE → EXIT**.
The binding rules are in `CLAUDE.md`. Build progress is in `BUILD_STATUS.md`.

Current stage: **Phase 1 (foundation)**. Paper mode only. There is no strategy and no live execution path yet.

## Layout

| Path | What |
|---|---|
| `jinx/core/` | typed market events, dedup, the two-lane event bus |
| `jinx/store.py`, `registry.py`, `ledger.py` | durable store, token/wallet registries, the one position ledger |
| `jinx/modes.py`, `paper.py`, `execution.py` | AUTO/COPY/OFF, paper wallet and fills, the single order gateway |
| `jinx/core_state.py`, `recovery.py`, `health.py` | exactly-once event application, restart recovery, health |
| `jinx/adapters/{gmgn,mako,d3ad,datanomad}/` | the four isolated source adapters |
| `jinx/app.py`, `jinx/sim.py` | the runtime and the replay harness |
| `UPSTREAM_PINS.json` | the four approved upstream repos, pinned by commit |

## Run (paper)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
python3 scripts/upstream_sync.py          # fetch the four upstreams at their pinned commits
scripts/build_gmgn_bridge.sh              # compile GMGN's client (needs Node >= 20.18.1)
.venv/bin/python -m pytest                # full test suite
.venv/bin/python -m jinx run --seconds 60 # live engine; GET :8080/health
```

Optional environment: `PUMPPORTAL_API_KEY` (enables PumpPortal trade streams, which need a key funded with 0.02 SOL), and `GMGN_API_KEY` / `GMGN_PRIVATE_KEY` (or `~/.config/gmgn/.env` written by `gmgn-cli config --apply`). Secrets never go in this repo.

## Replay a capture

```bash
.venv/bin/python -m jinx run --seconds 900 --capture data/captures/live.jsonl.gz
.venv/bin/python -m jinx replay data/captures/live.jsonl.gz --shuffle-seed 1 --duplicate-every 3 --restart-at 100
```
