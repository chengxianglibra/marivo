"""Unified datasource management API (md.*)."""

from __future__ import annotations

import builtins
import copy
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

import pandas as pd
from pandas.api.types import is_object_dtype

from marivo._authoring.model import AuthoringRepair
from marivo.datasource import credentials as cr
from marivo.datasource import secrets as _secrets
from marivo.datasource import store as _store
from marivo.datasource._builtin import DEFAULT_DATASOURCE_DESCRIPTION, DEFAULT_DATASOURCE_NAME
from marivo.datasource._lifetime import BackendLease
from marivo.datasource.authoring import (
    DatasourceSpec,
    _storage_name,
)
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.engines.base import decode_cursor_frame
from marivo.datasource.errors import (
    DatasourceConnectionTimeoutError,
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
    DatasourceError,
    DatasourceMissingError,
    DatasourceObservedEffects,
    DatasourceRawSqlError,
    _backend_failure_summary,
    repair,
)
from marivo.datasource.runtime import (
    DEFAULT_CONNECTION_TIMEOUT_SECONDS as DEFAULT_CONNECTION_TIMEOUT_SECONDS,
)
from marivo.datasource.runtime import (
    DatasourceConnectionService,
    deadline_worker,
    load_datasource,
    open_backend,
)
from marivo.refs import DatasourceKind, Ref
from marivo.render import Card, RenderableResult, result_repr


@dataclass(frozen=True, repr=False)
class DatasourceSummary(RenderableResult):
    """Summary row for one configured project datasource."""

    name: str
    backend_type: str

    @property
    def semantic_id(self) -> str:
        """Stable id used by discovery surfaces; equals ``name``."""
        return self.name

    def _repr_identity(self) -> str:
        return f"DatasourceSummary name={self.name} backend={self.backend_type}"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        if self.name == DEFAULT_DATASOURCE_NAME:
            card.field("source", DEFAULT_DATASOURCE_DESCRIPTION)
        return card


@dataclass(frozen=True, repr=False)
class DatasourceList(RenderableResult):
    """Displayable collection of configured project datasource summaries."""

    _items: tuple[DatasourceSummary, ...]

    @property
    def items(self) -> tuple[DatasourceSummary, ...]:
        """Return all datasource summary rows."""
        return self._items

    def ids(self) -> builtins.list[str]:
        """Return datasource names in display order."""
        return [item.name for item in self._items]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[DatasourceSummary]:
        return iter(self._items)

    def __getitem__(self, index: int) -> DatasourceSummary:
        return self._items[index]

    def _repr_identity(self) -> str:
        return f"DatasourceList count={len(self._items)}"

    def _card(self) -> Card:
        rows = [
            [
                item.name,
                item.backend_type,
                DEFAULT_DATASOURCE_DESCRIPTION
                if item.name == DEFAULT_DATASOURCE_NAME
                else "Project declaration",
            ]
            for item in self._items
        ]
        return Card(
            identity=self._repr_identity(),
            available=(".items", ".ids()", ".show()"),
        ).table(columns=["name", "backend", "source"], rows=rows, row_count=len(self._items))


@dataclass(frozen=True, repr=False)
class DatasourceDescription(RenderableResult):
    """Literal fields and env refs for one datasource."""

    name: str
    backend_type: str
    literal_fields: dict[str, Any]
    env_refs: dict[str, str]

    def _repr_identity(self) -> str:
        return (
            f"DatasourceDescription name={self.name} backend={self.backend_type} "
            f"fields={len(self.literal_fields)} env_refs={len(self.env_refs)}"
        )

    def _card(self) -> Card:
        field_names = sorted(self.literal_fields)
        env_ref_names = sorted(self.env_refs)
        card = Card(identity=self._repr_identity(), available=(".show()",))
        if self.name == DEFAULT_DATASOURCE_NAME:
            card.field("source", DEFAULT_DATASOURCE_DESCRIPTION)
        return card.field(
            label="columns",
            value=" | ".join(field_names + [f"{name}_env" for name in env_ref_names]),
        )


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


@dataclass(frozen=True, repr=False)
class RawSqlResult(RenderableResult):
    """Complete terminal query result from the datasource raw-SQL execution path."""

    datasource: Ref[DatasourceKind]
    backend_type: str
    sql: str
    reason: str
    columns: tuple[str, ...]
    types: dict[str, str]
    rows: tuple[dict[str, object], ...]
    returned_row_count: int
    timeout_seconds: int
    duration_ms: int
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.returned_row_count != len(self.rows):
            raise ValueError(
                "returned_row_count must equal the number of returned rows: "
                f"returned_row_count={self.returned_row_count}, rows={len(self.rows)}"
            )

    @property
    def shape(self) -> tuple[int, int]:
        """Return query result rows by declared columns."""
        return (self.returned_row_count, len(self.columns))

    @property
    def row_count(self) -> int:
        """Return query result rows, not full-source cardinality."""
        return self.returned_row_count

    def _repr_identity(self) -> str:
        return (
            f"RawSqlResult datasource={self.datasource.path} "
            f"rows={self.returned_row_count} terminal_only"
        )

    def _card(self) -> Card:
        preview_rows = tuple(
            tuple(str(row.get(column)) for column in self.columns) for row in self.rows
        )
        card = (
            Card(
                identity=self._repr_identity(),
                available=(
                    ".rows",
                    ".columns",
                    ".types",
                    ".shape",
                    ".row_count",
                    ".to_pandas()",
                    ".show()",
                ),
            )
            .status(f"terminal query result warnings={len(self.warnings)}")
            .field("terminal_only", "true")
            .field("typed_reentry", "false")
            .field("row_count_semantics", "returned_query_rows")
            .field("returned_row_count", str(self.returned_row_count))
            .field(
                "preserves",
                "query result rows, declared columns/types, datasource, SQL reason",
            )
            .field(
                "does_not_preserve",
                "semantic identity, canonical lineage, typed affordances",
            )
            .field(
                "row_count_scope",
                "returned rows are not full-source cardinality",
            )
            .field("datasource", self.datasource.path)
            .field("backend_type", self.backend_type)
            .field("reason", self.reason)
            .field("timeout_seconds", str(self.timeout_seconds))
            .field("duration_ms", str(self.duration_ms))
            .table(self.columns, preview_rows, row_count=self.returned_row_count)
            .field(
                "scope",
                'callers control query size; SQL LIMIT does not bound scan cost; see marivo.help("datasource.raw_sql")',
            )
        )
        if self.warnings:
            card.listing("warnings", self.warnings)
        return card

    def to_pandas(self) -> pd.DataFrame:
        """Return a defensively isolated pandas DataFrame from complete query result rows.

        The DataFrame is built in declared column order. Object-dtype columns
        are recursively deep-copied so mutations to the DataFrame or mutable
        values within object columns cannot propagate back to this result.
        The conversion does not use backend type labels to coerce values,
        execute a new query, or preserve Marivo metadata on the DataFrame.
        """
        df = pd.DataFrame(builtins.list(self.rows), columns=builtins.list(self.columns))
        for column in df.columns:
            if is_object_dtype(df[column].dtype):
                df[column] = df[column].map(copy.deepcopy)
        return df


class DatasourceConnection:
    """Context-manageable datasource backend connection.

    Args:
        backend: The live ibis backend opened for a project datasource.

    Returns:
        A connection proxy that delegates backend methods and owns cleanup.

    Example:
        >>> import marivo.datasource as md
        >>> with md.connect("wh") as con:
        ...     con.raw_sql("SELECT 1")

    Constraints:
        ``with`` blocks yield the raw ibis backend and disconnect on exit.
        Scripts that cannot use ``with`` may call ``.disconnect()`` manually.
        The ``.backend`` property exposes the raw backend for explicit handoff.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._lease = BackendLease(backend)

    @property
    def backend(self) -> Any:
        """Return the wrapped raw ibis backend."""
        return self._backend

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    def __enter__(self) -> Any:
        return self._backend

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> Literal[False]:
        self._disconnect(suppress_errors=exc_type is not None)
        return False

    def _disconnect(self, *, suppress_errors: bool) -> None:
        self._lease.close(suppress_errors=suppress_errors)

    def disconnect(self) -> None:
        """Disconnect the backend once; repeated calls are no-ops."""
        self._disconnect(suppress_errors=False)

    def __repr__(self) -> str:
        state = "closed" if self._lease.closed else "open"
        return result_repr(f"DatasourceConnection backend={type(self._backend).__name__} {state}")


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
        Use one of the public typed specs. Sensitive fields use named
        ``*_env`` references, not plaintext literals or generic keyword bags.
        The built-in name ``default`` is reserved and cannot be registered.
        Every explicit ``*_env`` name is persisted; no credential names are
        inferred or omitted by convention.
    """
    stored = _store.save_one(spec, project_root=project_root)
    return DatasourceSummary(name=stored.name, backend_type=stored.backend_type)


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
        The built-in ``default`` datasource cannot be removed.
    """
    return _store.delete_one(name)


def list() -> DatasourceList:
    """List configured project datasources as a displayable DatasourceList.

    Returns:
        ``DatasourceList`` containing sorted ``DatasourceSummary`` rows.

    Example:
        >>> import marivo.datasource as md
        >>> md.list().show()
        >>> md.list().items

    Constraints:
        Includes the built-in in-memory DuckDB ``default`` without registration
        or a project file, plus persisted project datasources.
    """
    return DatasourceList(
        tuple(
            DatasourceSummary(name=p.name, backend_type=p.backend_type)
            for p in sorted(_store.load_all().values(), key=lambda item: item.name)
        )
    )


def describe(name: str) -> DatasourceDescription:
    """Show literal fields and env refs for one datasource.

    Args:
        name: The datasource name to describe.

    Returns:
        A ``DatasourceDescription`` with literal_fields and env_refs.

    Example:
        >>> import marivo.datasource as md
        >>> md.describe("wh")

    Constraints:
        Includes the built-in ``default`` datasource. Raises
        ``DatasourceMissingError`` for unknown names; no fallback is applied.
    """
    datasource = _store.load_one(name)
    if datasource is None:
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
                candidates=tuple(_store.list_names()),
            ),
        )
    return DatasourceDescription(
        name=datasource.name,
        backend_type=datasource.backend_type,
        literal_fields=dict(datasource.fields),
        env_refs=dict(datasource.env_refs),
    )


def connect(
    name: str,
    *,
    timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
) -> DatasourceConnection:
    """Open a context-manageable live ibis backend for a datasource.

    Args:
        name: The datasource name to connect to.
        timeout_seconds: Wall-clock deadline for the backend-connect handshake.
            Defaults to ``DEFAULT_CONNECTION_TIMEOUT_SECONDS``. A non-positive
            value is rejected before any connection is attempted.

    Returns:
        A ``DatasourceConnection`` proxy that delegates backend methods and
        disconnects automatically when used as a context manager.

    Example:
        >>> import marivo.datasource as md
        >>> with md.connect("wh") as con:
        ...     con.raw_sql("SELECT 1")

    Constraints:
        The built-in ``default`` opens an independent in-memory DuckDB. Its
        temporary tables are not shared with other connections or processes.
        Prefer ``with md.connect(...) as con`` so cleanup is automatic. For
        manual lifetime management, call ``connection.disconnect()`` when done.
        Env-sourced secrets used to open this backend are remembered on the
        connection object so that a subsequent round-trip validation can persist
        them via ``secrets.persist_backend_env_sourced``.

        The connect handshake is fail-closed: if the backend cannot establish
        within ``timeout_seconds`` a ``DatasourceConnectionTimeoutError`` with
        ``stage="connection_timeout"`` is raised. The timeout is a Marivo-side
        wall-clock bound and does not rely on the backend's own query timeout.

        Thread-affine backends (SQLite) are opened inline on the calling thread
        so the returned connection stays usable there; every other backend is
        opened on a deadline worker thread so a hanging gateway fails closed
        rather than blocking indefinitely.
    """
    return _connect_internal(name, timeout_seconds=timeout_seconds)


def _connect_internal(
    name: str,
    *,
    project_root: Path | None = None,
    include_semantic_layers: bool = False,
    timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
) -> DatasourceConnection:
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive.")
    project_root = cr.operation_root(project_root)
    datasource = load_datasource(
        name, project_root, include_semantic_layers=include_semantic_layers
    )
    built = open_backend(datasource, project_root=project_root, timeout_seconds=timeout_seconds)
    connection = DatasourceConnection(built.backend)
    connection._lease = built.lease
    return connection


def _datasource_name(value: str | Ref[DatasourceKind]) -> str:
    return _storage_name(value)


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
        canonical_id="test",
        action="Reconnect the datasource after fixing its connection settings.",
    )


def _datasource_failure(
    exc: Exception,
    *,
    code: DatasourceFailureCode,
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
    summary = _backend_failure_summary(exc)
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


def _release_connection(connection: object) -> None:
    if isinstance(connection, DatasourceConnection):
        connection._disconnect(suppress_errors=True)
        return
    disconnect = getattr(connection, "disconnect", None)
    if callable(disconnect):
        with suppress(Exception):
            disconnect()


def _run_roundtrip_with_deadline(
    fn: Callable[[dict[str, Any]], DatasourceTestResult],
    *,
    timeout_seconds: int,
    datasource_name: str,
    project_root: Path | None = None,
) -> DatasourceTestResult:
    with cr.operation_context(
        project_root=project_root, timeout_seconds=timeout_seconds
    ) as operation:
        return _run_roundtrip_with_deadline_bound(
            fn,
            timeout_seconds=timeout_seconds,
            datasource_name=datasource_name,
            operation=operation,
        )


def _run_roundtrip_with_deadline_bound(
    fn: Callable[[dict[str, Any]], DatasourceTestResult],
    *,
    operation: cr.CredentialOperation,
    timeout_seconds: int,
    datasource_name: str,
) -> DatasourceTestResult:
    """Run a full connectivity round-trip on one worker thread with a deadline.

    ``fn`` performs connect + ``SELECT 1`` (+ optional secret persist) and
    mutates ``state["backend"]`` and ``state["phase"]`` as it progresses. The
    entire round-trip stays on a single worker thread so thread-affine backends
    (SQLite) never cross a thread boundary.

    When the deadline is exceeded the caller disconnects any backend that was
    already opened (which also unblocks a parked network call) and returns a
    typed timeout result whose ``code`` distinguishes the connect handshake from
    the ``SELECT 1`` round-trip. A raised error is mapped to a failure code
    using the phase it surfaced in.

    The worker cannot be forcibly killed. A completed round-trip checks operation
    cancellation before starting default-cache persistence. An already-started
    cache write cannot be rolled back; injected credentials are never cached.
    """
    state: dict[str, Any] = {"backend": None, "phase": "connection", "started": 0.0}
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            with deadline_worker():
                outcome["result"] = fn(state)
        except BaseException as exc:
            values = cr.injected_values(state.get("backend"))
            if values and isinstance(exc, Exception) and not isinstance(exc, DatasourceError):
                outcome["error"] = cr.connection_error(exc, values)
            else:
                outcome["error"] = exc
        finally:
            _release_connection(state.get("backend"))

    context = copy_context()
    thread = threading.Thread(target=lambda: context.run(worker), daemon=True)
    started = time.perf_counter()
    state["started"] = started
    thread.start()
    thread.join(
        max(0.0, (operation.deadline or time.monotonic() + timeout_seconds) - time.monotonic())
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if thread.is_alive() or operation.cancelled:
        credential_error = operation.timeout_error()
        if credential_error is None and isinstance(outcome.get("error"), DatasourceCredentialError):
            credential_error = outcome["error"]
        operation.cancel.set()
        stage: Literal["connection_timeout", "connection_roundtrip_timeout"] = (
            "connection_timeout"
            if state["phase"] == "connection"
            else "connection_roundtrip_timeout"
        )
        exc: Exception = credential_error or DatasourceConnectionTimeoutError(
            stage=stage,
            timeout_seconds=timeout_seconds,
            elapsed_ms=elapsed_ms,
            datasource_name=datasource_name,
            location=f"md.test({datasource_name!r})",
        )
        threading.Thread(
            target=lambda: _release_connection(state.get("backend")),
            daemon=True,
        ).start()
        return DatasourceTestResult(
            name=datasource_name,
            ok=False,
            latency_ms=elapsed_ms,
            failure=_datasource_failure(exc, code=stage),
            repair=_connection_repair(exc, failure_code=stage),
        )

    if "error" in outcome:
        exc = outcome["error"]
        if isinstance(exc, DatasourceCredentialScopeError):
            raise exc
        if isinstance(exc, DatasourceConnectionTimeoutError):
            # The connect phase raised its own typed timeout before the outer
            # deadline fired; keep the precise stage instead of the generic
            # phase-based classification.
            return DatasourceTestResult(
                name=datasource_name,
                ok=False,
                latency_ms=elapsed_ms,
                failure=_datasource_failure(exc, code=exc.stage),
                repair=_connection_repair(exc, failure_code=exc.stage),
            )
        failure_code = _failure_code_for_phase(state["phase"])
        return DatasourceTestResult(
            name=datasource_name,
            ok=False,
            latency_ms=elapsed_ms,
            failure=_datasource_failure(exc, code=failure_code),
            repair=_connection_repair(exc, failure_code=failure_code),
        )

    return cast("DatasourceTestResult", outcome["result"])


def test(
    name: str | Ref[DatasourceKind],
    *,
    timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
) -> DatasourceTestResult:
    """Round-trip the backend and best-effort cache validated env secrets.

    Args:
        name: The datasource name or ``Ref[DatasourceKind]`` to test.
        timeout_seconds: Wall-clock deadline for the backend-connect handshake
            and the ``SELECT 1`` round-trip. Defaults to
            ``DEFAULT_CONNECTION_TIMEOUT_SECONDS``. A non-positive value is
            rejected before any connection is attempted.

    Returns:
        A ``DatasourceTestResult`` with ok status, latency, structured failure,
        and typed repair. A timeout is reported as a structured failure whose
        ``code`` is ``connection_timeout`` (handshake) or
        ``connection_roundtrip_timeout`` (``SELECT 1``), with a truthful
        ``latency_ms`` and a ``repair`` suggesting a larger timeout or a
        reachability check.

    Example:
        >>> import marivo.datasource as md
        >>> md.test(ms.ref.datasource("wh"))

    Constraints:
        On success, env-sourced secrets that resolved correctly are
        offered to the user-global plaintext cache. Cache write failures
        emit a warning without changing the successful result. The backend
        is always disconnected.

        Both the connect handshake and the ``SELECT 1`` round-trip are bounded
        by a Marivo-side wall-clock deadline; neither depends on the backend's
        own query timeout. If the deadline is exceeded the call fails closed
        rather than blocking indefinitely, even when the backend itself cannot
        be interrupted.
    """
    datasource_name = _datasource_name(name)
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive.")

    def roundtrip(state: dict[str, Any]) -> DatasourceTestResult:
        state["backend"] = connect(datasource_name, timeout_seconds=timeout_seconds)
        state["phase"] = "roundtrip"
        state["backend"].raw_sql("SELECT 1")
        with cr.operation_context() as operation:
            if not operation.cancelled:
                _secrets.try_persist_backend_env_sourced(state["backend"])
        latency_ms = int((time.perf_counter() - state["started"]) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
            ok=True,
            latency_ms=latency_ms,
            failure=None,
            repair=None,
        )

    return _run_roundtrip_with_deadline(
        roundtrip,
        timeout_seconds=timeout_seconds,
        datasource_name=datasource_name,
    )


def test_no_persist(
    name: str | Ref[DatasourceKind],
    *,
    timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
    project_root: Path | None = None,
    include_semantic_layers: bool = False,
) -> DatasourceTestResult:
    """Round-trip the backend without persisting resolved secrets.

    Args:
        name: The datasource name or ``Ref[DatasourceKind]`` to test.
        timeout_seconds: Wall-clock deadline for the backend-connect handshake
            and the ``SELECT 1`` round-trip. Defaults to
            ``DEFAULT_CONNECTION_TIMEOUT_SECONDS``. A non-positive value is
            rejected before any connection is attempted.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        A ``DatasourceTestResult`` with ok status, latency, structured failure,
        and typed repair. Timeouts are reported with the same structured
        ``connection_timeout`` / ``connection_roundtrip_timeout`` codes as
        ``md.test``.

    Constraints:
        Intended for read-only diagnostics such as ``marivo doctor --connect``.
        Does not write ``~/.marivo/secrets.toml``. The backend is always
        disconnected.

        Both the connect handshake and the ``SELECT 1`` round-trip are bounded
        by a Marivo-side wall-clock deadline; if the deadline is exceeded the
        call fails closed rather than blocking indefinitely.
    """
    datasource_name = _datasource_name(name)
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive.")

    def roundtrip(state: dict[str, Any]) -> DatasourceTestResult:
        state["backend"] = _connect_internal(
            datasource_name,
            project_root=project_root,
            include_semantic_layers=include_semantic_layers,
        )
        state["phase"] = "roundtrip"
        state["backend"].raw_sql("SELECT 1")
        latency_ms = int((time.perf_counter() - state["started"]) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
            ok=True,
            latency_ms=latency_ms,
            failure=None,
            repair=None,
        )

    return _run_roundtrip_with_deadline(
        roundtrip,
        timeout_seconds=timeout_seconds,
        datasource_name=datasource_name,
        project_root=project_root,
    )


def _require_raw_sql_reason(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be non-empty.")
    return reason.strip()


def _has_sql_statement(sql: str) -> bool:
    """Look past leading separators and comments without parsing executable SQL."""
    position = 0
    comment_depth = 0
    while position < len(sql):
        if comment_depth:
            if sql.startswith("/*", position):
                comment_depth += 1
                position += 2
            elif sql.startswith("*/", position):
                comment_depth -= 1
                position += 2
            else:
                position += 1
        elif sql[position].isspace() or sql[position] == ";":
            position += 1
        elif sql.startswith("--", position) or sql[position] == "#":
            while position < len(sql) and sql[position] not in "\r\n":
                position += 1
        elif sql.startswith("/*", position):
            # MySQL/MariaDB executable comments must reach the backend unchanged.
            if sql.startswith(("/*!", "/*M!"), position):
                return True
            comment_depth = 1
            position += 2
        else:
            return True
    return False


def _require_single_statement(sql: str) -> str:
    """Reject SQL without a statement and ``;``-separated multi-statement input.

    This check only guards statement count. Read-only execution depends on backend
    connection/transaction protections or database permissions, never this check.
    """
    text = sql.strip()
    if not _has_sql_statement(text):
        raise ValueError(
            "sql must contain a statement, not only whitespace, semicolons, or comments."
        )
    stripped = text.rstrip(";")
    if ";" in stripped:
        raise ValueError("raw_sql accepts a single read-only statement.")
    return stripped


def _extract_raw_sql_frame(
    cursor: object,
    include_types: bool,
) -> tuple[tuple[str, ...], tuple[dict[str, object], ...], dict[str, str]]:
    """Decode the complete query result without a client-side row limit."""
    frame = decode_cursor_frame(cursor, include_types=include_types, max_rows=None)
    return frame.columns, frame.rows, frame.types


def raw_sql(
    datasource: Ref[DatasourceKind],
    sql: str,
    *,
    reason: str,
    timeout_seconds: int = 30,
    include_types: bool = True,
    project_root: Path | None = None,
) -> RawSqlResult:
    """Run terminal SQL exploration and load the complete query result.

    Args:
        datasource: Datasource reference returned by ``ms.ref.datasource("warehouse")``.
        sql: Single read-only SQL statement in the datasource's native dialect.
            Apart from trimming whitespace and trailing semicolons, the statement
            executes unchanged, without rewriting or an injected row limit.
        reason: Required exploration reason shown in the result. Name the
            physical or semantic question and disclose inferred assumptions.
        timeout_seconds: Backend execution timeout; fail-closed if unenforceable.
            Connection acquisition has a separate default 30-second handshake
            budget. This value is not an end-to-end operation deadline.
        include_types: Whether to include returned column type labels when available.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        A complete ``RawSqlResult`` labeled as ``terminal_only``. The result
        describes the submitted query, not necessarily the full source population.

    Example:
        >>> import marivo.datasource as md
        >>> import marivo.semantic as ms
        >>> md.raw_sql(ms.ref.datasource("default"), "SELECT 1 AS ok LIMIT 1", reason="check query path")

    Constraints:
        Rejects empty reasons, SQL without a statement (including comment-only
        or semicolon-only input), multi-statement SQL, and non-positive
        timeout before execution. All returned rows are loaded into client memory;
        there is no client-side row or byte cap and no truncation probe. Callers
        must control query size using filters, partition predicates, aggregation,
        and SQL ``LIMIT``. A returned-row limit does not bound backend scan cost.
        Use query plans and a narrow statement to control expensive diagnostics.

        Use read-only SQL and credentials. Existing backend read-only connection
        or transaction protections remain active where supported. Trino relies on
        database-side permissions: use an account denied writes. Marivo does not
        parse SQL for write detection or enforce read-only execution on Trino.

        The backend timeout remains armed during execution and complete result
        fetching. An unenforceable timeout raises ``DatasourceRawSqlError`` before
        the user statement executes. Execution or fetching failures raise a
        ``DatasourceRawSqlError`` without returning a partial result, and the
        backend is always disconnected. Errors do not certify absence of side effects.

        This is a normal source-exploration option when inspection or a generic
        sample cannot answer the current question. Inferred semantics remain
        provisional and must be disclosed at closeout. The result carries no
        metric, time-scope, slice, lineage, or canonical analysis contract and
        cannot re-enter typed analysis. Display previews may omit rows; ``rows``
        and ``to_pandas()`` expose the complete returned query result.
    """
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive.")
    reason_text = _require_raw_sql_reason(reason)
    statement = _require_single_statement(sql)
    datasource_id = _storage_name(datasource)
    datasource_ir = _store.load_one(datasource_id, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMissingError(
            message=f"datasource {datasource_id!r} is not configured",
            expected="a registered project datasource",
            received=datasource_id,
            location="models/datasources/",
            repair=repair(
                kind="register",
                canonical_id="register",
                action="Register the datasource before retrying.",
                snippet=f'md.register(md.duckdb(name={datasource_id!r}, path=":memory:"))',
                candidates=tuple(_store.list_names(project_root)),
            ),
        )
    backend_type = datasource_ir.backend_type
    profile = require_profile_for_backend_type(backend_type)
    timeout = profile.authoring_timeout
    if timeout is None:
        raise DatasourceRawSqlError(
            message="raw_sql failed: backend profile has no enforceable timeout.",
            expected="a backend profile with authoring_timeout configured",
            received=f"backend_type={backend_type!r} has no authoring_timeout",
            location=f"md.raw_sql({datasource_id!r}) backend_type={backend_type!r}",
            effect_observed=DatasourceObservedEffects(query_executed=False),
            repair=repair(
                kind="configure",
                canonical_id="raw_sql",
                action="Configure authoring_timeout for this backend profile before retrying.",
            ),
        )
    service = DatasourceConnectionService(project_root)
    with service.use_backend(datasource_id, read_only=True) as backend:
        start = time.monotonic()
        try:
            with timeout(backend, timeout_seconds):
                cursor = backend.raw_sql(statement)
                columns, rows, types = _extract_raw_sql_frame(
                    cursor,
                    include_types,
                )
        except DatasourceError:
            raise
        except Exception as exc:
            raise DatasourceRawSqlError(
                message="raw_sql execution or result fetching failed.",
                expected="a read-only diagnostic the datasource backend can execute",
                received=cr.redact(str(exc), cr.injected_values(backend)),
                location=f"md.raw_sql({datasource_id!r}) backend_type={backend_type!r}",
                effect_observed=DatasourceObservedEffects(query_executed=True),
                repair=repair(
                    kind="reconnect",
                    canonical_id="raw_sql",
                    action="Verify the datasource connection and retry the diagnostic.",
                ),
            ) from cr.safe_backend_exception(exc, backend)
        duration_ms = int((time.monotonic() - start) * 1000)
        warnings = [
            "raw SQL diagnostics can be expensive; callers control query size and all returned rows load into client memory",
            "terminal custom analysis; no metric, time-scope, slice, lineage, or canonical analysis contract",
        ]
        return RawSqlResult(
            datasource=datasource,
            backend_type=backend_type,
            sql=statement,
            reason=reason_text,
            columns=columns,
            types=types,
            rows=rows,
            returned_row_count=len(rows),
            timeout_seconds=timeout_seconds,
            duration_ms=duration_ms,
            warnings=tuple(warnings),
        )
