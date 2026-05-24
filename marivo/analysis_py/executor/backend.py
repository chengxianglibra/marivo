"""Per-session backend cache."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

from marivo.analysis_py.errors import NoBackendFactoryError


class BackendCache:
    def __init__(self, factory: Callable[[str], Any] | None) -> None:
        self._factory = factory
        self._cache: dict[str, Any] = {}

    def get_or_create(self, datasource_name: str) -> Any:
        if self._factory is None:
            raise NoBackendFactoryError(
                message="session has no backend_factory; data-materializing intents need one",
                hint="Pass backends={...} or backend_factory=... when creating or attaching.",
            )
        if datasource_name not in self._cache:
            self._cache[datasource_name] = self._factory(datasource_name)
        return self._cache[datasource_name]

    def close_all(self) -> None:
        for backend in self._cache.values():
            disconnect = getattr(backend, "disconnect", None)
            if callable(disconnect):
                with suppress(Exception):
                    disconnect()
        self._cache.clear()
