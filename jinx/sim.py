"""Replay of captured live PumpPortal messages through the real JINX pipeline.

A capture is JSON lines of {"recv_ns": int, "raw": <message exactly as Mako's
stream delivered it>}. Replay runs each message through the same normalizer,
bus (with durable dedup), and core state as the live engine. Sequence numbers
come from the record's position in the capture, so any reordering of the same
records must produce identical final state.

The optional accounting exercise opens and closes paper positions mechanically
on the captured reserves to check paper accounting against real market data.
It is not a strategy: entries are not signals, and its P&L means nothing.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from jinx.adapters.mako.normalize import MalformedMessage, normalize
from jinx.core.bus import EventBus, Lane
from jinx.core.events import EventKind
from jinx.core_state import CoreState
from jinx.execution import EntryIntent, ExecutionGateway, ExitIntent
from jinx.health import percentiles_ms
from jinx.ledger import PositionLedger, Status
from jinx.modes import Mode, set_mode
from jinx.paper import PaperExecutor, PaperWallet
from jinx.recovery import recover
from jinx.registry import TokenRegistry, WalletRegistry
from jinx.store import Store
from jinx import config


def load_capture(path: Path) -> list[dict]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as f:
        return [json.loads(line) for line in f if line.strip()]


@dataclass
class ReplayResult:
    records: int = 0
    malformed: int = 0
    events_admitted: int = 0
    duplicates: int = 0
    tokens: int = 0
    wallets: int = 0
    digest: str = ""
    unapplied_after: int = 0
    elapsed_s: float = 0.0
    latency_ms: dict | None = None
    wallet_reconciled: bool = True
    accounting: dict = field(default_factory=dict)


class _Pipeline:
    def __init__(self, db_path: Path):
        self.store = Store(db_path)
        self.core = CoreState(self.store, TokenRegistry(self.store), WalletRegistry(self.store))
        self.ledger = PositionLedger(self.store)
        self.wallet = PaperWallet(self.store, config.PAPER_START_LAMPORTS)
        self.paper = PaperExecutor(self.store, self.ledger, self.wallet, self.core.tokens, config.PUMP_CURVE_FEES,
                                   config.NETWORK_FEE_LAMPORTS)
        self.gateway = ExecutionGateway(self.store, self.ledger, self.paper)
        self.bus = EventBus(persist=self.store.record_event)
        self.bus.subscribe(Lane.FAST, self.core.apply, name="core_state")
        self.report = recover(self.store, self.core, self.ledger, self.wallet, bus=self.bus)


async def _feed(pipe: _Pipeline, indexed: list[tuple[int, dict]], result: ReplayResult, exercise=None) -> None:
    runner = asyncio.create_task(pipe.bus.run())
    try:
        for idx, rec in indexed:
            seqs = iter((2 * idx + 1, 2 * idx + 2))
            try:
                events = normalize(rec["raw"], rec["recv_ns"], seqs.__next__)
            except MalformedMessage:
                result.malformed += 1
                continue
            for ev in events:
                if await pipe.bus.publish(ev):
                    result.events_admitted += 1
                else:
                    result.duplicates += 1
            if exercise is not None:
                await pipe.bus.drain()
                exercise.on_events(events)
        await pipe.bus.drain()
    finally:
        runner.cancel()


def replay(records: list[dict], db_path: Path, shuffle_seed: int | None = None, duplicate_every: int | None = None,
           restart_at: int | None = None, accounting_exercise: bool = False, exit_after: int = 0) -> ReplayResult:
    indexed = list(enumerate(records))
    if duplicate_every:
        indexed = [x for i, x in enumerate(indexed) for _ in range(2 if i % duplicate_every == 0 else 1)]
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(indexed)
    result = ReplayResult(records=len(indexed))
    t0 = time.perf_counter()
    latency: list[int] = []
    chunks = [indexed] if restart_at is None else [indexed[:restart_at], indexed[restart_at:]]
    pipe = None
    for chunk in chunks:
        if pipe is not None:      # simulate a stop and restart between chunks
            latency += pipe.bus.latency_ns(Lane.FAST)
            pipe.store.close()
        pipe = _Pipeline(db_path)
        exercise = _AccountingExercise(pipe, exit_after) if accounting_exercise else None
        if exercise:
            set_mode(pipe.store, Mode.AUTO, by="accounting-exercise")
        asyncio.run(_feed(pipe, chunk, result, exercise))
        if exercise:
            result.accounting = exercise.summary()
    latency += pipe.bus.latency_ns(Lane.FAST)
    result.elapsed_s = time.perf_counter() - t0
    result.tokens, result.wallets = len(pipe.core.tokens), len(pipe.core.wallets)
    result.digest = pipe.core.tokens.digest()
    result.unapplied_after = len(pipe.store.unapplied_events())
    result.latency_ms = percentiles_ms(latency)
    result.wallet_reconciled = pipe.wallet.reconcile().ok
    pipe.store.close()
    return result


class _AccountingExercise:
    """Mechanical paper round trips on captured reserves, one position at a time (owner's one-slot limit):
    enter 0.01 SOL at a token's first curve observation and exit fully after `exit_after` further curve
    trades of that token (0 = exit at the same observation, a flat-price round trip)."""
    BUDGET = 10_000_000

    def __init__(self, pipe: _Pipeline, exit_after: int = 0):
        self.pipe = pipe
        self.exit_after = exit_after
        self.position_id: int | None = None
        self.mint: str | None = None
        self.seen = 0
        self.closed: list[int] = []
        self.refused: dict[str, int] = {}
        self.start = pipe.wallet.balance()

    def on_events(self, events) -> None:
        for ev in events:
            if ev.kind is not EventKind.TRADE or ev.v_sol_lamports is None:
                continue
            if self.position_id is None:
                r = self.pipe.gateway.submit_entry(EntryIntent(f"ex:{ev.idempotency_key}", ev.mint, self.BUDGET,
                                                               origin="accounting-exercise"))
                if r.ok:
                    self.position_id, self.mint, self.seen = r.position_id, ev.mint, 0
                else:
                    self.refused[r.reason] = self.refused.get(r.reason, 0) + 1
                    continue
                if self.exit_after == 0:
                    self._exit()
            elif ev.mint == self.mint:
                self.seen += 1
                if self.seen >= self.exit_after:
                    self._exit()

    def _exit(self) -> None:
        r = self.pipe.gateway.submit_exit(ExitIntent(f"ex-exit:{self.position_id}", self.position_id))
        if r.ok and self.pipe.ledger.get(self.position_id).status is Status.CLOSED:
            self.closed.append(self.position_id)
            self.position_id = self.mint = None

    def summary(self) -> dict:
        positions = [self.pipe.ledger.get(p) for p in self.closed]
        realized = sum(p.realized_pnl_lamports for p in positions)
        held_cost = sum(p.cost_lamports + p.network_fees_lamports for p in self.pipe.ledger.active())
        losses_bps = [(p.cost_lamports - p.proceeds_lamports) * 10_000 // p.cost_lamports for p in positions]
        wallet_change = self.pipe.wallet.balance() - self.start
        return {"note": "mechanical accounting check on real captured reserves; not a strategy; P&L is meaningless",
                "closed_round_trips": len(positions), "open_positions": len(self.pipe.ledger.active()),
                "realized_pnl_lamports": realized, "wallet_change_lamports": wallet_change,
                "flat_round_trip_cost_bps": {"min": min(losses_bps), "max": max(losses_bps)} if losses_bps else None,
                "wallet_change_equals_realized_minus_open_cost": wallet_change == realized - held_cost,
                "refused": self.refused, "wallet_reconciled": self.pipe.wallet.reconcile().ok}
