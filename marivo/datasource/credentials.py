"""Context-local, host-owned datasource credential resolution."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import NoReturn, Protocol, SupportsIndex

from marivo.datasource.errors import (
    DatasourceConnectionError,
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
    repair,
)
from marivo.project import resolve_project_root


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
        if not isinstance(value, str) or not value:
            raise ValueError("SecretValue requires a non-empty string.")
        self.__value = value

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

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    __str__ = __repr__

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("SecretValue cannot be serialized or copied.")


@dataclass(frozen=True, repr=False)
class CredentialRequest:
    """Read-only reference and operation facts passed to a host resolver.

    Marivo supplies reference, bound project_root, datasource, sorted fields,
    and deadline_monotonic. The cancelled property follows the operation.
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
    deadline_monotonic: float | None = None
    _cancel: Event = field(default_factory=Event, repr=False, compare=False)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set() or (
            self.deadline_monotonic is not None and time.monotonic() >= self.deadline_monotonic
        )

    def __repr__(self) -> str:
        return (
            f"CredentialRequest reference={self.reference[:60]!r} "
            f"datasource={self.datasource[:60]!r}; use .show()"
        )

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

    def resolve(self, request: CredentialRequest) -> SecretValue:
        """Read one reference using the request's bound project and operation.

        Args:
            request: Marivo-owned reference, datasource, fields and budget.

        Returns:
            Non-empty secret material, or raises DatasourceCredentialError.

        Example:
            >>> value = resolver.resolve(request)

        Constraints:
            Return promptly; never wait for interactive user input.
        """
        ...


_RESOLVER: ContextVar[CredentialResolver | None] = ContextVar("marivo_credentials", default=None)


def current_resolver() -> CredentialResolver | None:
    return _RESOLVER.get()


@contextmanager
def bind_resolver(resolver: CredentialResolver | None) -> Iterator[None]:
    token = _RESOLVER.set(resolver)
    try:
        yield
    finally:
        _RESOLVER.reset(token)


def credential_scope(*, resolver: CredentialResolver) -> AbstractContextManager[None]:
    """Select a host credential resolver for new operations and runtimes.

    Args:
        resolver: Synchronous read-only host adapter, replacing env/cache reads.

    Returns:
        A context manager yielding None. Existing runtimes keep their resolver.

    Example:
        >>> with md.credential_scope(resolver=host_resolver):
        ...     result = md.test("warehouse")

    Constraints:
        No fallback or secret-cache writes. Exit restores selection only; use
        normal connection/Session cleanup. Raw backend calls are not intercepted.
    """
    if not callable(getattr(resolver, "resolve", None)):
        raise TypeError("resolver must implement resolve(request) -> SecretValue")
    return bind_resolver(resolver)


def check_binding(resolver: CredentialResolver | None) -> None:
    active = current_resolver()
    if active is not None and active is not resolver:
        raise DatasourceCredentialScopeError()


@dataclass(repr=False)
class CredentialOperation:
    project_root: Path
    deadline: float | None
    cancel: Event = field(default_factory=Event)
    request: CredentialRequest | None = None

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set() or (
            self.deadline is not None and time.monotonic() >= self.deadline
        )

    def timeout_error(self) -> DatasourceCredentialError | None:
        request = self.request
        if request is None:
            return None
        return DatasourceCredentialError(
            reason="timeout",
            reference=request.reference,
            datasource=request.datasource,
            fields=request.fields,
        )


_OPERATION: ContextVar[CredentialOperation | None] = ContextVar(
    "marivo_credential_operation", default=None
)


@contextmanager
def operation_context(
    *,
    project_root: Path | None = None,
    timeout_seconds: int | None = None,
) -> Iterator[CredentialOperation]:
    parent = _OPERATION.get()
    if parent is not None:
        yield parent
        return
    operation = CredentialOperation(
        project_root=(project_root or resolve_project_root()).resolve(),
        deadline=None if timeout_seconds is None else time.monotonic() + timeout_seconds,
    )
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
    resolver: CredentialResolver,
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
        deadline_monotonic=operation.deadline,
        _cancel=operation.cancel,
    )
    operation.request = request
    try:
        if request.cancelled:
            raise DatasourceCredentialError(
                reason="timeout", reference=reference, datasource=datasource, fields=fields
            )
        try:
            value = resolver.resolve(request)
        except DatasourceCredentialError as exc:
            # Rebuild from Marivo facts; never trust provider messages or causes.
            raise DatasourceCredentialError(
                reason=exc.reason, reference=reference, datasource=datasource, fields=fields
            ) from None
        except Exception:
            raise DatasourceCredentialError(
                reason="unavailable", reference=reference, datasource=datasource, fields=fields
            ) from None
        if request.cancelled:
            raise DatasourceCredentialError(
                reason="timeout", reference=reference, datasource=datasource, fields=fields
            )
        if not isinstance(value, SecretValue):
            raise DatasourceCredentialError(
                reason="invalid-response", reference=reference, datasource=datasource, fields=fields
            )
        return value
    finally:
        operation.request = None


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
        expected="a successful datasource connection or query",
        received="backend operation failed",
        repair=repair(
            kind="reconnect",
            canonical_id="test",
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
    except Exception as exc:
        values = injected_values(backend)
        if values:
            raise connection_error(exc, values) from None
        raise
