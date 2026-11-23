"""Unified datasource management API (md.*)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias

from marivo._authoring.model import AuthoringRepair
from marivo._kernel import execution_log as _execution_log
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.render import Card, RenderableResult
from marivo._kernel.symbolic import (
    DatasourceIdentityHandle,
    DatasourceSymbol,
    native_symbol_ref,
)
from marivo.datasource import credentials as cr
from marivo.datasource.credentials import secrets as _secrets
from marivo.datasource.credentials.inputs import credential_labels
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import (
    DatasourceConnectionTimeoutError,
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
    DatasourceMissingError,
    _backend_failure_summary,
    repair,
)
from marivo.datasource.runtime.backends import BuiltDatasourceBackend
from marivo.datasource.runtime.connections import (
    load_datasource,
    open_backend,
)
from marivo.datasource.runtime.session_factory import probe_backend
from marivo.datasource.store import declarations as _store
from marivo.datasource.store.authoring import (
    DatasourceSpec,
    _loaded_datasource_ref,
    _storage_name,
)
from marivo.datasource.store.builtin import (
    DEFAULT_DATASOURCE_DESCRIPTION,
    DEFAULT_DATASOURCE_NAME,
)
from marivo.telemetry import traced

DEFAULT_CONNECTION_TIMEOUT_SECONDS = 30
"""Default requested native timeout for connection and probe settings.

Unsupported native settings are skipped; there is no total call deadline.
A non-positive timeout is rejected before connection acquisition.
"""


@dataclass(frozen=True, repr=False)
class DatasourceSummary(RenderableResult):
    """Summary row for one configured project datasource."""

    name: str
    backend_type: str

    def _repr_identity(self) -> str:
        return f"DatasourceSummary name={self.name} backend={self.backend_type}"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        if self.name == DEFAULT_DATASOURCE_NAME:
            card = card.field("source", DEFAULT_DATASOURCE_DESCRIPTION)
        return card


@dataclass(frozen=True, repr=False)
class DatasourceList(RenderableResult):
    """Displayable collection of configured project datasource summaries."""

    _items: tuple[DatasourceSummary, ...]

    @property
    def items(self) -> tuple[DatasourceSummary, ...]:
        """Return all datasource summary rows."""
        return self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[DatasourceSummary]:
        return iter(self._items)

    def __getitem__(self, index: int) -> DatasourceSummary:
        return self._items[index]

    def _repr_identity(self) -> str:
        return f"DatasourceList count={len(self._items)}"

    def _card(self) -> Card:
        rows = [[item.name, item.backend_type] for item in self._items]
        card = Card(
            identity=self._repr_identity(),
            available=(".items", ".show()"),
        ).table(columns=["name", "backend"], rows=rows, row_count=len(self._items))
        if any(item.name == DEFAULT_DATASOURCE_NAME for item in self._items):
            card = card.field("default", DEFAULT_DATASOURCE_DESCRIPTION)
        return card


@dataclass(frozen=True, repr=False)
class DatasourceDescription(RenderableResult):
    """Connection fields, env refs and declared source timezone for one datasource."""

    name: str
    backend_type: str
    literal_fields: dict[str, Any]
    env_refs: dict[str, str]
    wall_clock_timezone: str | None = None

    def _repr_identity(self) -> str:
        return (
            f"DatasourceDescription name={self.name} backend={self.backend_type} "
            f"fields={len(self.literal_fields)} env_refs={len(self.env_refs)}"
        )

    def _card(self) -> Card:
        field_names = sorted(self.literal_fields)
        env_ref_names = credential_labels(self.literal_fields, self.env_refs)
        card = Card(identity=self._repr_identity(), available=(".show()",)).field(
            label="columns",
            value=" | ".join(field_names + env_ref_names),
        )

        card.field("wall-clock timezone", self.wall_clock_timezone or "undeclared")
        if self.name == DEFAULT_DATASOURCE_NAME:
            card = card.field("source", DEFAULT_DATASOURCE_DESCRIPTION)
        return card


@dataclass(frozen=True)
class DatasourceFailure:
    """Bounded structured cause for one failed datasource connection test."""

    code: Literal[
        "connection_open_failed",
        "connection_roundtrip_failed",
        "connection_timeout",
        "connection_roundtrip_timeout",
        "credential_missing",
        "credential_denied",
        "credential_unavailable",
        "credential_expired",
        "credential_timeout",
        "credential_invalid_response",
    ]
    exception_type: str
    backend_code: str | None
    backend_name: str | None
    message: str
    timeout_seconds: int | None = None


@dataclass(frozen=True, repr=False)
class DatasourceTestResult(RenderableResult):
    """Result of a datasource connectivity round-trip."""

    name: str
    ok: bool
    latency_ms: int | None
    failure: DatasourceFailure | None
    repair: AuthoringRepair | None

    def __post_init__(self) -> None:
        if self.ok and (self.failure is not None or self.repair is not None):
            raise ValueError(
                "DatasourceTestResult with ok=True requires failure=None and repair=None"
            )
        if not self.ok and (self.failure is None or self.repair is None):
            raise ValueError("DatasourceTestResult with ok=False requires both failure and repair")

    def _repr_identity(self) -> str:
        latency = "n/a" if self.latency_ms is None else f"{self.latency_ms}ms"
        return f"DatasourceTestResult name={self.name} ok={self.ok} latency={latency}"

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".failure", ".repair", ".show()"),
        )
        if self.failure is not None:
            detail = self.failure.exception_type
            if self.failure.backend_code is not None:
                detail += f" code={self.failure.backend_code}"
            if self.failure.backend_name is not None:
                detail += f" name={self.failure.backend_name}"
            card.status(self.failure.code)
            card.field("failure", detail)
            card.field("message", self.failure.message)
        if self.repair is not None:
            card.field("repair", self.repair.action)
            card.field(
                "repair help",
                (
                    f'marivo.help("{self.repair.help_target.surface}.'
                    f'{self.repair.help_target.canonical_id}")'
                ),
            )
            if self.repair.snippet is not None:
                card.field("repair snippet", self.repair.snippet)
        return card


@traced(surface="datasource", operation="register")
def register(
    spec: DatasourceSpec,
    *,
    project_root: Path | None = None,
) -> DatasourceSummary:
    """Create or replace a project datasource file from a DatasourceSpec.

    Args:
        spec: A public backend datasource spec returned by helpers such as
            ``md.duckdb(...)`` or ``md.trino(...)``.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``DatasourceSummary`` for the newly stored datasource.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.duckdb(name="wh", path=":memory:")
        >>> md.register(spec)

    Constraints:
        Call only from setup scripts outside datasource and semantic model
        loading. Declaration files call datasource constructors directly;
        constructors auto-declare while the datasource loader runs.
        Use one of the public typed specs. Sensitive fields use named
        ``md.env(...)`` references, not plaintext literals or generic keyword bags.
        Every explicit ``md.env(...)`` name is persisted; no credential names are
        inferred or omitted by convention.
        Saves only the local target file without loading other declarations.
        Success confirms persistence; load the project separately to validate it.
    """
    stored = _store.save_one(spec, project_root=project_root)
    return DatasourceSummary(name=stored.name, backend_type=stored.backend_type)


@traced(surface="datasource", operation="remove")
def remove(name: str) -> bool:
    """Delete the named project datasource file.

    Args:
        name: The datasource name to remove.

    Returns:
        True if the file existed and was deleted; False if it was not found.

    Example:
        >>> import marivo.datasource as md
        >>> md.remove("wh")
        True

    Constraints:
        Only the project-local ``models/datasources/<name>.py`` file is removed.
    """
    return _store.delete_one(name)


def _list_in_project(*, project_root: Path | None) -> DatasourceList:
    return DatasourceList(
        tuple(
            DatasourceSummary(name=p.name, backend_type=p.backend_type)
            for p in sorted(
                _store.load_all(project_root).values(),
                key=lambda item: item.name,
            )
        )
    )


def _describe_in_project(name: str, *, project_root: Path) -> DatasourceDescription:
    datasource = _store.load_one(name, project_root)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            expected="a registered project datasource",
            received=name,
            location="models/datasources/",
            repair=repair(
                kind="register",
                canonical_id="register",
                action="Choose an existing datasource or register its actual backend and connection in this project before retrying.",
                candidates=tuple(_store.list_names(project_root)),
            ),
        )
    return DatasourceDescription(
        name=datasource.name,
        backend_type=datasource.backend_type,
        literal_fields=dict(datasource.fields),
        env_refs=dict(datasource.env_refs),
        wall_clock_timezone=datasource.wall_clock_timezone,
    )


def _datasource_name(
    value: str | DatasourceSymbol | DatasourceIdentityHandle, *, project_root: Path | None = None
) -> str:
    if isinstance(value, str):
        return _storage_name(value)
    reference = _loaded_datasource_ref(value, project_root=cr.operation_root(project_root))
    if reference is None:
        symbolic = native_symbol_ref(value)
        reference = (
            ref_factory.datasource(symbolic.path)
            if symbolic is not None and symbolic.kind.value == "datasource"
            else None
        )
    if reference is None:
        raise TypeError(
            "datasource must be a name, loaded Datasource object, or native datasource lookup symbol."
        )
    return reference.path


DatasourceFailureCode: TypeAlias = Literal[
    "connection_open_failed",
    "connection_roundtrip_failed",
    "connection_timeout",
    "connection_roundtrip_timeout",
    "credential_missing",
    "credential_denied",
    "credential_unavailable",
    "credential_expired",
    "credential_timeout",
    "credential_invalid_response",
]


def _connection_repair(
    exc: Exception,
    *,
    failure_code: DatasourceFailureCode,
) -> AuthoringRepair:
    existing = getattr(exc, "repair", None)
    if isinstance(existing, AuthoringRepair):
        return existing
    # Timeout failures carry their own repair on DatasourceConnectionTimeoutError
    # (see marivo.datasource.errors._connection_timeout_repair), which the
    # ``existing`` check above returns before this point.
    return repair(
        kind="reconnect",
        canonical_id="datasource_catalog.test",
        action="Reconnect the datasource after fixing its connection settings.",
    )


def _datasource_failure(
    exc: Exception,
    *,
    code: DatasourceFailureCode,
    backend_error: Exception | None = None,
) -> DatasourceFailure:
    if isinstance(exc, DatasourceCredentialError):
        codes: dict[str, DatasourceFailureCode] = {
            "missing": "credential_missing",
            "denied": "credential_denied",
            "unavailable": "credential_unavailable",
            "expired": "credential_expired",
            "timeout": "credential_timeout",
            "invalid-response": "credential_invalid_response",
        }
        code = codes[exc.reason]
    summary = _backend_failure_summary(backend_error or exc)
    timeout_seconds = (
        exc.timeout_seconds if isinstance(exc, DatasourceConnectionTimeoutError) else None
    )
    return DatasourceFailure(
        code=code,
        exception_type=summary.exception_type,
        backend_code=summary.backend_code,
        backend_name=summary.backend_name,
        message=summary.message,
        timeout_seconds=timeout_seconds,
    )


def _failure_code_for_phase(phase: str) -> DatasourceFailureCode:
    """Map the round-trip phase in which an error surfaced to its failure code."""
    if phase == "roundtrip":
        return "connection_roundtrip_failed"
    return "connection_open_failed"


@dataclass
class _ProbeState:
    backend: BuiltDatasourceBackend | None
    phase: Literal["connection", "roundtrip"]
    started: float


def _run_roundtrip(
    fn: Callable[[_ProbeState], DatasourceTestResult],
    *,
    timeout_seconds: int,
    datasource_name: str,
    project_root: Path | None = None,
) -> DatasourceTestResult:
    """Run a probe on the caller thread using native settings and owned cleanup."""
    state = _ProbeState(None, "connection", time.perf_counter())
    with cr.operation_context(project_root=project_root):
        try:
            return fn(state)
        except DatasourceCredentialScopeError:
            raise
        except Exception as error:
            backend = state.backend.backend if state.backend is not None else None
            exc = cr.safe_backend_exception(error, backend)
            original = exc
            native_code = getattr(error, "code", None)
            if native_code is None and error.args and type(error.args[0]) is int:
                native_code = error.args[0]
            native_timeout = (
                isinstance(error, TimeoutError)
                or (type(native_code) in (int, str) and str(native_code) in {"3024", "159"})
                or getattr(error, "error_name", None) == "EXCEEDED_TIME_LIMIT"
            )
            if native_timeout:
                exc = DatasourceConnectionTimeoutError(
                    stage="connection_roundtrip_timeout"
                    if state.phase == "roundtrip"
                    else "connection_timeout",
                    timeout_seconds=timeout_seconds,
                    elapsed_ms=int((time.perf_counter() - state.started) * 1000),
                    datasource_name=datasource_name,
                )
            failure_code = (
                exc.stage
                if isinstance(exc, DatasourceConnectionTimeoutError)
                else _failure_code_for_phase(state.phase)
            )
            return DatasourceTestResult(
                name=datasource_name,
                ok=False,
                latency_ms=int((time.perf_counter() - state.started) * 1000),
                failure=_datasource_failure(exc, code=failure_code, backend_error=original),
                repair=_connection_repair(exc, failure_code=failure_code),
            )
        finally:
            if state.backend is not None:
                state.backend.disconnect()


def _test_in_project(
    name: str | DatasourceSymbol | DatasourceIdentityHandle,
    *,
    timeout_seconds: int,
    project_root: Path | None,
    persist_env_secrets: bool,
) -> DatasourceTestResult:
    datasource_name = _datasource_name(name, project_root=project_root)
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive.")

    def roundtrip(state: _ProbeState) -> DatasourceTestResult:
        connection = open_backend(
            load_datasource(datasource_name, cr.operation_root(project_root)),
            project_root=project_root,
            timeout_seconds=timeout_seconds,
            terminal_timeout_seconds=timeout_seconds,
        )
        state.backend = connection
        state.phase = "roundtrip"
        selected_backend = connection.backend
        profile = require_profile_for_backend_type(selected_backend.name)
        timeout = profile.native_query_timeout
        with timeout(selected_backend, timeout_seconds) if timeout is not None else nullcontext():
            probe_backend(profile.name, selected_backend)
        if persist_env_secrets:
            _secrets.try_persist_backend_env_sourced(selected_backend)
        latency_ms = int((time.perf_counter() - state.started) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
            ok=True,
            latency_ms=latency_ms,
            failure=None,
            repair=None,
        )

    with _execution_log.scope(project_root, datasource=datasource_name):
        return _run_roundtrip(
            roundtrip,
            timeout_seconds=timeout_seconds,
            datasource_name=datasource_name,
            project_root=project_root,
        )


def test_no_persist(
    name: str | DatasourceSymbol | DatasourceIdentityHandle,
    *,
    timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
    project_root: Path | None = None,
) -> DatasourceTestResult:
    """Round-trip the backend without persisting resolved secrets.

    Args:
        name: The datasource name, loaded Datasource object, or native datasource lookup symbol to test.
        timeout_seconds: Requested native connection and probe-query timeouts.
            Unsupported query timeouts are skipped. Defaults to
            ``DEFAULT_CONNECTION_TIMEOUT_SECONDS``. A non-positive value is
            rejected before any connection is attempted.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        A ``DatasourceTestResult`` with ok status, latency, structured failure,
        and typed repair. Timeouts are reported with the same structured
        ``connection_timeout`` / ``connection_roundtrip_timeout`` codes as
        ``DatasourceCatalog.test``.

    Constraints:
        Intended for read-only diagnostics such as ``marivo doctor --connect``.
        Does not write ``~/.marivo/secrets.toml``. The backend is always
        disconnected. Actual probe SQL is recorded in project-local execution logs.

        Native connection and probe-query settings are independent requests.
        Unsupported query timeouts are skipped. No client deadline limits the
        complete call or cleanup.
    """
    return _test_in_project(
        name,
        timeout_seconds=timeout_seconds,
        project_root=project_root,
        persist_env_secrets=False,
    )
