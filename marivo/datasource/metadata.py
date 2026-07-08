"""Datasource table metadata inspection DTOs and backend adapters."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from marivo.datasource import backends as _backends
from marivo.datasource import store as _store
from marivo.datasource.errors import DatasourceMetadataError
from marivo.datasource.ir import (
    CsvSourceIR,
    EntitySourceIR,
    ParquetSourceIR,
    TableSourceIR,
    source_name,
)
from marivo.render import Card, RenderableResult

MetadataWarningKind = Literal[
    "comments_unavailable",
    "table_comments_unavailable",
    "column_comments_unavailable",
    "nullable_unavailable",
    "partitions_unavailable",
    "primary_keys_unavailable",
    "metadata_query_failed",
    "schema_only_fallback",
]


@dataclass(frozen=True)
class MetadataWarning:
    kind: MetadataWarningKind
    message: str
    columns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "message": self.message,
            "columns": list(self.columns),
        }


@dataclass(frozen=True)
class ColumnMetadata:
    name: str
    type: str
    nullable: bool | None
    comment: str | None
    ordinal_position: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "comment": self.comment,
            "ordinal_position": self.ordinal_position,
        }


@dataclass(frozen=True)
class PartitionMetadata:
    name: str
    type: str | None = None
    transform: str | None = None
    comment: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type,
            "transform": self.transform,
            "comment": self.comment,
        }


@dataclass(frozen=True)
class UniqueConstraintMetadata:
    """A declared primary-key or unique constraint on a table.

    Attributes:
        name: Constraint name if the backend exposes one, else ``None``.
        columns: Column names participating in the constraint, in declared order.
        kind: ``"primary"`` for primary-key constraints, ``"unique"`` for unique
            constraints or unique indexes.
    """

    name: str | None
    columns: tuple[str, ...]
    kind: Literal["primary", "unique"]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "columns": list(self.columns),
            "kind": self.kind,
        }


@dataclass(frozen=True, repr=False)
class TableMetadata(RenderableResult):
    datasource: str
    table: str
    database: str | tuple[str, ...] | None
    backend_type: str
    comment: str | None
    columns: tuple[ColumnMetadata, ...]
    partitions: tuple[PartitionMetadata, ...]
    warnings: tuple[MetadataWarning, ...]
    is_view: bool = False
    view_definition: str | None = None
    primary_keys: tuple[str, ...] = ()
    unique_constraints: tuple[UniqueConstraintMetadata, ...] = ()
    row_count: int | None = None

    @property
    def partition(self) -> PartitionMetadata | None:
        """First partition, or None if no partitions.

        For all partitions use ``.partitions``.
        """
        return self.partitions[0] if self.partitions else None

    @property
    def ref(self) -> str:
        if self.database is None:
            return f"{self.datasource}.{self.table}"
        database = ".".join(self.database) if isinstance(self.database, tuple) else self.database
        return f"{self.datasource}.{database}.{self.table}"

    def _repr_identity(self) -> str:
        return (
            f"TableMetadata ref={self.ref} backend={self.backend_type} columns={len(self.columns)}"
        )

    def _card(self) -> Card:
        def column_rows() -> Iterable[Sequence[str]]:
            for c in self.columns:
                yield [
                    c.name,
                    c.type,
                    "Y" if c.nullable else ("N" if c.nullable is False else "?"),
                    c.comment or "",
                ]

        parts: list[str] = []
        if self.is_view:
            parts.append("view=yes")
        if self.warnings:
            parts.append(f"warnings={len(self.warnings)}")
        if self.partitions:
            parts.append(f"partitions={len(self.partitions)}")
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()"))
        if parts:
            card.status(" ".join(parts))
        if self.comment:
            card.field(label="comment", value=self.comment)
        return card.lazy_table(
            columns=["column", "type", "nullable", "comment"],
            rows_provider=column_rows,
            row_count=len(self.columns),
        )

    def to_dict(self) -> dict[str, object]:
        database: str | list[str] | None = (
            list(self.database) if isinstance(self.database, tuple) else self.database
        )
        return {
            "datasource": self.datasource,
            "table": self.table,
            "database": database,
            "backend_type": self.backend_type,
            "comment": self.comment,
            "columns": [column.to_dict() for column in self.columns],
            "partitions": [partition.to_dict() for partition in self.partitions],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "is_view": self.is_view,
            "view_definition": self.view_definition,
            "primary_keys": list(self.primary_keys),
            "unique_constraints": [uc.to_dict() for uc in self.unique_constraints],
            "row_count": self.row_count,
            "ref": self.ref,
        }


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _database_label(database: str | tuple[str, ...] | None) -> str | None:
    if database is None:
        return None
    return ".".join(database) if isinstance(database, tuple) else database


def _table_ref(table: str, database: str | tuple[str, ...] | None) -> str:
    if database is None:
        return _quote_identifier(table)
    parts = database if isinstance(database, tuple) else (database,)
    return ".".join(_quote_identifier(part) for part in (*parts, table))


def _cursor_rows(cursor: Any) -> list[dict[str, object]]:
    from marivo.datasource.engines.base import decode_cursor_frame

    return list(decode_cursor_frame(cursor, include_types=False, max_rows=None).rows)


def _query_rows(backend: Any, sql: str) -> list[dict[str, object]]:
    raw_sql = getattr(backend, "raw_sql", None)
    if not callable(raw_sql):
        return []
    cursor = raw_sql(sql)
    return _cursor_rows(cursor)


def _schema_columns(table_expr: Any) -> tuple[ColumnMetadata, ...]:
    schema = table_expr.schema()
    return tuple(
        ColumnMetadata(
            name=str(name),
            type=str(dtype),
            nullable=None,
            comment=None,
            ordinal_position=index,
        )
        for index, (name, dtype) in enumerate(schema.items(), start=1)
    )


def _merge_columns(
    schema_columns: Sequence[ColumnMetadata],
    catalog_columns: Mapping[str, ColumnMetadata],
) -> tuple[ColumnMetadata, ...]:
    out: list[ColumnMetadata] = []
    for column in schema_columns:
        catalog = catalog_columns.get(column.name)
        if catalog is None:
            out.append(column)
            continue
        out.append(
            ColumnMetadata(
                name=column.name,
                type=catalog.type or column.type,
                nullable=catalog.nullable,
                comment=catalog.comment,
                ordinal_position=catalog.ordinal_position or column.ordinal_position,
            )
        )
    return tuple(out)


def _empty_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _is_missing_metadata_column(exc: Exception, column: str) -> bool:
    message = str(exc).lower()
    lowered = column.lower()
    return (
        "column_not_found" in message
        or "column not found" in message
        or "cannot be resolved" in message
        or "missing columns" in message
    ) and lowered in message


def _bool_from_nullable(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"YES", "Y", "TRUE", "1"}:
        return True
    if text in {"NO", "N", "FALSE", "0"}:
        return False
    return None


_SIMPLE_PARTITION_COLUMN_RE = re.compile(r'^[`"]?([A-Za-z_][A-Za-z0-9_]*)[`"]?$')


def _split_top_level_expressions(text: str) -> tuple[str, ...]:
    expressions: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {'"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            expressions.append(text[start:index].strip())
            start = index + 1
        index += 1
    tail = text[start:].strip()
    if tail:
        expressions.append(tail)
    return tuple(expressions)


def _simple_partition_column(expression: object) -> str | None:
    text = str(expression or "").strip()
    if not text:
        return None
    match = _SIMPLE_PARTITION_COLUMN_RE.match(text)
    if match:
        return match.group(1)
    return None


def _partition_columns_from_expression(expression: object) -> tuple[str, ...]:
    text = str(expression or "").strip()
    if not text:
        return ()
    simple = _simple_partition_column(text)
    if simple is not None:
        return (simple,)
    for prefix in ("range", "list", "hash"):
        wrapped = re.match(rf"^{prefix}\s*\((.*)\)$", text, re.IGNORECASE | re.DOTALL)
        if not wrapped:
            continue
        columns: list[str] = []
        for element in _split_top_level_expressions(wrapped.group(1)):
            column = _simple_partition_column(element)
            if column is None:
                return ()
            columns.append(column)
        return tuple(columns)
    return ()


def _partition_column_from_expression(expression: object) -> str | None:
    columns = _partition_columns_from_expression(expression)
    return columns[0] if len(columns) == 1 else None


def _schema_only(
    *,
    datasource: str,
    table: str,
    database: str | tuple[str, ...] | None,
    backend_type: str,
    table_expr: Any,
    warnings: Iterable[MetadataWarning],
) -> TableMetadata:
    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type=backend_type,
        comment=None,
        columns=_schema_columns(table_expr),
        partitions=(),
        warnings=(
            *warnings,
            MetadataWarning(
                kind="schema_only_fallback",
                message="metadata inspection returned schema-only metadata",
            ),
        ),
    )


def _with_primary_key_capability_warning(metadata: TableMetadata) -> TableMetadata:
    """Append ``primary_keys_unavailable`` for backends that do not expose PK metadata.

    DuckDB exposes primary keys via ``duckdb_constraints()`` and is left alone.
    Other backends get a single capability warning so the absence is never silent.
    """
    if metadata.backend_type == "duckdb":
        return metadata
    if metadata.primary_keys:
        return metadata
    if any(warning.kind == "primary_keys_unavailable" for warning in metadata.warnings):
        return metadata
    return replace(
        metadata,
        warnings=(
            *metadata.warnings,
            MetadataWarning(
                kind="primary_keys_unavailable",
                message=f"{metadata.backend_type} primary key metadata is not exposed by this adapter",
            ),
        ),
    )


def inspect_table(
    datasource: str,
    *,
    table: str,
    database: str | tuple[str, ...] | None = None,
    include_partitions: bool = True,
    project_root: Path | None = None,
) -> TableMetadata:
    datasource_ir = _store.load_one(datasource, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMetadataError(
            message=f"datasource {datasource!r} is not configured",
            details={"datasource": datasource, "table": table, "available": _store.list_names()},
        )

    backend: Any = None
    try:
        try:
            backend = _backends.build_backend(datasource_ir)
            table_expr = (
                backend.table(table)
                if database is None
                else backend.table(table, database=database)
            )
        except Exception as exc:
            raise DatasourceMetadataError(
                message=f"failed to inspect datasource table {datasource!r}.{table!r}: {exc}",
                details={
                    "datasource": datasource,
                    "table": table,
                    "database": _database_label(database),
                    "cause": str(exc),
                },
            ) from exc

        try:
            from marivo.datasource.engines import require_profile_for_backend_type
            from marivo.datasource.engines.base import MetadataInspectRequest

            profile = require_profile_for_backend_type(datasource_ir.backend_type)
            metadata = profile.metadata.inspect_table(
                MetadataInspectRequest(
                    datasource=datasource,
                    backend=backend,
                    table=table,
                    database=database,
                    table_expr=table_expr,
                    include_partitions=include_partitions,
                    datasource_ir=datasource_ir,
                )
            )
        except DatasourceMetadataError:
            raise
        except Exception as exc:
            metadata = _schema_only(
                datasource=datasource,
                table=table,
                database=database,
                backend_type=datasource_ir.backend_type,
                table_expr=table_expr,
                warnings=(
                    MetadataWarning(
                        kind="metadata_query_failed",
                        message=f"{datasource_ir.backend_type} metadata query failed: {exc}",
                    ),
                ),
            )
    finally:
        # The backend is an internal handle owned by this function; release it
        # so it does not outlive the inspection. A lingering read-write handle
        # would block read-only opens to the same DuckDB file from raw_sql in a
        # later call.
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
            with suppress(Exception):
                disconnect()
    return _with_primary_key_capability_warning(metadata)


def _inspect_source(
    datasource: str,
    *,
    source: EntitySourceIR,
    include_partitions: bool = True,
    project_root: Path | None = None,
) -> TableMetadata:
    if isinstance(source, TableSourceIR):
        return inspect_table(
            datasource,
            table=str(source.table),
            database=source.database,
            include_partitions=include_partitions,
            project_root=project_root,
        )
    if not isinstance(source, (ParquetSourceIR, CsvSourceIR)):
        raise DatasourceMetadataError(
            message=f"unsupported datasource source kind {getattr(source, 'kind', None)!r}",
            details={"datasource": datasource, "source_kind": getattr(source, "kind", None)},
        )

    datasource_ir = _store.load_one(datasource, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMetadataError(
            message=f"datasource {datasource!r} is not configured",
            details={"datasource": datasource, "available": _store.list_names()},
        )
    try:
        backend = _backends.build_backend(datasource_ir)
        if isinstance(source, ParquetSourceIR):
            reader = getattr(backend, "read_parquet", None)
            if reader is None:
                raise AttributeError("backend has no read_parquet()")
            kwargs: dict[str, object] = {}
            if source.hive_partitioning:
                kwargs["hive_partitioning"] = source.hive_partitioning
            if source.columns is not None:
                kwargs["columns"] = list(source.columns)
            table_expr = reader(source.path, **kwargs)
        else:  # CsvSourceIR
            reader = getattr(backend, "read_csv", None)
            if reader is None:
                raise AttributeError("backend has no read_csv()")
            kwargs = {}
            if not source.header:
                kwargs["header"] = source.header
            if source.delimiter != ",":
                kwargs["delimiter"] = source.delimiter
            if source.columns is not None:
                kwargs["columns"] = list(source.columns)
            table_expr = reader(source.path, **kwargs)
    except Exception as exc:
        raise DatasourceMetadataError(
            message=f"failed to inspect datasource file source {datasource!r}.{source.path!r}: {exc}",
            details={
                "datasource": datasource,
                "path": source.path,
                "source_kind": source.kind,
                "cause": str(exc),
            },
        ) from exc

    return _with_primary_key_capability_warning(
        _schema_only(
            datasource=datasource,
            table=source_name(source),
            database=None,
            backend_type=datasource_ir.backend_type,
            table_expr=table_expr,
            warnings=(
                MetadataWarning(
                    kind="comments_unavailable",
                    message="file source comments are not available",
                ),
                MetadataWarning(
                    kind="nullable_unavailable",
                    message="file source nullable flags are not available",
                ),
                MetadataWarning(
                    kind="partitions_unavailable",
                    message="file source partition metadata is not available",
                ),
            ),
        )
    )
