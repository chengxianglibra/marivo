"""Physical source descriptors and explicit authoring acquisition scopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, TypeAlias

from marivo._compat import UTC
from marivo.datasource.ir import (
    CsvSourceIR,
    JsonBodyParam,
    JsonBodyValue,
    JsonQueryParamValue,
    JsonSourceIR,
    ParquetSourceIR,
    SourceParamIR,
    TableSourceIR,
    normalize_json_body,
)
from marivo.render import Card, RenderableResult

TableSource: TypeAlias = TableSourceIR | ParquetSourceIR | CsvSourceIR | JsonSourceIR


@dataclass(frozen=True)
class _TimeRangePredicate:
    column: str
    start: date | datetime
    end: date | datetime


@dataclass(frozen=True)
class PartitionScope(RenderableResult):
    """Explicit pruned selection and positive acquisition guards."""

    values: tuple[tuple[str, str], ...]
    max_rows: int
    timeout_seconds: int
    _time_range: _TimeRangePredicate | None = field(default=None, init=False, repr=False)

    def __repr__(self) -> str:
        if self._time_range is None:
            return (
                f"PartitionScope(values={self.values!r}, max_rows={self.max_rows!r}, "
                f"timeout_seconds={self.timeout_seconds!r})"
            )
        predicate = self._time_range
        return (
            f"PartitionScope(time_range=({predicate.column!r}, {predicate.start!r}, "
            f"{predicate.end!r}), max_rows={self.max_rows!r}, "
            f"timeout_seconds={self.timeout_seconds!r})"
        )

    def _repr_identity(self) -> str:
        kind = "time_range" if self._time_range is not None else "partition"
        return f"PartitionScope kind={kind}"

    def _card(self) -> Card:
        if self._time_range is not None:
            predicate = self._time_range
            return (
                Card(
                    identity=self._repr_identity(),
                    available=(".show()",),
                )
                .status(
                    f"explicit scope max_rows={self.max_rows} "
                    f"timeout_seconds={self.timeout_seconds}"
                )
                .field(
                    "predicate",
                    f"{predicate.column} in [{predicate.start!r}, {predicate.end!r})",
                )
            )

        return (
            Card(
                identity=self._repr_identity(),
                available=(".values", ".show()"),
            )
            .status(
                f"explicit scope max_rows={self.max_rows} timeout_seconds={self.timeout_seconds}"
            )
            .table(
                columns=("field", "value"),
                rows=self.values,
                row_count=len(self.values),
                label="predicate",
                show_omission_counts=True,
                bounded_row_limit=4,
                recovery="Read scope.values for the complete predicate.",
            )
        )


@dataclass(frozen=True)
class UnprunedScope(RenderableResult):
    """Explicit unpruned acquisition with positive guards."""

    max_rows: int
    timeout_seconds: int

    def _repr_identity(self) -> str:
        return "UnprunedScope kind=unpruned"

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(),
            available=(".show()",),
        ).status(
            "broad read within explicit guards "
            f"max_rows={self.max_rows} timeout_seconds={self.timeout_seconds}"
        )


AuthoringScope: TypeAlias = PartitionScope | UnprunedScope


def _require_positive(value: int, *, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")


def partition(values: Mapping[str, str], *, max_rows: int, timeout_seconds: int) -> PartitionScope:
    """Build an explicitly partitioned authoring acquisition scope.

    Args:
        values: Non-empty partition field-to-value mapping.
        max_rows: Positive maximum number of rows to acquire.
        timeout_seconds: Positive acquisition timeout in seconds.

    Returns:
        A frozen ``PartitionScope`` preserving mapping insertion order.

    Example:
        ``md.partition({"log_date": "20260710"}, max_rows=1000, timeout_seconds=30)``

    Constraints:
        All guards are required and positive; at least one partition field is required.
    """
    normalized = tuple((str(key), str(value)) for key, value in values.items())
    if not normalized:
        raise ValueError("md.partition(...) requires at least one partition field.")
    _require_positive(max_rows, field="max_rows")
    _require_positive(timeout_seconds, field="timeout_seconds")
    return PartitionScope(normalized, max_rows, timeout_seconds)


def _normalize_time_bound(value: date | datetime | str, *, field_name: str) -> date | datetime:
    if type(value) is date or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date, datetime, or string.")
    if not value:
        raise ValueError(f"{field_name} must be non-empty.")
    if "T" not in value and " " not in value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date or datetime.") from exc


def time_range(
    column: str,
    /,
    *,
    start: date | datetime | str,
    end: date | datetime | str,
    max_rows: int,
    timeout_seconds: int,
) -> PartitionScope:
    """Build a bounded half-open temporal acquisition scope.

    Args:
        column: One inspected date or timestamp column.
        start: Inclusive ISO date or datetime boundary.
        end: Exclusive ISO date or datetime boundary.
        max_rows: Positive maximum number of rows to acquire.
        timeout_seconds: Positive acquisition timeout in seconds.

    Returns:
        A frozen ``PartitionScope`` carrying the half-open temporal predicate.

    Example:
        ``md.time_range("timestamp", start="2026-08-01", end="2026-08-02", max_rows=1000, timeout_seconds=30)``

    Constraints:
        Boundaries must have the same date/datetime kind, datetime awareness,
        and satisfy ``start < end``. Aware datetimes are normalized to UTC. The
        column must be temporal at inspection.
    """
    if not isinstance(column, str):
        raise TypeError("column must be str.")
    if not column:
        raise ValueError("column must be non-empty.")
    normalized_start = _normalize_time_bound(start, field_name="start")
    normalized_end = _normalize_time_bound(end, field_name="end")
    if type(normalized_start) is not type(normalized_end):
        raise ValueError("start and end must both be dates or both be datetimes.")
    if isinstance(normalized_start, datetime) and isinstance(normalized_end, datetime):
        start_aware = (
            normalized_start.tzinfo is not None and normalized_start.utcoffset() is not None
        )
        end_aware = normalized_end.tzinfo is not None and normalized_end.utcoffset() is not None
        if start_aware != end_aware:
            raise ValueError("start and end datetimes must use matching timezone awareness.")
        if start_aware:
            normalized_start = normalized_start.astimezone(UTC)
            normalized_end = normalized_end.astimezone(UTC)
    if normalized_start >= normalized_end:
        raise ValueError("start must be earlier than end.")
    _require_positive(max_rows, field="max_rows")
    _require_positive(timeout_seconds, field="timeout_seconds")
    scope = PartitionScope(
        values=(),
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
    )
    object.__setattr__(
        scope,
        "_time_range",
        _TimeRangePredicate(
            column=column,
            start=normalized_start,
            end=normalized_end,
        ),
    )
    return scope


def unpruned(*, max_rows: int, timeout_seconds: int) -> UnprunedScope:
    """Build an explicitly unpruned authoring acquisition scope.

    Args:
        max_rows: Positive maximum number of rows to acquire.
        timeout_seconds: Positive acquisition timeout in seconds.

    Returns:
        A frozen ``UnprunedScope``.

    Example:
        ``md.unpruned(max_rows=1000, timeout_seconds=30)``

    Constraints:
        Both guards are required positive integers.
    """
    _require_positive(max_rows, field="max_rows")
    _require_positive(timeout_seconds, field="timeout_seconds")
    return UnprunedScope(max_rows, timeout_seconds)


def _normalize_source_columns(
    columns: Mapping[str, str] | None,
    *,
    field: str,
) -> tuple[tuple[str, str], ...]:
    if columns is None:
        return ()
    if not isinstance(columns, Mapping):
        raise TypeError(f"{field} must be a mapping of output names to source fields.")
    if not columns:
        raise ValueError(f"{field} must contain at least one projected column when supplied.")
    if any(not isinstance(name, str) or not name for name in columns):
        raise TypeError(f"{field} output names must be non-empty strings.")
    if any(not isinstance(source, str) or not source for source in columns.values()):
        raise TypeError(f"{field} source fields must be non-empty strings.")
    return tuple(columns.items())


def _normalize_query_params(
    query_params: Mapping[str, JsonQueryParamValue] | None,
) -> tuple[tuple[str, JsonQueryParamValue], ...]:
    if query_params is None:
        return ()
    if not isinstance(query_params, Mapping):
        raise TypeError(
            "md.json(query_params=...) must be a mapping of query parameter names to "
            "scalar values, lists of scalar values, or md.source_param(...)."
        )
    return tuple(query_params.items())


def source_param(name: str, /) -> SourceParamIR:
    """Declare one required runtime request parameter for a physical source.

    Args:
        name: Stable parameter name supplied by an analysis source-binding scope.

    Returns:
        A validated ``SourceParamIR`` for one complete query-string or JSON-body value.

    Example:
        ``md.source_param("start")``

    Constraints:
        The parameter occupies one complete query-string or JSON-body value;
        substring templates and secret values are not supported.
    """
    return SourceParamIR(name=name)


def table(
    name: str,
    /,
    *,
    database: str | tuple[str, ...] | None = None,
    columns: Mapping[str, str] | None = None,
) -> TableSourceIR:
    """Build a physical table source descriptor.

    This descriptor identifies an internal table or view; it is not a datasource declaration.

    Args:
        name: Table or view name inside the datasource.
        database: Optional database/catalog name or namespace tuple.
        columns: Optional output-name to physical-column projection mapping
            (``Mapping[str, str]``). Values are names, not type declarations.
            Physical types come from source metadata when execution needs them;
            ``md.inspect(...)`` exposes current names and types.

    Returns:
        A validated ``TableSourceIR``.

    Example:
        ``md.table("orders", database="sales")`` or
        ``md.table("events", columns={"event_time": "event.timestamp"})``

    Constraints:
        The name and any database namespace parts must be non-empty. When
        ``columns`` is supplied, it must be non-empty and each physical column
        is a complete identifier, not SQL. One physical column may be projected
        under more than one output name.
        Each projection value is a physical name, not an object describing
        a column or its type.
    """
    normalized_columns = _normalize_source_columns(columns, field="md.table(columns=...)")
    return TableSourceIR(table=name, database=database, columns=normalized_columns)


def parquet(
    path: str,
    /,
    *,
    hive_partitioning: bool = False,
    columns: tuple[str, ...] | list[str] | None = None,
) -> ParquetSourceIR:
    """Build a DuckDB file source descriptor for Parquet files.

    This descriptor is not a datasource declaration.

    Args:
        path: File path or glob pattern.
        hive_partitioning: Whether the source uses Hive partitioning.
        columns: Optional physical projection.

    Returns:
        A validated ``ParquetSourceIR``.

    Example:
        ``md.parquet("data/orders/*.parquet", columns=("order_id",))``

    Constraints:
        The path and any projected column names must be non-empty.
    """
    normalized_columns = tuple(columns) if columns is not None else None
    return ParquetSourceIR(
        path=path, hive_partitioning=hive_partitioning, columns=normalized_columns
    )


def csv(
    path: str,
    /,
    *,
    columns: Mapping[str, str] | None = None,
    header: bool = True,
    delimiter: str = ",",
) -> CsvSourceIR:
    """Build a DuckDB file source descriptor for CSV files.

    This descriptor is not a datasource declaration.

    Args:
        path: File path or glob pattern.
        columns: Optional output-name to physical-header projection mapping.
        header: Whether the CSV file has a header row.
        delimiter: Column delimiter.

    Returns:
        A validated ``CsvSourceIR``.

    Example:
        ``md.csv("orders.csv", columns={"order_id": "Order ID"})``

    Constraints:
        Types are inferred from the source during reading. Projected source names
        must be non-empty strings and must exist in the file.
    """
    return CsvSourceIR(
        path=path,
        columns=_normalize_source_columns(columns, field="md.csv(columns=...)"),
        header=header,
        delimiter=delimiter,
    )


def json(
    path: str,
    /,
    *,
    columns: Mapping[str, str] | None = None,
    format: Literal["auto", "newline_delimited", "array"] = "auto",
    records_path: str | None = None,
    query_params: Mapping[str, JsonQueryParamValue] | None = None,
    method: Literal["GET", "POST"] = "GET",
    body: Mapping[str, JsonBodyValue] | None = None,
) -> JsonSourceIR:
    """Build a DuckDB JSON physical-source descriptor.

    This descriptor is not a datasource declaration.

    Args:
        path: File path, glob pattern, or supported URL.
        columns: Optional output-name to JSON field-path projection mapping.
            Paths support object members, fixed array indexes, and one shared
            array traversal such as ``"apps[].name"``.
        format: JSON layout.
        records_path: Optional object-member path to the array of records inside
            a wrapped response, for example ``"$.data"`` or ``"$.result.items"``.
        query_params: Optional query-string mapping. Values are fixed scalars or
            lists of scalars, or required runtime parameters from
            ``md.source_param(...)``. List values URL-encode as repeated keys.
        method: HTTP method. ``POST`` sends the JSON object in ``body``.
        body: JSON object for a ``POST`` request. Values may contain required
            runtime parameters from ``md.source_param(...)``, whose bound value
            may be a scalar or a flat scalar list.

    Returns:
        A validated ``JsonSourceIR``.

    Example:
        ``md.json("events.json", columns={"event_id": "event_id"})``

        ``md.json("events.json", columns={"event_id": "event.id"}, records_path="$.data")``

        ``md.json("https://api.example/graphql", columns={"id": "id"},
        method="POST", body={"query": "{ items { id } }"}, records_path="$.data.items")``

    Constraints:
        Types are inferred from the source during reading. Projected output names
        and paths must be non-empty strings. Multiple traversed fields must share
        one array path and are projected from the same array element; independent
        traversal roots and multiple traversals in one path are rejected.
        A declared records path must resolve to an array at execution; a missing
        path or non-array value fails instead of materializing zero rows.
        For wrapped records, requested fields are projected in mapping order;
        missing fields fail when read and additional object fields are ignored.
        Present field types are inferred from their values.
        A traversal path (``"a[].b"``) expands one row per element; a record
        whose traversal array is missing, empty, or null produces no rows, and
        sibling fields from that array remain correlated.
        A body is only supported for ``POST`` and must be a JSON object. Runtime
        parameters occupy complete JSON values — a scalar or a flat, non-empty
        scalar list — and substring templates are unsupported.
    """
    body_json: str | None = None
    body_params: tuple[JsonBodyParam, ...] = ()
    if body is not None:
        body_json, body_params = normalize_json_body(body)
    return JsonSourceIR(
        path=path,
        format=format,
        records_path=records_path,
        columns=_normalize_source_columns(columns, field="md.json(columns=...)"),
        query_params=_normalize_query_params(query_params),
        method=method,
        body_json=body_json,
        body_params=body_params,
    )
