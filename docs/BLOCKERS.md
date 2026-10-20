# Blockers needing an owner decision or access (as of Phase 1, 2026-10-20)

Each entry: what is blocked, the evidence, what is needed, and one reversible option. Work that does not depend on a blocker continues.

## B-01 PumpPortal trade stream needs a funded API key (blocks live trades in Phase 1 capture; needed for Phase 2 signals)
- Evidence: PumpPortal replied on 2026-10-20: "'subscribeTokenTrade' and 'subscribeAccountTrade' methods are only available when connecting with an API key funded with at least 0.02 SOL." Mako's `pumpportal.py` predates this and assumes free trade streams.
- Effect now: the live feed carries token creations only (each including the creator's initial buy). Health reports `trade_stream: BLOCKED`.
- GMGN checked first (owner directive 2026-10-20): GMGN's API is polled request/response with plan rate limits. It gives aggregates (swaps, holders, curve progress) and smart-money/KOL trade lists, but no per-buy stream with the fee-payer wallet and time. It cannot drive CHAD's "3 buys · 3 unique wallets · 30-second window" trigger. See `docs/INTEGRATION_MAP.md` §4.
- Re-checked with the live Plus key, 2026-10-20 19:16 UTC (`docs/GMGN_CAPABILITY_INVENTORY.md` §5):
  - Smart Money and KOL trades carry maker, transaction hash and timestamp, but only for GMGN-tagged wallets.
  - Top traders are per-wallet aggregates.
  - No route streams every buy.

  B-01 still stands.
- Needed: an owner decision to create a PumpPortal API key and fund it with at least 0.02 SOL. Usage then costs 0.01 SOL per 10,000 trades streamed (pumpportal.fun/fees, read 2026-10-20). This is spending, so it waits for the owner. It is the minimum additional feed and plugs into the Mako stream JINX already runs.
- Reversible option: set `PUMPPORTAL_API_KEY`; the feed then subscribes every new mint's trades automatically. Unset it to return to creations only.

## B-02 CHAD contradicts itself on the Early Momentum curve gate
- Evidence: CHAD Bot 10 says "all below the 35 SOL bonding curve threshold", and the same section says "0.5–10 SOL bonding curve gate … (0.5 SOL floor) … (10 SOL ceiling)".
- Needed: the owner picks which gate applies, or how the two combine.

## B-03 CHAD values that are not specified (Phases 2-3)
These are left unset, and entries that depend on them stay disabled:
safety 0–100 formula and pass threshold; momentum "stall" and "sustained volume" quantities; X Scout verdict cut-offs and the @SolBotChad bonus; graduation progress → 0–20 points mapping; cluster "short window"; freshness decay function; dev-reputation formula and cut-off; confluence weights and the global score threshold; Kelly cap, payout-ratio prior and bucket edges; stop-loss and trailing values and the "high-conviction" cut-off; priority-fee escalation amounts; the shared-funder look-back.

## B-04 Position count: CHAD tiers vs the owner's standing slot rule
- CHAD product tiers allow 3, 10 or unlimited positions. The owner's permanent directive (2026-10-17) is one global slot, two only by owner order, never three.
- The GMGN mandate of 2026-10-20 says: "Do not hardcode a one-position limit unless that is explicitly present in the owner's approved configuration." The 2026-10-17 directive is that explicit configuration (the owner's global CLAUDE.md: "NO EXECUTION PATH MAY BYPASS THIS LIMIT"), so the limit stays. The slot count is a stored setting changed only by owner command (`jinx/ops/entry_slots.py`), not a literal in the decision code.
- Current build: the owner rule is enforced (`jinx/ledger.py`, `jinx/ops/entry_slots.py`). Changing it needs an explicit owner order.

## B-05 X Scout has no data source among the four approved repos
- Needed: X API access (or an approved X data provider), plus the Claude Haiku narrative call the owner specified.

## B-06 TG Scout has no data source among the four approved repos
- Needed: a Telegram account/API credentials for JINX and the owner's list of alpha groups.

## B-07 Helius gRPC (CHAD Bot 5 and Bot 10 "on-chain gRPC stream")
- CHAD names Helius gRPC. GMGN gives curve progress only by polling (`launchpad_progress`). The PumpPortal trade stream (B-01) carries curve reserves on every trade, which covers the same data without Helius.
- Needed: the owner's choice between Helius (paid) as CHAD names it, or B-01's stream as the source of curve data.

## B-08 GMGN API key — RESOLVED 2026-10-20
- The owner created the key with jinx-nyc's public key and trusted IP 45.77.159.191. `gmgn-cli config --apply` reported "Configuration verified successfully".
- Authenticated read-only checks pass through JINX's own bridge (`docs/GMGN_CAPABILITY_INVENTORY.md`).

## B-09 Codex on jinx-nyc
- `codex` 0.163.0 on jinx-nyc is not logged in. The Phase 1 review instead ran on the owner's Windows PC, where Codex (0.160.0) is logged in, against a clean clone of the GitHub repo plus the pinned upstreams. For future reviews from jinx-nyc, the owner runs `codex login` there once.

## B-10 Runtime host
- GMGN does not host custom code (its docs describe agents running on the customer's machine and calling the API), so JINX needs its own runtime.
- Under the owner's "minimum suitable existing environment" rule, that is jinx-nyc. It already runs the engine and holds the GMGN request key, and 45.77.159.191 is the trusted IP given to GMGN.
- Railway is not provisioned. It would need a demonstrated need, owner approval, and paid static outbound IPs (Pro) added to GMGN's trusted list. The repo's Dockerfile and `railway.json` stay ready but unused.

## B-11 DataNomad license
- No license file at the pinned commit; README names none. Code stays reference-only unless the author grants permission.

## B-12 Live pump.fun fee rates (Phase 3)
- Paper fills use D3AD's recorded mainnet values (95 + 5 bps). Phase 3 must read the live values from the pump.fun global account.

## B-13 D3AD fast path (superseded 2026-10-20)
- GMGN executes orders, so D3AD's ShredStream-based sender is not wired and ShredStream access is not requested. D3AD stays a pinned donor for pieces GMGN does not provide.

## B-14 Separate GMGN wallets for native copy trading and JINX
- If GMGN native copy trading and JINX share one GMGN wallet, copy-trade positions land in JINX's reconciliation and use up the owner's one-slot limit.
- Needed: the owner confirms JINX gets its own GMGN wallet (the API key binds to one wallet).
- Observed 2026-10-20 (read-only, from GMGN wallet activity): the wallet bound to JINX's API key traded ENERGY at 20:15–20:23 UTC (two buys of 0.297 SOL, two sells). No JINX process was running, and the bridge refuses every trading call. So these trades came from outside JINX, through GMGN copy trading or manually. Until B-14 is decided, JINX's Phase 3 reconciliation would have to tell those positions apart from its own.

## B-15 GMGN plan tier sets JINX's polling budget — RESOLVED 2026-10-20
- GMGN rate limits per plan: Free 5/5, Plus 20/20, Pro 50/50 weight per second. 1-second candles are Pro only.
- Measured on the owner's key: **Plus**. Evidence: GMGN's response header `x-ratelimit-limit: 20` (`docs/evidence/gmgn_capability_check_20261020T1952.json`), and 1-second candles refused with `PRO_PLAN_REQUIRED`.
- The owner's message said "Pro". The owner then confirmed: "its not pro its plus who cares claude use what we have available maximize jinx with what gmgn gives us".
- JINX paces itself to Plus (`jinx/adapters/gmgn/budget.py`). Changing plans is a one-line change (`CURRENT_PLAN`).

## B-16 GMGN API key was shared in chat; owner keeps using it (risk accepted)
- Evidence: the key was pasted into the session on 2026-10-20. The mandate said to treat it as exposed and rotate it; the owner then ordered "use the fucking key claude".
- Current state:
  - The key is only in jinx-nyc `~/.config/gmgn/.env` (mode 600). It is not in the repo, docs, tests or logs; `tests/test_secrets.py` checks every committable file against the configured value.
  - Someone holding only the key can spend this account's read quota.
  - Trading also needs the request-signing key, which never left jinx-nyc, and the trusted IP.
- Reversible option: rotate at any time with the steps in `docs/SECRETS.md` §4. No code change is needed.
