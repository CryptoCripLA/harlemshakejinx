"""GMGN adapter on the owner's Plus plan: the read allowlist matches GMGN's pinned client, the request-signing
key stays out of paper runs, and calls stay inside the Plus rate budget."""
import asyncio
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "upstream" / "gmgn" / "src" / "client" / "OpenApiClient.ts"
BRIDGE = ROOT / "jinx" / "adapters" / "gmgn" / "bridge.mjs"
TRADING = {"swap", "multiSwap", "createStrategyOrder", "cancelStrategyOrder", "createToken"}


def client_methods() -> set[str]:
    src = CLIENT.read_text()
    body = src[src.index("export class OpenApiClient"):]
    return {m for m in re.findall(r"^\s+async (\w+)\(", body, re.M)}


def bridge_allowlist() -> set[str]:
    text = BRIDGE.read_text()
    block = text[text.index("const READ_ONLY = new Set(["):text.index("]);", text.index("const READ_ONLY"))]
    return set(re.findall(r'"(\w+)"', block))


def test_bridge_allows_exactly_every_non_trading_method_of_the_pinned_client():
    methods = client_methods()
    assert "getTokenSignalV2" in methods and TRADING <= methods
    assert bridge_allowlist() == methods - TRADING


def test_paper_bridge_does_not_receive_the_signing_key_by_default(tmp_path):
    from jinx.adapters.gmgn.adapter import GmgnAdapter
    a = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k", "GMGN_PRIVATE_KEY": "secret"}, home=tmp_path)
    env = a.child_env()
    assert "GMGN_PRIVATE_KEY" not in env and env.get("JINX_GMGN_NO_SIGNING_KEY") == "1"
    allowed = GmgnAdapter(ROOT / "build" / "gmgn", env={"GMGN_API_KEY": "k", "GMGN_PRIVATE_KEY": "secret"},
                          home=tmp_path, allow_signing_key=True).child_env()
    assert allowed["GMGN_PRIVATE_KEY"] == "secret" and "JINX_GMGN_NO_SIGNING_KEY" not in allowed


@pytest.mark.skipif(not (ROOT / "build" / "gmgn" / "dist" / "client" / "OpenApiClient.js").exists()
                    or shutil.which("node") is None, reason="GMGN bridge not built")
def test_bridge_ignores_the_signing_key_file_when_told_to(tmp_path):
    """Even with a credentials file in HOME (as gmgn-cli writes it), a paper bridge reports signing off."""
    from jinx.adapters.gmgn.adapter import GmgnAdapter
    cfg = tmp_path / ".config" / "gmgn"
    cfg.mkdir(parents=True)
    (cfg / ".env").write_text("GMGN_API_KEY=k\nGMGN_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\\nX\\n-----END PRIVATE KEY-----\n")

    async def main():
        a = GmgnAdapter(ROOT / "build" / "gmgn", env={}, home=tmp_path)
        await a.start()
        try:
            return await a.health()
        finally:
            await a.stop()
    h = asyncio.run(main())
    assert h["configured"] is True and h["signing"] is False


def test_runtime_loads_the_signing_key_for_signed_reads_by_owner_decision_but_trading_stays_refused():
    from jinx import config
    assert config.GMGN_ALLOW_SIGNING_KEY is True          # owner 2026-10-20: real data in paper mode
    assert not (bridge_allowlist() & TRADING)              # swaps and strategy orders still refused in code
    app_src = (ROOT / "jinx" / "app.py").read_text()
    assert "allow_signing_key=config.GMGN_ALLOW_SIGNING_KEY" in app_src


# ---- Plus rate budget -------------------------------------------------------------------------
def test_every_allowlisted_method_has_a_documented_or_conservative_weight():
    from jinx.adapters.gmgn.budget import CONSERVATIVE_WEIGHT, ROUTE_WEIGHTS, weight
    assert bridge_allowlist() <= set(ROUTE_WEIGHTS) | {"getCookingStatistics"}
    # spot checks against GMGN's pinned skill docs (rate-limit tables)
    assert (weight("getFollowWallet"), weight("getTrenches"), weight("getTokenTopHolders"), weight("getSmartMoney"),
            weight("quoteOrder"), weight("getTokenSignalV2")) == (10, 2, 5, 1, 10, 1)
    assert weight("getCookingStatistics") == CONSERVATIVE_WEIGHT == 10  # not documented: assume the maximum


def test_plans_match_gmgns_documented_leaky_buckets():
    from jinx.adapters.gmgn.budget import PLANS
    assert {k: (p.rate, p.capacity) for k, p in PLANS.items()} == {"free": (5, 5), "plus": (20, 20), "pro": (50, 50)}


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    async def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_bucket_allows_a_full_burst_then_paces_to_the_plan_rate():
    from jinx.adapters.gmgn.budget import PLANS, RateBudget
    clock = FakeClock()
    b = RateBudget(PLANS["plus"], now=clock.now, sleep=clock.sleep)

    async def main():
        for _ in range(10):
            await b.acquire(2)          # 20 weight: exactly the Plus capacity, no waiting
        assert clock.sleeps == []
        await b.acquire(10)             # empty bucket: must wait 10 / 20 per second = 0.5 s
        return clock.t
    assert asyncio.run(main()) == pytest.approx(0.5)
    assert b.stats["waited_s"] == pytest.approx(0.5) and b.stats["weight_spent"] == 30


def test_bucket_refills_with_time_and_never_beyond_capacity():
    from jinx.adapters.gmgn.budget import PLANS, RateBudget
    clock = FakeClock()
    b = RateBudget(PLANS["plus"], now=clock.now, sleep=clock.sleep)

    async def main():
        await b.acquire(20)
        clock.t += 100                  # idle for a long time
        await b.acquire(20)             # capacity caps the refill: one full burst, no more
        await b.acquire(1)
    asyncio.run(main())
    assert clock.sleeps == [pytest.approx(1 / 20)]


def test_a_call_heavier_than_the_plan_capacity_is_refused_not_queued_forever():
    from jinx.adapters.gmgn.budget import PLANS, PlanCapacityExceeded, RateBudget
    b = RateBudget(PLANS["free"])
    with pytest.raises(PlanCapacityExceeded):
        asyncio.run(b.acquire(10))      # e.g. follow-wallet (10) can never fit a Free bucket of 5


def test_adapter_spends_budget_per_call_weight():
    from jinx.adapters.gmgn.adapter import GmgnAdapter
    from jinx.adapters.gmgn.budget import PLANS, RateBudget
    clock = FakeClock()
    budget = RateBudget(PLANS["plus"], now=clock.now, sleep=clock.sleep)
    a = GmgnAdapter(ROOT / "build" / "gmgn", env={}, budget=budget)

    async def fake_send(method, args):
        return {"ok": True, "result": method}
    a._send = fake_send

    async def main():
        for _ in range(3):
            await a.call("getFollowWallet", "sol")   # 3 x 10 weight on a 20/s bucket
    asyncio.run(main())
    assert budget.stats["weight_spent"] == 30 and clock.t == pytest.approx(0.5)
