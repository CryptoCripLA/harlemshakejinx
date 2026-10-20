"""GMGN request budget: the plan's leaky bucket, so JINX can use all of it without tripping bans.

GMGN rate-limits each API key with a leaky bucket per plan (rate refilled per second / capacity), and each
route costs a documented weight. Requests over the limit return 429; repeated requests during a cooldown
extend the ban by 5 s each, up to 5 minutes. So JINX paces itself instead of probing the limit.

Sources (gmgn-skills @ 402671e): skills/gmgn-token/SKILL.md, gmgn-market, gmgn-track, gmgn-portfolio,
gmgn-swap and gmgn-callout "Rate Limit Handling" tables. The owner's key measured x-ratelimit-limit: 20 and was
refused 1-second candles with PRO_PLAN_REQUIRED on 2026-10-20, i.e. the Plus plan.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    name: str
    rate: float      # weight refilled per second
    capacity: float  # bucket size (largest burst)


PLANS = {
    "free": Plan("free", 5, 5),
    "plus": Plan("plus", 20, 20),
    "pro": Plan("pro", 50, 50),
}
CURRENT_PLAN = "plus"

# weight per GMGN client method, as documented in the pinned skill rate-limit tables
ROUTE_WEIGHTS = {
    "getTokenInfo": 1, "getTokenSecurity": 1, "getTokenPoolInfo": 1,
    "getTokenTopHolders": 5, "getTokenTopTraders": 5,
    "getTokenKline": 2, "getTrendingSwaps": 3, "getTrenches": 2, "getTokenSignalV2": 1,
    "getHotSearches": 3, "searchMarket": 1,
    "getFollowTokens": 3, "getFollowGroupNames": 1, "getFollowWallet": 10, "getKol": 1, "getSmartMoney": 1,
    "getWalletHoldings": 2, "getUserInfo": 2, "getWalletActivity": 3, "getWalletStats": 3,
    "getWalletProfits": 3, "getWalletTokenBalance": 2, "getCreatedTokens": 2,
    "getCalloutRecord": 3, "getCalloutGlobal": 3,
    "quoteOrder": 10, "queryOrder": 5, "getStrategyOrders": 1, "getGasPrice": 1,
    "swap": 10, "multiSwap": 10, "createStrategyOrder": 5, "cancelStrategyOrder": 2,
}
CONSERVATIVE_WEIGHT = 10  # for routes GMGN does not document (e.g. getCookingStatistics): assume the maximum


def weight(method: str) -> int:
    return ROUTE_WEIGHTS.get(method, CONSERVATIVE_WEIGHT)


class PlanCapacityExceeded(Exception):
    """The call costs more than the plan's whole bucket; GMGN answers 403 PLAN_UPGRADE_REQUIRED."""


class RateBudget:
    def __init__(self, plan: Plan, now=time.monotonic, sleep=asyncio.sleep) -> None:
        self.plan = plan
        self._now, self._sleep = now, sleep
        self._tokens = plan.capacity
        self._last = now()
        self._lock: asyncio.Lock | None = None
        self.stats = {"weight_spent": 0, "waited_s": 0.0, "calls": 0}

    def _refill(self) -> None:
        t = self._now()
        self._tokens = min(self.plan.capacity, self._tokens + (t - self._last) * self.plan.rate)
        self._last = t

    async def acquire(self, w: float) -> None:
        if w > self.plan.capacity:
            raise PlanCapacityExceeded(f"weight {w} exceeds the {self.plan.name} plan capacity {self.plan.capacity}")
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:  # first come, first served: no caller can starve another
            self._refill()
            if self._tokens < w:
                wait = (w - self._tokens) / self.plan.rate
                self.stats["waited_s"] += wait
                await self._sleep(wait)
                self._refill()
            self._tokens -= w
            self.stats["weight_spent"] += w
            self.stats["calls"] += 1
