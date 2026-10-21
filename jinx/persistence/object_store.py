"""Daily ledger backup to Neon Object Storage (S3 API), with retention JINX enforces itself (owner storage rule 8:
"Backups: Keep only necessary recoverable data with defined retention. No unlimited backup accumulation").

What is backed up: a consistent snapshot of the durable ledger store (positions, fills, exits, paper wallet,
settings). Market data is RAM-only and is never backed up. One object per UTC day
(`ledger/jinx-ledger-YYYYMMDD.db.gz`); a second backup the same day replaces that day's object. After every upload
every backup beyond the newest `keep` is deleted. Neon stores S3 lifecycle rules but does not run them
(neon.com/docs/storage/s3-compatibility, read 2026-10-21), so expiry cannot be left to the bucket.

The snapshot is taken with SQLite's online backup into memory and gzipped in memory: no temporary file is written.
boto3 is blocking, so the upload and pruning run in a worker thread, off the event loop. Credentials come from the
standard AWS_* environment variables boto3 reads itself; they are never stored on JINX objects.
"""
from __future__ import annotations

import asyncio
import gzip
import sqlite3
import time
from datetime import datetime, timezone

from jinx.store import Store

PREFIX = "ledger/"


def snapshot(store: Store) -> bytes:
    """A consistent, gzipped copy of the store's database, built in memory."""
    dst = sqlite3.connect(":memory:")
    try:
        with store._lock:                       # no write of ours interleaves with the copy
            store.conn.backup(dst)
        image = bytearray(dst.serialize())
        image[18:20] = b"\x01\x01"            # header: rollback journal, not WAL, so the copy opens anywhere
        return gzip.compress(bytes(image), 6)
    finally:
        dst.close()


def restore(data: bytes) -> sqlite3.Connection:
    """Open a backup object as an in-memory database (for inspection or to write it back to a file)."""
    conn = sqlite3.connect(":memory:")
    conn.deserialize(gzip.decompress(data))
    return conn


class LedgerBackups:
    def __init__(self, client, bucket: str = "jinx-backups", keep: int = 7) -> None:
        if keep < 1:
            raise ValueError("keep must be >= 1")
        self.client, self.bucket, self.keep = client, bucket, keep
        self.stats = {"backups": 0, "deleted": 0, "failures": 0, "last_key": None, "last_bytes": None,
                      "last_ok_ns": None, "last_error": None, "objects": None, "stored_bytes": None}

    @classmethod
    def from_env(cls, endpoint: str, region: str | None, bucket: str, keep: int) -> "LedgerBackups":
        import boto3
        from botocore.config import Config
        client = boto3.client("s3", region_name=region, endpoint_url=endpoint,
                              config=Config(s3={"addressing_style": "path"},          # Neon: path-style only
                                            retries={"max_attempts": 3, "mode": "standard"},
                                            connect_timeout=10, read_timeout=30))
        return cls(client, bucket, keep)

    @staticmethod
    def key_for(now_ns: int) -> str:
        day = datetime.fromtimestamp(now_ns / 1e9, tz=timezone.utc).strftime("%Y%m%d")
        return f"{PREFIX}jinx-ledger-{day}.db.gz"

    def _ensure_bucket(self) -> None:
        names = {b["Name"] for b in self.client.list_buckets().get("Buckets", [])}
        if self.bucket not in names:
            self.client.create_bucket(Bucket=self.bucket)

    def _keys(self) -> list[tuple[str, int]]:
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": PREFIX}
            if token:
                kw["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kw)
            out += [(o["Key"], o["Size"]) for o in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                return sorted(out)
            token = page.get("NextContinuationToken")

    def _upload_and_prune(self, key: str, data: bytes) -> dict:
        self._ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="application/gzip")
        keys = self._keys()
        expired = [k for k, _ in keys[:-self.keep]] if len(keys) > self.keep else []
        if expired:
            self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in expired]})
        kept = [(k, size) for k, size in keys if k not in expired]
        return {"key": key, "bytes": len(data), "deleted": len(expired), "objects": len(kept),
                "stored_bytes": sum(size for _, size in kept)}

    async def backup(self, store: Store, now_ns: int | None = None) -> dict | None:
        now_ns = now_ns or time.time_ns()
        data = snapshot(store)
        try:
            report = await asyncio.to_thread(self._upload_and_prune, self.key_for(now_ns), data)
        except Exception as e:
            self.stats["failures"] += 1
            self.stats["last_error"] = type(e).__name__
            return None
        self.stats.update(backups=self.stats["backups"] + 1, deleted=self.stats["deleted"] + report["deleted"],
                          last_key=report["key"], last_bytes=report["bytes"], last_ok_ns=time.time_ns(),
                          last_error=None, objects=report["objects"], stored_bytes=report["stored_bytes"])
        return report

    async def latest(self) -> bytes | None:
        def fetch():
            keys = self._keys()
            if not keys:
                return None
            return self.client.get_object(Bucket=self.bucket, Key=keys[-1][0])["Body"].read()
        return await asyncio.to_thread(fetch)

    def health(self) -> dict:
        return {"component": "object_storage", "bucket": self.bucket, "keep": self.keep, **self.stats}
