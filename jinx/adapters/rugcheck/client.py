"""RugCheck client: the official public API, read-only and unauthenticated (owner order 2026-10-21, B-19 resolved).

Endpoint: `GET https://api.rugcheck.xyz/v1/tokens/{mint}/report` (spec: https://api.rugcheck.xyz/swagger/index.html).
Published free access allows one unauthenticated request per second, so every request goes through one worker that
spaces calls at `min_interval_s` and honours `Retry-After` on 429.

What JINX takes from a report (`interpret`), with no threshold of its own:
- FAIL  RugCheck's own determinations that match CHAD's hard checks: `rugged: true`, or a mint or freeze authority
        still present. These are hard safety vetoes.
- PASS  a well-formed report that states `rugged: false` and both authorities as revoked (null; any other non-address
        value is unreadable and gives UNKNOWN, Phase 4 finding P4-F13).
- UNKNOWN anything else: missing fields, HTTP errors, 429, timeouts, malformed bodies, a full queue.
Risk findings (name, level, score, value), the raw `score`, `score_normalised` and liquidity are recorded as
evidence. They are not converted into a CHAD safety score or an approval threshold: no mapping is approved.

A token RugCheck has not indexed yet (HTTP 400 "not found"; measured 7.9-10.2 s after creation on 2026-10-21) is
UNKNOWN with error `not_indexed` and is not cached, so the caller can ask again.
Requests are deduplicated (one in-flight call per mint), fresh results are reused for `fresh_s`, candidates are
served by priority (lower number first, then arrival), and the cache and queue are bounded (owner storage rule 2).
Nothing is written to disk. Exits never call this client: protective exits do not wait on RugCheck.
"""
from __future__ import annotations

import asyncio
import itertools
import re
import time
from collections import OrderedDict, deque

import httpx

BASE_URL = "https://api.rugcheck.xyz"
SOURCE = "rugcheck.report"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")     # base58
DANGER_STATUS = "recorded as evidence; not a veto (no approved RugCheck-to-CHAD mapping)"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def unknown(error: str, detail: str = "") -> dict:
    return {"verdict": "UNKNOWN", "source": SOURCE, "error": error, "detail": detail or error, "facts": {}}


def _authority(value) -> str:
    """RugCheck reports a revoked authority as null and a held one as its address. Anything else ("", false, 0, [],
    {}, ...) is unreadable: never treated as revoked."""
    if value is None:
        return "revoked"
    if isinstance(value, str) and value:
        return "held"
    return "unreadable"


def interpret(report) -> dict:
    """Map one RugCheck report onto CHAD's RugCheck predicate (see the module docstring)."""
    if not isinstance(report, dict) or not all(k in report for k in ("rugged", "mintAuthority", "freezeAuthority")):
        return unknown("malformed", "report lacks rugged / mintAuthority / freezeAuthority")
    risks = [{"name": r.get("name"), "level": r.get("level"), "score": r.get("score"), "value": r.get("value")}
             for r in report.get("risks") or [] if isinstance(r, dict)]
    markets = [{"market_type": m.get("marketType"), "pubkey": m.get("pubkey"),
                "lp_locked_pct": (m.get("lp") or {}).get("lpLockedPct"), "lp_locked_usd": (m.get("lp") or {}).get("lpLockedUSD")}
               for m in report.get("markets") or [] if isinstance(m, dict)]
    facts = {
        "rugged": report.get("rugged"),
        "score": report.get("score"),
        "score_normalised": report.get("score_normalised"),
        "risks": risks,
        "danger_findings": [r["name"] for r in risks if r["level"] == "danger"],
        "danger_status": DANGER_STATUS,
        "liquidity": {"total_market_usd": _num(report.get("totalMarketLiquidity")),
                      "total_stable_usd": _num(report.get("totalStableLiquidity")),
                      "lp_providers": report.get("totalLPProviders"), "markets": markets},
        "holders": report.get("totalHolders"),
        "creator": report.get("creator"),
        "detected_at": report.get("detectedAt"),
    }
    fails, unreadable = [], []
    if report.get("rugged") is True:
        fails.append("RugCheck marks the token rugged")
    for key, label in (("mintAuthority", "mint"), ("freezeAuthority", "freeze")):
        state = _authority(report.get(key))
        if state == "held":
            fails.append(f"{label} authority present (RugCheck)")
        elif state == "unreadable":
            unreadable.append(f"{key}={report.get(key)!r}")
    if fails:
        return {"verdict": "FAIL", "source": SOURCE, "detail": "; ".join(fails), "facts": facts}
    if unreadable:      # Phase 4 finding P4-F13: only RugCheck's documented null means revoked
        return {"verdict": "UNKNOWN", "source": SOURCE, "error": "unreadable_authority",
                "detail": "authority not readable: " + ", ".join(unreadable), "facts": facts}
    if report.get("rugged") is False:
        return {"verdict": "PASS", "source": SOURCE, "detail": "", "facts": facts}
    return {"verdict": "UNKNOWN", "source": SOURCE, "error": "unstated", "detail": "rugged not stated", "facts": facts}


class RugCheckClient:
    def __init__(self, base_url: str = BASE_URL, min_interval_s: float = 1.0, timeout_s: float = 10.0,
                 fresh_s: float = 60.0, max_cache: int = 2_000, max_queue: int = 200,
                 transport: httpx.AsyncBaseTransport | None = None, clock=time.monotonic) -> None:
        self.base_url, self.min_interval_s, self.timeout_s, self.fresh_s = base_url, min_interval_s, timeout_s, fresh_s
        self.max_cache, self.max_queue = max_cache, max_queue
        self._transport, self._clock = transport, clock
        self._queue: asyncio.PriorityQueue | None = None
        self._inflight: dict[str, asyncio.Future] = {}
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._seq = itertools.count()
        self._not_before = 0.0
        self._latency_ms: deque[float] = deque(maxlen=500)
        self.stats = {"requests": 0, "ok": 0, "failed": 0, "rate_limited": 0, "timeouts": 0, "cache_hits": 0,
                      "deduplicated": 0, "queue_full": 0, "last_error": None, "last_ok_ns": None}

    def _q(self) -> asyncio.PriorityQueue:
        if self._queue is None:
            self._queue = asyncio.PriorityQueue()
        return self._queue

    async def report(self, mint: str, priority: int = 10) -> dict:
        """RugCheck's predicate for `mint`; never raises. Lower `priority` is served first."""
        if not isinstance(mint, str) or not MINT_RE.match(mint):
            return unknown("invalid_mint")
        hit = self._cache.get(mint)
        if hit and self._clock() - hit[0] < self.fresh_s:
            self._cache.move_to_end(mint)
            self.stats["cache_hits"] += 1
            return hit[1]
        if mint in self._inflight:
            self.stats["deduplicated"] += 1
            return await asyncio.shield(self._inflight[mint])
        q = self._q()
        if q.qsize() >= self.max_queue:
            self.stats["queue_full"] += 1
            return unknown("queue_full", "RugCheck queue full; candidate not checked")
        fut = asyncio.get_running_loop().create_future()
        self._inflight[mint] = fut
        q.put_nowait((priority, next(self._seq), mint, fut))
        return await asyncio.shield(fut)

    async def run(self) -> None:
        """The single worker: one request at a time, spaced to the free rate."""
        q = self._q()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s, transport=self._transport,
                                     headers={"Accept": "application/json", "User-Agent": "jinx/1"}) as http:
            while True:
                _, _, mint, fut = await q.get()
                wait = self._not_before - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                result = await self._fetch(http, mint)
                self._not_before = max(self._not_before, time.monotonic() + self.min_interval_s)
                if result["verdict"] != "UNKNOWN" or result.get("error") == "unstated":
                    self._cache[mint] = (self._clock(), result)
                    self._cache.move_to_end(mint)
                    while len(self._cache) > self.max_cache:
                        self._cache.popitem(last=False)
                self._inflight.pop(mint, None)
                if not fut.done():
                    fut.set_result(result)

    async def _fetch(self, http: httpx.AsyncClient, mint: str) -> dict:
        self.stats["requests"] += 1
        t0 = time.perf_counter()
        try:
            r = await http.get(f"/v1/tokens/{mint}/report")
        except httpx.TimeoutException:
            self.stats["timeouts"] += 1
            return self._failed("timeout")
        except httpx.HTTPError as e:
            return self._failed("network", type(e).__name__)
        self._latency_ms.append((time.perf_counter() - t0) * 1000)
        if r.status_code == 429:
            self.stats["rate_limited"] += 1
            retry = _num(r.headers.get("Retry-After"))
            self._not_before = time.monotonic() + (retry if retry is not None else 5.0)
            return self._failed("rate_limited")
        if r.status_code == 400 and "not found" in r.text.lower():
            # RugCheck has not indexed the token yet: measured 7.9-10.2 s after creation (2026-10-21)
            return self._failed("not_indexed")
        if r.status_code != 200:
            return self._failed(f"http_{r.status_code}")
        try:
            body = r.json()
        except ValueError:
            return self._failed("malformed")
        result = interpret(body)
        if result["verdict"] == "UNKNOWN":
            self.stats["failed"] += 1
            self.stats["last_error"] = result.get("error")
        else:
            self.stats["ok"] += 1
            self.stats["last_ok_ns"] = time.time_ns()
        return result

    def _failed(self, error: str, detail: str = "") -> dict:
        self.stats["failed"] += 1
        self.stats["last_error"] = error
        return unknown(error, detail)

    def health(self) -> dict:
        lat = sorted(self._latency_ms)
        pick = (lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 1)) if lat else (lambda q: None)
        return {"adapter": "rugcheck", "connected": self.stats["last_ok_ns"] is not None,
                "queue": self._queue.qsize() if self._queue else 0, "cache": len(self._cache),
                "latency_ms": {"n": len(lat), "p50": pick(0.5), "p95": pick(0.95), "p99": pick(0.99)}, **self.stats}
