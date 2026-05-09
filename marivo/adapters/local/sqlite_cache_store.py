from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from marivo.contracts.ids import CacheKey
from marivo.contracts.values import CacheValue


class SqliteCacheStore:
    """SQLite-backed CacheStore with TTL-based expiration and lazy cleanup.

    Values are stored as BLOBs (CacheValue is a bytes NewType).
    Optional TTL is supported via an expires_at column; expired entries
    are lazily removed on read.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def get(self, key: CacheKey) -> CacheValue | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value, expires_at FROM cache_entries WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            value_bytes, expires_at = row
            if expires_at is not None:
                expiry = datetime.fromisoformat(expires_at)
                if expiry < datetime.now(UTC):
                    conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                    conn.commit()
                    return None
            return CacheValue(value_bytes)
        except Exception:
            return None  # cache miss is safe
        finally:
            conn.close()

    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None:
        conn = self._connect()
        try:
            expires_at: str | None = None
            if ttl is not None:
                expires_at = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO cache_entries (key, value, expires_at) VALUES (?, ?, ?)",
                (key, bytes(value), expires_at),
            )
            conn.commit()
        except Exception:
            pass  # cache write failure degrades performance, not correctness
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS cache_entries (
                    key         TEXT PRIMARY KEY,
                    value       BLOB NOT NULL,
                    expires_at  TEXT
                )"""
            )
            conn.commit()
        finally:
            conn.close()
