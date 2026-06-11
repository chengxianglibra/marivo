"""Datasource table metadata inspection DTOs and backend adapters."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from marivo.datasource import backends as _backends
from marivo.datasource import store as _store
from marivo.datasource.errors import DatasourceMetadataError
from marivo.datasource.ir import EntitySourceIR, FileSourceIR, TableSourceIR, source_name

MetadataWarningKind = Literal[
    "comments_unavailable",
    "nullable_unavailable",
    "partitions_unavailable",
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
class TableMetadata:
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

    @property
    def ref(self) -> str:
        if self.database is None:
            return f"{self.datasource}.{self.table}"
        database = ".".join(self.database) if isinstance(self.database, tuple) else self.database
        return f"{self.datasource}.{database}.{self.table}"

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


def _duckdb_view_predicate(
    table: str,
    database: str | tuple[str, ...] | None,
    *,
    default_database: str | None = None,
    default_schema: str = "main",
) -> str:
    predicates = [
        f"view_name = {_quote_literal(table)}",
        "internal = false",
    ]
    if isinstance(database, tuple):
        if len(database) == 1:
            predicates.append(f"schema_name = {_quote_literal(database[0])}")
        elif len(database) >= 2:
            predicates.append(f"database_name = {_quote_literal(database[0])}")
            predicates.append(f"schema_name = {_quote_literal(database[1])}")
    elif database is not None:
        predicates.append(f"schema_name = {_quote_literal(database)}")
    else:
        if default_database is not None:
            predicates.append(f"database_name = {_quote_literal(default_database)}")
        predicates.append(f"schema_name = {_quote_literal(default_schema)}")
    return " AND ".join(predicates)


def _cursor_rows(cursor: Any) -> list[dict[str, object]]:
    # DB-API cursor path (duckdb, mysql, postgres, trino)
    description = getattr(cursor, "description", None)
    fetchall = getattr(cursor, "fetchall", None)
    if description is not None and callable(fetchall):
        columns = [str(item[0]) for item in description]
        return [dict(zip(columns, row, strict=True)) for row in fetchall()]

    # QueryResult path (clickhouse via clickhouse_connect)
    column_names = getattr(cursor, "column_names", None)
    result_rows = getattr(cursor, "result_rows", None)
    if column_names and result_rows is not None:
        columns = [str(name) for name in column_names]
        return [dict(zip(columns, row, strict=True)) for row in result_rows]

    return []


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


def _bool_from_nullable(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"YES", "Y", "TRUE", "1"}:
        return True
    if text in {"NO", "N", "FALSE", "0"}:
        return False
    return None


def _nullable_from_clickhouse(is_nullable_value: object, type_str: str) -> bool | None:
    result = _bool_from_nullable(is_nullable_value)
    if result is not None:
        return result
    return bool(type_str.startswith("Nullable("))


_CH_PARTITION_FUNC_RE = re.compile(r"^(\w+)\((\w+)\)$")
_CH_PARTITION_BARE_RE = re.compile(r"^(\w+)$")


def _parse_clickhouse_partition_key(
    partition_key: str,
    catalog_columns: Mapping[str, ColumnMetadata],
) -> tuple[PartitionMetadata, ...]:
    if not partition_key or partition_key == "tuple()":
        return ()
    # Split on commas only outside parentheses (depth-aware).
    elements: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(partition_key):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            elements.append(partition_key[start:i].strip())
            start = i + 1
    elements.append(partition_key[start:].strip())
    parts: list[PartitionMetadata] = []
    for element in elements:
        func_match = _CH_PARTITION_FUNC_RE.match(element)
        if func_match:
            func_name, col_name = func_match.group(1), func_match.group(2)
            col = catalog_columns.get(col_name)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=col_name,
                        type=col.type,
                        transform=func_name,
                        comment=None,
                    )
                )
            continue
        bare_match = _CH_PARTITION_BARE_RE.match(element)
        if bare_match:
            col_name = bare_match.group(1)
            col = catalog_columns.get(col_name)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=col_name,
                        type=col.type,
                        transform=None,
                        comment=None,
                    )
                )
            continue
        # Unparseable expression: store full raw string as transform.
        # Try to identify a column name from identifier-like tokens,
        # stripping commas so inner args like "uid," in intDiv(uid, 100) match.
        for token in element.replace("(", " ").replace(")", " ").replace(",", " ").split():
            col = catalog_columns.get(token)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=token,
                        type=col.type,
                        transform=element,
                        comment=None,
                    )
                )
                break
    return tuple(parts)


_CH_DISTRIBUTED_ENGINE_RE = re.compile(r"^Distributed\('([^']+)',\s*'([^']+)',\s*'([^']+)'")


def _dereference_clickhouse_distributed(
    backend: Any,
    engine_full: str,
    ch_database: str,
    warnings: list[MetadataWarning],
) -> str:
    match = _CH_DISTRIBUTED_ENGINE_RE.match(engine_full)
    if not match:
        return ""
    local_database, local_table = match.group(2), match.group(3)
    try:
        local_rows = _query_rows(
            backend,
            "SELECT partition_key FROM system.tables "
            f"WHERE name = {_quote_literal(local_table)} "
            f"AND database = {_quote_literal(local_database)} LIMIT 1",
        )
        if local_rows:
            return str(local_rows[0].get("partition_key") or "")
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"clickhouse distributed table dereference failed: {exc}",
            )
        )
    return ""


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


def _inspect_duckdb(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
) -> TableMetadata:
    schema_columns = _schema_columns(table_expr)
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None
    catalog_columns: dict[str, ColumnMetadata] = {}
    is_view = False
    view_definition: str | None = None

    try:
        table_rows = _query_rows(
            backend,
            "SELECT comment FROM duckdb_tables() "
            f"WHERE table_name = {_quote_literal(table)} LIMIT 1",
        )
        if table_rows:
            table_comment = _empty_to_none(table_rows[0].get("comment"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"duckdb table comment query failed: {exc}",
            )
        )

    try:
        column_rows = _query_rows(
            backend,
            "SELECT column_name, data_type, is_nullable, comment "
            "FROM duckdb_columns() "
            f"WHERE table_name = {_quote_literal(table)} "
            "ORDER BY column_index",
        )
        for index, row in enumerate(column_rows, start=1):
            name = str(row.get("column_name"))
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=str(row.get("data_type") or ""),
                nullable=_bool_from_nullable(row.get("is_nullable")),
                comment=_empty_to_none(row.get("comment")),
                ordinal_position=index,
            )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"duckdb column metadata query failed: {exc}",
            )
        )

    try:
        default_database: str | None = None
        default_schema = "main"
        if database is None:
            namespace_rows = _query_rows(
                backend,
                "SELECT current_database() AS database_name, current_schema() AS schema_name",
            )
            if namespace_rows:
                default_database = _empty_to_none(namespace_rows[0].get("database_name"))
                default_schema = _empty_to_none(namespace_rows[0].get("schema_name")) or "main"
        view_rows = _query_rows(
            backend,
            "SELECT sql FROM duckdb_views() "
            "WHERE "
            f"{_duckdb_view_predicate(table, database, default_database=default_database, default_schema=default_schema)} "
            "LIMIT 1",
        )
        if view_rows:
            is_view = True
            view_definition = _empty_to_none(view_rows[0].get("sql"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"duckdb view metadata query failed: {exc}",
            )
        )

    if include_partitions:
        warnings.append(
            MetadataWarning(
                kind="partitions_unavailable",
                message="duckdb does not expose table partition metadata through this adapter",
            )
        )

    columns = _merge_columns(schema_columns, catalog_columns)
    if not any(column.comment for column in columns) and table_comment is None:
        warnings.append(
            MetadataWarning(
                kind="comments_unavailable",
                message="duckdb table and column comments are unavailable for this table",
            )
        )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="duckdb",
        comment=table_comment,
        columns=columns,
        partitions=(),
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
    )


def _inspect_mysql(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
    default_database: str | None,
) -> TableMetadata:
    schema_columns = _schema_columns(table_expr)
    schema_name = _database_label(database) or default_database
    table_comment: str | None = None
    warnings: list[MetadataWarning] = []

    table_comment_sql = (
        "SELECT TABLE_COMMENT FROM information_schema.tables "
        f"WHERE table_name = {_quote_literal(table)}"
    )
    if schema_name is not None:
        table_comment_sql += f" AND table_schema = {_quote_literal(schema_name)}"
    try:
        table_rows = _query_rows(backend, table_comment_sql)
        if table_rows:
            table_comment = _empty_to_none(table_rows[0].get("TABLE_COMMENT"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"mysql table comment query failed: {exc}",
            )
        )

    catalog_columns: dict[str, ColumnMetadata] = {}
    table_ref = _table_ref(table, database)
    try:
        column_rows = _query_rows(backend, f"SHOW FULL COLUMNS FROM {table_ref}")
        for index, row in enumerate(column_rows, start=1):
            name = str(row.get("Field"))
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=str(row.get("Type") or ""),
                nullable=_bool_from_nullable(row.get("Null")),
                comment=_empty_to_none(row.get("Comment")),
                ordinal_position=index,
            )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"mysql column metadata query failed: {exc}",
            )
        )

    if include_partitions:
        warnings.append(
            MetadataWarning(
                kind="partitions_unavailable",
                message="mysql partition metadata is not exposed by this adapter",
            )
        )

    is_view = False
    view_definition: str | None = None
    type_sql = (
        "SELECT TABLE_TYPE FROM information_schema.tables "
        f"WHERE table_name = {_quote_literal(table)}"
    )
    if schema_name is not None:
        type_sql += f" AND table_schema = {_quote_literal(schema_name)}"
    try:
        type_rows = _query_rows(backend, type_sql)
        if type_rows and str(type_rows[0].get("TABLE_TYPE") or "").upper() == "VIEW":
            is_view = True
            def_rows = _query_rows(
                backend,
                "SELECT VIEW_DEFINITION FROM information_schema.views "
                f"WHERE table_name = {_quote_literal(table)}"
                + (f" AND table_schema = {_quote_literal(schema_name)}" if schema_name else ""),
            )
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("VIEW_DEFINITION"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"mysql view metadata query failed: {exc}",
            )
        )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="mysql",
        comment=table_comment,
        columns=_merge_columns(schema_columns, catalog_columns),
        partitions=(),
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
    )


def _inspect_trino(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
    catalog: str,
    default_schema: str | None,
) -> TableMetadata:
    schema_columns = _schema_columns(table_expr)
    schema_name = _database_label(database) or default_schema
    if schema_name is None:
        return _schema_only(
            datasource=datasource,
            table=table,
            database=database,
            backend_type="trino",
            table_expr=table_expr,
            warnings=(
                MetadataWarning(
                    kind="comments_unavailable",
                    message="trino metadata inspection requires database= or datasource schema",
                ),
                MetadataWarning(
                    kind="nullable_unavailable",
                    message="trino metadata inspection requires database= or datasource schema",
                ),
            ),
        )
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None

    table_predicates = [
        f"table_catalog = {_quote_literal(catalog)}",
        f"table_schema = {_quote_literal(schema_name)}",
        f"table_name = {_quote_literal(table)}",
    ]
    where_clause = " AND ".join(table_predicates)

    try:
        table_rows = _query_rows(
            backend,
            f"SELECT comment FROM information_schema.tables WHERE {where_clause} LIMIT 1",
        )
        if table_rows:
            table_comment = _empty_to_none(table_rows[0].get("comment"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"trino table comment query failed: {exc}",
            )
        )

    catalog_columns: dict[str, ColumnMetadata] = {}
    try:
        column_rows = _query_rows(
            backend,
            "SELECT column_name, data_type, is_nullable, comment, ordinal_position "
            "FROM information_schema.columns "
            f"WHERE {where_clause} ORDER BY ordinal_position",
        )
        for row in column_rows:
            name = str(row.get("column_name"))
            ordinal = row.get("ordinal_position")
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=str(row.get("data_type") or ""),
                nullable=_bool_from_nullable(row.get("is_nullable")),
                comment=_empty_to_none(row.get("comment")),
                ordinal_position=int(str(ordinal)) if ordinal is not None else None,
            )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"trino column metadata query failed: {exc}",
            )
        )

    if include_partitions:
        warnings.append(
            MetadataWarning(
                kind="partitions_unavailable",
                message="trino partition metadata is connector-specific and not exposed by this adapter",
            )
        )

    is_view = False
    view_definition: str | None = None
    try:
        type_rows = _query_rows(
            backend,
            f"SELECT table_type FROM information_schema.tables WHERE {where_clause} LIMIT 1",
        )
        if type_rows and str(type_rows[0].get("table_type") or "").upper() == "VIEW":
            is_view = True
            def_rows = _query_rows(
                backend,
                f"SELECT view_definition FROM information_schema.views WHERE {where_clause} LIMIT 1",
            )
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("view_definition"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"trino view metadata query failed: {exc}",
            )
        )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="trino",
        comment=table_comment,
        columns=_merge_columns(schema_columns, catalog_columns),
        partitions=(),
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
    )


def _inspect_clickhouse(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
) -> TableMetadata:
    schema_columns = _schema_columns(table_expr)
    ch_database = _database_label(database) or "default"
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None
    partition_key = ""
    engine = ""
    engine_full = ""

    try:
        table_rows = _query_rows(
            backend,
            "SELECT comment, partition_key, engine, engine_full FROM system.tables "
            f"WHERE name = {_quote_literal(table)} "
            f"AND database = {_quote_literal(ch_database)} LIMIT 1",
        )
        if table_rows:
            table_comment = _empty_to_none(table_rows[0].get("comment"))
            partition_key = str(table_rows[0].get("partition_key") or "")
            engine = str(table_rows[0].get("engine") or "")
            engine_full = str(table_rows[0].get("engine_full") or "")
    except Exception:
        try:
            table_rows = _query_rows(
                backend,
                "SELECT comment FROM system.tables "
                f"WHERE name = {_quote_literal(table)} "
                f"AND database = {_quote_literal(ch_database)} LIMIT 1",
            )
            if table_rows:
                table_comment = _empty_to_none(table_rows[0].get("comment"))
        except Exception as exc2:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"clickhouse table metadata query failed: {exc2}",
                )
            )

    catalog_columns: dict[str, ColumnMetadata] = {}
    try:
        column_rows = _query_rows(
            backend,
            "SELECT name, type, is_nullable, comment, position "
            "FROM system.columns "
            f"WHERE table = {_quote_literal(table)} "
            f"AND database = {_quote_literal(ch_database)} "
            "ORDER BY position",
        )
        for row in column_rows:
            name = str(row.get("name"))
            type_str = str(row.get("type") or "")
            ordinal = row.get("position")
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=type_str,
                nullable=_nullable_from_clickhouse(row.get("is_nullable"), type_str),
                comment=_empty_to_none(row.get("comment")),
                ordinal_position=int(str(ordinal)) if ordinal is not None else None,
            )
    except Exception:
        try:
            column_rows = _query_rows(
                backend,
                "SELECT name, type, comment, position "
                "FROM system.columns "
                f"WHERE table = {_quote_literal(table)} "
                f"AND database = {_quote_literal(ch_database)} "
                "ORDER BY position",
            )
            for row in column_rows:
                name = str(row.get("name"))
                type_str = str(row.get("type") or "")
                ordinal = row.get("position")
                catalog_columns[name] = ColumnMetadata(
                    name=name,
                    type=type_str,
                    nullable=_nullable_from_clickhouse(None, type_str),
                    comment=_empty_to_none(row.get("comment")),
                    ordinal_position=int(str(ordinal)) if ordinal is not None else None,
                )
        except Exception as exc2:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"clickhouse column metadata query failed: {exc2}",
                )
            )

    partitions: tuple[PartitionMetadata, ...] = ()
    if include_partitions:
        if engine == "Distributed":
            local_pk = _dereference_clickhouse_distributed(
                backend,
                engine_full,
                ch_database,
                warnings,
            )
            if local_pk:
                partitions = _parse_clickhouse_partition_key(local_pk, catalog_columns)
        elif partition_key and partition_key != "tuple()":
            partitions = _parse_clickhouse_partition_key(partition_key, catalog_columns)

    columns = _merge_columns(schema_columns, catalog_columns)
    if not any(column.comment for column in columns) and table_comment is None:
        warnings.append(
            MetadataWarning(
                kind="comments_unavailable",
                message="clickhouse table and column comments are unavailable for this table",
            )
        )

    is_view = engine in ("View", "MaterializedView")
    view_definition: str | None = None
    if is_view:
        try:
            def_rows = _query_rows(
                backend,
                "SELECT create_table_query FROM system.tables "
                f"WHERE name = {_quote_literal(table)} "
                f"AND database = {_quote_literal(ch_database)} LIMIT 1",
            )
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("create_table_query"))
        except Exception as exc:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"clickhouse view metadata query failed: {exc}",
                )
            )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="clickhouse",
        comment=table_comment,
        columns=columns,
        partitions=partitions,
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
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
            details={
                "datasource": datasource,
                "table": table,
                "available": _store.list_names(project_root),
            },
        )

    try:
        backend = _backends.build_backend(datasource_ir)
        table_expr = (
            backend.table(table) if database is None else backend.table(table, database=database)
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
        if datasource_ir.backend_type == "duckdb":
            return _inspect_duckdb(
                datasource=datasource,
                backend=backend,
                table=table,
                database=database,
                table_expr=table_expr,
                include_partitions=include_partitions,
            )
        if datasource_ir.backend_type == "mysql":
            return _inspect_mysql(
                datasource=datasource,
                backend=backend,
                table=table,
                database=database,
                table_expr=table_expr,
                include_partitions=include_partitions,
                default_database=(
                    str(datasource_ir.fields["database"])
                    if datasource_ir.fields.get("database") is not None
                    else None
                ),
            )
        if datasource_ir.backend_type == "trino":
            return _inspect_trino(
                datasource=datasource,
                backend=backend,
                table=table,
                database=database,
                table_expr=table_expr,
                include_partitions=include_partitions,
                catalog=str(datasource_ir.fields["catalog"]),
                default_schema=(
                    str(datasource_ir.fields["schema"])
                    if datasource_ir.fields.get("schema") is not None
                    else None
                ),
            )
        if datasource_ir.backend_type == "clickhouse":
            ch_database = (
                database
                if database is not None
                else datasource_ir.fields.get("database", "default")
            )
            return _inspect_clickhouse(
                datasource=datasource,
                backend=backend,
                table=table,
                database=ch_database,
                table_expr=table_expr,
                include_partitions=include_partitions,
            )
    except DatasourceMetadataError:
        raise
    except Exception as exc:
        return _schema_only(
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

    return _schema_only(
        datasource=datasource,
        table=table,
        database=database,
        backend_type=datasource_ir.backend_type,
        table_expr=table_expr,
        warnings=(
            MetadataWarning(
                kind="comments_unavailable",
                message=f"{datasource_ir.backend_type} comments are not supported by this adapter",
            ),
            MetadataWarning(
                kind="nullable_unavailable",
                message=f"{datasource_ir.backend_type} nullable flags are not supported by this adapter",
            ),
            MetadataWarning(
                kind="partitions_unavailable",
                message=f"{datasource_ir.backend_type} partition metadata is not supported by this adapter",
            ),
        ),
    )


def inspect_source(
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
    if not isinstance(source, FileSourceIR):
        raise DatasourceMetadataError(
            message=f"unsupported datasource source kind {getattr(source, 'kind', None)!r}",
            details={"datasource": datasource, "source_kind": getattr(source, "kind", None)},
        )

    datasource_ir = _store.load_one(datasource, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMetadataError(
            message=f"datasource {datasource!r} is not configured",
            details={"datasource": datasource, "available": _store.list_names(project_root)},
        )
    try:
        backend = _backends.build_backend(datasource_ir)
        reader_name = "read_parquet" if source.format == "parquet" else "read_csv"
        reader = getattr(backend, reader_name, None)
        if reader is None:
            raise AttributeError(f"backend has no {reader_name}()")
        table_expr = reader(source.path, **source.options)
    except Exception as exc:
        raise DatasourceMetadataError(
            message=f"failed to inspect datasource file source {datasource!r}.{source.path!r}: {exc}",
            details={
                "datasource": datasource,
                "path": source.path,
                "format": source.format,
                "cause": str(exc),
            },
        ) from exc

    return _schema_only(
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
