from __future__ import annotations

from typing import Protocol

from marivo.contracts.ids import CacheKey
from marivo.contracts.values import CacheValue


class CacheStore(Protocol):
    def get(self, key: CacheKey) -> CacheValue | None: ...
    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None: ...
