"""Internal datasource connection service with scoped lifetime management."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from marivo.datasource import backends, store
from marivo.datasource.errors import DatasourceMissingError


def _disconnect(backend: Any) -> None:
    """Disconnect a backend, silently ignoring errors or missing method."""
    disconnect = getattr(backend, "disconnect", None)
    if callable(disconnect):
        with suppress(Exception):
            disconnect()


def _build_backend_from_store(name: str, project_root: Path | None) -> Any:
    """Load a datasource from the project store and open a live backend."""
    datasource_ir = store.load_one(name, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            details={"datasource": name, "available": store.list_names(project_root)},
        )
    return backends.build_backend(datasource_ir)


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

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    @contextmanager
    def use_backend(self, name: str) -> Iterator[Any]:
        """Yield a live backend, disconnecting on exit (success or error)."""
        backend = _build_backend_from_store(name, self._project_root)
        try:
            yield backend
        finally:
            _disconnect(backend)

    def _build_session_backend(self, name: str) -> Any:
        override = self._backend_overrides.get(name)
        if override is not None:
            return override()
        if self._backend_factory is not None:
            return self._backend_factory(name)
        if self._use_datasources:
            return _build_backend_from_store(name, self._project_root)
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured for this session",
            details={"datasource": name, "available": sorted(self._backend_overrides)},
        )

    def session_backend(self, name: str) -> Any:
        """Return a cached backend for the named datasource.

        The same backend instance is returned on repeated calls for the
        same name until ``close_all()`` is called.
        """
        backend = self._session_backends.get(name)
        if backend is None:
            backend = self._build_session_backend(name)
            self._session_backends[name] = backend
        return backend

    def close_all(self) -> None:
        """Disconnect all cached session backends and clear the cache."""
        for backend in self._session_backends.values():
            _disconnect(backend)
        self._session_backends.clear()
