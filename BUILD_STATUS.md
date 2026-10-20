# BUILD STATUS — harlemshakejinx

Current stage: Phase 1 plan and approval pending.

- Full owner four-phase directive is committed in CLAUDE.md.
- New remote checkout: /home/atlasops/work/harlemshakejinx.
- Railway is the intended JINX runtime; GMGN is the API and separate hosted copy-trading service.
- All 11 core skill files are present remotely; Claude reports invoking all 11 through its Skill tool.
- Codex CLI installed and responds with version 0.163.0, but is not yet authenticated.
- GitHub push from jinx-nyc verified 2026-10-20: `gh` 2.102.0 is logged in as CryptoCripLA (owner's fine-grained token, never committed), and `git push --dry-run` to this repo succeeded.
- Independent Codex review has not run.
- CHAD threshold conflicts remain for the owner's decisions, not Claude's assumptions.

## GMGN Agent API authentication (jinx-nyc)

Purpose: JINX's automatic trading integration. There is no manual buy/sell workflow.

| Component | Status | Source | Tests | Evidence |
|---|---|---|---|---|
| Official `gmgn-cli` installed | PASSED | npm `gmgn-cli@1.6.7` (maintainer gmgn_infra, repo GMGNAI/gmgn-skills) | `gmgn-cli --version` → 1.6.7 | `~/.local/bin/gmgn-cli` |
| GMGN skills installed | PASSED | `npx skills add GMGNAI/gmgn-skills -g -a claude-code` (skills CLI 1.7.4) | 19 `gmgn-*` skills listed | `~/.claude/skills/gmgn-*` |
| Ed25519 request-signing key pair | PASSED | `gmgn-cli config` (official generator, `crypto.generateKeyPairSync("ed25519")`) | `openssl pkey` reads an ED25519 public key | `~/.config/gmgn/keypair.pem`, mode 600, dir 700. The private key never leaves jinx-nyc and is not in this repo |
| API key created on gmgn.ai with this public key | BLOCKED | Owner action in the GMGN Create API Key form | — | Waiting for owner |
| `gmgn-cli config --apply` + authenticated call | NOT STARTED | `gmgn-cli` verify (`track follow-wallet`, signed) and one read call | Pending | — |
| Railway secrets `GMGN_API_KEY` / `GMGN_PRIVATE_KEY` | NOT STARTED | `gmgn-cli` reads both from env | Pending | — |

Public key registered for this host (public, not a secret):

```
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAut2OCGLiTFMpgSNhpuAX0ZaNggCXGaD1svVq4+aQOeE=
-----END PUBLIC KEY-----
```

`GMGN_PRIVATE_KEY` signs API requests. It is not a Solana wallet key, but with the API key it can submit trades on the linked GMGN wallet. Keep it out of git, logs and chat.

Phase 1 is an **implementation phase**: isolated pinned donors, typed events, event bus, wallet/token registries, one position ledger, durable storage, AUTO/COPY/OFF, paper trading and recovery. No engine work starts before Claude presents the owner's source-faithful phase plan.

For each phase: source-to-code map, focused tests, full relevant tests, paper evidence, independent review, owner acceptance. Profits and early entries are the central objectives.

Do not expose credentials or copy the previous JINX runtime into this clean repo.
