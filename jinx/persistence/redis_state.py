"""Transient JINX state in Redis, every key with an expiry (owner order 2026-10-21: "Redis for transient state
with expiration").

Written in the background from the status loop, never on the hot path: the health snapshot and a snapshot of each
token whose market view changed since the last write. Values are JSON; keys are `jinx:health` and
`jinx:token:<mint>`. Nothing in Redis is needed to trade; it lets operators and later components see recent
state without disk on the execution server.

Bounded (owner storage rule 4): every key expires, at most `max_tokens` token snapshots are written per pass (the
most recently active first), and `memory()` reports what Redis actually holds for the storage monitor.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Iterable

from jinx.registry import TokenState


class RedisState:
    def __init__(self, client, prefix: str = "jinx:", token_ttl_s: int = 7200, health_ttl_s: int = 120,
                 max_tokens: int = 2_000) -> None:
        self.client, self.prefix = client, prefix
        self.max_tokens = max_tokens
        self.token_ttl_s, self.health_ttl_s = token_ttl_s, health_ttl_s
        self._written: dict[str, tuple] = {}
        self.stats = {"snapshots": 0, "token_writes": 0, "failures": 0, "last_error": None}

    async def snapshot(self, health: dict, tokens: Iterable[TokenState]) -> int:
        pipe = self.client.pipeline(transaction=False)
        pipe.set(f"{self.prefix}health", json.dumps(health, default=str), ex=self.health_ttl_s)
        written, current = 0, set()
        pending: dict[str, tuple] = {}
        recent = sorted(tokens, key=lambda t: (t.latest_time_ns or t.first_time_ns or 0), reverse=True)
        for t in recent[:self.max_tokens]:
            current.add(t.mint)
            mark = (t.latest_seq, t.latest_time_ns, t.buys, t.sells, t.v_sol_lamports)
            if self._written.get(t.mint) == mark:
                continue
            pipe.set(f"{self.prefix}token:{t.mint}", json.dumps(asdict(t)), ex=self.token_ttl_s)
            pending[t.mint] = mark
            written += 1
        try:
            await pipe.execute()
        except Exception as e:
            self.stats["failures"] += 1
            self.stats["last_error"] = type(e).__name__
            return 0
        self._written.update(pending)
        for mint in set(self._written) - current:   # evicted from RAM: its Redis key simply expires
            del self._written[mint]
        self.stats["snapshots"] += 1
        self.stats["token_writes"] += written
        return written

    async def memory(self) -> dict:
        try:
            used = (await self.client.info("memory")).get("used_memory")
        except Exception:          # a server that does not offer INFO still reports its key count
            used = None
        return {"used_bytes": used, "keys": await self.client.dbsize()}
