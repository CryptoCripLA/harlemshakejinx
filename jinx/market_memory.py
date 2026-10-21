"""RAM-only market data (owner order 2026-10-21: "Raw PumpAPI events are processed in RAM and discarded after
useful information is extracted. No accumulating raw-event archives on the JINX execution server.").

The market store is an in-memory SQLite database (`Store(":memory:")`), so the Phase 1 event path stays exactly as
reviewed (durable dedup by idempotency key, exactly-once apply, registries), but nothing reaches the disk. This
module bounds its size:
- an applied raw event is discarded once older than `event_ttl_s`. The window only has to cover the same fact
  arriving from two feeds (PumpAPI and PumpPortal deliver within ~1.5 s of each other, measured 2026-10-21);
- an unapplied event is never discarded (it still has to be applied);
- a token with no activity for `idle_ttl_s` is evicted with its trader set, unless JINX holds it; a wallet with no
  activity for `idle_ttl_s` is evicted.
Hard caps sit on top of the TTLs (owner storage rule 2: "No unbounded queues, caches, or memory growth"): beyond
`max_events` the oldest applied events go first, beyond `max_tokens` the least recently active tokens JINX does not
hold, beyond `max_wallets` the least recently active wallets. Freed pages are returned after every pass, and an
applied event's raw payload is already released at apply time (jinx/store.py `mark_applied`).
Intelligence worth keeping is written to PostgreSQL by its own component; trading records live in the durable
ledger store. TTLs and caps are engineering bounds on memory, not strategy parameters.
"""
from __future__ import annotations

from jinx.core_state import CoreState
from jinx.store import Store

NS = 1_000_000_000
DEFAULT_EVENT_TTL_S = 300
DEFAULT_IDLE_TTL_S = 7200


class MarketMemory:
    def __init__(self, store: Store, core: CoreState, event_ttl_s: int = DEFAULT_EVENT_TTL_S,
                 idle_ttl_s: int = DEFAULT_IDLE_TTL_S, max_events: int = 60_000, max_tokens: int = 20_000,
                 max_wallets: int = 200_000) -> None:
        self.store, self.core = store, core
        self.event_ttl_s, self.idle_ttl_s = event_ttl_s, idle_ttl_s
        self.max_events, self.max_tokens, self.max_wallets = max_events, max_tokens, max_wallets
        self.totals = {"events_discarded": 0, "tokens_evicted": 0, "wallets_evicted": 0}

    def prune(self, now_ns: int, held: set[str] | frozenset = frozenset()) -> dict:
        event_cutoff = now_ns - self.event_ttl_s * NS
        idle_cutoff = now_ns - self.idle_ttl_s * NS
        with self.store.transaction() as c:
            events = c.execute("DELETE FROM events WHERE applied = 1 AND recv_ns < ?", (event_cutoff,)).rowcount
            idle = [r[0] for r in c.execute(
                "SELECT mint FROM tokens WHERE COALESCE(latest_time_ns, first_time_ns, 0) < ?", (idle_cutoff,))
                    if r[0] not in held]
            for mint in idle:
                c.execute("DELETE FROM token_traders WHERE mint = ?", (mint,))
                c.execute("DELETE FROM tokens WHERE mint = ?", (mint,))
            wallets = c.execute("DELETE FROM wallets WHERE COALESCE(last_time_ns, 0) < ?", (idle_cutoff,)).rowcount
            # hard caps: oldest first; unapplied events and held tokens are never removed
            over = c.execute("SELECT COUNT(*) FROM events").fetchone()[0] - self.max_events
            if over > 0:
                events += c.execute("DELETE FROM events WHERE idempotency_key IN (SELECT idempotency_key FROM events"
                                    " WHERE applied = 1 ORDER BY recv_ns, seq LIMIT ?)", (over,)).rowcount
            over = c.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] - self.max_tokens
            if over > 0:
                oldest = [r[0] for r in c.execute("SELECT mint FROM tokens ORDER BY COALESCE(latest_time_ns,"
                                                  " first_time_ns, 0), mint") if r[0] not in held][:over]
                for mint in oldest:
                    c.execute("DELETE FROM token_traders WHERE mint = ?", (mint,))
                    c.execute("DELETE FROM tokens WHERE mint = ?", (mint,))
                idle += oldest
            over = c.execute("SELECT COUNT(*) FROM wallets").fetchone()[0] - self.max_wallets
            if over > 0:
                wallets += c.execute("DELETE FROM wallets WHERE address IN (SELECT address FROM wallets"
                                     " ORDER BY COALESCE(last_time_ns, 0), address LIMIT ?)", (over,)).rowcount
        self.store.conn.execute("PRAGMA incremental_vacuum").fetchall()   # give freed pages back
        self.core.tokens.reload()
        self.core.wallets.reload()
        report = {"events_discarded": events, "tokens_evicted": len(idle), "wallets_evicted": wallets}
        for k, v in report.items():
            self.totals[k] += v
        return report
