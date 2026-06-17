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
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    instructions: str | None = None
    owner_notes: str | None = None


DatasourceAiContextIR = AiContextIR


@dataclass(frozen=True)
class DatasourceIR:
    """Project-level datasource configuration."""

    semantic_id: str
    name: str
    backend_type: str
    fields: dict[str, Any]
    env_refs: dict[str, str]
    description: str | None
    ai_context: AiContextIR
    python_symbol: str
    location: DatasourceSourceLocation


# ---------------------------------------------------------------------------
# Physical source descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableSourceIR:
    """Physical table source for a dataset."""

    table: str
    database: str | tuple[str, ...] | None = None
    kind: Literal["table"] = "table"

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
    header: bool = True
    delimiter: str = ","
    columns: tuple[str, ...] | None = None
    kind: Literal["csv"] = "csv"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "header": self.header,
            "delimiter": self.delimiter,
            "columns": list(self.columns) if self.columns is not None else None,
        }

    def to_ir(self) -> CsvSourceIR:
        return self


EntitySourceIR = TableSourceIR | ParquetSourceIR | CsvSourceIR

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
