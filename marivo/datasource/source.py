"""Physical source descriptors and explicit authoring acquisition scopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from marivo._authoring.model import AuthoringContract
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR

type TableSource = TableSourceIR | ParquetSourceIR | CsvSourceIR | JsonSourceIR


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


type AuthoringScope = PartitionScope | UnprunedScope


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


def table(name: str, /, *, database: str | tuple[str, ...] | None = None) -> TableSourceIR:
    """Build a physical table source descriptor.

    This descriptor identifies an internal table or view; it is not a datasource declaration.

    Args:
        name: Table or view name inside the datasource.
        database: Optional database/catalog name or namespace tuple.

    Returns:
        A validated ``TableSourceIR``.

    Example:
        ``md.table("orders", database="sales")``

    Constraints:
        The name and any database namespace parts must be non-empty.
    """
    return TableSourceIR(table=name, database=database)


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
) -> JsonSourceIR:
    """Build a typed DuckDB file source descriptor for JSON files.

    This descriptor is not a datasource declaration.

    Args:
        path: File path, glob pattern, or supported URL.
        schema: Non-empty insertion-ordered column-to-type mapping.
        format: JSON layout.

    Returns:
        A validated ``JsonSourceIR``.

    Example:
        ``md.json("events.json", schema={"event_id": "string"})``

    Constraints:
        Schema column names and type names must be non-empty strings.
    """
    return JsonSourceIR(
        path=path,
        schema=_normalize_schema(schema, field="md.json(schema=...)"),
        format=format,
    )
