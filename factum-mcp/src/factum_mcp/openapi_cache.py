from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class _OpenApiCacheEntry:
    value: dict[str, object]
    expires_at: float


class OpenApiResponseCache:
    """TTL cache for OpenAPI discovery tool responses."""

    def __init__(
        self,
        ttl_sec: int,
        *,
        time_fn: Callable[[], float] = monotonic,
    ) -> None:
        self._ttl_sec = ttl_sec
        self._time_fn = time_fn
        self._entries: dict[tuple[object, ...], _OpenApiCacheEntry] = {}

    def get(self, key: tuple[object, ...]) -> dict[str, object] | None:
        if self._ttl_sec == 0:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        now = self._time_fn()
        if now >= entry.expires_at:
            self._entries.pop(key, None)
            return None
        return deepcopy(entry.value)

    def set(self, key: tuple[object, ...], value: dict[str, object]) -> None:
        if self._ttl_sec == 0:
            return
        self._entries[key] = _OpenApiCacheEntry(
            value=deepcopy(value),
            expires_at=self._time_fn() + self._ttl_sec,
        )
