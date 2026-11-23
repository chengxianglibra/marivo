"""Unified datasource management API (md.*)."""

from __future__ import annotations

import builtins
import copy
import time
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pandas.api.types import is_object_dtype
from typing_extensions import override

from marivo._authoring.model import AuthoringRepair
from marivo._kernel import execution_log as _execution_log
from marivo._kernel.data_render import _DataCard, _DataResult
from marivo._kernel.refs import DatasourceKind, Ref
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.symbolic import (
    DatasourceIdentityHandle,
    DatasourceSymbol,
    native_symbol_ref,
    symbol_factory,
)
from marivo._kernel.validation import integer, nonblank_text
from marivo.datasource import credentials as cr
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.engines.base import decode_cursor_frame, structured_exception_chain
from marivo.datasource.errors import (
    DatasourceError,
    DatasourceFieldInvalidError,
    DatasourceMissingError,
    DatasourceObservedEffects,
    DatasourceRawSqlError,
    _backend_failure_summary,
    _execution_error_fields,
    _RawSqlFailureCode,
    repair,
)
from marivo.datasource.runtime.backends import _NativeQueryTimeoutError
from marivo.datasource.runtime.connections import (
    DatasourceConnectionService,
)
from marivo.datasource.store import declarations as _store
from marivo.datasource.store.authoring import (
    _loaded_datasource_ref,
)
from marivo.telemetry import traced


@dataclass(frozen=True, repr=False)
class RawSqlResult(_DataResult):
    """Complete terminal query result from the datasource raw-SQL execution path."""

    _datasource: Ref[DatasourceKind]
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

    @property
    def datasource(self) -> DatasourceSymbol:
        """Native lookup selector for the datasource used by this result."""
        return symbol_factory.datasource(self._datasource.path)

    def __post_init__(self) -> None:
        if self.returned_row_count != len(self.rows):
            raise ValueError(
                "returned_row_count must equal the number of returned query rows: "
                f"returned_row_count={self.returned_row_count}, rows={len(self.rows)}"
            )

    @property
    def shape(self) -> tuple[int, int]:
        """Return complete query rows by declared columns."""
        return (self.returned_row_count, len(self.columns))

    @override
    def _repr_identity(self) -> str:
        return (
            f"RawSqlResult datasource={self._datasource.path} "
            f"rows={self.returned_row_count} terminal_only"
        )

    @override
    def _data_card(self) -> _DataCard:
        return _DataCard(
            identity=f"RawSqlResult datasource={self._datasource.path} terminal_only",
            columns=self.columns,
            rows=lambda: (tuple(row.get(column) for column in self.columns) for row in self.rows),
            row_count=self.returned_row_count,
            row_scope="complete returned query rows; not full-source cardinality",
            facts=(("reason", self.reason),),
            boundaries=(
                ("business_coverage", "unknown; raw SQL does not establish semantic coverage"),
                ("boundary", "terminal only; no semantic identity or typed analysis reentry"),
                *(("warning", warning) for warning in self.warnings),
            ),
        )

    @traced(surface="datasource", operation="raw_sql_result.to_pandas", kind="method")
    def to_pandas(self) -> pd.DataFrame:
        """Return an isolated pandas DataFrame containing all retained query rows.

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


def _invalid_text(value: object, blank: str) -> str:
    return blank if isinstance(value, str) else type(value).__name__


def _require_raw_sql_reason(reason: str) -> str:
    if not nonblank_text(reason):
        raise _invalid_raw_sql(
            "a non-empty reason",
            _invalid_text(reason, "an empty reason"),
            "Pass a non-empty reason for the terminal SQL query.",
        )
    return reason.strip()


def _require_nonempty_sql(sql: str) -> str:
    """Validate presence without parsing or rewriting the caller's SQL."""
    if not nonblank_text(sql):
        raise _invalid_raw_sql(
            "a non-empty SQL statement",
            _invalid_text(sql, "an empty SQL statement"),
            "Pass the non-empty SQL statement to execute.",
        )
    return sql


def _invalid_raw_sql(expected: str, received: str, action: str) -> DatasourceFieldInvalidError:
    return DatasourceFieldInvalidError(
        message="Invalid raw SQL input.",
        expected=expected,
        received=received,
        location="md.raw_sql",
        effect_observed=DatasourceObservedEffects(query_executed=False),
        repair=repair(kind="retry", canonical_id="raw_sql", action=action),
    )


def _extract_raw_sql_frame(
    cursor: object,
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


def _raw_sql_diagnostic(
    exc: Exception, backend_type: str, *, query_started: bool
) -> tuple[_RawSqlFailureCode, AuthoringRepair]:
    if not query_started:
        if isinstance(exc, DatasourceError) and exc.repair is not None:
            return "timeout_configuration", exc.repair
        return "timeout_configuration", repair(
            kind="configure",
            canonical_id="raw_sql",
            action="Check the reported native timeout setting and whether the connection permits it.",
        )
    name: str | None = None
    connection_unavailable = isinstance(exc, ConnectionError)
    if backend_type == "trino":
        from requests.exceptions import ConnectionError as HttpConnectionError  # noqa: PLC0415  # Optional Trino transport.
        from requests.exceptions import Timeout as HttpTimeout  # noqa: PLC0415  # Optional Trino transport.
        from trino.exceptions import TrinoConnectionError, TrinoQueryError  # noqa: PLC0415  # Optional Trino transport.

        if isinstance(exc, TrinoQueryError):
            name = exc.error_name
        elif isinstance(exc, TrinoConnectionError):
            # Trino wraps every HTTP RequestException in this class, including
            # timeouts and invalid request configuration. Inspect the cause.
            chain = structured_exception_chain(exc)
            if any(isinstance(cause, HttpTimeout) for cause in chain):
                return "timeout", repair(
                    kind="inspect",
                    canonical_id="raw_sql",
                    action=(
                        "Check the coordinator response and HTTP request timeout. "
                        "Confirm any source changes before resubmitting the SQL."
                    ),
                )
            connection_unavailable = any(
                isinstance(cause, (ConnectionError, HttpConnectionError)) for cause in chain
            )
    if name == "MISSING_SCHEMA_NAME":
        return "invalid_sql", repair(
            kind="retry",
            canonical_id="raw_sql",
            action=(
                "Qualify the table as catalog.schema.table and quote each identifier separately "
                'when needed, for example "catalog"."schema"."table$partitions". '
                "To use unqualified names, configure the intended default schema in the Trino datasource."
            ),
        )
    if name in ("COLUMN_NOT_FOUND", "TABLE_NOT_FOUND", "SCHEMA_NOT_FOUND", "CATALOG_NOT_FOUND"):
        return "invalid_sql", repair(
            kind="inspect",
            canonical_id="raw_sql",
            action=(
                "Verify the catalog, schema, table and column names. "
                "Run SHOW COLUMNS FROM catalog.schema.table for the intended table, then correct the SQL."
            ),
        )
    if name == "SYNTAX_ERROR":
        return "invalid_sql", repair(
            kind="retry",
            canonical_id="raw_sql",
            action="Correct the SQL at the position reported by Trino.",
        )
    if name == "PERMISSION_DENIED":
        return "permission_denied", repair(
            kind="inspect",
            canonical_id="raw_sql",
            action="Check that the datasource identity can access the objects in the statement.",
        )
    if name == "EXCEEDED_TIME_LIMIT" or isinstance(exc, TimeoutError):
        return "timeout", repair(
            kind="inspect",
            canonical_id="raw_sql",
            action="Check the native timeout and query cost. Narrow the query or adjust the applicable timeout.",
        )
    if connection_unavailable:
        return "connection_unavailable", repair(
            kind="reconnect",
            canonical_id="datasource_catalog.test",
            action="Test connectivity to the datasource. Confirm any source changes before retrying the SQL.",
        )
    return "unknown", repair(
        kind="inspect",
        canonical_id="raw_sql",
        action="Inspect the native error and confirm any source changes before retrying.",
    )


def _raw_sql_failure(
    exc: Exception,
    datasource_id: str,
    backend_type: str,
    *,
    query_started: bool,
    values: tuple[cr.SecretValue, ...] = (),
) -> DatasourceRawSqlError:
    code, next_step = _raw_sql_diagnostic(exc, backend_type, query_started=query_started)
    failure = _backend_failure_summary(exc, redact=lambda text: cr.redact(text, values))
    expected = (
        "SQL the datasource backend can execute under its connection permissions"
        if query_started
        else "native timeout settings supported by this connection"
    )
    received = f"{failure.identity}: {failure.message}"
    if isinstance(exc, DatasourceError):
        if exc.expected is not None:
            expected = cr.redact(exc.expected, values)
        if exc.received is not None:
            received += f" ({cr.redact(exc.received, values)})"
    return DatasourceRawSqlError(
        code=code,
        message="raw_sql execution or result fetching failed."
        if query_started
        else "raw_sql native timeout configuration failed before execution.",
        expected=expected,
        received=received,
        location=f"md.raw_sql({datasource_id!r}) backend_type={backend_type!r}",
        effect_observed=DatasourceObservedEffects(query_executed=query_started),
        repair=next_step,
        _backend_failure=failure,
    )


@traced(surface="datasource", operation="raw_sql", stage="connect")
def raw_sql(
    datasource: DatasourceSymbol | DatasourceIdentityHandle,
    sql: str,
    *,
    reason: str,
    timeout_seconds: int = 600,
    include_types: bool = True,
    project_root: Path | None = None,
) -> RawSqlResult:
    """Run terminal SQL exploration and load the complete query result.

    Args:
        datasource: Loaded Datasource object or a selector returned by ``ms.ref.datasource("warehouse")``.
        sql: Single read-only SQL statement in the datasource's native dialect.
            The statement executes verbatim, without parsing, rewriting, or an injected row limit.
        reason: Required exploration reason shown in the result. Name the
            physical or semantic question and disclose inferred assumptions.
        timeout_seconds: Requested native query timeout, defaulting to 600 seconds;
            skipped when unsupported.
            Connection acquisition requests separate native 30-second settings.
            This value does not bound the complete call or fetching.
        include_types: Whether to include returned column type labels when available.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        A complete ``RawSqlResult`` labeled as ``terminal_only``. The result
        describes the submitted query, not necessarily the full source population.
        Call ``result.to_pandas()`` to convert all returned rows to a pandas
        DataFrame without re-executing SQL.

    Example:
        >>> import marivo.datasource as md
        >>> import marivo.semantic as ms
        >>> result = md.raw_sql(ms.ref.datasource("default"), "SELECT 1 AS ok", reason="check query path")
        >>> df = result.to_pandas()

    Constraints:
        Rejects empty reasons, empty SQL, and non-positive timeout before execution. SQL syntax and permissions are enforced by the selected backend. All returned rows are loaded into client memory;
        there is no client-side row or byte cap and no truncation probe. Callers
        must control query size using filters, partition predicates, aggregation,
        and SQL ``LIMIT``. A returned-row limit does not bound backend scan cost.
        Use query plans and a narrow statement to control expensive diagnostics.

        Use read-only SQL and credentials. The backend applies its native connection
        settings and permissions to the submitted SQL. Write-capable credentials can
        allow changes even when the call reports an error.

        Query timeout coverage follows the backend's native semantics; it does
        not guarantee a total execution-and-fetching deadline. Unsupported native
        query timeouts do not block execution. Query timeout setup, execution or fetching failures raise a
        ``DatasourceRawSqlError`` without returning a partial result, and the
        backend is always disconnected. Errors do not certify absence of side effects.

        In Trino, qualify the table with catalog and schema if no default schema
        is configured. Quote identifier components separately and use
        ``SHOW COLUMNS FROM catalog.schema.table`` to check column names, including
        for metadata tables such as ``"catalog"."schema"."table$partitions"``.

        This is a normal source-exploration option when inspection or a generic
        sample cannot answer the current question. Inferred semantics remain
        provisional and must be disclosed at closeout. The result carries no
        metric, time-scope, slice, lineage, or canonical analysis contract and
        cannot re-enter typed analysis. Display previews may omit rows; ``rows``
        contains the complete query result as Python records.
    """
    if not integer(timeout_seconds) or timeout_seconds < 1:
        raise _invalid_raw_sql(
            "a positive integer timeout_seconds",
            repr(timeout_seconds),
            "Pass a positive integer timeout_seconds.",
        )
    reason_text = _require_raw_sql_reason(reason)
    statement = _require_nonempty_sql(sql)
    reference = _loaded_datasource_ref(datasource, project_root=cr.operation_root(project_root))
    if reference is None:
        symbolic = native_symbol_ref(datasource)
        reference = (
            ref_factory.datasource(symbolic.path)
            if symbolic is not None and symbolic.kind.value == "datasource"
            else None
        )
    if reference is None:
        raise _invalid_raw_sql(
            "a native datasource lookup symbol",
            type(datasource).__name__,
            "Pass an exact datasource symbol from ms.ref.datasource(...) or a loaded datasource.",
        )
    datasource_id = reference.path
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
                action="Choose an existing datasource or register its actual backend and connection in this project before retrying.",
                candidates=tuple(_store.list_names(project_root)),
            ),
        )
    return _run_raw_sql(
        datasource_id,
        datasource_ir.backend_type,
        statement,
        reason_text,
        timeout_seconds=timeout_seconds,
        include_types=include_types,
        project_root=project_root,
    )


def _run_raw_sql(
    datasource_id: str,
    backend_type: str,
    statement: str,
    reason_text: str,
    *,
    timeout_seconds: int,
    include_types: bool,
    project_root: Path | None,
) -> RawSqlResult:
    profile = require_profile_for_backend_type(backend_type)
    timeout = profile.native_query_timeout
    service = DatasourceConnectionService(project_root)
    with ExitStack() as stack:
        try:
            backend = stack.enter_context(
                service.use_backend(datasource_id, terminal_timeout_seconds=timeout_seconds)
            )
        except _NativeQueryTimeoutError as exc:
            raise _raw_sql_failure(exc, datasource_id, backend_type, query_started=False) from None
        start = time.monotonic()
        query_started = False
        try:
            with timeout(backend, timeout_seconds) if timeout is not None else nullcontext():
                query_started = True
                with (
                    _execution_log.QueryLog(
                        statement,
                        backend=backend_type,
                        purpose="datasource.raw_sql",
                        project_root=project_root,
                        datasource=datasource_id,
                        sensitive=bool(cr.injected_values(backend)),
                        format_error=_execution_error_fields,
                    ) as query,
                ):
                    cursor = backend.raw_sql(statement)
                    try:
                        columns, extracted_rows, types = _extract_raw_sql_frame(
                            cursor,
                            include_types,
                        )
                        query.rows = len(extracted_rows)
                    finally:
                        close = getattr(cursor, "close", None)
                        if backend_type != "duckdb" and callable(close):
                            close()
        except Exception as exc:
            if query_started and isinstance(exc, DatasourceError):
                raise
            raise _raw_sql_failure(
                exc,
                datasource_id,
                backend_type,
                query_started=query_started,
                values=cr.injected_values(backend),
            ) from None
        duration_ms = int((time.monotonic() - start) * 1000)
        rows = extracted_rows
        warnings = [
            "raw SQL diagnostics can be expensive; all returned rows load into client memory",
            "terminal custom analysis; no metric, time-scope, slice, lineage, or canonical analysis contract",
            "read-only behavior depends on connection and backend permissions; writes may be possible",
        ]
        return RawSqlResult(
            _datasource=ref_factory.datasource(datasource_id),
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
