"""Host-owned datasource credential resolution with scoped and process defaults."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import NoReturn, Protocol, SupportsIndex, TypeAlias, TypeGuard

from typing_extensions import override

from marivo._kernel.errors import MarivoError
from marivo._kernel.project import resolve_project_root
from marivo._kernel.validation import nonempty_text
from marivo.datasource.errors import (
    DatasourceConnectionError,
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
    _backend_failure_summary,
    repair,
)
from marivo.introspection.live.reflect import import_registered_callable
from marivo.telemetry import traced


class SecretValue:
    """An explicitly readable, non-serializable secret string.

    Args:
        value: Non-empty credential material supplied by the host.

    Example:
        >>> secret = SecretValue("example-only")
        >>> repr(secret)
        'SecretValue(<redacted>)'

    Constraints:
        This wrapper masks normal display; it is not a Python sandbox.
    """

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not nonempty_text(value):
            raise ValueError("SecretValue requires a non-empty string.")
        self.__value = value

    @traced(surface="datasource", operation="secret_value.reveal", kind="method")
    def reveal(self) -> str:
        """Read the secret for a trusted adapter.

        Returns:
            The original non-empty string; takes no parameters.

        Example:
            >>> SecretValue("example-only").reveal()
            'example-only'

        Constraints:
            Do not print or persist the returned material.
        """
        return self.__value

    @override
    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    __str__ = __repr__

    @override
    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("SecretValue cannot be serialized or copied.")


@dataclass(frozen=True, repr=False)
class CredentialRequest:
    """Read-only reference and operation facts passed to a host resolver.

    Marivo supplies reference, bound project_root, datasource, sorted fields,
    and field identities. Hosts own credential-service timeouts.
    Hosts consume requests; they do not need to construct them.

    Example:
        >>> def resolve(request: CredentialRequest) -> SecretValue:
        ...     return SecretValue(host_read(request.reference))

    Constraints:
        Request facts are not authorization. Validate against host-owned scope.
    """

    reference: str
    project_root: Path
    datasource: str
    fields: tuple[str, ...]

    @override
    def __repr__(self) -> str:
        return (
            f"CredentialRequest reference={self.reference[:60]!r} "
            f"datasource={self.datasource[:60]!r}; use .show()"
        )

    @traced(surface="datasource", operation="credential_request.show", kind="method")
    def show(self) -> None:
        """Print bounded request facts, without resolving credentials.

        Returns:
            None. Takes no parameters.

        Example:
            >>> request.show()

        Constraints:
            Omits the full project path and secret material.
        """
        print(repr(self))
        print(f"Fields: {', '.join(self.fields)[:200]}")


class CredentialResolver(Protocol):
    """Synchronous host adapter; owns authorization and service timeouts."""

    @traced(surface="datasource", operation="credential_resolver.resolve", kind="method")
    def resolve(self, request: CredentialRequest) -> SecretValue:
        """Read one reference using the request's bound project and operation.

        Args:
            request: Marivo-owned reference, datasource and fields.

        Returns:
            Non-empty secret material, or raises DatasourceCredentialError.

        Example:
            >>> value = resolver.resolve(request)

        Constraints:
            Return promptly; never wait for interactive user input.
        """
        ...


_PROCESS_RESOLVER_ENV = "MARIVO_CREDENTIAL_RESOLVER"


def _is_resolver(value: object) -> TypeGuard[CredentialResolver]:
    return callable(getattr(value, "resolve", None))


def _load_process_resolver() -> CredentialResolver | None:
    spec = os.environ.get(_PROCESS_RESOLVER_ENV, "")
    module_name, _, factory_name = spec.partition(":")
    if not module_name or not factory_name.isidentifier():
        return None
    try:
        factory = import_registered_callable(spec)
        if callable(factory):
            resolver: object = factory()
            if _is_resolver(resolver):
                return resolver
    except Exception:
        # Import and factory errors may contain secret material.
        return None
    return None


class _ProcessResolver:
    """Stable binding for the process resolver before and after initializing it."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._attempted = False
        self._resolver: CredentialResolver | None = None

    def reset_after_fork(self) -> None:
        # Captured bindings still reference this object in the child.
        self._lock = RLock()
        self._attempted = False
        self._resolver = None

    def configured(self) -> bool:
        return self._attempted or _PROCESS_RESOLVER_ENV in os.environ

    def matches(self, resolver: CredentialResolver) -> bool:
        return self._resolver is resolver

    def load(self, request: CredentialRequest) -> CredentialResolver:
        with self._lock:
            if not self._attempted:
                self._attempted = True
                self._resolver = _load_process_resolver()
            resolver = self._resolver
        if resolver is None:
            error = DatasourceCredentialError(
                reason="unavailable",
                reference=request.reference,
                datasource=request.datasource,
                fields=request.fields,
            )
            error.repair = repair(
                kind="configure",
                canonical_id="credential_scope",
                action=(
                    f"Set {_PROCESS_RESOLVER_ENV} to an importable module and a "
                    "zero-argument resolver factory (module:factory), then restart the process."
                ),
            )
            raise error
        return resolver


ResolverBinding: TypeAlias = CredentialResolver | _ProcessResolver
_DEFAULT_RESOLVER = _ProcessResolver()
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_DEFAULT_RESOLVER.reset_after_fork)

_RESOLVER: ContextVar[ResolverBinding | None] = ContextVar("marivo_credentials", default=None)


def current_resolver() -> ResolverBinding | None:
    scoped = _RESOLVER.get()
    if scoped is not None:
        return scoped
    return _DEFAULT_RESOLVER if _DEFAULT_RESOLVER.configured() else None


@contextmanager
def bind_resolver(resolver: ResolverBinding | None) -> Iterator[None]:
    token = _RESOLVER.set(resolver)
    try:
        yield
    finally:
        _RESOLVER.reset(token)


@traced(surface="datasource", operation="credential_scope")
def credential_scope(*, resolver: CredentialResolver) -> AbstractContextManager[None]:
    """Select a host credential resolver for new operations and runtimes.

    Args:
        resolver: Synchronous read-only host adapter, replacing env/cache reads.

    Returns:
        A context manager yielding None. Existing runtimes keep their resolver.

    Example:
        >>> with md.credential_scope(resolver=host_resolver):
        ...     result = md.load().test("warehouse")

    Constraints:
        The scope takes precedence over MARIVO_CREDENTIAL_RESOLVER, an optional
        process-wide module:factory loaded on the first credential request.
        Host resolution bypasses the environment and secret cache. Exit restores
        selection only. Use normal connection and Session cleanup.
    """
    if not _is_resolver(resolver):
        raise TypeError("resolver must implement resolve(request) -> SecretValue")
    return bind_resolver(resolver)


def check_binding(resolver: ResolverBinding | None) -> None:
    active = _RESOLVER.get()
    # Only an explicit selection can conflict with an existing binding.
    if active is None or active is resolver or isinstance(active, _ProcessResolver):
        return
    if isinstance(resolver, _ProcessResolver) and resolver.matches(active):
        return
    raise DatasourceCredentialScopeError()


@dataclass(repr=False)
class CredentialOperation:
    project_root: Path


_OPERATION: ContextVar[CredentialOperation | None] = ContextVar(
    "marivo_credential_operation", default=None
)


@contextmanager
def operation_context(
    *,
    project_root: Path | None = None,
) -> Iterator[CredentialOperation]:
    parent = _OPERATION.get()
    if parent is not None:
        yield parent
        return
    operation = CredentialOperation(project_root=(project_root or resolve_project_root()).resolve())
    token = _OPERATION.set(operation)
    try:
        yield operation
    finally:
        _OPERATION.reset(token)


def operation_root(project_root: Path | None) -> Path:
    operation = _OPERATION.get()
    return (
        project_root or (operation.project_root if operation else resolve_project_root())
    ).resolve()


def resolve_reference(
    resolver: ResolverBinding,
    operation: CredentialOperation,
    *,
    reference: str,
    datasource: str,
    fields: tuple[str, ...],
) -> SecretValue:
    request = CredentialRequest(
        reference=reference,
        project_root=operation.project_root,
        datasource=datasource,
        fields=fields,
    )
    if isinstance(resolver, _ProcessResolver):
        resolver = resolver.load(request)
    try:
        value = resolver.resolve(request)
    except DatasourceCredentialError as exc:
        # Retain host reasons without exposing host messages or secret causes.
        raise DatasourceCredentialError(
            reason=exc.reason, reference=reference, datasource=datasource, fields=fields
        ) from None
    except Exception:
        raise DatasourceCredentialError(
            reason="unavailable", reference=reference, datasource=datasource, fields=fields
        ) from None
    if not isinstance(value, SecretValue):
        raise DatasourceCredentialError(
            reason="invalid-response", reference=reference, datasource=datasource, fields=fields
        )
    return value


_SECRET_ATTR = "_marivo_injected_credentials"


def remember_injected(backend: object, values: tuple[SecretValue, ...]) -> None:
    if values:
        setattr(backend, _SECRET_ATTR, values)


def injected_values(backend: object) -> tuple[SecretValue, ...]:
    values = getattr(backend, _SECRET_ATTR, ())
    if isinstance(values, tuple):
        return tuple(value for value in values if isinstance(value, SecretValue))
    return ()


def redact(text: str, values: tuple[SecretValue, ...]) -> str:
    for value in sorted(values, key=lambda item: len(item.reveal()), reverse=True):
        text = text.replace(value.reveal(), "<redacted>")
    return text


def connection_error(exc: Exception, values: tuple[SecretValue, ...]) -> DatasourceConnectionError:
    return DatasourceConnectionError(
        message=redact(str(exc), values),
        _backend_failure=_backend_failure_summary(exc, redact=lambda text: redact(text, values)),
        expected="a successful datasource connection or query",
        received="backend operation failed",
        repair=repair(
            kind="reconnect",
            canonical_id="datasource_catalog.test",
            action="Check the datasource connection and query permissions, then retry.",
        ),
    )


def safe_backend_exception(exc: Exception, backend: object) -> Exception:
    values = injected_values(backend)
    return connection_error(exc, values) if values else exc


@contextmanager
def backend_errors(backend: object) -> Iterator[None]:
    """Sanitize driver failures only while Marivo owns the operation."""
    try:
        yield
    except MarivoError:
        raise
    except Exception as exc:
        values = injected_values(backend)
        if values:
            raise connection_error(exc, values) from None
        raise
