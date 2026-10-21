# JINX infrastructure, data flow and storage hygiene

Canonical. Owner orders of 2026-10-21: a clean slate on new services, and the permanent storage requirement
("No storage system should grow indefinitely because of unnecessary data"). Nothing here is migrated from the old
JINX servers or databases.

## 1. Roles

| # | Service | Role | Where |
|---|---|---|---|
| 1 | **Contabo Cloud VPS 6** | Exclusive trading execution: the JINX engine, ledger, order path | `vmi3672752`, 13.140.224.208, Carlstadt NJ (US-east): 6 vCPU, 12 GB RAM, 193 GB disk, Ubuntu 24.04.5. Reinstalled 2026-10-21 with key-only login (`ubuntu`, deploy key `jinx-deploy@jinx-nyc`); firewall allows SSH only. JINX runs as the `jinx` systemd service in paper mode, deployed by `deploy/contabo/deploy.sh` |
| 2 | **Redis Cloud** | Fast transient state: health and token snapshots, every key expires | database `database-MVHIAO2Q` (id 14706944) |
| 3 | **Neon PostgreSQL** | Durable trading ledger: positions with realized P&L, fills, exits, paper wallet, settings | org `org-shy-term-30862447`, project `flat-hat-34042458`, branch `production` (`br-icy-art-b48n1bjp`), AWS us-east-2 |
| 3a | Neon Object Storage | Daily ledger backup, retention enforced by JINX | `br-icy-art-b48n1bjp.storage.c-6.us-east-2.aws.neon.tech`, bucket `jinx-backups` |
| 3b | Neon AI Gateway | Model access for asynchronous context only; never on the trading path | `br-icy-art-b48n1bjp-api.ai.c-6.us-east-2.aws.neon.tech` (60 models, 2026-10-21) |
| 4 | **Railway PostgreSQL** | Wallet intelligence, research, analytics, performance data | Railway project `jinx` (`f59a870a-5b58-4759-a9ed-33f4e525eded`), service `Postgres` (PostgreSQL 18), 5 GB volume, region sfo, public TCP proxy `shuttle.proxy.rlwy.net:36046` |
| 5 | **Railway Hobby** | Hosts the Railway services above and future supporting workloads (research jobs, FACE). No trading | same project |

Connection strings and keys live only in a private env file (`/etc/jinx/jinx.env` on Contabo,
`~/.config/jinx/jinx.env` on the engineering host; template `deploy/contabo/jinx.env.example`). `python -m jinx run`
loads it. See docs/SECRETS.md.

## 2. Data flow

```
PumpAPI.io + PumpPortal (free WebSockets)            GMGN (trading platform, read-only today)
              |                                                  |
              v                                                  v
 CONTABO ── JINX engine (one process) ─────────────────────────────────────────────────────────────
   bounded bus queues -> in-memory market store (RAM only) -> token / wallet registries (RAM)
        raw payload released at apply; events TTL 300 s, cap 60k; tokens/wallets idle 2 h, caps 20k/200k
   trigger -> size -> paper order -> LEDGER (SQLite on local disk, WAL) -> exit -> realized P&L
        the order path touches only RAM and the local ledger; no network storage call sits on it
   ledger change -> outbox row in the same transaction (at most one pending row per record)
 ─────────────────────────────── background only, never on the order path ──────────────────────────
   outbox  --(PostgresReplicator, ~1 s, batched upserts)-->  NEON POSTGRES   trading ledger mirror
   ledger  --(end of day, gzip snapshot in memory)------->  NEON OBJECT STORAGE  keep newest 7
   status  --(every 60 s, TTL keys)----------------------->  REDIS            health + 2,000 newest tokens
   wallets --(every 300 s, totals increase)--------------->  RAILWAY POSTGRES wallet_stats, perf_samples
   context --(on demand, at most 2 requests in flight)--->  NEON AI GATEWAY  never consulted to trade
```

If any remote service is down, trading is unaffected: the outbox waits (bounded), Redis writes are skipped, research
increases are sent on the next pass, the backup is retried the next day. Health reports each service.

## 3. Permanent storage rules and how each is enforced

| Rule (owner, 2026-10-21) | Mechanism (automatic) | Code |
|---|---|---|
| 1. Contabo execution only; no archives, dumps, oversized logs, abandoned temp files | Market data never touches disk; the only state file is the ledger in `/var/lib/jinx`; captures are opt-in and capped at 50 MB; systemd `PrivateTmp` empties /tmp on stop | `jinx/app.py`, `deploy/contabo/jinx.service` |
| 2. RAM: process, extract, release; nothing unbounded | Raw payload dropped when the event is applied; event TTL 300 s + cap 60,000; idle eviction 2 h + caps 20,000 tokens / 200,000 wallets (held tokens kept); freed pages returned each pass; bus queues, dedup (200k), followed mints (5,000), watched mints (300) all bounded; `MemoryMax=2G` | `jinx/market_memory.py`, `jinx/store.py` |
| 3. PostgreSQL: compact, necessary records; retention on obsolete ones | Neon gets trading records only (no market data). Railway: `wallet_stats` rows unseen for 30 days and `perf_samples` older than 30 days are deleted each day | `jinx/persistence/postgres.py`, `jinx/persistence/research.py` |
| 4. Redis: bounded, expiring | Every key has a TTL (health 120 s, tokens 2 h); at most 2,000 token snapshots per pass; unchanged tokens not rewritten | `jinx/persistence/redis_state.py` |
| 5. Logs: rotate, compress, expire | JINX logs only to the journal; journald caps it at 500 MB, 50 MB files, compressed, 14 days, 2 GB always kept free | `deploy/contabo/journald-jinx.conf` |
| 6. Tests clean up | pytest keeps no temp directories and no cache; the throwaway PostgreSQL cluster is deleted; `jinx replay` without `--db` deletes its database | `pyproject.toml`, `tests/test_persistence.py`, `jinx/__main__.py` |
| 7. Outboxes and queues bounded, completed entries removed | A change to a record replaces its pending row (outbox ≤ number of records); rows are deleted once PostgreSQL commits them; without PostgreSQL the end-of-day pass clears it and a full resync on start covers any gap | `jinx/store.py` (v9), `jinx/persistence/postgres.py` |
| 8. Backups with defined retention | One gzip ledger snapshot per UTC day in Neon Object Storage, newest 7 kept, older deleted by JINX (Neon stores S3 lifecycle rules but does not run them). Neon PostgreSQL keeps its own restore window | `jinx/persistence/object_store.py` |
| 9. End of day | Once per UTC day: WAL checkpoint and truncate, release free pages, refresh statistics, clear disposable outbox rows, Railway retention, ledger backup. Open positions and trade history are never touched | `JinxApp.end_of_day` |
| 10. Storage monitoring | Every 60 s: process RSS, market store, data directory, filesystem, Neon DB size, Redis memory, Railway DB size, backup bytes, each against a budget. `warn` at 80% or when growth reaches the budget within 24 h; `critical` at 95% or within 2 h (health becomes `degraded`) | `jinx/storage.py`, `/health` → `storage` |

Budgets (env-overridable): RAM 1 GB, data directory 1 GB, Neon 400 MB (free plan 0.5 GB), Redis 25 MB (free
30 MB), Railway PostgreSQL 4 GB (5 GB volume), object storage 1 GB (free plan 5 GB).

## 4. Measured footprint and latency (engineering host jinx-nyc, 2026-10-21, live feeds, 100 s run)

| Component | Measured | Bound |
|---|---|---|
| Process RAM (RSS) | 87 MB | budget 1 GB, hard ceiling 2 GB |
| In-memory market store | 2.1 MB for 1,701 events, 116 tokens, 501 wallets | ~2 KB/event measured → cap 60,000 ≈ 120 MB |
| Ledger file on disk | 172 KB | grows only with trades: one paper round trip ≈ 2.6 KB of records |
| Outbox | 0 rows after replication or end of day | ≤ one row per trading record |
| Neon Object Storage | 1 object, 4.8 KB (first real backup, `ledger/jinx-ledger-20261021.db.gz`) | newest 7 |
| Neon AI Gateway | reachable, 60 models | 2 requests in flight |
| Neon PostgreSQL | connected 2026-10-21: PostgreSQL 18.6 via the pooler, fresh schema created by JINX (7 ledger tables + `jinx_meta`), 7.9 MB database (Neon's baseline), round trip 20 ms from jinx-nyc | budget 400 MB |
| Redis Cloud | connected: Redis 8.6.2, 228 keys after one pass, every key with a TTL (97 s to 2 h, 0 without), 2.3 MB used, eviction policy `volatile-lru`, round trip 7 ms. Plain `redis://` (the free database offers no TLS): it carries only expiring snapshots, no credentials or trade records | budget 25 MB |
| Railway PostgreSQL | connected 2026-10-21 (credentials pulled with the project token through Railway's API): PostgreSQL 18.6, research schema created by JINX, first pass wrote 737 live wallets (`wallet_stats` 256 kB) and 1 performance sample; database 8.0 MB; round trip 76 ms (sfo region, off the trading path) | budget 4 GB of the 5 GB volume |
| pytest temp area | 0 bytes after a full run (was 99 MB) | none retained |
| Filesystem (jinx-nyc, not JINX data) | 88% used, 3.7 GB free → monitor reports `warn` | Contabo will be measured on first deploy |

Latency (local processing; order-execution latency to the network is measured separately in Phase 4):
receive → state p50 0.42 ms, p95 0.78 ms, p99 1.10 ms (3,007 live events); with Neon, Redis, object storage and the AI Gateway all connected, p50 0.44 ms, p95 0.80 ms, p99 1.22 ms (5,009 events, 130 s), RSS 102 MB; with all five services connected (Neon, Railway, Redis, object storage, AI Gateway): p50 0.43 ms, p95 0.78 ms, p99 1.10 ms (4,454 events, 135 s), RSS 102 MB, health `ok`, 0 service failures. Paper order path, 400 entry+exit cycles:
entry p50 0.30 ms / p95 0.48 ms with the outbox triggers, 0.20 / 0.29 ms without them. The outbox adds ≈0.1 ms
locally, and nothing remote sits on the order path.

### 4.1 Contabo, live (2026-10-21, release a8129a7, ~3 minutes after start, mode OFF, paper)

| Item | Measured |
|---|---|
| Health | `ok`; PumpAPI and PumpPortal connected; 4,806 events applied, 0 unapplied; 0 failures on all five services |
| Local processing, receive → state | p50 0.79 ms, p95 2.06 ms, p99 4.97 ms (4,806 events). Slower than the engineering host (p50 0.43 ms): shared vCPU |
| Process RAM | 99 MB (budget 1 GB, ceiling 2 GB); market store 2.3 MB in RAM (261 tokens, 1,211 wallets) |
| Disk | ledger directory `/var/lib/jinx` 204 KB; code and venv `/opt/jinx` 157 MB (newest 3 releases kept); journal 16 MB (cap 500 MB); filesystem 2% used |
| Neon / Railway / Redis / object storage | Neon 7.9 MB, Railway 8.2 MB (643 wallets written by the first pass), Redis 2.5 MB, backup 4.8 KB: all `ok` against budget |
| TCP connect from Contabo (best of 5) | GMGN 1.9 ms, PumpPortal 2.3 ms, Railway proxy 7.0 ms, Neon 29.9 ms, PumpAPI 83.0 ms. PumpAPI's endpoint is far from US-east; it still delivered every event, but its stream lag is the larger of the two feeds |

## 5. Restore

Ledger: download the newest `ledger/…db.gz` (`neon buckets object get jinx-backups/<key> --file x.gz`, or
`LedgerBackups.latest()`), gunzip, place as `jinx.db` in the data directory before start. Neon PostgreSQL holds the
same records row for row. Market data is never restored: it is rebuilt from the live feeds.

## 6. Credentials and open items

- All service credentials are in the private env file only; the Railway project token is in `~/.config/jinx/railway.env`
  (mode 600) for automation. Nothing is in Git, logs or docs (enforced by `tests/test_secrets.py`).
- Neon `neondb_owner` password rotated 2026-10-21 with SQL `ALTER ROLE` (Neon's documented way to set a chosen
  password): the new one is verified and the previous one is rejected. Neon's schema and data were untouched.
- Contabo access: reinstalled by owner approval 2026-10-21 with the deploy key only (no password login).
- Before any live trading (Phase 3+), GMGN's trusted IP must include 13.140.224.208 (today it is the engineering host).
- The object-storage keys and AI Gateway token shared in chat remain in use (owner decision); Neon offers rotation for
  them in the console only.
