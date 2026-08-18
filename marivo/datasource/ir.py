"""Intermediate representation for project-level datasources."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import PurePosixPath
from typing import Any, Literal, TypeAlias

import ibis

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
    "QueryParamScalarList",
    "SourceParamIR",
    "TableColumnBindingIR",
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

QueryParamScalar: TypeAlias = str | int | float | bool
QueryParamScalarList: TypeAlias = Sequence[QueryParamScalar]


def _format_database_identity(database: str | tuple[str, ...] | None) -> str:
    """Render a table database identity without collapsing distinct source forms."""
    if database is None:
        return "unspecified (datasource default)"
    if isinstance(database, tuple):
        return f"segments={database!r}"
    return f"name={database!r}"


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


JsonQueryParamValue: TypeAlias = (
    QueryParamScalar | SourceParamIR | Sequence[QueryParamScalar | SourceParamIR]
)
JsonBodyPathPart: TypeAlias = str | int
JsonBodyParam: TypeAlias = tuple[tuple[JsonBodyPathPart, ...], SourceParamIR]
JsonBodyValue: TypeAlias = (
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


def _validate_json_query_param_value(item: object, *, name: str) -> None:
    """Validate one query parameter value: scalar, source param, or a flat list of them."""
    if isinstance(item, SourceParamIR):
        return
    if isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
        if not item:
            raise ValueError(
                f"JsonSourceIR.query_params value for {name!r} must not be an empty list."
            )
        for element in item:
            if not isinstance(element, str | int | float | bool | SourceParamIR):
                raise TypeError(
                    "JsonSourceIR.query_params list values must contain str, int, float, "
                    f"bool, or SourceParamIR; got {type(element).__name__} for {name!r}."
                )
            if isinstance(element, float) and not isfinite(element):
                raise ValueError(
                    f"JsonSourceIR.query_params value for {name!r} must be a finite float."
                )
        return
    if isinstance(item, str | int | bool):
        return
    if isinstance(item, float):
        if not isfinite(item):
            raise ValueError(
                f"JsonSourceIR.query_params value for {name!r} must be a finite float."
            )
        return
    raise TypeError(
        "JsonSourceIR.query_params values must be str, int, float, bool, SourceParamIR, "
        f"or a list of these; got {type(item).__name__} for {name!r}."
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
        _validate_json_query_param_value(item, name=name)


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


def _validate_ibis_schema_types(
    schema: tuple[tuple[str, str], ...],
    field_name: str,
) -> None:
    for _name, type_name in schema:
        try:
            ibis.dtype(type_name)
        except (TypeError, ValueError, RuntimeError):
            raise ValueError(
                f"{field_name} type name {type_name!r} must be a valid Ibis type string "
                "(e.g. 'int64', 'string', 'float64'), not a SQL/DuckDB name such as "
                "'BIGINT'."
            ) from None


_JSON_PATH_MEMBER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _parse_json_path(name: str) -> tuple[tuple[str, object], ...]:
    """Parse one JSON field path into member/index/traverse segments.

    A column path selects a nested value relative to each record object:
    ``a`` (top-level member), ``a.b`` (nested object member), ``a[0].b``
    (array index), or ``a[].b`` (array traversal, expanding one row per element).
    """
    segments: list[tuple[str, object]] = []
    for part in name.split("."):
        if not part:
            raise ValueError(f"JSON field path {name!r} must not contain empty segments.")
        match = _JSON_PATH_MEMBER.match(part)
        if match is None:
            raise ValueError(
                f"JSON field path {name!r} segment {part!r} must start with a field name."
            )
        segments.append(("member", match.group(0)))
        rest = part[match.end() :]
        while rest:
            if not rest.startswith("["):
                raise ValueError(
                    f"JSON field path {name!r} has invalid syntax in segment {part!r}."
                )
            close = rest.find("]")
            if close == -1:
                raise ValueError(f"JSON field path {name!r} has an unclosed '['.")
            inner = rest[1:close]
            if inner == "":
                segments.append(("traverse", None))
            elif inner.isdigit():
                segments.append(("index", int(inner)))
            else:
                raise ValueError(f"JSON field path {name!r} has an invalid index {inner!r}.")
            rest = rest[close + 1 :]
    if not segments:
        raise ValueError("JSON field path must not be empty.")
    return tuple(segments)


def _validate_json_field_paths(
    schema: tuple[tuple[str, str], ...],
    field_paths: object,
) -> None:
    if not isinstance(field_paths, tuple):
        raise TypeError(
            "JsonSourceIR.field_paths must be tuple[tuple[str, str], ...], "
            f"got {type(field_paths).__name__}."
        )

    schema_names = {name for name, _ in schema}
    seen: set[str] = set()
    traversal_prefix: tuple[tuple[str, object], ...] | None = None
    for entry in field_paths:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise TypeError("JsonSourceIR.field_paths entries must be (output, path) tuples.")
        raw_output_name, raw_path = entry
        output_name = _require_non_empty_str(
            raw_output_name, "JsonSourceIR.field_paths output name"
        )
        path = _require_non_empty_str(raw_path, "JsonSourceIR.field_paths path")
        if output_name not in schema_names:
            raise ValueError(
                f"JsonSourceIR.field_paths output {output_name!r} is not declared in schema."
            )
        if output_name in seen:
            raise ValueError(f"JsonSourceIR.field_paths contains duplicate output {output_name!r}.")
        seen.add(output_name)

        segments = _parse_json_path(path)
        traversal_positions = [
            index for index, (kind, _arg) in enumerate(segments) if kind == "traverse"
        ]
        if len(traversal_positions) > 1:
            raise ValueError(
                f"JsonSourceIR.field_paths path {path!r} contains more than one array traversal."
            )
        if not traversal_positions:
            continue
        prefix = segments[: traversal_positions[0]]
        if traversal_prefix is None:
            traversal_prefix = prefix
        elif traversal_prefix != prefix:
            raise ValueError(
                "JsonSourceIR.field_paths may traverse only one shared array path; "
                f"got both {_format_json_path(traversal_prefix)!r} and "
                f"{_format_json_path(prefix)!r}."
            )


def _format_json_path(segments: tuple[tuple[str, object], ...]) -> str:
    rendered = ""
    for kind, arg in segments:
        if kind == "member":
            rendered += ("." if rendered else "") + str(arg)
        elif kind == "index":
            rendered += f"[{arg}]"
        else:
            rendered += "[]"
    return rendered


def _require_no_nul(value: str, field_name: str) -> None:
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL.")


@dataclass(frozen=True)
class TableColumnBindingIR:
    """One typed physical identifier in a projected table source."""

    source: str
    data_type: str

    def __post_init__(self) -> None:
        source = _require_non_empty_str(self.source, "TableColumnBindingIR.source")
        _require_no_nul(source, "TableColumnBindingIR.source")
        data_type = _require_non_empty_str(
            self.data_type,
            "TableColumnBindingIR.data_type",
        )
        _require_no_nul(data_type, "TableColumnBindingIR.data_type")
        try:
            canonical_data_type = str(ibis.dtype(data_type))
        except (TypeError, ValueError, RuntimeError):
            raise ValueError(
                "TableColumnBindingIR.data_type must be a valid Ibis type string, "
                f"got {data_type!r}."
            ) from None
        object.__setattr__(self, "data_type", canonical_data_type)

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "data_type": self.data_type}


def _normalize_table_column_bindings(
    value: object,
) -> tuple[tuple[str, TableColumnBindingIR], ...]:
    if not isinstance(value, tuple):
        raise TypeError(
            "TableSourceIR.columns must be "
            "tuple[tuple[str, TableColumnBindingIR], ...], "
            f"got {type(value).__name__}."
        )

    normalized: list[tuple[str, TableColumnBindingIR]] = []
    output_names: set[str] = set()
    physical_sources: set[str] = set()
    for entry in value:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise TypeError(
                "TableSourceIR.columns entries must be tuple[str, TableColumnBindingIR]."
            )
        output_name, binding = entry
        output_name = _require_non_empty_str(
            output_name,
            "TableSourceIR.columns output name",
        )
        _require_no_nul(output_name, "TableSourceIR.columns output name")
        if type(binding) is not TableColumnBindingIR:
            raise TypeError(
                "TableSourceIR.columns values must be TableColumnBindingIR, "
                f"got {type(binding).__name__} for output {output_name!r}."
            )
        if output_name in output_names:
            raise ValueError(
                f"TableSourceIR.columns contains duplicate output name {output_name!r}."
            )
        if binding.source in physical_sources:
            raise ValueError(
                f"TableSourceIR.columns contains duplicate physical source {binding.source!r}."
            )
        output_names.add(output_name)
        physical_sources.add(binding.source)
        normalized.append((output_name, binding))
    return tuple(sorted(normalized, key=lambda item: item[0]))


@dataclass(frozen=True)
class TableSourceIR:
    """Physical table source for a dataset."""

    table: str
    database: str | tuple[str, ...] | None = None
    columns: tuple[tuple[str, TableColumnBindingIR], ...] = ()
    kind: Literal["table"] = "table"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.table, "TableSourceIR.table")
        _validate_database(self.database)
        object.__setattr__(self, "columns", _normalize_table_column_bindings(self.columns))
        _require_kind(self.kind, field_name="TableSourceIR.kind", expected="table")

    def to_dict(self) -> dict[str, object]:
        database: str | list[str] | None = (
            list(self.database) if isinstance(self.database, tuple) else self.database
        )
        result: dict[str, object] = {
            "kind": self.kind,
            "table": self.table,
            "database": database,
        }
        if self.columns:
            result["columns"] = {
                output_name: binding.to_dict() for output_name, binding in self.columns
            }
        return result

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
    field_paths: tuple[tuple[str, str], ...] = ()
    query_params: tuple[tuple[str, JsonQueryParamValue], ...] = ()
    method: Literal["GET", "POST"] = "GET"
    body_json: str | None = None
    body_params: tuple[JsonBodyParam, ...] = ()
    kind: Literal["json"] = "json"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.path, "JsonSourceIR.path")
        _validate_schema(self.schema, "JsonSourceIR.schema")
        _validate_ibis_schema_types(self.schema, "JsonSourceIR.schema")
        _validate_json_field_paths(self.schema, self.field_paths)
        paths_by_output = dict(self.field_paths)
        object.__setattr__(
            self,
            "field_paths",
            tuple(
                (output_name, paths_by_output[output_name])
                for output_name, _type_name in self.schema
                if output_name in paths_by_output
            ),
        )
        if self.field_paths and self.records_path is None:
            raise ValueError("JsonSourceIR.field_paths requires records_path.")
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
        result: dict[str, object] = {
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
        if self.field_paths:
            result["field_paths"] = dict(self.field_paths)
        return result

    def to_ir(self) -> JsonSourceIR:
        return self


def _iter_source_param_names(value: object) -> Iterator[str]:
    if isinstance(value, SourceParamIR):
        yield value.name
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for element in value:
            yield from _iter_source_param_names(element)


def json_source_param_names(source: JsonSourceIR) -> tuple[str, ...]:
    """Return unique runtime parameter names in request declaration order."""
    declared: list[str] = []
    for _, value in source.query_params:
        declared.extend(_iter_source_param_names(value))
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
        return source.to_dict()
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
