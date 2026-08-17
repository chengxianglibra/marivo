"""Physical source descriptors and explicit authoring acquisition scopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo._authoring.model import AuthoringContract
from marivo.datasource.ir import (
    CsvSourceIR,
    JsonBodyParam,
    JsonBodyValue,
    JsonQueryParamValue,
    JsonSourceIR,
    ParquetSourceIR,
    SourceParamIR,
    TableColumnBindingIR,
    TableSourceIR,
    normalize_json_body,
)

TableSource: TypeAlias = TableSourceIR | ParquetSourceIR | CsvSourceIR | JsonSourceIR


@dataclass(frozen=True)
class PartitionScope:
    """Explicit partition selection and positive acquisition guards."""

    values: tuple[tuple[str, str], ...]
    max_rows: int
    timeout_seconds: int

    def contract(self) -> AuthoringContract:
        """Return the blocked acquisition contract for this explicit scope."""
        from marivo.datasource._capabilities.contracts import contract_for_scope

        return contract_for_scope("partition")


@dataclass(frozen=True)
class UnprunedScope:
    """Explicit unpruned acquisition with positive guards."""

    max_rows: int
    timeout_seconds: int

    def contract(self) -> AuthoringContract:
        """Return the blocked acquisition contract for this explicit scope."""
        from marivo.datasource._capabilities.contracts import contract_for_scope

        return contract_for_scope("unpruned")


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


def _normalize_schema(schema: Mapping[str, str], *, field: str) -> tuple[tuple[str, str], ...]:
    normalized = tuple(schema.items())
    if not normalized:
        raise ValueError(f"{field} must contain at least one typed column.")
    if any(not isinstance(name, str) or not name for name, _type in normalized):
        raise TypeError(f"{field} column names must be non-empty strings.")
    if any(not isinstance(_type, str) or not _type for _name, _type in normalized):
        raise TypeError(f"{field} type names must be non-empty strings.")
    return normalized


def _normalize_query_params(
    query_params: Mapping[str, JsonQueryParamValue] | None,
) -> tuple[tuple[str, JsonQueryParamValue], ...]:
    if query_params is None:
        return ()
    if not isinstance(query_params, Mapping):
        raise TypeError(
            "md.json(query_params=...) must be a mapping of query parameter names to "
            "scalar values or md.source_param(...)."
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


def source_column(name: str, /, *, data_type: str) -> TableColumnBindingIR:
    """Declare one typed physical identifier for a table column binding.

    Args:
        name: One complete physical identifier, quoted atomically at runtime.
        data_type: Non-empty Ibis type string asserted for the projected output.

    Returns:
        A frozen ``TableColumnBindingIR`` for use in ``md.table(columns=...)``.

    Example:
        ``md.source_column("event.timestamp", data_type="timestamp(3)")``

    Constraints:
        The name is an identifier, not an SQL expression. The declared type
        supplies the output schema and does not cast the physical value.
    """
    return TableColumnBindingIR(source=name, data_type=data_type)


def table(
    name: str,
    /,
    *,
    database: str | tuple[str, ...] | None = None,
    columns: Mapping[str, TableColumnBindingIR] | None = None,
) -> TableSourceIR:
    """Build a physical table source descriptor.

    This descriptor identifies an internal table or view; it is not a datasource declaration.

    Args:
        name: Table or view name inside the datasource.
        database: Optional database/catalog name or namespace tuple.
        columns: Optional complete output-name to typed physical-column mapping.

    Returns:
        A validated ``TableSourceIR``.

    Example:
        ``md.table("orders", database="sales")`` or
        ``md.table("events", columns={"event_time": md.source_column("event.timestamp", data_type="timestamp")})``

    Constraints:
        The name and any database namespace parts must be non-empty. When
        ``columns`` is supplied, it must be non-empty and every output must use
        one unique ``TableColumnBindingIR`` physical source.
    """
    if columns is None:
        normalized_columns: tuple[tuple[str, TableColumnBindingIR], ...] = ()
    else:
        if not isinstance(columns, Mapping):
            raise TypeError(
                "md.table(columns=...) must be a mapping of output names to "
                f"TableColumnBindingIR values, got {type(columns).__name__}."
            )
        if not columns:
            raise ValueError("md.table(columns=...) must contain at least one binding.")
        normalized_columns = tuple(columns.items())
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
    schema: Mapping[str, str],
    header: bool = True,
    delimiter: str = ",",
) -> CsvSourceIR:
    """Build a typed DuckDB file source descriptor for CSV files.

    This descriptor is not a datasource declaration.

    Args:
        path: File path or glob pattern.
        schema: Non-empty insertion-ordered column-to-type mapping.
        header: Whether the CSV file has a header row.
        delimiter: Column delimiter.

    Returns:
        A validated ``CsvSourceIR``.

    Example:
        ``md.csv("orders.csv", schema={"order_id": "string"})``

    Constraints:
        Schema column names and type names must be non-empty strings.
    """
    return CsvSourceIR(
        path=path,
        schema=_normalize_schema(schema, field="md.csv(schema=...)"),
        header=header,
        delimiter=delimiter,
    )


def json(
    path: str,
    /,
    *,
    schema: Mapping[str, str],
    format: Literal["auto", "newline_delimited", "array"] = "auto",
    records_path: str | None = None,
    query_params: Mapping[str, JsonQueryParamValue] | None = None,
    method: Literal["GET", "POST"] = "GET",
    body: Mapping[str, JsonBodyValue] | None = None,
) -> JsonSourceIR:
    """Build a typed DuckDB JSON physical-source descriptor.

    This descriptor is not a datasource declaration.

    Args:
        path: File path, glob pattern, or supported URL.
        schema: Non-empty insertion-ordered column-to-type mapping.
        format: JSON layout.
        records_path: Optional object-member path to the array of records inside
            a wrapped response, for example ``"$.data"`` or ``"$.result.items"``.
        query_params: Optional query-string mapping. Values are fixed scalars or
            required runtime parameters from ``md.source_param(...)``.
        method: HTTP method. ``POST`` sends the JSON object in ``body``.
        body: JSON object for a ``POST`` request. Values may contain required
            runtime parameters from ``md.source_param(...)``.

    Returns:
        A validated ``JsonSourceIR``.

    Example:
        ``md.json("events.json", schema={"event_id": "string"})``

        ``md.json("events.json", schema={"event_id": "string"}, records_path="$.data")``

        ``md.json("https://api.example/graphql", schema={"id": "string"},
        method="POST", body={"query": "{ items { id } }"}, records_path="$.data.items")``

    Constraints:
        Schema column names and type names must be non-empty strings.
        A declared records path must resolve to an array at execution; a missing
        path or non-array value fails instead of materializing zero rows.
        For wrapped records, declared fields are projected in schema order;
        missing fields become typed nulls and additional object fields are
        ignored. Present fields must be convertible to their declared types.
        A body is only supported for ``POST`` and must be a JSON object. Runtime
        parameters occupy complete JSON values; substring templates are unsupported.
    """
    body_json: str | None = None
    body_params: tuple[JsonBodyParam, ...] = ()
    if body is not None:
        body_json, body_params = normalize_json_body(body)
    return JsonSourceIR(
        path=path,
        schema=_normalize_schema(schema, field="md.json(schema=...)"),
        format=format,
        records_path=records_path,
        query_params=_normalize_query_params(query_params),
        method=method,
        body_json=body_json,
        body_params=body_params,
    )
