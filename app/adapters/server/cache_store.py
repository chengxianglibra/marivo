from __future__ import annotations

from app.contracts.ids import CacheKey
from app.contracts.values import CacheValue


class InMemoryCacheStore:
    """Simple dict-backed cache store. TTL is ignored."""

    def __init__(self) -> None:
        self._cache: dict[str, bytes] = {}

    def get(self, key: CacheKey) -> CacheValue | None:
        raw = self._cache.get(key)
        if raw is None:
            return None
        return CacheValue(raw)

    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None:
        self._cache[key] = bytes(value)
