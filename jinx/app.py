"""JINX runtime: the composition root that wires adapters to core.

Runtime: live Pump.fun events (Mako's PumpPortal stream and the PumpAPI.io
stream) -> bus -> in-memory market store and registries (raw events are
discarded after their useful content is applied: owner order 2026-10-21);
durable ledger store for trading records; paper wallet, ledger and order
gateway; GMGN bridge health; a /health endpoint for the host.

Services, all off the trading path and all optional (owner orders 2026-10-21):
PostgreSQL (Neon) receives the trading records from the ledger outbox; Redis
holds expiring snapshots; Neon Object Storage holds the daily ledger backup with
JINX-enforced retention; the Neon AI Gateway is available for asynchronous
context. A storage monitor watches RAM, disk, PostgreSQL, Redis and object
storage against budgets, and an end-of-day pass releases space.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import signal
import time
from collections import OrderedDict, deque
from decimal import Decimal
from pathlib import Path

from jinx import config
from jinx.adapters.d3ad.adapter import D3adExecutionAdapter
from jinx.adapters.datanomad.adapter import DataNomadResearchAdapter
from jinx.adapters.gmgn.adapter import GmgnAdapter
from jinx.adapters.mako.feed import MakoPumpPortalFeed
from jinx.adapters.neonai.gateway import NeonAiGateway
from jinx.adapters.pumpapi.feed import PumpApiFeed
from jinx.adapters.rugcheck.client import RugCheckClient
from jinx.core.bus import EventBus, Lane
from jinx.core.lifecycle import State as LifeState
from jinx.confluence.board import ConfluenceBoard
from jinx.confluence.entry import EntryCoordinator
from jinx.core_state import CoreState
from jinx.execution import ExecutionGateway
from jinx.exits.chad import SybilEmergencyExit
from jinx.exits.executioner import CHECK_INTERVAL_S, ConfluenceCollapse, Executioner
from jinx.exits.monitor import PositionMonitor
from jinx.health import build_health
from jinx.intel.bundles import BundleTracker
from jinx.intel.early_momentum import EarlyMomentum, Funding
from jinx.intel.graduation import GraduationTracker
from jinx.intel.migration import MigrationTracker
from jinx.intel.readings import Clock
from jinx.intel.replay import percentiles
from jinx.intel.safety import SafetyService
from jinx.intel.whales import DISCOVERY_INTERVAL_S, WhaleBook, discovery_pass
from jinx.ledger import PositionLedger
from jinx.market_memory import MarketMemory
from jinx.modes import get_mode
from jinx.operator_view import SolUsd, operator_view, render_text
from jinx.paper import PaperExecutor, PaperWallet
from jinx.persistence.object_store import LedgerBackups
from jinx.persistence.postgres import PostgresReplicator
from jinx.persistence.redis_state import RedisState
from jinx.persistence.research import ResearchStore
from jinx.recovery import recover
from jinx.registry import TokenRegistry, WalletRegistry
from jinx.sizing.kelly import QuarterKelly, load_kelly_config
from jinx.storage import MB, DailyRun, StorageMonitor, filesystem, process_rss_bytes, tree_bytes
from jinx.store import Store

log = logging.getLogger("jinx")


class JinxApp:
    def __init__(self, settings: config.Settings, capture_path: Path | None = None, ws_url: str | None = None) -> None:
        self.s = settings
        self.capture_path = capture_path
        self.ws_url = ws_url
        self._capture = None
        self._capture_bytes = 0
        self._tasks: dict[str, asyncio.Task] = {}
        self._server = None
        self.status_errors = 0
        self.task_restarts: dict[str, int] = {}
        self.replicator = self.redis_state = self.backups = self.ai_gateway = self.research = None
        self.safety = None                                  # set in start() when GMGN is running
        self.last_trenches: dict | None = None
        self.sol_usd: SolUsd | None = None                  # owner-facing dollar rate (GMGN), never on the trading path
        self._research_at = 0.0
        self.daily = DailyRun()
        self.monitor = StorageMonitor({"ram": settings.ram_budget_mb * MB, "disk": settings.disk_budget_mb * MB,
                                       "postgres": settings.pg_budget_mb * MB, "redis": settings.redis_budget_mb * MB,
                                       "research_postgres": settings.research_pg_budget_mb * MB,
                                       "object_storage": settings.s3_budget_mb * MB})

    def _build_core(self) -> None:
        """Two stores: market events and registries in RAM (`market_db`, ":memory:" by default), trading records in
        the durable ledger store. A restart starts the market view empty; positions, fills and wallet survive."""
        s = self.s
        self.market = Store(s.market_db)
        self.store = Store(s.db_path)
        self.core = CoreState(self.market, TokenRegistry(self.market), WalletRegistry(self.market))
        self.memory = MarketMemory(self.market, self.core, s.market_event_ttl_s, s.market_idle_ttl_s,
                                   s.market_max_events, s.market_max_tokens, s.market_max_wallets)
        self.ledger = PositionLedger(self.store)
        self.wallet = PaperWallet(self.store, config.PAPER_START_LAMPORTS)
        self.paper = PaperExecutor(self.store, self.ledger, self.wallet, self.core.tokens, config.PUMP_CURVE_FEES,
                                   config.NETWORK_FEE_LAMPORTS)
        self.gateway = ExecutionGateway(self.store, self.ledger, self.paper)
        self.bus = EventBus(persist=self.market.record_event)
        self._handler_ns: dict[str, deque] = {}            # per-handler processing time (bounded, RAM)
        self.bus.subscribe(Lane.FAST, self._timed("core_state", self.core.apply), name="core_state")
        # Phase 3 EXIT: right after core state, so every exit sees the newest reserves before any intelligence runs.
        # CHAD's mandatory protective exits first (Sybil emergency, Confluence Collapse), then the position's paper
        # policy (BIGMAC_PAPER); one intent through the one gateway (jinx/exits/monitor.py).
        self.board = ConfluenceBoard()
        self.collapse = ConfluenceCollapse(self.board.score, self.ledger)
        self.exit_monitor = PositionMonitor(self.ledger, self.core.tokens, self.gateway, config.PUMP_CURVE_FEES,
                                       protective=[SybilEmergencyExit(lambda m: self.early.evidence(m)), self.collapse])
        self.bus.subscribe(Lane.FAST, self._timed("position_monitor", self.exit_monitor.on_event), name="position_monitor")
        self.executioner = Executioner(self.exit_monitor, self.ledger, config.NETWORK_FEE_LAMPORTS)
        # Phase 3 SIZE + FIRE: quarter-Kelly from the owner's stored settings (none = no entries, B-03)
        self.sizing = QuarterKelly(self.ledger, self.wallet, load_kelly_config(self.store), config.NETWORK_FEE_LAMPORTS)
        self.entries = EntryCoordinator(self.store, self.ledger, self.gateway, self.sizing, self.board, self.entry_check,
                                        self.core.tokens, config.PUMP_CURVE_FEES,
                                        reload_sizing=lambda: load_kelly_config(self.store))
        self.bundles = BundleTracker()                     # R1 evidence, off the fast lane
        self.bus.subscribe(Lane.BACKGROUND, self.bundles.on_event, name="bundles")
        # CHAD Bot 10 (C10) on the fast lane: synchronous, no I/O; detections are screened in the background
        self.early = EarlyMomentum(notify=self._on_early_momentum, request_funders=self._request_funders)
        self.bus.subscribe(Lane.FAST, self._timed("early_momentum", self.early.on_event), name="early_momentum")
        # CHAD Bot 5 (C05) and migration intelligence (M1-M3) on the fast lane too: synchronous, no I/O, and the
        # lifecycle must not lose a migration (the background lane drops when full). GMGN lookups they ask for run
        # as background tasks, never on the event path.
        self.graduation = GraduationTracker()
        self.bus.subscribe(Lane.FAST, self._timed("graduation", self.graduation.on_event), name="graduation")
        self.migration = MigrationTracker(notify=self._on_migration, request_pool=self._request_pool,
                                          request_holders=self._request_holders)
        self.bus.subscribe(Lane.FAST, self._timed("migration", self.migration.on_event), name="migration")
        self.migrations: OrderedDict[str, dict] = OrderedDict()   # newest 500 migrations and their status (RAM)
        self.candidates: OrderedDict[str, dict] = OrderedDict()   # newest 500 detections and their safety (RAM)
        self._background: set[asyncio.Task] = set()
        self._inflight: dict[tuple, asyncio.Task] = {}
        self.background_stats = {"spawned": 0, "shed": 0, "deduplicated": 0}
        self.screen_stats = {"screened": 0, "skipped": 0, "funder_lookups_skipped": 0}
        self.whales = WhaleBook()                          # CHAD C04: Layer-1 alerts and discovery candidates
        self.bus.subscribe(Lane.BACKGROUND, self.whales.on_event, name="whales")
        self.last_discovery: dict | None = None
        self.recovery = recover(self.market, self.core, self.ledger, self.wallet, bus=self.bus)

    async def start(self) -> None:
        s = self.s
        self._build_core()
        log.info("recovery: %s", self.recovery)
        if self.capture_path:
            self.capture_path.parent.mkdir(parents=True, exist_ok=True)
            self._capture = gzip.open(self.capture_path, "at")
        self.feed = MakoPumpPortalFeed(self.bus, s.upstream_dir / "mako", ws_url=self.ws_url,
                                       max_watched_tokens=s.max_watched_tokens, tap=self._tap if self._capture else None,
                                       api_key=os.environ.get("PUMPPORTAL_API_KEY") or None,
                                       mode_provider=lambda: get_mode(self.store),
                                       held_mints_provider=lambda: {p.mint for p in self.ledger.active()})
        held = lambda: {p.mint for p in self.ledger.active()}
        self.pumpapi = (PumpApiFeed(self.bus, held_mints_provider=held, on_connect=self._on_trade_feed_connect)
                        if s.pumpapi_enabled else None)
        self.gmgn = GmgnAdapter(s.gmgn_build_dir, allow_signing_key=config.GMGN_ALLOW_SIGNING_KEY)
        self.d3ad = D3adExecutionAdapter(s.upstream_dir / "d3ad")
        self.datanomad = DataNomadResearchAdapter(s.upstream_dir / "datanomad")
        if (s.gmgn_build_dir / "dist" / "client" / "OpenApiClient.js").exists():
            await self.gmgn.start()
        self._build_services()
        # CHAD C01/C08: candidates get their safety and developer check in parallel, from GMGN, off the trading path
        self.rugcheck = RugCheckClient() if s.rugcheck_enabled else None
        self.safety = SafetyService(self.gmgn, rugcheck=self.rugcheck) if self.gmgn.running else None
        self._tasks = {"bus": asyncio.create_task(self.bus.run(), name="bus"),
                       "mako-feed": asyncio.create_task(self.feed.run(), name="mako-feed"),
                       "status": asyncio.create_task(self._status_loop(), name="status")}
        if self.pumpapi is not None:
            self._tasks["pumpapi-feed"] = asyncio.create_task(self.pumpapi.run(), name="pumpapi-feed")
        if self.replicator is not None:
            self._tasks["postgres"] = asyncio.create_task(self.replicator.run(), name="postgres")
        if self.rugcheck is not None:
            self._tasks["rugcheck"] = asyncio.create_task(self.rugcheck.run(), name="rugcheck")
        if self.safety is not None:
            self._tasks["whale-discovery"] = asyncio.create_task(self._whale_discovery_loop(), name="whale-discovery")
        if self.gmgn.running:
            self._tasks["gmgn-trenches"] = asyncio.create_task(self._trenches_loop(), name="gmgn-trenches")
            self._tasks["sol-usd"] = asyncio.create_task(self._sol_usd_loop(), name="sol-usd")
        if self.ai_gateway is not None:
            self._tasks["ai-gateway-probe"] = asyncio.create_task(self._probe_gateway(), name="ai-gateway-probe")
        self._tasks["position-monitor"] = asyncio.create_task(self._monitor_loop(), name="position-monitor")
        self._server = await asyncio.start_server(self._http, "0.0.0.0", s.http_port)
        log.info("JINX up: db=%s port=%s mode=%s", s.db_path, s.http_port, (await self.health())["mode"])

    def _build_services(self) -> None:
        """Wire whichever services are configured (environment, see docs/INFRASTRUCTURE.md). None is required to
        trade and none sits between a trigger and an order."""
        s, env = self.s, os.environ
        if s.database_url:
            self.replicator = PostgresReplicator(self.store, s.database_url)
        if s.research_database_url:
            self.research = ResearchStore(s.research_database_url, s.research_retention_days, s.research_retention_days)
        if s.redis_url:
            import redis.asyncio as aioredis
            self.redis_state = RedisState(aioredis.from_url(s.redis_url, socket_timeout=5, socket_connect_timeout=5),
                                          max_tokens=s.redis_max_tokens)
        if s.s3_endpoint and env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY"):
            self.backups = LedgerBackups.from_env(s.s3_endpoint, s.s3_region, s.s3_bucket, s.backup_keep)
        if s.ai_gateway_url and env.get("NEON_AI_GATEWAY_TOKEN"):
            self.ai_gateway = NeonAiGateway(s.ai_gateway_url, env["NEON_AI_GATEWAY_TOKEN"])
        log.info("services: postgres=%s research=%s redis=%s object_storage=%s ai_gateway=%s",
                 self.replicator is not None, self.research is not None, self.redis_state is not None,
                 self.backups is not None, self.ai_gateway is not None)

    async def check_candidate(self, mint: str, creator: str | None, priority: int = 10):
        """CHAD C01/C08 for one candidate before execution eligibility: GMGN security and developer history in
        parallel, then RugCheck unless GMGN already vetoed it. Off the trading path; exits never call this."""
        if self.safety is None:
            return None
        return await self.safety.evaluate(mint, creator, time.time(), priority=priority)

    # Engineering bound (owner storage rule 2), not a trading rule: background lookups waiting on GMGN or RugCheck
    # are capped; a shed lookup leaves its evidence UNAVAILABLE, which blocks (Codex Phase 2 round 1, F2).
    MAX_BACKGROUND_TASKS = 64

    def _timed(self, name: str, handler):
        """Each fast-lane handler's own processing time, the newest 20,000 per handler (local processing only)."""
        samples = self._handler_ns.setdefault(name, deque(maxlen=20_000))
        clock = time.perf_counter_ns

        def run(ev):
            t0 = clock()
            try:
                return handler(ev)
            finally:
                samples.append(clock() - t0)
        return run

    def _spawn(self, coro, key=None) -> None:
        """Run a lookup in the background, never on the event path. At most MAX_BACKGROUND_TASKS at once; a lookup
        with the same `key` already running is not started again."""
        stats = self.background_stats
        if key is not None and key in self._inflight:
            stats["deduplicated"] += 1
            coro.close()
            return
        if len(self._background) >= self.MAX_BACKGROUND_TASKS:
            stats["shed"] += 1
            coro.close()
            return
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:                       # no loop (offline replay): nothing to schedule
            coro.close()
            return
        stats["spawned"] += 1
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        if key is not None:
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))

    def _on_early_momentum(self, evidence) -> None:
        """Fast-path notification: record the detection and screen it at once (CHAD: "Full safety check still
        fires … in parallel"). Never blocks the event path."""
        entry = self.candidates.setdefault(evidence.mint, {"first_detected_ns": evidence.detected_ns})
        entry.update(evidence=evidence.to_dict(), trade_permitted=evidence.trade_permitted)
        self.board.record(evidence.mint, "early_momentum", value=None, source=",".join(evidence.sources),
                          observed_ns=evidence.observed_ns, event_ns=evidence.event_ns,
                          blocking=not evidence.trade_permitted, payload={"blocking": list(evidence.blocking)})
        self.candidates.move_to_end(evidence.mint)
        while len(self.candidates) > 500:
            self.candidates.popitem(last=False)
        if "screening" not in entry:
            entry["screening"] = True
            never = self._never_eligible(evidence.mint)
            if never:                       # spend no GMGN or RugCheck requests on a coin that cannot qualify
                entry["safety"] = {"skipped": never}
                entry["entry_check"] = self.entry_check(evidence.mint)
                self.screen_stats["skipped"] += 1
            else:
                self.screen_stats["screened"] += 1
                self._spawn(self._screen(evidence.mint, evidence.creator))
        elif (entry.get("safety") or {}).get("eligible"):
            self._fast_entry(evidence)          # safety already passed: decide now, on the event path (CHAD Bot 10)

    def _fast_entry(self, evidence) -> None:
        """CHAD Bot 10's fast path: the entry decision runs at once, never behind a background queue. A failure here
        is logged and never stops the feed."""
        if not evidence.trade_permitted:
            return
        try:
            d = self.entries.consider_fast_path(evidence)
        except Exception:
            log.exception("fast-path entry decision failed for %s; no order was placed", evidence.mint)
            return
        entry = self.candidates.get(evidence.mint)
        if entry is not None:
            entry["entry_decision"] = {"ok": d.ok, "reason": d.reason, "intent_id": d.intent_id,
                                       "position_id": d.position_id}

    def _never_eligible(self, mint: str) -> str | None:
        """A reason the B-02 entry rule can never pass for this coin, whatever happens next: its verified peak
        already exceeded the ceiling, its history cannot be proven, or it migrated. None while it still can."""
        check = self.early.reserve_check(mint, feed_live=True)
        peak = check.get("peak") or {}
        if "migrated" in check.get("reason", ""):
            return "migrated"
        if peak.get("status") in ("FAIL", "UNAVAILABLE"):
            return f"entry rule: {(peak.get('detail') or {}).get('reason')}"
        return None

    # Engineering bound, not a trading rule: RugCheck indexes a new token 7.9-10.2 s after creation (measured
    # 2026-10-21), so a not-yet-indexed candidate is asked again every 2 s, at most 8 times.
    SCREEN_RETRY_S, SCREEN_ATTEMPTS = 2.0, 8

    def _trade_feed_live(self) -> bool:
        """The PumpAPI trade stream is connected and not stalled, so the newest reserve reading is current."""
        if getattr(self, "pumpapi", None) is None:
            return False
        h = self.pumpapi.health()
        return bool(h.get("connected")) and not h.get("ingest_stalled")

    def _entry_summary(self) -> dict:
        checks = [c["entry_check"] for c in self.candidates.values() if c.get("entry_check")]
        reasons: dict[str, int] = {}
        for c in checks:
            if not c["reserve_eligible"]:
                key = c["reserve_reason"].split(":")[0] if c["reserve_reason"].startswith(("stale", "migrated")) \
                    else c["reserve_reason"]
                reasons[key] = reasons.get(key, 0) + 1
        return {"checked": len(checks), "reserve_ok": sum(c["reserve_eligible"] for c in checks),
                "safety_ok": sum(c["safety_eligible"] for c in checks),
                "reserve_and_safety_ok": sum(c["reserve_eligible"] and c["safety_eligible"] for c in checks),
                "all_checks_ok": sum(c["all_checks_pass"] for c in checks),
                "reserve_blocked": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:6])}

    def entry_check(self, mint: str) -> dict:
        """Everything known about entering `mint` right now (owner decision B-02 §5: re-checked at the decision).
        Phase 2 records it; nothing here places an order. Graduation and lifecycle are shown with it, so the
        decision sees every module's view of the token in one place."""
        entry = self.candidates.get(mint) or {}
        reserve = self.early.reserve_check(mint, self._trade_feed_live())
        evidence = self.early.evidence(mint)
        safety = entry.get("safety") or {}
        blocking = list(evidence.blocking) if evidence else ["no detection"]
        grad = self.graduation.evidence(mint)
        life = self.migration.lifecycle(mint)
        # pre-graduation path only (owner B-02): a token the lifecycle has seen migrate is never an entry here,
        # whichever module heard the migration first (Codex round 3, R3-1)
        migrated = life is not None and (life.last_consistent_state.value >= LifeState.MIGRATION_OBSERVED.value
                                         or life.state is LifeState.CONFLICTING)
        return {"reserve_eligible": reserve["eligible"], "reserve_reason": reserve["reason"],
                "safety_eligible": bool(safety.get("eligible")), "early_momentum_blocking": blocking,
                "all_checks_pass": (reserve["eligible"] and bool(safety.get("eligible")) and not blocking
                                    and not migrated),
                "migrated": migrated,
                "lifecycle_state": life.state.name if life else None,
                "graduation": None if grad is None else {
                    "progress": grad.latest.progress, "standard_curve": grad.latest.standard_curve,
                    "near_threshold": grad.near_threshold, "provider_status": grad.provider_status},
                "migration": self.migration_view(mint)}

    def _on_migration(self, evidence) -> None:
        """Index of migrated tokens in shared state (newest 500). Their evidence is always read from the tracker,
        so shared state never holds a stale copy (Codex Phase 2 round 1, F5). Never an order."""
        self.migrations.setdefault(evidence.mint, evidence.migration.observed_ns)
        self.migrations.move_to_end(evidence.mint)
        while len(self.migrations) > 500:
            self.migrations.popitem(last=False)

    def migration_view(self, mint: str) -> dict | None:
        """The tracker's current migration evidence for one token, as the decision engine reads it."""
        ev = self.migration.evidence(mint)
        if ev is None:
            return None
        m = ev.migration
        return {"state": ev.state, "verified": ev.verified, "verification_status": ev.verification_status,
                "pool": m.pool_id, "pools_named": list(m.pools_named), "sources": list(m.sources),
                "event_ns": m.event_ns, "clock": str(m.clock), "first_verified_liquidity": ev.first_verified_liquidity,
                "holder_concentration": ev.holder_distribution, "developer_selling": ev.developer_selling,
                "largest_sells": ev.sells_around_graduation[:3]}

    def _on_trade_feed_connect(self, now_ns: int) -> None:
        """The PumpAPI trade feed (re)connected: trades sent while it was down are lost, so every history tracked so
        far is incomplete from here on (Codex Phase 2 round 1, F1)."""
        self.early.mark_feed_gap(now_ns, "PumpAPI trade feed (re)connected")
        self.migration.mark_feed_gap(f"PumpAPI trade feed (re)connected at {now_ns} ns", gap_ns=now_ns)

    def _gmgn_live(self) -> bool:
        return getattr(self, "gmgn", None) is not None and self.gmgn.running

    def _request_pool(self, mint: str) -> None:
        """A migration record without a pool (PumpPortal and Trenches name only the venue): GMGN token info carries
        `migrated_pool`, which can lag the completion, so it is asked again a bounded number of times."""
        if self._gmgn_live():
            self._spawn(self._fetch_migrated_pool(mint), key=("pool", mint))

    async def _fetch_migrated_pool(self, mint: str) -> None:
        for _ in range(self.SCREEN_ATTEMPTS):
            ev = self.migration.evidence(mint)
            if ev is None or ev.migration.pool_id is not None:
                return
            try:
                info = await self.gmgn.call("getTokenInfo", "sol", mint)
            except Exception as e:
                log.warning("migrated_pool lookup failed (%s); the migration stays unverified", type(e).__name__)
                return
            pool = info.get("migrated_pool") if isinstance(info, dict) else None
            ts = info.get("migrated_timestamp") if isinstance(info, dict) else None
            if isinstance(pool, str) and pool and isinstance(ts, (int, float)) and ts > 0:
                self.migration.record_report(mint, "gmgn.getTokenInfo", int(ts) * 1_000_000_000, Clock.PROVIDER,
                                             time.time_ns(), pool=pool)
                return
            await asyncio.sleep(self.SCREEN_RETRY_S)

    def _request_holders(self, mint: str) -> None:
        """Owner order 2026-10-21 item 8: concentration from verified balances where available (GMGN top holders)."""
        if self._gmgn_live():
            self._spawn(self._fetch_holders(mint), key=("holders", mint))

    async def _fetch_holders(self, mint: str) -> None:
        try:
            res = await self.gmgn.call("getTokenTopHolders", "sol", mint, {"limit": 20})
        except Exception as e:
            log.warning("holder lookup failed (%s); holder concentration stays UNAVAILABLE", type(e).__name__)
            return
        rows = res.get("list") if isinstance(res, dict) else res
        if isinstance(rows, list):
            self.migration.record_holders(mint, rows, "gmgn.getTokenTopHolders", time.time_ns())

    # Engineering cadence, not a trading rule: one Trenches read (weight 2 of the Plus plan's 20/s) every 10 s.
    TRENCHES_POLL_S = 10.0

    async def poll_trenches_once(self) -> dict:
        """GMGN Trenches for Pump.fun: the provider's `near_completion` status (graduation evidence and the
        lifecycle's APPROACHING GRADUATION) and `completed` curves (a migration report) for tokens JINX tracks."""
        res = await self.gmgn.call("getTrenches", "sol", ["near_completion", "completed"], ["Pump.fun"], 80)
        now, seen = time.time_ns(), {"near_completion": 0, "completed": 0, "tracked": 0}
        for category in ("near_completion", "completed"):
            for item in (res.get(category) or []) if isinstance(res, dict) else []:
                mint = item.get("address") if isinstance(item, dict) else None
                if not isinstance(mint, str) or not mint:
                    continue
                seen[category] += 1
                if not self.migration.tracks(mint):
                    continue
                seen["tracked"] += 1
                progress = item.get("progress")
                self.graduation.record_provider_status(mint, category, "gmgn.trenches", now, progress)
                self.migration.record_provider_status(mint, category, "gmgn.trenches", now, progress)
                done = item.get("complete_timestamp")
                if category == "completed" and isinstance(done, (int, float)) and done > 0:
                    self.migration.record_report(mint, "gmgn.trenches", int(done) * 1_000_000_000, Clock.PROVIDER,
                                                 now, venue_name=item.get("exchange") or None)
        self.last_trenches = {"read_ns": now, **seen}
        return self.last_trenches

    async def _trenches_loop(self) -> None:
        while True:
            try:
                await self.poll_trenches_once()
            except Exception as e:
                log.warning("GMGN Trenches read failed (%s); provider status waits for the next read", type(e).__name__)
            await asyncio.sleep(self.TRENCHES_POLL_S)

    async def _screen(self, mint: str, creator: str | None) -> None:
        for attempt in range(1, self.SCREEN_ATTEMPTS + 1):
            result = await self.check_candidate(mint, creator, priority=0)
            entry = self.candidates.get(mint)
            if result is None or entry is None:
                return
            rc_error = (result.safety.facts.get("rugcheck_error") if result.safety.facts else None)
            entry["safety"] = {"verdict": result.safety.verdict.value, "eligible": result.safety.eligible,
                               "veto": result.safety.veto_reasons, "rugcheck_error": rc_error, "attempts": attempt,
                               "checks": {c.name: c.verdict.value for c in result.safety.checks},
                               "latency_ms": round(result.latency_ms, 1)}
            entry["entry_check"] = self.entry_check(mint)
            self.board.record_safety(mint, verdict=result.safety.verdict.value, veto=result.safety.veto_reasons,
                                     observed_ns=time.time_ns(), source="gmgn+rugcheck")
            if result.safety.eligible:
                evidence = self.early.evidence(mint)
                if evidence is not None:
                    self._fast_entry(evidence)  # the moment the safety check passes (CHAD Bot 10)
            if result.safety.verdict.value != "UNKNOWN" or rc_error not in ("not_indexed", "rate_limited", "timeout"):
                return
            await asyncio.sleep(self.SCREEN_RETRY_S)

    def _request_funders(self, mint: str, wallets: tuple[str, ...]) -> None:
        """CHAD's Sybil funder check needs each buyer's SOL funder: GMGN wallet stats carry `fund_from_address`."""
        if self._never_eligible(mint):
            self.screen_stats["funder_lookups_skipped"] += 1
            return
        if self._gmgn_live():
            self._spawn(self._fetch_funders(tuple(wallets)), key=("funders", mint, tuple(wallets)))

    async def _fetch_funders(self, wallets: tuple[str, ...]) -> None:
        # One wallet per request: given several addresses GMGN's wallet_stats answers only the first (live 2026-10-21).
        results = await asyncio.gather(*(self.gmgn.call("getWalletStats", "sol", [w], "7d") for w in wallets),
                                       return_exceptions=True)
        for w, res in zip(wallets, results):
            if isinstance(res, Exception):
                log.warning("funder lookup for one wallet failed (%s); the Sybil predicate stays UNAVAILABLE",
                            type(res).__name__)
        for stats in (r for res in results if not isinstance(res, Exception)
                      for r in (res if isinstance(res, list) else [res] if isinstance(res, dict) else [])):
            common = stats.get("common") or {}
            funder, wallet = common.get("fund_from_address"), stats.get("wallet_address")
            if wallet and funder:
                ts = common.get("fund_from_ts")
                self.early.record_funding(Funding(wallet, funder, "gmgn.getWalletStats.common.fund_from_address",
                                                  time.time_ns(), int(ts) * 1_000_000_000 if ts else None))

    # Engineering cadence, not a trading rule: one getGasPrice read (weight 1) a minute for the owner's dollar figures.
    SOL_USD_POLL_S = 60.0

    async def _sol_usd_loop(self) -> None:
        while True:
            try:
                res = await self.gmgn.call("getGasPrice", "sol")
                price = res.get("native_token_usd_price") if isinstance(res, dict) else None
                if isinstance(price, (int, float)) and price > 0:
                    self.sol_usd = SolUsd(Decimal(str(price)), "GMGN getGasPrice native_token_usd_price", time.time_ns())
            except Exception as e:
                log.warning("SOL/USD read failed (%s); dollar figures keep the last rate", type(e).__name__)
            await asyncio.sleep(self.SOL_USD_POLL_S)

    async def _monitor_loop(self) -> None:
        """CHAD Bot 9: every open position checked at least every 10 seconds (Confluence Collapse, Sybil emergency)."""
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)
            try:
                self.exit_monitor.sweep()
                self.board.hold({p.mint for p in self.ledger.active()})
            except Exception:
                log.exception("position sweep failed; next sweep in %s s", CHECK_INTERVAL_S)

    async def _whale_discovery_loop(self) -> None:
        """CHAD: discovery "runs every 4 hours". Promoted whales persist in the research store and come back on start."""
        if self.research is not None:
            try:
                for address, source, evidence in await self.research.load_tracked_wallets():
                    self.whales.track(address, source, evidence)
            except Exception:
                log.warning("could not reload tracked wallets; discovery rebuilds them")
        while True:
            await asyncio.sleep(DISCOVERY_INTERVAL_S)
            try:
                self.last_discovery = await discovery_pass(self.gmgn, self.whales, research=self.research)
                log.info("whale discovery: %s", {k: v for k, v in self.last_discovery.items() if k != "promoted"})
            except Exception:
                log.exception("whale discovery pass failed; next pass in 4 hours")

    async def _probe_gateway(self) -> None:
        try:
            await self.ai_gateway.models()
        except Exception:
            log.warning("Neon AI Gateway probe failed: %s", self.ai_gateway.stats["last_error"])

    def _tap(self, recv_ns: int, raw) -> None:
        if self._capture_bytes >= self.s.capture_max_bytes:     # captures are capped (owner storage rule 6)
            return
        line = json.dumps({"recv_ns": recv_ns, "raw": raw}) + "\n"
        self._capture_bytes += len(line)
        self._capture.write(line)

    async def health(self) -> dict:
        adapters = [self.feed.health(), await self.gmgn.health(), self.d3ad.health(), self.datanomad.health()]
        if getattr(self, "pumpapi", None) is not None:
            adapters.append(self.pumpapi.health())
        if getattr(self, "rugcheck", None) is not None:
            adapters.append(self.rugcheck.health())
        h = build_health(self.store, self.ledger, self.wallet, adapters, self.bus.stats, time.time_ns(),
                         latency_ns=self.bus.latency_ns(Lane.FAST),
                         feed_stale_after_ns=self.s.feed_stale_after_s * 1_000_000_000,
                         receive_latency_ns=self.core.receive_latency_ns(), market_store=self.market)
        h["market_memory"] = {"tokens": len(self.core.tokens), "wallets": len(self.core.wallets), **self.memory.totals}
        h["services"] = {
            "postgres": self.replicator.health() if self.replicator else {"configured": False},
            "research_postgres": self.research.health() if self.research else {"configured": False},
            "redis": dict(self.redis_state.stats) if self.redis_state else {"configured": False},
            "object_storage": self.backups.health() if self.backups else {"configured": False},
            "ai_gateway": self.ai_gateway.health() if self.ai_gateway else {"configured": False},
        }
        h["intel"] = self.intel_health()
        h["trading"] = {"monitor": self.exit_monitor.health(), "executioner": dict(self.executioner.stats),
                        "confluence_collapse": {"unavailable": self.collapse.unavailable,
                                                "no_threshold": self.collapse.no_threshold},
                        "entries": dict(self.entries.stats), "latest_entries": list(self.entries.decisions)[-5:],
                        "sizing_configured": self.sizing.config is not None or load_kelly_config(self.store) is not None}
        h["storage"] = self.monitor.report()
        h["end_of_day"] = {"last_day": self.daily.last_day, "report": self.daily.last_report}
        if h["storage"]["status"] == "critical" and h["status"] == "ok":
            h["status"] = "degraded"
        return h

    def intel_health(self) -> dict:
        """What the intelligence modules hold in shared state right now, and the fast lane's measured delay."""
        lat = percentiles([x / 1e6 for x in self.bus.latency_ns(Lane.FAST)])
        detection = percentiles([c["evidence"]["freshness"]["detection_delay_ms"] for c in self.candidates.values()
                                 if c.get("evidence")])
        summaries = [s_ for s_ in (self.migration.summary(m) for m in self.migrations) if s_]
        signal = percentiles([(s_["observed_ns"] - s_["event_ns"]) / 1e6 for s_ in summaries
                              if s_["clock"] != "receipt"])
        return {"safety": dict(self.safety.stats) if self.safety else {"configured": False},
                "bundles_tracked": len(self.bundles._tokens),
                "early_momentum": {**self.early.stats, "candidates": len(self.candidates),
                                   "detection_delay_ms": detection,
                                   "latest": [{"mint": m, "safety": c.get("safety"),
                                               "trade_permitted": c.get("trade_permitted"),
                                               "entry_check": c.get("entry_check")}
                                              for m, c in list(self.candidates.items())[-5:]],
                                   "entry_summary": self._entry_summary(), "screening": dict(self.screen_stats)},
                "graduation": {**self.graduation.stats, "tracked": self.graduation.tracked_mints()},
                "migration": {**self.migration.stats, "tracked": self.migration.tracked_mints(),
                              "verified": self.migration.verified_mints(), "signal_delay_ms": signal,
                              "latest": [{"mint": m, **(self.migration.summary(m) or {"state": "evicted"})}
                                         for m in list(self.migrations)[-5:]]},
                "background": {**self.background_stats, "running": len(self._background)},
                "gmgn_trenches": self.last_trenches,
                "whales": {"tracked": len(self.whales.tracked), "candidates": len(self.whales._candidates),
                           "alerts": len(self.whales.alerts), "last_discovery": self.last_discovery},
                "fast_lane_admission_to_handled_ms": lat,
                "handler_processing_us": {n: percentiles([x / 1e3 for x in v]) for n, v in self._handler_ns.items()}}

    async def _http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), 5)
            path = request.split()[1].decode() if len(request.split()) > 1 else "/"
            if path == "/health":
                h = await self.health()
                body, code = json.dumps(h, default=str).encode(), (200 if h["status"] != "failed" else 503)
            elif path == "/operator":
                body, code = json.dumps(operator_view(self), default=str).encode(), 200
            elif path == "/operator.txt":
                body, code = render_text(operator_view(self)).encode(), 200
            else:
                body, code = b'{"error": "not found"}', 404
            writer.write(f"HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                         f"Connection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except Exception:
            log.exception("health endpoint error")
        finally:
            writer.close()

    async def _status_loop(self, interval_s: float = 60) -> None:
        """Retry failed applies, supervise runtime tasks and log status. A failing tick never ends the loop."""
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self._status_tick()
            except Exception:
                self.status_errors += 1
                log.exception("status tick failed; will retry next interval")

    async def _status_tick(self) -> None:
        self._supervise()
        await self.feed.sync_subscriptions()  # OFF drops paid trade subscriptions except held positions
        healed = self.core.apply_pending()  # retries events whose apply failed; exactly-once guarded
        self.memory.prune(time.time_ns(), held={p.mint for p in self.ledger.active()})
        if self.daily.due(time.time_ns()):
            await self.end_of_day(time.time_ns())
        await self.sample_storage()
        if self.redis_state is not None:
            await self.redis_state.snapshot(await self.health(), self.core.tokens.all())
        if self.research is not None and time.monotonic() - self._research_at >= self.s.research_every_s:
            self._research_at = time.monotonic()
            await self.research.flush(self.core.wallets.all(), await self.performance_sample())
        if healed:
            log.warning("re-applied %s stored events that had failed to apply", healed)
        h = await self.health()
        log.info("status=%s events=%s unapplied=%s tokens=%s feed=%s latency=%s", h["status"], h["store"]["events"],
                 h["store"]["unapplied"], len(self.core.tokens),
                 {k: h["adapters"][0].get(k) for k in ("connected", "received", "malformed", "ingest_stalled")},
                 h["receive_to_state_ms"])

    async def end_of_day(self, now_ns: int) -> dict:
        """Owner storage rule 9: release space, drop what is disposable, back up what is essential. Open positions
        and trade history are never touched."""
        report: dict = {"ledger": self.store.maintain(), "market": self.market.maintain()}
        if self.replicator is None:
            report["outbox_cleared"] = self.store.clear_outbox()   # nothing to replicate to; resync covers a later one
        else:
            try:
                report["postgres_bytes"] = await self.replicator.database_bytes()
            except Exception as e:
                report["postgres_bytes"] = f"unavailable ({type(e).__name__})"
        if self.research is not None:
            try:
                report["research_pruned"] = await self.research.prune(now_ns)
            except Exception as e:
                report["research_pruned"] = f"unavailable ({type(e).__name__})"
        if self.backups is not None:
            report["backup"] = await self.backups.backup(self.store, now_ns)
        self.daily.done(now_ns, report)
        log.info("end-of-day clean-up: %s", report)
        return report

    async def performance_sample(self) -> dict:
        """One compact performance row for the research store: latency percentiles, throughput, sizes, storage."""
        h = await self.health()
        return {"status": h["status"], "receive_to_state_ms": h["receive_to_state_ms"],
                "admission_to_state_ms": h["admission_to_state_ms"], "events": h["store"]["events"],
                "bus": {k: v for k, v in h["bus"].items() if isinstance(v, (int, float))},
                "market_memory": h["market_memory"],
                "storage": {k: {f: c.get(f) for f in ("used_bytes", "growth_bytes_per_h", "status")}
                            for k, c in h["storage"]["components"].items()}}

    async def sample_storage(self) -> None:
        now_s, s = time.time(), self.s
        self.monitor.record("ram", process_rss_bytes(), now_s,
                            {"market_store_bytes": self.market.size_bytes(), "events": self.market.event_count(),
                             "tokens": len(self.core.tokens), "wallets": len(self.core.wallets)})
        self.monitor.record("disk", tree_bytes(s.data_dir), now_s,
                            {"path": str(s.data_dir), "ledger_bytes": self.store.size_bytes(),
                             "outbox_rows": self.store.outbox_backlog()})
        fs = filesystem(s.data_dir)
        self.monitor.record("filesystem", fs["used_bytes"], now_s, {"free_bytes": fs["free_bytes"]},
                            budget_bytes=fs["total_bytes"])
        if self.replicator is not None:
            self.monitor.record("postgres", self.replicator.stats["db_bytes"], now_s,
                                {"outbox_backlog": self.replicator.backlog()})
        if self.redis_state is not None:
            try:
                mem = await self.redis_state.memory()
                self.monitor.record("redis", mem["used_bytes"], now_s, {"keys": mem["keys"]})
            except Exception as e:
                self.monitor.record("redis", None, now_s, {"error": type(e).__name__})
        if self.research is not None:
            self.monitor.record("research_postgres", self.research.stats["db_bytes"], now_s,
                                {"tracked_wallets": len(self.research._sent)})
        if self.backups is not None:
            self.monitor.record("object_storage", self.backups.stats["stored_bytes"], now_s,
                                {"objects": self.backups.stats["objects"], "keep": self.backups.keep})

    def _supervise(self) -> None:
        """Restart the bus or feed task if it ended unexpectedly (an exception, not a cancel)."""
        starters = {"bus": lambda: self.bus.run(), "mako-feed": lambda: self.feed.run(),
                    "position-monitor": lambda: self._monitor_loop()}
        if getattr(self, "rugcheck", None) is not None:
            starters["rugcheck"] = lambda: self.rugcheck.run()
        if getattr(self, "pumpapi", None) is not None:
            starters["pumpapi-feed"] = lambda: self.pumpapi.run()
        if self.replicator is not None:
            starters["postgres"] = lambda: self.replicator.run()
        for name, start in starters.items():
            task = self._tasks.get(name)
            if task is None or not task.done() or task.cancelled():
                continue
            log.error("task %s ended unexpectedly (%r); restarting", name, task.exception())
            self._tasks[name] = asyncio.create_task(start(), name=name)
            self.task_restarts[name] = self.task_restarts.get(name, 0) + 1

    async def stop(self) -> None:
        for name, t in self._tasks.items():
            if name != "bus":
                t.cancel()
        try:
            await asyncio.wait_for(self.bus.drain(), 10)
        except asyncio.TimeoutError:
            log.warning("bus did not drain in 10s; unapplied events will be replayed on next start")
        for t in self._tasks.values():
            t.cancel()
        if self._server:
            self._server.close()
        await self.gmgn.stop()
        if self.replicator is not None:            # hand the last trading records over before closing
            try:
                await asyncio.wait_for(self.replicator.flush(), 5)
            except Exception:
                log.warning("final PostgreSQL flush failed; the outbox keeps the rows for the next start")
            await self.replicator._reset()
        if self.redis_state is not None:
            await self.redis_state.client.aclose()
        if self._capture:
            self._capture.close()
        self.store.close()
        self.market.close()


async def run(settings: config.Settings, seconds: float | None = None, capture_path: Path | None = None) -> dict:
    app = JinxApp(settings, capture_path=capture_path)
    await app.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await asyncio.wait_for(stop.wait(), seconds) if seconds else await stop.wait()
    except asyncio.TimeoutError:
        pass
    final = await app.health()
    await app.stop()
    return final
