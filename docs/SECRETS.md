# Secrets handling

The repository is public. No credential, key, token, wallet secret, runtime database or log may be committed, printed into chat, written into tests, prompts or documentation, or passed to a process that does not need it.

`tests/test_secrets.py` enforces this. It scans every file git would commit: credential-shaped strings, plus the exact GMGN values configured on the host, compared without being printed.

## 1. Inventory

| Secret | Purpose | Where it lives | Who reads it |
|---|---|---|---|
| `GMGN_API_KEY` | Authenticates every GMGN API request. With the signing key, it can also trade the GMGN-hosted wallet bound to it | jinx-nyc `~/.config/gmgn/.env` (mode 600, directory 700), written by `gmgn-cli config --apply` | GMGN bridge only (`bridge.mjs`), via env or that file |
| `GMGN_PRIVATE_KEY` | Ed25519 **request-signing** key for GMGN's signed routes (trading, followed wallets, holdings, order status). Not a Solana wallet key | jinx-nyc `~/.config/gmgn/.env` and `~/.config/gmgn/keypair.pem` (both 600). It was generated on jinx-nyc and has never left it; GMGN holds only its public key | GMGN bridge, when the adapter allows it (§3) |
| GitHub token | push to `CryptoCripLA/harlemshakejinx` | `gh` credential store on jinx-nyc | `git` / `gh` only |
| PumpPortal API key (future, B-01) | paid trade stream | `PUMPPORTAL_API_KEY` environment variable, when the owner provides one | Mako feed adapter only |
| X / Telegram credentials (future, B-05, B-06) | social scouts | not provided | — |

The GMGN wallet's own Solana keys are held by GMGN ("private keys are not stored locally"). JINX never sees them.

## 2. Rules

1. **Never committed.** `.gitignore` covers `.env`, `.env.*`, `*.pem`, `*.db*`, `data/` and `build/`. The secrets test fails on any committable file containing a GMGN key, a PEM private-key body, a GitHub token, or the configured GMGN values.
2. **Never printed.**
   - The bridge deletes `GMGN_DEBUG` (it makes the upstream client print full requests, API key included).
   - Health output reports only `configured` and `signing` as booleans.
   - Evidence files hold field names, counts and timings, never raw requests. Wallet addresses are shortened to first4…last4.
3. **Least exposure.** The bridge process gets only the variables listed in `docs/GMGN_ADAPTER_CONTRACT.md` §1. Nothing else from JINX's environment is passed.
4. **Trading needs three things.** A GMGN trade request needs:
   - the API key;
   - the signing key, which exists only on jinx-nyc;
   - the trusted IP 45.77.159.191 (jinx-nyc), registered with GMGN for trading.

   On JINX's side, the bridge also refuses every trading method, and `ExecutionGateway` LIVE is unavailable until the owner activates live trading (Phase 3).

## 3. Signing key in paper mode (owner decision)

The mandate said "Paper testing must not load any live signing key." The adapter's default follows that: without `allow_signing_key=True`, the variable is removed and the bridge ignores the file's value. Tested: `test_paper_bridge_does_not_receive_the_signing_key_by_default`, `test_bridge_ignores_the_signing_key_file_when_told_to`.

The owner then decided on 2026-10-20: "its ok for paper we need real data i dont care if it loaded whatever". The runtime therefore sets `jinx/config.py::GMGN_ALLOW_SIGNING_KEY = True`. Paper runs can read GMGN's signed routes: followed-wallet trades, own holdings, order status.

Trading stays refused by the bridge allowlist whatever the key state. Tested: `test_runtime_loads_the_signing_key_for_signed_reads_by_owner_decision_but_trading_stays_refused`. To withdraw the decision, set the constant to `False`; nothing else changes.

## 4. The GMGN API key shared in chat (owner decision)

On 2026-10-20 the GMGN API key was pasted into the conversation. The mandate said to treat it as exposed and rotate it. The owner then decided to keep using it: "use the fucking key claude".

| Fact | Detail |
|---|---|
| Where the key is now | Only in jinx-nyc `~/.config/gmgn/.env`. It is not in the repository (enforced by test), docs, tests or commit messages. The session transcript on jinx-nyc still contains it, because the owner pasted it there |
| What someone holding only the API key can do | Call GMGN's read routes against this account's quota. Draining the quota would slow JINX's data |
| What they cannot do | Trade. Trading also needs the signing key, which never left jinx-nyc, sent from the trusted IP |
| Risk accepted by | the owner, 2026-10-20 (recorded as B-16) |

**Rotation procedure** (when the owner chooses to rotate):
1. On gmgn.ai, create a new API key with the same public key (in `docs/BUILD_STATUS.md`) and trusted IP 45.77.159.191.
2. On jinx-nyc, run `gmgn-cli config --apply` with the new key, then `scripts/gmgn_capability_check.py`.
3. Delete the old key on gmgn.ai.

No code change is needed.

## 5. If a secret leaks into a commit

1. Rotate the secret first. Removing it from history does not un-publish a public repo.
2. Remove it from the tree and record the incident in `docs/BLOCKERS.md`.
3. Rewrite history only on the owner's explicit order.
