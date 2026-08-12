"""Intermediate representation for project-level datasources."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import PurePosixPath
from typing import Any, Literal

__all__ = [
    "AiContextIR",
    "CsvSourceIR",
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "EntitySourceIR",
    "JsonBodyParam",
    "JsonBodyPathPart",
    "JsonBodyValue",
    "JsonQueryParamValue",
    "JsonSourceIR",
    "ParquetSourceIR",
    "QueryParamScalar",
    "SourceParamIR",
    "TableSourceIR",
    "json_body_to_string",
    "normalize_json_body",
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
_JSON_RECORDS_PATH = re.compile(r"^\$(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")

type QueryParamScalar = str | int | float | bool


@dataclass(frozen=True)
class SourceParamIR:
    """Required runtime parameter referenced by one physical source."""

    name: str

    def __post_init__(self) -> None:
        _require_non_empty_str(self.name, "SourceParamIR.name")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise ValueError(
                "SourceParamIR.name must contain ASCII letters, digits, and underscores, "
                "and must not start with a digit."
            )

    def to_dict(self) -> dict[str, str]:
        return {"kind": "source_param", "name": self.name}


type JsonQueryParamValue = QueryParamScalar | SourceParamIR
type JsonBodyPathPart = str | int
type JsonBodyParam = tuple[tuple[JsonBodyPathPart, ...], SourceParamIR]
type JsonBodyValue = (
    str
    | int
    | float
    | bool
    | SourceParamIR
    | Mapping[str, "JsonBodyValue"]
    | Sequence["JsonBodyValue"]
    | None
)


def _normalize_json_body_value(
    value: object,
    *,
    field_name: str,
    path: tuple[JsonBodyPathPart, ...],
    params: list[JsonBodyParam],
) -> object:
    if isinstance(value, SourceParamIR):
        params.append((path, value))
        return None
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{field_name} floats must be finite.")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} object keys must be strings.")
            normalized[key] = _normalize_json_body_value(
                item,
                field_name=field_name,
                path=(*path, key),
                params=params,
            )
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [
            _normalize_json_body_value(
                item,
                field_name=field_name,
                path=(*path, index),
                params=params,
            )
            for index, item in enumerate(value)
        ]
    raise TypeError(f"{field_name} values must be JSON-compatible; got {type(value).__name__}.")


def normalize_json_body(value: object) -> tuple[str, tuple[JsonBodyParam, ...]]:
    """Return one canonical JSON object plus separately typed runtime parameters."""
    if not isinstance(value, Mapping):
        raise TypeError("md.json(body=...) must be a JSON object mapping.")
    params: list[JsonBodyParam] = []
    normalized = _normalize_json_body_value(
        value,
        field_name="md.json(body=...)",
        path=(),
        params=params,
    )
    return (
        json.dumps(normalized, ensure_ascii=True, allow_nan=False, separators=(",", ":")),
        tuple(params),
    )


def json_body_to_string(value: object) -> str:
    """Validate one concrete JSON object body and return its canonical representation."""
    body_json, params = normalize_json_body(value)
    if params:
        raise TypeError("concrete JSON bodies cannot contain md.source_param(...).")
    return body_json


def _require_json_format(value: object, field_name: str) -> None:
    if value not in _JSON_FORMATS:
        raise TypeError(f"{field_name} must be one of {_JSON_FORMATS!r}, got {value!r}.")


def _validate_json_records_path(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError(
            f"JsonSourceIR.records_path must be str | None, got {type(value).__name__}."
        )
    if not _JSON_RECORDS_PATH.fullmatch(value):
        raise ValueError(
            "JsonSourceIR.records_path must be an object-member path such as "
            "'$.data' or '$.result.items'."
        )


def _validate_json_query_params(value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError(
            "JsonSourceIR.query_params must be tuple[tuple[str, JsonQueryParamValue], ...], "
            f"got {type(value).__name__}."
        )
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise TypeError("JsonSourceIR.query_params entries must be (name, value) tuples.")
        name, item = entry
        _require_non_empty_str(name, "JsonSourceIR.query_params name")
        if name in seen:
            raise ValueError(f"JsonSourceIR.query_params contains duplicate name {name!r}.")
        seen.add(name)
        if not isinstance(item, str | int | float | bool | SourceParamIR):
            raise TypeError(
                "JsonSourceIR.query_params values must be str, int, float, bool, or "
                f"SourceParamIR; got {type(item).__name__} for {name!r}."
            )
        if isinstance(item, float) and not isfinite(item):
            raise ValueError(
                f"JsonSourceIR.query_params value for {name!r} must be a finite float."
            )


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
    records_path: str | None = None
    query_params: tuple[tuple[str, JsonQueryParamValue], ...] = ()
    method: Literal["GET", "POST"] = "GET"
    body_json: str | None = None
    body_params: tuple[JsonBodyParam, ...] = ()
    kind: Literal["json"] = "json"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.path, "JsonSourceIR.path")
        _validate_schema(self.schema, "JsonSourceIR.schema")
        _require_json_format(self.format, "JsonSourceIR.format")
        _validate_json_records_path(self.records_path)
        _validate_json_query_params(self.query_params)
        if self.method not in ("GET", "POST"):
            raise ValueError(f"JsonSourceIR.method must be 'GET' or 'POST', got {self.method!r}.")
        if self.method == "GET" and (self.body_json is not None or self.body_params):
            raise ValueError("JsonSourceIR.body_json requires method='POST'.")
        if self.method == "POST" and self.body_json is None:
            raise ValueError("JsonSourceIR.method='POST' requires a JSON body.")
        if self.method == "POST" and not re.match(r"^https?://", self.path, re.IGNORECASE):
            raise ValueError("JsonSourceIR.method='POST' requires an HTTP(S) path.")
        if self.method == "POST" and self.format != "auto":
            raise ValueError("JsonSourceIR.method='POST' only supports format='auto'.")
        if self.body_json is not None:
            if not isinstance(self.body_json, str):
                raise TypeError("JsonSourceIR.body_json must be str | None.")
            try:
                parsed_body = json.loads(self.body_json)
            except (TypeError, ValueError) as exc:
                raise ValueError("JsonSourceIR.body_json must contain valid JSON.") from exc
            if not isinstance(parsed_body, dict):
                raise ValueError("JsonSourceIR.body_json must contain a JSON object.")
            seen_paths: set[tuple[JsonBodyPathPart, ...]] = set()
            for entry in self.body_params:
                if not isinstance(entry, tuple) or len(entry) != 2:
                    raise TypeError(
                        "JsonSourceIR.body_params entries must be (path, SourceParamIR) tuples."
                    )
                path, param = entry
                if not isinstance(path, tuple) or not path:
                    raise TypeError("JsonSourceIR.body_params paths must be non-empty tuples.")
                if path in seen_paths:
                    raise ValueError(f"JsonSourceIR.body_params contains duplicate path {path!r}.")
                seen_paths.add(path)
                if not isinstance(param, SourceParamIR):
                    raise TypeError("JsonSourceIR.body_params values must be SourceParamIR values.")
                cursor: object = parsed_body
                for part in path:
                    valid_part = (
                        isinstance(part, str) and isinstance(cursor, dict) and part in cursor
                    ) or (
                        isinstance(part, int)
                        and not isinstance(part, bool)
                        and isinstance(cursor, list)
                        and 0 <= part < len(cursor)
                    )
                    if not valid_part:
                        raise ValueError(
                            f"JsonSourceIR.body_params path {path!r} does not exist in body_json."
                        )
                    if isinstance(cursor, list):
                        assert isinstance(part, int) and not isinstance(part, bool)
                        cursor = cursor[part]
                    else:
                        assert isinstance(cursor, dict) and isinstance(part, str)
                        cursor = cursor[part]
                if cursor is not None:
                    raise ValueError(
                        f"JsonSourceIR.body_params path {path!r} must point to a null placeholder."
                    )
        elif self.body_params:
            raise ValueError("JsonSourceIR.body_params require body_json.")
        _require_kind(self.kind, field_name="JsonSourceIR.kind", expected="json")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "schema": dict(self.schema),
            "format": self.format,
            "records_path": self.records_path,
            "query_params": {
                name: value.to_dict() if isinstance(value, SourceParamIR) else value
                for name, value in self.query_params
            },
            "method": self.method,
            "body": json.loads(self.body_json) if self.body_json is not None else None,
            "body_params": [
                {"path": list(path), "name": param.name} for path, param in self.body_params
            ],
        }

    def to_ir(self) -> JsonSourceIR:
        return self


def json_source_param_names(source: JsonSourceIR) -> tuple[str, ...]:
    """Return unique runtime parameter names in request declaration order."""
    declared = [value.name for _, value in source.query_params if isinstance(value, SourceParamIR)]
    declared.extend(param.name for _, param in source.body_params)
    return tuple(dict.fromkeys(declared))


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
