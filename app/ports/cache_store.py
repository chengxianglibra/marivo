from __future__ import annotations

from typing import Protocol

from app.contracts.ids import CacheKey
from app.contracts.values import CacheValue


class CacheStore(Protocol):
    def get(self, key: CacheKey) -> CacheValue | None: ...
    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None: ...
