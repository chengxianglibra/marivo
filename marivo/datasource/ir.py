"""Intermediate representation for project-level datasources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

__all__ = [
    "AiContextIR",
    "CsvSourceIR",
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "EntitySourceIR",
    "JsonSourceIR",
    "ParquetSourceIR",
    "TableSourceIR",
    "qualify_provenance_sql",
    "source_name",
    "source_to_dict",
]


@dataclass(frozen=True)
class DatasourceSourceLocation:
    """Absolute source location for datasource error reporting."""

    file: str
    line: int


@dataclass(frozen=True)
class AiContextIR:
    """Immutable AI-facing context stored on semantic and datasource objects."""

    business_definition: str | None = None
    guardrails: tuple[str, ...] = ()


DatasourceAiContextIR = AiContextIR


@dataclass(frozen=True)
class DatasourceIR:
    """Project-level datasource configuration."""

    semantic_id: str
    name: str
    backend_type: str
    fields: dict[str, Any]
    env_refs: dict[str, str]
    ai_context: AiContextIR
    python_symbol: str
    location: DatasourceSourceLocation


# ---------------------------------------------------------------------------
# Physical source descriptors
# ---------------------------------------------------------------------------


def _require_non_empty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str, got {type(value).__name__}.")
    if not value:
        raise ValueError(f"{field_name} must be non-empty.")
    return value


def _require_kind(value: object, *, field_name: str, expected: str) -> None:
    if value != expected:
        raise ValueError(f"{field_name} must be {expected!r}, got {value!r}.")


_JSON_FORMATS = ("auto", "newline_delimited", "array")


def _require_json_format(value: object, field_name: str) -> None:
    if value not in _JSON_FORMATS:
        raise TypeError(f"{field_name} must be one of {_JSON_FORMATS!r}, got {value!r}.")


def _validate_database(value: object) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if not value:
            raise ValueError("TableSourceIR.database must be non-empty when provided.")
        return
    if isinstance(value, tuple):
        if not value:
            raise ValueError("TableSourceIR.database tuple must be non-empty when provided.")
        for part in value:
            _require_non_empty_str(part, "TableSourceIR.database")
        return
    raise TypeError(
        f"TableSourceIR.database must be str | tuple[str, ...] | None, got {type(value).__name__}."
    )


def _validate_columns(value: object, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be tuple[str, ...] | None, got {type(value).__name__}.")
    for column in value:
        _require_non_empty_str(column, field_name)


def _validate_schema(value: object, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(
            f"{field_name} must be tuple[tuple[str, str], ...], got {type(value).__name__}."
        )
    if not value:
        raise ValueError(f"{field_name} must contain at least one typed column.")
    for entry in value:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise TypeError(f"{field_name} entries must be tuple[str, str].")
        name, type_name = entry
        _require_non_empty_str(name, f"{field_name} column name")
        _require_non_empty_str(type_name, f"{field_name} type name")


@dataclass(frozen=True)
class TableSourceIR:
    """Physical table source for a dataset."""

    table: str
    database: str | tuple[str, ...] | None = None
    kind: Literal["table"] = "table"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.table, "TableSourceIR.table")
        _validate_database(self.database)
        _require_kind(self.kind, field_name="TableSourceIR.kind", expected="table")

    def to_dict(self) -> dict[str, object]:
        database: str | list[str] | None = (
            list(self.database) if isinstance(self.database, tuple) else self.database
        )
        return {"kind": self.kind, "table": self.table, "database": database}

    def to_ir(self) -> TableSourceIR:
        return self


@dataclass(frozen=True)
class ParquetSourceIR:
    """Physical parquet source for an entity."""

    path: str
    hive_partitioning: bool = False
    columns: tuple[str, ...] | None = None
    kind: Literal["parquet"] = "parquet"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.path, "ParquetSourceIR.path")
        if type(self.hive_partitioning) is not bool:
            raise TypeError(
                f"ParquetSourceIR.hive_partitioning must be bool, "
                f"got {type(self.hive_partitioning).__name__}."
            )
        _validate_columns(self.columns, "ParquetSourceIR.columns")
        _require_kind(self.kind, field_name="ParquetSourceIR.kind", expected="parquet")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "hive_partitioning": self.hive_partitioning,
            "columns": list(self.columns) if self.columns is not None else None,
        }

    def to_ir(self) -> ParquetSourceIR:
        return self


@dataclass(frozen=True)
class CsvSourceIR:
    """Physical CSV source for an entity."""

    path: str
    schema: tuple[tuple[str, str], ...]
    header: bool = True
    delimiter: str = ","
    kind: Literal["csv"] = "csv"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.path, "CsvSourceIR.path")
        _validate_schema(self.schema, "CsvSourceIR.schema")
        if type(self.header) is not bool:
            raise TypeError(f"CsvSourceIR.header must be bool, got {type(self.header).__name__}.")
        _require_non_empty_str(self.delimiter, "CsvSourceIR.delimiter")
        _require_kind(self.kind, field_name="CsvSourceIR.kind", expected="csv")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "schema": dict(self.schema),
            "header": self.header,
            "delimiter": self.delimiter,
        }

    def to_ir(self) -> CsvSourceIR:
        return self


@dataclass(frozen=True)
class JsonSourceIR:
    """Physical JSON source for an entity."""

    path: str
    schema: tuple[tuple[str, str], ...]
    format: Literal["auto", "newline_delimited", "array"] = "auto"
    kind: Literal["json"] = "json"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.path, "JsonSourceIR.path")
        _validate_schema(self.schema, "JsonSourceIR.schema")
        _require_json_format(self.format, "JsonSourceIR.format")
        _require_kind(self.kind, field_name="JsonSourceIR.kind", expected="json")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "schema": dict(self.schema),
            "format": self.format,
        }

    def to_ir(self) -> JsonSourceIR:
        return self


EntitySourceIR = TableSourceIR | ParquetSourceIR | CsvSourceIR | JsonSourceIR

_GLOB_CHARS = re.compile(r"[*?\\[]")
_SOURCE_NAME_CHARS = re.compile(r"[^0-9A-Za-z_]+")


def _sanitize_source_name(value: str) -> str:
    name = _SOURCE_NAME_CHARS.sub("_", value).strip("_").lower()
    return name or "file_source"


def source_name(source: EntitySourceIR) -> str:
    if isinstance(source, TableSourceIR):
        return source.table

    normalized_path = source.path.replace("\\", "/").rstrip("/")
    path = PurePosixPath(normalized_path)
    raw_name = path.name
    raw_name = path.parent.name if _GLOB_CHARS.search(raw_name) else PurePosixPath(raw_name).stem
    return _sanitize_source_name(raw_name)


def source_to_dict(source: EntitySourceIR) -> dict[str, object]:
    if isinstance(source, TableSourceIR):
        database: str | list[str] | None = (
            list(source.database) if isinstance(source.database, tuple) else source.database
        )
        return {"kind": "table", "table": source.table, "database": database}
    return source.to_dict()


def qualify_provenance_sql(
    provenance_sql: str,
    table_qualifiers: dict[str, str],
    *,
    dialect: str | None = None,
) -> str:
    """Qualify unqualified table references in provenance SQL.

    Rewrites bare table names that match keys in *table_qualifiers* to their
    fully-qualified form (e.g. ``orders`` -> ``iceberg_inf.orders``).
    Tables that are already qualified or that reference CTE aliases are left
    unchanged.  If sqlglot cannot parse the SQL, the original string is
    returned unchanged.

    Args:
        provenance_sql: Raw SQL string from metric provenance.
        table_qualifiers: Mapping from bare table name to database-qualified
            name (e.g. ``{"orders": "iceberg_inf.orders"}``).
        dialect: Optional sqlglot dialect for parsing and generating.

    Returns:
        SQL string with unqualified table references replaced by qualified ones.
    """
    if not table_qualifiers:
        return provenance_sql

    import sqlglot
    from sqlglot import exp

    try:
        parsed = sqlglot.parse_one(provenance_sql, dialect=dialect)
    except sqlglot.errors.ParseError:
        return provenance_sql

    # Collect CTE alias names so we don't qualify CTE references.
    cte_names: set[str] = set()
    for cte in parsed.find_all(exp.CTE):
        alias = cte.alias
        cte_names.add(alias if isinstance(alias, str) else alias.sql(dialect=dialect))

    for table_node in parsed.find_all(exp.Table):
        # Skip CTE references.
        if table_node.name in cte_names:
            continue
        # Skip tables that are already qualified.
        if table_node.db:
            continue
        qualified = table_qualifiers.get(table_node.name)
        if qualified is None:
            continue
        # Split qualified name into catalog/db/name parts.
        # "db.table" -> db + table
        # "catalog.db.table" -> catalog + db + table
        parts = qualified.split(".")
        if len(parts) == 2:
            table_node.set("db", exp.to_identifier(parts[0]))
            table_node.set("this", exp.to_identifier(parts[1]))
        elif len(parts) == 3:
            table_node.set("catalog", exp.to_identifier(parts[0]))
            table_node.set("db", exp.to_identifier(parts[1]))
            table_node.set("this", exp.to_identifier(parts[2]))
        else:
            # Can't map arbitrary multi-part names; skip.
            continue

    return str(parsed.sql(dialect=dialect))
