"""Phase 4 harness: one documented interface for replay, fault injection and state digests.

Shared by the Phase 4 tests and any other builder (the storage session calls the same functions for its memory and
replay proof instead of keeping a copy; owner order 2026-10-21, docs/AGENT_COORDINATION.md).

Contract (inputs -> outputs; nothing here places an order on its own, nothing touches the network):

    build_app(data_dir, gmgn=None, rugcheck=None, safety=None, **settings) -> JinxApp
        The real composition root (`JinxApp._build_core`): bus with dedup and durable write, core state, ledger,
        paper wallet, the one order gateway, and every intel module on its real lane. No feeds, no services.
        `gmgn` / `rugcheck` are optional fakes (below); with `gmgn`, a SafetyService is wired as `start()` would.
    deliveries(records, source, shuffle_seed=None, duplicate_every=None, drop_every=None) -> list[list | None]
        A capture as MarketEvents in delivery order. Sequence numbers come from each record's capture position
        (jinx/sim.py::capture_events), so reordering or resending the same records gives the same facts.
        `drop_every=n` removes every n-th record (lost messages) after duplication and before shuffling.
    run(app, deliveries, settle=True) -> RunStats
        Publishes every event through `app.bus` and drains both lanes; with `settle`, waits for the background
        lookups the app spawned (safety screening, funders, pools, holders).
    state_view(app) -> dict ; state_digest(app) -> str
        The decision-relevant state, canonical JSON: token and wallet registries, every Early Momentum detection
        (window, fee payers, predicate statuses and details), both reserve gates as the entry decision reads them,
        graduation and migration evidence, the ledger and the paper wallet. Wall-clock and arrival-order receipt
        fields are excluded and listed in VOLATILE_KEYS; nothing else is.
    close(app)

Fakes (fault injection): FakeGmgn(mode=...) and FakeRugCheck(mode=...) with modes "ok", "down" (raises),
"timeout" (never answers), "garbage" (malformed body), "rate_limited". `.calls` records every request.
"""
from __future__ import annotations

import asyncio
import dataclasses
import enum
import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from jinx import config
from jinx.app import JinxApp
from jinx.intel.safety import SafetyService
from jinx.sim import capture_events

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Fields that record when something happened on this machine or which arrival completed something; they differ
# between two deliveries of the same facts by design. Everything else in the view must be identical.
VOLATILE_KEYS = frozenset({
    "detected_ns",          # wall clock when Early Momentum first emitted
    "processing_ns", "processing_us",
    "observed_ns",          # receipt time of the arrival that completed a detection / a report
    "freshness", "detection_delay_ms", "feed_delay_ms",
    "latency_ms", "read_ns", "fetched_ns", "first_detected_ns",
    "late_readings",        # graduation diagnostic: how many readings arrived after a newer one (arrival order by
                            # definition); the latest reading and the peaks it feeds are compared in full
})


def build_app(data_dir: Path, gmgn=None, rugcheck=None, safety=None, **settings) -> JinxApp:
    s = config.load_settings(str(data_dir))
    s = dataclasses.replace(s, pumpapi_enabled=False, rugcheck_enabled=False, database_url=None,
                            research_database_url=None, redis_url=None, s3_endpoint=None, ai_gateway_url=None,
                            **settings)
    app = JinxApp(s)
    app._build_core()
    app.SCREEN_RETRY_S = 0.01
    if gmgn is not None:
        app.gmgn = gmgn
        app.rugcheck = rugcheck
        app.safety = safety or SafetyService(gmgn, rugcheck=rugcheck, timeout_s=0.2)
    return app


def deliveries(records: list[dict], source: str = "pumpapi", shuffle_seed: int | None = None,
               duplicate_every: int | None = None, drop_every: int | None = None) -> list[list | None]:
    if drop_every:
        records = [r for i, r in enumerate(records) if (i + 1) % drop_every]
    return capture_events(records, source=source, shuffle_seed=shuffle_seed, duplicate_every=duplicate_every)


@dataclass
class RunStats:
    published: int = 0
    duplicates: int = 0
    refused_records: int = 0
    background_left: int = 0
    handler_errors: dict = field(default_factory=dict)


async def run_async(app: JinxApp, delivered, settle: bool = True, settle_s: float = 5.0) -> RunStats:
    st = RunStats()
    runner = asyncio.create_task(app.bus.run())
    try:
        for evs in delivered:
            if evs is None:
                st.refused_records += 1
                continue
            for ev in evs:
                if await app.bus.publish(ev):
                    st.published += 1
                else:
                    st.duplicates += 1
        await app.bus.drain()
        if settle:
            for _ in range(int(settle_s / 0.01)):
                if not app._background:
                    break
                await asyncio.sleep(0.01)
        st.background_left = len(app._background)
    finally:
        runner.cancel()
        for t in list(app._background):
            t.cancel()
    st.handler_errors = dict(app.bus.stats["handler_errors"])
    return st


def run(app: JinxApp, delivered, settle: bool = True) -> RunStats:
    return asyncio.run(run_async(app, delivered, settle))


def _canon(x):
    """JSON-safe, order-independent form with volatile keys removed."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        x = {f.name: getattr(x, f.name) for f in dataclasses.fields(x)}
    if isinstance(x, dict):
        return {str(k): _canon(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0])) if k not in VOLATILE_KEYS}
    if isinstance(x, (list, tuple)):
        return [_canon(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted((_canon(v) for v in x), key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(x, enum.Enum):
        return str(x.value)
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    return str(x)


def state_view(app: JinxApp) -> dict:
    tokens = sorted(t.mint for t in app.core.tokens.all())
    view = {
        "tokens": app.core.tokens.digest(),
        "wallets": sorted((_canon(w) for w in app.core.wallets.all()), key=lambda w: w["address"]),
        "early_momentum": {}, "reserve_gates": {}, "graduation": {}, "migration": {},
        "ledger": [_canon(p) for p in app.ledger.all()],
        "paper_wallet": dataclasses.asdict(app.wallet.reconcile()),
    }
    for m in tokens:
        ev = app.early.evidence(m)
        if ev is not None:
            view["early_momentum"][m] = _canon(ev.to_dict())
        gates = app.early.reserve_check(m, feed_live=True)
        if gates.get("current") is not None:
            view["reserve_gates"][m] = _canon(gates)
        g = app.graduation.evidence(m)
        if g is not None:
            view["graduation"][m] = _canon(g)
        mig = app.migration_view(m)
        if mig is not None:
            view["migration"][m] = _canon({**mig, "summary": app.migration.summary(m)})
    for m in sorted(app.migrations):
        if m not in view["migration"]:
            view["migration"][m] = _canon(app.migration.summary(m))
    return view


def state_digest(app: JinxApp) -> str:
    return hashlib.sha256(json.dumps(state_view(app), sort_keys=True).encode()).hexdigest()


def close(app: JinxApp) -> None:
    app.market.close()
    app.store.close()


# ---- fault-injecting fakes ------------------------------------------------------------------------------------------

class ProviderDown(ConnectionError):
    pass


class FakeGmgn:
    """GMGN adapter stand-in with the adapter's interface (`running`, `call`, `health`). Answers are the recorded live
    shapes when `mode == "ok"`; other modes inject the failure on every call (or only on methods in `fail_methods`)."""

    def __init__(self, mode: str = "ok", security: dict | None = None, created=None, wallet_stats=None,
                 fail_methods: set[str] | None = None, delay_s: float = 0.0):
        self.mode, self.fail_methods, self.delay_s = mode, fail_methods, delay_s
        self.security = security if security is not None else json.loads(
            (FIXTURES / "gmgn_security_sample_20261021.json").read_text())["security"]
        self.created = created if created is not None else []
        self.wallet_stats = wallet_stats or {}
        self.calls: list[tuple] = []
        self.running = True

    async def call(self, method: str, *args):
        self.calls.append((method, args))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        failing = self.mode != "ok" and (self.fail_methods is None or method in self.fail_methods)
        if failing:
            if self.mode == "down":
                raise ProviderDown(f"GMGN {method}: connection refused")
            if self.mode == "timeout":
                await asyncio.sleep(3600)
            if self.mode == "garbage":
                return "<html>502 Bad Gateway</html>"
            if self.mode == "rate_limited":
                raise ProviderDown(f"GMGN {method}: 429 rate limited")
        if method == "getTokenSecurity":
            return self.security
        if method == "getCreatedTokens":
            return self.created
        if method == "getWalletStats":
            w = args[1][0] if len(args) > 1 and args[1] else None
            return self.wallet_stats.get(w, [])
        if method == "getTrenches":
            return {"near_completion": [], "completed": []}
        return {}

    async def health(self) -> dict:
        return {"adapter": "gmgn", "running": self.running, "mode": self.mode}


class FakeRugCheck:
    """RugCheck client stand-in (`report`, `health`, `run`) returning what jinx/adapters/rugcheck/client.py returns."""

    def __init__(self, mode: str = "ok", verdict: str = "PASS"):
        self.mode, self.verdict = mode, verdict
        self.calls: list[str] = []

    async def report(self, mint: str, priority: int = 10) -> dict:
        self.calls.append(mint)
        if self.mode == "down":
            return {"verdict": "UNKNOWN", "source": "rugcheck", "error": "network", "detail": "connection refused"}
        if self.mode == "timeout":
            return {"verdict": "UNKNOWN", "source": "rugcheck", "error": "timeout", "detail": "timed out"}
        if self.mode == "rate_limited":
            return {"verdict": "UNKNOWN", "source": "rugcheck", "error": "rate_limited", "detail": "429"}
        if self.mode == "garbage":
            return {"verdict": "UNKNOWN", "source": "rugcheck", "error": "malformed", "detail": "not JSON"}
        return {"verdict": self.verdict, "source": "rugcheck", "detail": "", "facts": {}}

    def health(self) -> dict:
        return {"adapter": "rugcheck", "mode": self.mode}

    async def run(self) -> None:
        await asyncio.Event().wait()


def shuffled(xs: list, seed: int) -> list:
    out = list(xs)
    random.Random(seed).shuffle(out)
    return out
