"""Internal datasource connection service with scoped lifetime management."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from typing import TypeVar

from ibis.backends import BaseBackend

from marivo.datasource import backends, store
from marivo.datasource import credentials as cr
from marivo.datasource._lifetime import BackendLease
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
from marivo.datasource.ir import DatasourceIR
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
) -> backends.BuiltDatasourceBackend:
    """Open one owned backend using the engine's thread and credential policy."""
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive.")
    profile = require_profile_for_backend_type(datasource.backend_type)

    def build() -> backends.BuiltDatasourceBackend:
        try:
            return backends.build_backend(datasource, read_only=read_only)
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


def load_datasource(
    name: str,
    project_root: Path | None,
    *,
    include_semantic_layers: bool = False,
) -> DatasourceIR:
    """Resolve a datasource declaration through the requested project layers."""
    datasource_ir = (
        store.load_one_layered(name, project_root=project_root)
        if include_semantic_layers
        else store.load_one(name, project_root=project_root)
    )
    if datasource_ir is None:
        available = (
            store.list_names_layered(project_root)
            if include_semantic_layers
            else store.list_names(project_root)
        )
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
    include_semantic_layers: bool = False,
) -> backends.BuiltDatasourceBackend:
    return open_backend(
        load_datasource(name, project_root, include_semantic_layers=include_semantic_layers),
        project_root=project_root,
        read_only=read_only,
    )


@dataclass(frozen=True)
class DatasourceConnectionConfig:
    """Reader-owned connection configuration without live backend state."""

    project_root: Path
    resolver: cr.CredentialResolver | None = field(repr=False)
    include_semantic_layers: bool = False

    @contextmanager
    def operation(self) -> Iterator[DatasourceConnectionService]:
        with cr.bind_resolver(self.resolver):
            service = DatasourceConnectionService(
                self.project_root,
                include_semantic_layers=self.include_semantic_layers,
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

    ``operation()`` creates an isolated batch owner with the same captured
    resolver and configuration. Nested consumers explicitly borrow that owner.
    Every Marivo-built connection retains credential provenance.
    """

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        backends: dict[str, Callable[[], BaseBackend]] | None = None,
        backend_factory: Callable[[str], BaseBackend] | None = None,
        use_datasources: bool = True,
        include_semantic_layers: bool = False,
    ) -> None:
        self._project_root = cr.operation_root(None if project_root is None else Path(project_root))
        self._resolver = cr.current_resolver()
        self._backend_overrides = dict(backends or {})
        self._backend_factory = backend_factory
        self._use_datasources = use_datasources
        self._include_semantic_layers = include_semantic_layers
        self._session_backends: dict[str, BaseBackend] = {}
        self._leases: dict[str, BackendLease] = {}
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
                include_semantic_layers=self._include_semantic_layers,
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
        self, name: str | DatasourceIR, *, read_only: bool = False
    ) -> Iterator[BaseBackend]:
        """Yield a live backend, disconnecting on exit (success or error)."""
        datasource_name = name.name if isinstance(name, DatasourceIR) else _storage_name(name)
        cr.check_binding(self._resolver)
        with cr.bind_resolver(self._resolver):
            if isinstance(name, DatasourceIR):
                built = open_backend(name, project_root=self._project_root, read_only=read_only)
            elif self._include_semantic_layers:
                built = _build_backend_from_store(
                    datasource_name,
                    self._project_root,
                    read_only=read_only,
                    include_semantic_layers=True,
                )
            else:
                built = _build_backend_from_store(
                    datasource_name,
                    self._project_root,
                    read_only=read_only,
                )
        backend = built.backend
        try:
            yield backend
        except DatasourceError:
            # The operation owns typed errors, their redaction, and observed effects.
            raise
        except Exception as exc:
            values = cr.injected_values(backend)
            if values:
                raise cr.connection_error(exc, values) from None
            raise
        finally:
            built.disconnect()

    def _build_session_backend(self, name: str) -> BaseBackend:
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
                include_semantic_layers=self._include_semantic_layers,
            )
            self._leases[datasource_name] = built.lease
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

    def session_backend(self, name: str) -> BaseBackend:
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
            if external:
                backend = self._build_session_backend(datasource_name)
            else:
                with cr.bind_resolver(self._resolver):
                    backend = self._build_session_backend(datasource_name)
            self._session_backends[datasource_name] = backend
        return backend

    def engine_timezone(self, name: str) -> DatasourceEngineTimezone:
        """Return the cached engine timezone for a datasource session backend."""
        datasource_name = _storage_name(name)
        backend = self.session_backend(datasource_name)
        resolved = self._engine_timezones.get(datasource_name)
        if resolved is None:
            try:
                resolved = probe_engine_timezone(backend)
            except Exception as exc:
                values = cr.injected_values(backend)
                if values:
                    raise cr.connection_error(exc, values) from None
                raise
            self._engine_timezones[datasource_name] = resolved
        return resolved

    def close_all(self) -> None:
        """Disconnect all cached session backends and clear the cache."""
        seen: set[int] = set()
        for lease in self._leases.values():
            if id(lease.backend) not in seen:
                seen.add(id(lease.backend))
                lease.close()
        self._leases.clear()
        self._session_backends.clear()
        self._engine_timezones.clear()
