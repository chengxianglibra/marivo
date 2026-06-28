"""Internal datasource connection service with scoped lifetime management."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from marivo.datasource import backends, store
from marivo.datasource.authoring import _storage_name
from marivo.datasource.errors import DatasourceMissingError
from marivo.datasource.timezone import DatasourceEngineTimezone, probe_engine_timezone


def _disconnect(backend: Any) -> None:
    """Disconnect a backend, silently ignoring errors or missing method."""
    disconnect = getattr(backend, "disconnect", None)
    if callable(disconnect):
        with suppress(Exception):
            disconnect()


def _build_backend_from_store(
    name: str,
    project_root: Path | None,
    *,
    read_only: bool = False,
) -> Any:
    """Load a datasource from the project store and open a live backend."""
    datasource_ir = store.load_one(name, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            details={"datasource": name, "available": store.list_names(project_root)},
        )
    return backends.build_backend(datasource_ir, read_only=read_only)


class DatasourceConnectionService:
    """Manages scoped and session-scoped backend connections.

    Provides two access patterns:

    - ``use_backend(name)`` -- context manager that disconnects on exit,
      even if an error occurred inside the block.
    - ``session_backend(name)`` -- returns a cached backend that lives
      until ``close_all()`` is called.

    This service uses ``backends.build_backend()`` (without secrets
    tracking) because it is intended for short-lived scoped operations
    such as inspections and previews.  The public ``connect()`` API in
    ``manage.py`` continues to handle secrets separately.
    """

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        backends: dict[str, Callable[[], Any]] | None = None,
        backend_factory: Callable[[str], Any] | None = None,
        use_datasources: bool = True,
    ) -> None:
        self._project_root = None if project_root is None else Path(project_root)
        self._backend_overrides = dict(backends or {})
        self._backend_factory = backend_factory
        self._use_datasources = use_datasources
        self._session_backends: dict[str, Any] = {}
        self._engine_timezones: dict[str, DatasourceEngineTimezone] = {}

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    @contextmanager
    def use_backend(self, name: str, *, read_only: bool = False) -> Iterator[Any]:
        """Yield a live backend, disconnecting on exit (success or error)."""
        datasource_name = _storage_name(name)
        backend = _build_backend_from_store(
            datasource_name, self._project_root, read_only=read_only
        )
        try:
            yield backend
        finally:
            _disconnect(backend)

    def _build_session_backend(self, name: str) -> Any:
        datasource_name = _storage_name(name)
        override = self._backend_overrides.get(datasource_name)
        if override is not None:
            return override()
        if self._backend_factory is not None:
            return self._backend_factory(datasource_name)
        if self._use_datasources:
            return _build_backend_from_store(datasource_name, self._project_root)
        raise DatasourceMissingError(
            message=f"datasource {datasource_name!r} is not configured for this session",
            details={"datasource": datasource_name, "available": sorted(self._backend_overrides)},
        )

    def session_backend(self, name: str) -> Any:
        """Return a cached backend for the named datasource.

        The same backend instance is returned on repeated calls for the
        same name until ``close_all()`` is called.
        """
        datasource_name = _storage_name(name)
        backend = self._session_backends.get(datasource_name)
        if backend is None:
            backend = self._build_session_backend(datasource_name)
            self._session_backends[datasource_name] = backend
        return backend

    def engine_timezone(self, name: str) -> DatasourceEngineTimezone:
        """Return the cached engine timezone for a datasource session backend."""
        datasource_name = _storage_name(name)
        resolved = self._engine_timezones.get(datasource_name)
        if resolved is None:
            backend = self.session_backend(datasource_name)
            resolved = probe_engine_timezone(backend)
            self._engine_timezones[datasource_name] = resolved
        return resolved

    def close_all(self) -> None:
        """Disconnect all cached session backends and clear the cache."""
        for backend in self._session_backends.values():
            _disconnect(backend)
        self._session_backends.clear()
        self._engine_timezones.clear()
