"""Unified datasource management API (md.*)."""

from __future__ import annotations

import builtins
import copy
import re
import time
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pandas.api.types import is_object_dtype

from marivo._authoring.model import AuthoringContract, AuthoringRepair
from marivo.datasource import backends as _backends
from marivo.datasource import secrets as _secrets
from marivo.datasource import store as _store
from marivo.datasource.authoring import (
    DatasourceSpec,
    _storage_name,
)
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.engines.base import decode_cursor_frame
from marivo.datasource.errors import (
    DatasourceError,
    DatasourceMissingError,
    DatasourceObservedEffects,
    DatasourceRawSqlError,
    repair,
)
from marivo.datasource.runtime import DatasourceConnectionService
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

    def contract(self) -> AuthoringContract:
        """Return the validation and inspection contract for this datasource."""
        from marivo.datasource._capabilities.contracts import contract_for_registered

        return contract_for_registered(self.name)

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(), available=(".contract()", ".render()", ".show()")
        )


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
        rows = [[item.name, item.backend_type] for item in self._items]
        return Card(
            identity=self._repr_identity(),
            available=(".items", ".ids()", ".render()", ".show()"),
        ).table(columns=["name", "backend"], rows=rows, row_count=len(self._items))


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

    def contract(self) -> AuthoringContract:
        """Return the validation and inspection contract for this datasource."""
        from marivo.datasource._capabilities.contracts import contract_for_registered

        return contract_for_registered(self.name)

    def _card(self) -> Card:
        field_names = sorted(self.literal_fields)
        env_ref_names = sorted(self.env_refs)
        return Card(
            identity=self._repr_identity(), available=(".contract()", ".render()", ".show()")
        ).field(
            label="columns",
            value=" | ".join(field_names + [f"{name}_env" for name in env_ref_names]),
        )


@dataclass(frozen=True, repr=False)
class DatasourceTestResult(RenderableResult):
    """Result of a datasource connectivity round-trip."""

    name: str
    ok: bool
    latency_ms: int | None
    repair: AuthoringRepair | None

    def _repr_identity(self) -> str:
        latency = "n/a" if self.latency_ms is None else f"{self.latency_ms}ms"
        return f"DatasourceTestResult name={self.name} ok={self.ok} latency={latency}"

    def contract(self) -> AuthoringContract:
        """Return the observed connection-validation state for this result."""
        from marivo.datasource._capabilities.contracts import contract_for_connection_test

        return contract_for_connection_test(self.name, ok=self.ok)

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(), available=(".contract()", ".render()", ".show()")
        )
        if self.repair is not None:
            card.status(self.repair.action)
        return card


@dataclass(frozen=True, repr=False)
class RawSqlResult(RenderableResult):
    """Bounded terminal result from the datasource raw-SQL execution path."""

    datasource: Ref[DatasourceKind]
    backend_type: str
    sql: str
    reason: str
    columns: tuple[str, ...]
    types: dict[str, str]
    rows: tuple[dict[str, object], ...]
    requested_limit: int
    returned_row_count: int
    is_truncated: bool
    timeout_seconds: int
    duration_ms: int
    warnings: tuple[str, ...]

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
                available=(".rows", ".columns", ".types", ".to_pandas()", ".render()", ".show()"),
            )
            .status(f"terminal_only truncated={self.is_truncated} warnings={len(self.warnings)}")
            .field("datasource", self.datasource.path)
            .field("backend_type", self.backend_type)
            .field("reason", self.reason)
            .field(
                "rows",
                f"{self.returned_row_count} of {self.requested_limit} (truncated={self.is_truncated})",
            )
            .field("timeout_seconds", str(self.timeout_seconds))
            .field("duration_ms", str(self.duration_ms))
            .table(self.columns, preview_rows, row_count=self.returned_row_count)
            .field(
                "scope",
                'bounded returned rows do not guarantee a cheap diagnostic; see md.help("raw_sql")',
            )
        )
        if self.warnings:
            card.listing("warnings", self.warnings)
        return card

    def to_pandas(self) -> pd.DataFrame:
        """Return a defensively isolated pandas DataFrame from bounded result rows.

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
        self._closed = False

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
        if self._closed:
            return
        disconnect = getattr(self._backend, "disconnect", None)
        if not callable(disconnect):
            self._closed = True
            return
        try:
            disconnect()
        except Exception:
            if not suppress_errors:
                raise
        finally:
            self._closed = True

    def disconnect(self) -> None:
        """Disconnect the backend once; repeated calls are no-ops."""
        self._disconnect(suppress_errors=False)

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
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
        Only datasources with a persisted project file are included.
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
        Raises ``DatasourceMissingError`` when the name has no project file.
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


def connect(name: str) -> DatasourceConnection:
    """Open a context-manageable live ibis backend for a datasource.

    Args:
        name: The datasource name to connect to.

    Returns:
        A ``DatasourceConnection`` proxy that delegates backend methods and
        disconnects automatically when used as a context manager.

    Example:
        >>> import marivo.datasource as md
        >>> with md.connect("wh") as con:
        ...     con.raw_sql("SELECT 1")

    Constraints:
        Prefer ``with md.connect(...) as con`` so cleanup is automatic. For
        manual lifetime management, call ``connection.disconnect()`` when done.
        Env-sourced secrets used to open this backend are remembered on the
        connection object so that a subsequent round-trip validation can persist
        them via ``secrets.persist_backend_env_sourced``.
    """
    return _connect_internal(name)


def _connect_internal(
    name: str,
    *,
    project_root: Path | None = None,
    include_semantic_layers: bool = False,
) -> DatasourceConnection:
    datasource = (
        _store.load_one_layered(name, project_root=project_root)
        if include_semantic_layers
        else _store.load_one(name, project_root=project_root)
    )
    if datasource is None:
        available = (
            _store.list_names_layered(project_root)
            if include_semantic_layers
            else _store.list_names(project_root)
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
    built = _backends.build_backend_with_secrets(datasource)
    connection = DatasourceConnection(built.backend)
    _secrets.remember_env_sourced(built.backend, built.env_sourced_secrets)
    _secrets.remember_env_sourced(connection, built.env_sourced_secrets)
    return connection


def _datasource_name(value: str | Ref[DatasourceKind]) -> str:
    return _storage_name(value)


def _connection_repair(exc: Exception) -> AuthoringRepair:
    existing = getattr(exc, "repair", None)
    if isinstance(existing, AuthoringRepair):
        return existing
    return repair(
        kind="reconnect",
        canonical_id="test",
        action="Reconnect the datasource after fixing its connection settings.",
    )


def test(name: str | Ref[DatasourceKind]) -> DatasourceTestResult:
    """Round-trip the backend and persist validated env secrets.

    Args:
        name: The datasource name or ``Ref[DatasourceKind]`` to test.

    Returns:
        A ``DatasourceTestResult`` with ok status, latency, and typed repair.

    Example:
        >>> import marivo.datasource as md
        >>> md.test(ms.Ref.datasource("wh"))

    Constraints:
        On success, env-sourced secrets that resolved correctly are
        persisted to the user-global plaintext cache. The backend is
        always disconnected.
    """
    datasource_name = _datasource_name(name)
    start = time.perf_counter()
    backend: Any | None = None
    try:
        backend = connect(datasource_name)
        backend.raw_sql("SELECT 1")
        _secrets.persist_backend_env_sourced(backend)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
            ok=True,
            latency_ms=latency_ms,
            repair=None,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
            ok=False,
            latency_ms=latency_ms,
            repair=_connection_repair(exc),
        )
    finally:
        if backend is not None:
            disconnect = getattr(backend, "disconnect", None)
            if callable(disconnect):
                with suppress(Exception):
                    disconnect()


def test_no_persist(
    name: str | Ref[DatasourceKind],
    *,
    project_root: Path | None = None,
    include_semantic_layers: bool = False,
) -> DatasourceTestResult:
    """Round-trip the backend without persisting resolved secrets.

    Args:
        name: The datasource name or ``Ref[DatasourceKind]`` to test.

    Returns:
        A ``DatasourceTestResult`` with ok status, latency, and typed repair.

    Constraints:
        Intended for read-only diagnostics such as ``marivo doctor --connect``.
        Does not write ``~/.marivo/secrets.toml``. The backend is always
        disconnected.
    """
    datasource_name = _datasource_name(name)
    start = time.perf_counter()
    backend: Any | None = None
    try:
        backend = _connect_internal(
            datasource_name,
            project_root=project_root,
            include_semantic_layers=include_semantic_layers,
        )
        backend.raw_sql("SELECT 1")
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
            ok=True,
            latency_ms=latency_ms,
            repair=None,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
            ok=False,
            latency_ms=latency_ms,
            repair=_connection_repair(exc),
        )
    finally:
        if backend is not None:
            disconnect = getattr(backend, "disconnect", None)
            if callable(disconnect):
                with suppress(Exception):
                    disconnect()


def _require_raw_sql_reason(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be non-empty.")
    return reason.strip()


def _require_single_statement(sql: str) -> str:
    """Reject empty SQL and ``;``-separated multi-statement input.

    Read-only is enforced at the connection level (and via a read-only transaction
    for transaction-based backends), not by parsing the statement shape, so this
    check only guards statement count.
    """
    text = sql.strip()
    if not text:
        raise ValueError("sql must be non-empty.")
    stripped = text.rstrip(";")
    if ";" in stripped:
        raise ValueError("raw_sql accepts a single read-only statement.")
    return stripped


_RAW_SQL_METADATA_KEYWORDS = {"SHOW", "DESCRIBE", "DESC", "EXPLAIN"}


def _raw_sql_keyword(sql: str) -> str:
    match = re.match(r"([A-Za-z_]+)", sql.lstrip())
    return match.group(1).upper() if match else ""


def _is_metadata_diagnostic_sql(sql: str) -> bool:
    return _raw_sql_keyword(sql) in _RAW_SQL_METADATA_KEYWORDS


def _extract_raw_sql_frame(
    cursor: Any,
    include_types: bool,
    *,
    limit: int | None = None,
) -> tuple[tuple[str, ...], tuple[dict[str, object], ...], dict[str, str]]:
    """Extract columns, rows, and best-effort types from a backend cursor.

    Delegates to ``decode_cursor_frame`` which handles both the DB-API
    ``description``+``fetchall`` path (DuckDB/Postgres/Trino/MySQL) and the
    ``column_names``+``result_rows`` path (ClickHouse).
    """
    frame = decode_cursor_frame(cursor, include_types=include_types, max_rows=limit)
    return frame.columns, frame.rows, frame.types


def raw_sql(
    datasource: Ref[DatasourceKind],
    sql: str,
    *,
    reason: str,
    limit: int = 100,
    timeout_seconds: int = 30,
    include_types: bool = True,
    project_root: Path | None = None,
) -> RawSqlResult:
    """Run a bounded read-only SQL terminal diagnostic against a datasource.

    Args:
        datasource: Datasource reference returned by ``ms.Ref.datasource("warehouse")``.
        sql: Single read-only SQL statement. ``SELECT`` and ``WITH`` diagnostics
            are bounded with a wrapper query capped at ``limit + 1`` rows;
            metadata diagnostics such as ``SHOW``, ``DESCRIBE``, ``DESC``, and
            ``EXPLAIN`` execute directly so backend metadata syntax remains valid.
        reason: Required terminal-analysis reason; shown in the result. For a
            semantic-gap escape, name the gap, temporary analysis purpose, and
            inferred assumptions that make the statement provisional.
        limit: Maximum rows to return.
        timeout_seconds: Backend execution timeout; fail-closed if unenforceable.
        include_types: Whether to include returned column type labels when available.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        A bounded ``RawSqlResult`` labeled as ``terminal_only``.

    Example:
        >>> import marivo.datasource as md
        >>> md.raw_sql(ms.Ref.datasource("warehouse"), "SELECT 1 AS ok", reason="check query path")

    Constraints:
        Rejects empty reasons, empty SQL, multi-statement SQL, non-positive limit,
        and non-positive timeout before execution. Read-only is enforced at the
        connection level: DuckDB and ClickHouse open in read-only mode, Postgres
        and MySQL run inside a ``READ ONLY`` transaction via the engine profile
        ``authoring_timeout`` context, and Trino runs ordinary SELECT/WITH queries
        through a read-only subquery wrapper. The timeout remains armed from before
        the user statement executes through bounded result fetching; if the profile
        has no enforceable timeout the function fails closed with
        ``DatasourceRawSqlError(stage="timeout_setup")``.
        A semantic gap may use this terminal path without prior approval, but
        inferred semantics remain provisional and must be disclosed at closeout.
        The result cannot become a canonical metric or re-enter typed analysis.
        Returned rows are bounded, but the backend diagnostic itself can still be
        expensive; callers must inspect query plans and supply a narrow statement.
        Any execution failure (including a write attempt) surfaces as a
        ``DatasourceRawSqlError``; the backend is always disconnected. The result
        is terminal custom analysis — it carries no metric, time-scope, slice,
        lineage, or canonical analysis contract.
    """
    if limit < 1:
        raise ValueError("limit must be positive.")
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
        is_metadata_diagnostic = _is_metadata_diagnostic_sql(statement)
        fetch_limit = limit
        execution_sql = (
            statement
            if is_metadata_diagnostic
            else f"SELECT * FROM ({statement}) AS marivo_raw_sql LIMIT {limit + 1}"
        )
        start = time.monotonic()
        try:
            with timeout(backend, timeout_seconds):
                cursor = backend.raw_sql(execution_sql)
                columns, extracted_rows, types = _extract_raw_sql_frame(
                    cursor,
                    include_types,
                    limit=fetch_limit,
                )
        except DatasourceError:
            raise
        except Exception as exc:
            raise DatasourceRawSqlError(
                message="raw_sql execution or result fetching failed; no side effects were applied.",
                expected="a read-only diagnostic the datasource backend can execute",
                received=str(exc),
                location=f"md.raw_sql({datasource_id!r}) backend_type={backend_type!r}",
                effect_observed=DatasourceObservedEffects(query_executed=True),
                repair=repair(
                    kind="reconnect",
                    canonical_id="raw_sql",
                    action="Verify the datasource connection and retry the diagnostic.",
                ),
            ) from exc
        duration_ms = int((time.monotonic() - start) * 1000)
        rows = extracted_rows[:limit]
        is_truncated = len(extracted_rows) > limit
        return RawSqlResult(
            datasource=datasource,
            backend_type=backend_type,
            sql=statement,
            reason=reason_text,
            columns=columns,
            types=types,
            rows=rows,
            requested_limit=limit,
            returned_row_count=len(rows),
            is_truncated=is_truncated,
            timeout_seconds=timeout_seconds,
            duration_ms=duration_ms,
            warnings=(
                "raw SQL diagnostics can be expensive even when returned rows are bounded",
                "terminal custom analysis; no metric, time-scope, slice, lineage, or canonical analysis contract",
            ),
        )
