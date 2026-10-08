"""Internal datasource connection service with scoped lifetime management."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from typing import Any, TypeVar

import ibis.expr.types as ir
import pandas as pd
from ibis.backends import BaseBackend

from marivo import _execution_log
from marivo.datasource import backends, store
from marivo.datasource import credentials as cr
from marivo.datasource._lifetime import BackendLease
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.authoring import _storage_name
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import (
    DatasourceConnectionError,
    DatasourceConnectionTimeoutError,
    DatasourceCredentialError,
    DatasourceError,
    DatasourceMissingError,
    _backend_failure_summary,
    repair,
)
from marivo.datasource.ir import (
    DatasourceIR,
    EntitySourceIR,
    QueryParamScalar,
    QueryParamScalarList,
)
from marivo.datasource.timezone import DatasourceEngineTimezone, probe_engine_timezone

DEFAULT_CONNECTION_TIMEOUT_SECONDS = 30
_ResultT = TypeVar("_ResultT")
_IN_DEADLINE_WORKER: ContextVar[bool] = ContextVar("marivo_deadline_worker", default=False)


@contextmanager
def deadline_worker() -> Iterator[None]:
    """Keep a complete round-trip on its current deadline worker."""
    token = _IN_DEADLINE_WORKER.set(True)
    try:
        yield
    finally:
        _IN_DEADLINE_WORKER.reset(token)


def run_with_deadline(
    fn: Callable[[], _ResultT],
    *,
    timeout_seconds: int,
    datasource_name: str,
    operation: cr.CredentialOperation,
    release: Callable[[_ResultT], None],
) -> _ResultT:
    """Bound a handshake and discard late results without blocking on cleanup."""
    values: list[_ResultT] = []
    errors: list[BaseException] = []
    lock = Lock()

    def target() -> None:
        try:
            with deadline_worker():
                value = fn()
            with lock:
                late = operation.cancelled
                if not late:
                    values.append(value)
            if late:
                release(value)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    context = copy_context()
    worker = Thread(target=lambda: context.run(target), daemon=True)
    started = monotonic()
    try:
        worker.start()
        worker.join(max(0.0, (operation.deadline or started + timeout_seconds) - monotonic()))
        if worker.is_alive() or operation.cancelled:
            with lock:
                credential_error = operation.timeout_error()
                if (
                    credential_error is None
                    and errors
                    and isinstance(errors[0], DatasourceCredentialError)
                ):
                    credential_error = errors[0]
            if credential_error is not None:
                raise credential_error
            raise DatasourceConnectionTimeoutError(
                stage="connection_timeout",
                timeout_seconds=timeout_seconds,
                elapsed_ms=int((monotonic() - started) * 1000),
                datasource_name=datasource_name,
            )
        if errors:
            raise errors[0]
        return values[0]
    except BaseException:
        # Interrupted callers abandon ownership just like timed-out callers.
        # Synchronize with publication so either we or the worker releases it.
        with lock:
            operation.cancel.set()
            abandoned = values.pop() if values else None
        if abandoned is not None:
            # Third-party disconnect implementations may themselves block.
            Thread(target=lambda: release(abandoned), daemon=True).start()
        raise


def open_backend(
    datasource: DatasourceIR,
    *,
    project_root: Path | None = None,
    read_only: bool = False,
    timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
    terminal_timeout_seconds: int | None = None,
) -> backends.BuiltDatasourceBackend:
    """Open one owned backend using the engine's thread and credential policy."""
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive.")
    profile = require_profile_for_backend_type(datasource.backend_type)

    def build() -> backends.BuiltDatasourceBackend:
        try:
            return backends.build_backend_with_secrets(
                datasource, read_only=read_only, terminal_timeout_seconds=terminal_timeout_seconds
            )
        except DatasourceError:
            raise
        except Exception as exc:
            failure = _backend_failure_summary(exc)
            raise DatasourceConnectionError(
                message=f"The datasource backend could not be opened: {failure.message}",
                expected="a connection compatible with the declared datasource configuration",
                received=failure.identity,
                location=f"datasource {datasource.name!r}",
                repair=repair(
                    kind="reconnect",
                    canonical_id="test",
                    action="Check the datasource configuration and connection permissions, then retry.",
                ),
            ) from None

    with cr.operation_context(
        project_root=project_root, timeout_seconds=timeout_seconds
    ) as operation:
        if profile.connection_thread == "caller" or _IN_DEADLINE_WORKER.get():
            return build()
        return run_with_deadline(
            build,
            timeout_seconds=timeout_seconds,
            datasource_name=datasource.name,
            operation=operation,
            release=lambda built: built.disconnect(),
        )


def _disconnect(backend: Any) -> bool:
    """Disconnect a backend, silently ignoring errors or missing method."""
    disconnect = getattr(backend, "disconnect", None)
    if callable(disconnect):
        try:
            disconnect()
        except Exception:
            return False
        return True
    return False


def load_datasource(name: str, project_root: Path | None) -> DatasourceIR:
    """Load one declaration through the unified project/layer store."""
    datasource_ir = store.load_one(name, project_root=project_root)
    if datasource_ir is None:
        available = store.list_names(project_root)
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            expected="a registered project datasource",
            received=name,
            location="models/datasources/",
            repair=repair(
                kind="register",
                canonical_id="register",
                action="Register the datasource before retrying.",
                snippet=f'md.register(md.duckdb(name={name!r}, path=":memory:"))',
                candidates=tuple(available),
            ),
        )
    return datasource_ir


def _build_backend_from_store(
    name: str,
    project_root: Path | None,
    *,
    read_only: bool = False,
    terminal_timeout_seconds: int | None = None,
) -> backends.BuiltDatasourceBackend:
    return open_backend(
        load_datasource(name, project_root),
        project_root=project_root,
        read_only=read_only,
        terminal_timeout_seconds=terminal_timeout_seconds,
    )


@dataclass(frozen=True)
class DatasourceConnectionConfig:
    """Reader-owned connection configuration without live backend state."""

    project_root: Path
    resolver: cr.CredentialResolver | None = field(repr=False)

    @contextmanager
    def operation(self) -> Iterator[DatasourceConnectionService]:
        with cr.bind_resolver(self.resolver):
            service = DatasourceConnectionService(
                self.project_root,
            )
        try:
            yield service
        finally:
            service.close_all()


class DatasourceConnectionService:
    """Manages scoped and session-scoped backend connections.

    Provides two access patterns:

    - ``use_backend(name)`` -- context manager that disconnects on exit,
      even if an error occurred inside the block.
    - ``session_backend(name)`` -- returns a cached backend that lives
      until ``close_all()`` is called.

    This service uses ``backends.build_backend()`` (without secrets
    tracking) because it is intended for short-lived scoped operations
    such as inspections and previews. The private connectivity probe in
    ``manage.py`` handles validated secret caching separately.
    """

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        backends: dict[str, Callable[[], Any]] | None = None,
        backend_factory: Callable[[str], Any] | None = None,
        use_datasources: bool = True,
    ) -> None:
        self._project_root = cr.operation_root(None if project_root is None else Path(project_root))
        self._resolver = cr.current_resolver()
        self._backend_overrides = dict(backends or {})
        self._backend_factory = backend_factory
        self._use_datasources = use_datasources
        self._terminal_timeout_seconds: int | None = None
        self._session_backends: dict[str, Any] = {}
        self._leases: dict[str, BackendLease] = {}
        self._source_sessions: dict[str, SourceSession] = {}
        self._engine_timezones: dict[str, DatasourceEngineTimezone] = {}

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    @contextmanager
    def operation(self) -> Iterator[DatasourceConnectionService]:
        """Yield a fresh batch cache; never close the parent owner's connections."""
        with cr.bind_resolver(self._resolver):
            child = DatasourceConnectionService(
                self._project_root,
                backends=self._backend_overrides,
                backend_factory=self._backend_factory,
                use_datasources=self._use_datasources,
            )
        try:
            yield child
        finally:
            child.close_all()

    def backend_for(self, datasource: DatasourceIR, *, read_only: bool = False) -> BaseBackend:
        """Borrow a Marivo-owned backend from this operation's cache."""
        cr.check_binding(self._resolver)
        backend = self._session_backends.get(datasource.name)
        if backend is None:
            with cr.bind_resolver(self._resolver):
                built = open_backend(
                    datasource, project_root=self._project_root, read_only=read_only
                )
            self._session_backends[datasource.name] = built.backend
            self._leases[datasource.name] = built.lease
            backend = built.backend
        return backend

    @contextmanager
    def resolution_context(self) -> Iterator[None]:
        """Use the captured resolver for auxiliary Marivo-owned connections."""
        cr.check_binding(self._resolver)
        with cr.bind_resolver(self._resolver):
            yield

    @contextmanager
    def use_backend(
        self,
        name: str,
        *,
        read_only: bool = False,
        terminal_timeout_seconds: int | None = None,
        on_disconnect: Callable[[bool], None] | None = None,
    ) -> Iterator[Any]:
        """Yield a live backend, disconnecting on exit (success or error)."""
        datasource_name = _storage_name(name)
        with _execution_log.scope(self._project_root, datasource=datasource_name):
            with self.resolution_context():
                built = _build_backend_from_store(
                    datasource_name,
                    self._project_root,
                    read_only=read_only,
                    terminal_timeout_seconds=terminal_timeout_seconds,
                )
            try:
                with cr.backend_errors(built.backend):
                    yield built.backend
            finally:
                disconnected = built.lease.close()
                if on_disconnect is not None:
                    on_disconnect(disconnected)

    def _build_session_backend(self, name: str) -> Any:
        datasource_name = _storage_name(name)
        override = self._backend_overrides.get(datasource_name)
        if override is not None:
            backend = override()
            self._leases[datasource_name] = BackendLease(backend)
            return backend
        if self._backend_factory is not None:
            backend = self._backend_factory(datasource_name)
            self._leases[datasource_name] = BackendLease(backend)
            return backend
        if self._use_datasources:
            built = _build_backend_from_store(
                datasource_name,
                self._project_root,
                read_only=self._terminal_timeout_seconds is not None,
                terminal_timeout_seconds=self._terminal_timeout_seconds,
            )
            self._leases[datasource_name] = built.lease
            if self._terminal_timeout_seconds is not None:
                built.backend._marivo_certified_authoring = True
            return built.backend
        raise DatasourceMissingError(
            message=f"datasource {datasource_name!r} is not configured for this session",
            expected="a configured session datasource",
            received=datasource_name,
            location="datasource session",
            repair=repair(
                kind="register",
                canonical_id="register",
                action="Register the datasource or configure a session backend override.",
                snippet=f'md.register(md.duckdb(name={datasource_name!r}, path=":memory:"))',
                candidates=tuple(sorted(self._backend_overrides)),
            ),
        )

    @contextmanager
    def terminal_scope(self, timeout_seconds: int) -> Iterator[None]:
        """Isolate bounded authoring connections and release them before restoring the cache."""
        previous = (
            self._session_backends,
            self._leases,
            self._source_sessions,
            self._engine_timezones,
            self._terminal_timeout_seconds,
        )
        self._session_backends = {}
        self._leases = {}
        self._source_sessions = {}
        self._engine_timezones = {}
        self._terminal_timeout_seconds = timeout_seconds
        try:
            yield
        finally:
            try:
                self.close_all()
            finally:
                (
                    self._session_backends,
                    self._leases,
                    self._source_sessions,
                    self._engine_timezones,
                    self._terminal_timeout_seconds,
                ) = previous

    def session_backend(self, name: str) -> Any:
        """Return a cached backend for the named datasource.

        The same backend instance is returned on repeated calls for the
        same name until ``close_all()`` is called.
        """
        datasource_name = _storage_name(name)
        external = datasource_name in self._backend_overrides or self._backend_factory is not None
        if not external:
            cr.check_binding(self._resolver)
        backend = self._session_backends.get(datasource_name)
        if backend is None:
            with _execution_log.scope(self._project_root, datasource=datasource_name):
                if external:
                    backend = self._build_session_backend(datasource_name)
                else:
                    with cr.bind_resolver(self._resolver):
                        backend = self._build_session_backend(datasource_name)
            self._session_backends[datasource_name] = backend
        return backend

    def source_session(self, name: str, datasource: DatasourceIR) -> SourceSession:
        """Return the source owner sharing this connection service's backend."""
        datasource_name = _storage_name(name)
        session = self._source_sessions.get(datasource_name)
        if session is None:
            backend = self.session_backend(datasource_name)
            if not isinstance(backend, BaseBackend):
                raise TypeError("a live Ibis backend is required for governed source reads")
            session = SourceSession(
                provider_for(datasource.backend_type),
                datasource,
                backend,
                owns_backend=False,
                project_root=self._project_root,
            )
            self._source_sessions[datasource_name] = session
        return session

    def bind_source(
        self,
        name: str,
        identity: str,
        datasource: DatasourceIR,
        source: EntitySourceIR,
        source_params: dict[str, QueryParamScalar | QueryParamScalarList] | None = None,
    ) -> ir.Table:
        """Bind one Semantic entity to the same source owner used for reads."""
        session = self.source_session(name, datasource)
        if source_params:
            parameter_digest = hashlib.sha256(
                json.dumps(source_params, sort_keys=True, allow_nan=False).encode("utf-8")
            ).hexdigest()
            identity = f"{identity}@{parameter_digest}"
        return session.bind(source, source_identity=identity, source_params=source_params).relation

    def collect_source(
        self,
        name: str,
        expression: ir.Table,
        *,
        purpose: str,
        max_rows: int,
    ) -> pd.DataFrame:
        """Collect a bounded derived expression through its bound source session."""
        session = self._source_sessions.get(_storage_name(name))
        if session is None:
            raise RuntimeError("source expression has no session binding")
        frame = session.collect_bounded(expression, purpose=purpose, max_rows=max_rows).to_pandas()
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("source collection must produce a pandas DataFrame")
        return frame

    def engine_timezone(self, name: str) -> DatasourceEngineTimezone:
        """Return the cached engine timezone for a datasource session backend."""
        datasource_name = _storage_name(name)
        resolved = self._engine_timezones.get(datasource_name)
        if resolved is None:
            backend = self.session_backend(datasource_name)
            with cr.backend_errors(backend):
                resolved = probe_engine_timezone(backend)
            self._engine_timezones[datasource_name] = resolved
        return resolved

    def close_all(self) -> None:
        """Disconnect all cached session backends and clear the cache."""
        for session in self._source_sessions.values():
            session.close()
        for name, backend in self._session_backends.items():
            lease = self._leases.get(name)
            if lease.close() if lease is not None else _disconnect(backend):
                bound_session = self._source_sessions.get(name)
                if bound_session is not None:
                    bound_session.mark_backend_disconnected()
        self._source_sessions.clear()
        self._session_backends.clear()
        self._leases.clear()
        self._engine_timezones.clear()
