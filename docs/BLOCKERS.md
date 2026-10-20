# Blockers needing an owner decision or access (as of Phase 1, 2026-10-20)

Each entry: what is blocked, the evidence, what is needed, and one reversible option. Work that does not depend on a blocker continues.

## B-01 PumpPortal trade stream needs a funded API key (blocks live trades in Phase 1 capture; needed for Phase 2 signals)
- Evidence: PumpPortal replied on 2026-10-20: "'subscribeTokenTrade' and 'subscribeAccountTrade' methods are only available when connecting with an API key funded with at least 0.02 SOL." Mako's `pumpportal.py` predates this and assumes free trade streams.
- Effect now: the live feed carries token creations only (each including the creator's initial buy). Health reports `trade_stream: BLOCKED`.
- Needed: an owner decision to create a PumpPortal API key and fund it with 0.02 SOL. This is spending, so it waits for the owner.
- Reversible option: set `PUMPPORTAL_API_KEY`; the feed then subscribes every new mint's trades automatically. Unset it to return to creations only.

## B-02 CHAD contradicts itself on the Early Momentum curve gate
- Evidence: CHAD Bot 10 says "all below the 35 SOL bonding curve threshold", and the same section says "0.5–10 SOL bonding curve gate … (0.5 SOL floor) … (10 SOL ceiling)".
- Needed: the owner picks which gate applies, or how the two combine.

## B-03 CHAD values that are not specified (Phases 2-3)
These are left unset, and entries that depend on them stay disabled:
safety 0–100 formula and pass threshold; momentum "stall" and "sustained volume" quantities; X Scout verdict cut-offs and the @SolBotChad bonus; graduation progress → 0–20 points mapping; cluster "short window"; freshness decay function; dev-reputation formula and cut-off; confluence weights and the global score threshold; Kelly cap, payout-ratio prior and bucket edges; stop-loss and trailing values and the "high-conviction" cut-off; priority-fee escalation amounts; the shared-funder look-back.

## B-04 Position count: CHAD tiers vs the owner's standing slot rule
- CHAD product tiers allow 3, 10 or unlimited positions. The owner's permanent directive (2026-10-17) is one global slot, two only by owner order, never three.
- Current build: the owner rule is enforced (`jinx/ledger.py`, `jinx/ops/entry_slots.py`). Changing it needs an explicit owner order.

## B-05 X Scout has no data source among the four approved repos
- Needed: X API access (or an approved X data provider), plus the Claude Haiku narrative call the owner specified.

## B-06 TG Scout has no data source among the four approved repos
- Needed: a Telegram account/API credentials for JINX and the owner's list of alpha groups.

## B-07 Helius gRPC (CHAD Bot 5 and Bot 10 "on-chain gRPC stream")
- Needed: a Helius plan with gRPC access (paid). Alternative trade source: B-01.

## B-08 GMGN API key
- Status: key pair generated on jinx-nyc, public key handed to the owner, trusted IP 45.77.159.191. Waiting for the owner to create the key; then `gmgn-cli config --apply` and live verification.
- Effect now: the GMGN adapter is built and contract-tested against a fake GMGN server; live calls report NOT_CONFIGURED.

## B-09 Codex review cannot run from jinx-nyc yet
- `codex` 0.163.0 is installed but not logged in. Needed: the owner runs `codex login` on jinx-nyc once.

## B-10 Railway provisioning
- The repo has a Dockerfile, and the engine serves `/health` on `$PORT`. No Railway project, service or volume has been created. Needed: owner confirmation to create them (it costs money). For JINX to trade through GMGN from Railway, the service also needs static outbound IPs (Railway Pro) added to GMGN's trusted IP list.

## B-11 DataNomad license
- No license file at the pinned commit; README names none. Code stays reference-only unless the author grants permission.

## B-12 Live pump.fun fee rates (Phase 3)
- Paper fills use D3AD's recorded mainnet values (95 + 5 bps). Phase 3 must read the live values from the pump.fun global account.

## B-13 D3AD fast path prerequisites (Phase 3)
- D3AD's shred-sniper consumes Jito ShredStream through its proxy. Needed in Phase 3: ShredStream access and a funded trading wallet, both owner decisions.
