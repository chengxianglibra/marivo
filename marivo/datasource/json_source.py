"""Shared JSON source lowering for DuckDB-backed file sources."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.datasource.backends import apply_json_http_settings, json_http_headers
from marivo.datasource.ir import (
    JsonQueryParamValue,
    JsonSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
    SourceParamIR,
    _parse_json_path,
    json_body_to_string,
    json_source_param_names,
)


def _duckdb_error_signature(message: str) -> str:
    raise NotImplementedError


def _duckdb_json_type_signature(value: dt.JSON) -> str:
    raise NotImplementedError


# Ibis' builtin decorator is untyped; these casts retain the concrete expression
# signatures while the Python bodies remain declaration-only.
_duckdb_error = cast(
    "Callable[[ir.StringValue], ir.StringValue]",
    ibis.udf.scalar.builtin(name="error")(_duckdb_error_signature),
)
_duckdb_json_type = cast(
    "Callable[[ir.JSONValue], ir.StringValue]",
    ibis.udf.scalar.builtin(name="json_type")(_duckdb_json_type_signature),
)


def _duckdb_json_object_signature(key: str, value: dt.JSON) -> dt.JSON:
    raise NotImplementedError


_duckdb_json_object = cast(
    "Callable[[ir.StringValue, ir.JSONValue], ir.JSONValue]",
    ibis.udf.scalar.builtin(
        name="json_object",
        signature=((dt.string, dt.JSON), dt.JSON),
    )(_duckdb_json_object_signature),
)

_POST_TIMEOUT_SECONDS = 30


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_POST_OPENER = build_opener(_NoRedirectHandler())


def _post_json_envelope(
    backend: object,
    url: str,
    body_json: str,
) -> ir.Table:
    connection = getattr(backend, "con", None)
    create_function = getattr(connection, "create_function", None)
    sql = getattr(backend, "sql", None)
    if not callable(create_function) or not callable(sql):
        raise RuntimeError("POST JSON sources require a DuckDB backend")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **json_http_headers(backend, url),
    }

    def fetch(_marker: int) -> str:
        request = Request(
            url,
            data=body_json.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with _POST_OPENER.open(request, timeout=_POST_TIMEOUT_SECONDS) as response:
            encoding = response.headers.get_content_charset() or "utf-8"
            return cast("bytes", response.read()).decode(encoding)

    function_name = f"marivo_http_post_json_{uuid4().hex}"
    create_function(function_name, fetch, ["BIGINT"], "VARCHAR", side_effects=True)

    def signature(marker: int) -> str:
        raise NotImplementedError

    post_json = cast(
        "Callable[[ir.IntegerValue], ir.StringValue]",
        ibis.udf.scalar.builtin(name=function_name)(signature),
    )
    marker_table = cast("ir.Table", sql("SELECT 1::BIGINT AS __marivo_http_marker"))
    response_column = "__marivo_json_response"
    return marker_table.select(
        post_json(marker_table["__marivo_http_marker"]).cast(dt.json).name(response_column)
    )


def _has_traverse(segments: tuple[tuple[str, object], ...]) -> bool:
    return any(kind == "traverse" for kind, _ in segments)


def _apply_json_path(
    value: ir.JSONValue,
    segments: tuple[tuple[str, object], ...],
) -> ir.JSONValue:
    """Index a JSON value through member/index segments (no traverse)."""
    for kind, arg in segments:
        if kind == "member":
            value = value[cast("str", arg)]
        elif kind == "index":
            value = value[cast("int", arg)]
        else:
            raise AssertionError("traverse segment must be expanded before projection")
    return value


def _project_json_field(
    backend: object,
    value: ir.JSONValue,
    *,
    name: str,
    type_name: str,
    safe_name: str,
) -> ir.Value:
    field_schema = ((safe_name, type_name),)
    field_type = dt.Struct.from_tuples(field_schema)
    field_object = _duckdb_json_object(ibis.literal(safe_name), value)
    field_structure = ibis.literal(_duckdb_json_transform_structure(backend, field_schema))
    return _duckdb_json_transform_strict(field_type)(field_object, field_structure)[safe_name].name(
        name
    )


def _unpack_json_records(
    backend: object,
    envelope: ir.Table,
    records_json: Any,
    source: JsonSourceIR,
) -> ir.Table:
    path_label = source.records_path or "$"
    invalid_path = _duckdb_error(
        ibis.literal(
            f"md.json records_path {path_label!r} did not resolve to an array; "
            "verify the response envelope and API authentication"
        )
    ).cast(dt.json)
    records_json = (_duckdb_json_type(records_json) == "ARRAY").ifelse(records_json, invalid_path)
    record_column = "__marivo_json_record"
    while record_column in dict(source.schema):
        record_column = f"_{record_column}"
    records = envelope.select(
        records_json.unwrap_as(dt.Array(dt.json)).unnest().name(record_column)
    )

    declared_paths = dict(source.field_paths)
    parsed = [
        (
            name,
            type_name,
            _parse_json_path(declared_paths[name])
            if name in declared_paths
            else (("member", name),),
        )
        for name, type_name in source.schema
    ]

    # All traversal fields share one validated array prefix, so unnest once and
    # project every sibling field from the same element.
    table = records
    record_json = table[record_column]
    traversal_segments = next(
        (segments for _name, _type_name, segments in parsed if _has_traverse(segments)),
        None,
    )
    elem_column: str | None = None
    if traversal_segments is not None:
        traverse_at = next(
            index for index, (kind, _arg) in enumerate(traversal_segments) if kind == "traverse"
        )
        prefix = traversal_segments[:traverse_at]
        elem_column = "__marivo_traverse"
        while elem_column in dict(source.schema) or elem_column in table.columns:
            elem_column = f"_{elem_column}"
        array_json = _apply_json_path(record_json, prefix)
        unnest_column = array_json.unwrap_as(dt.Array(dt.json)).unnest().name(elem_column)
        existing = [table[column] for column in table.columns]
        table = table.select(*existing, unnest_column)
        record_json = table[record_column]

    typed_columns = []
    for idx, (name, type_name, segments) in enumerate(parsed):
        if _has_traverse(segments):
            assert elem_column is not None
            traverse_at = next(
                index for index, (kind, _arg) in enumerate(segments) if kind == "traverse"
            )
            suffix = segments[traverse_at + 1 :]
            value = _apply_json_path(table[elem_column], suffix)
        else:
            value = _apply_json_path(table[record_column], segments)
        typed_columns.append(
            _project_json_field(
                backend,
                value,
                name=name,
                type_name=type_name,
                safe_name=f"__marivo_field_{idx}",
            )
        )
    return table.select(*typed_columns)


def _duckdb_json_type_name(backend: object, value: dt.DataType) -> str:
    compiler = getattr(backend, "compiler", None)
    type_mapper = getattr(compiler, "type_mapper", None)
    from_ibis = getattr(type_mapper, "from_ibis", None)
    if not callable(from_ibis):
        raise RuntimeError("DuckDB backend does not expose an Ibis type mapper")
    return str(from_ibis(value))


def _duckdb_json_transform_type(backend: object, value: dt.DataType) -> str:
    if isinstance(value, dt.Array):
        return f"{_duckdb_json_transform_type(backend, value.value_type)}[]"
    if isinstance(value, dt.Map):
        key_type = _duckdb_json_transform_type(backend, value.key_type)
        value_type = _duckdb_json_transform_type(backend, value.value_type)
        return f"MAP({key_type}, {value_type})"
    if isinstance(value, dt.Struct):
        fields = ", ".join(
            f'"{name.replace(chr(34), chr(34) * 2)}" '
            f"{_duckdb_json_transform_type(backend, field_type)}"
            for name, field_type in value.fields.items()
        )
        return f"STRUCT({fields})"
    return _duckdb_json_type_name(backend, value)


def _duckdb_json_transform_structure(
    backend: object,
    schema: tuple[tuple[str, str], ...],
) -> str:
    return json.dumps(
        {
            name: _duckdb_json_transform_type(backend, dt.dtype(type_name))
            for name, type_name in schema
        },
        separators=(",", ":"),
    )


def _duckdb_json_transform_signature(
    record: dt.JSON,
    structure: str,
) -> dt.Struct:
    raise NotImplementedError


def _duckdb_json_transform_strict(
    record_type: dt.Struct,
) -> Callable[[ir.JSONValue, ir.StringValue], ir.StructValue]:
    return cast(
        "Callable[[ir.JSONValue, ir.StringValue], ir.StructValue]",
        ibis.udf.scalar.builtin(
            name="json_transform_strict",
            signature=((dt.JSON, dt.string), record_type),
        )(_duckdb_json_transform_signature),
    )


def _query_scalar(value: QueryParamScalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON source query parameter floats must be finite.")
    return str(value)


def _query_value(value: QueryParamScalar | QueryParamScalarList) -> str | list[str]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_query_scalar(item) for item in value]
    return _query_scalar(cast("QueryParamScalar", value))


def _resolve_query_param_value(
    value: JsonQueryParamValue,
    supplied: Mapping[str, QueryParamScalar | QueryParamScalarList],
) -> QueryParamScalar | QueryParamScalarList:
    if isinstance(value, SourceParamIR):
        return supplied[value.name]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        resolved: list[QueryParamScalar] = []
        for item in value:
            resolved_value = _resolve_query_param_value(item, supplied)
            if isinstance(resolved_value, Sequence) and not isinstance(
                resolved_value, str | bytes | bytearray
            ):
                resolved.extend(resolved_value)
            else:
                resolved.append(cast("QueryParamScalar", resolved_value))
        return resolved
    return cast("QueryParamScalar", value)


def _resolved_json_body(
    source: JsonSourceIR,
    supplied: Mapping[str, QueryParamScalar | QueryParamScalarList],
) -> str:
    if source.body_json is None:
        raise RuntimeError("POST JSON source has no request body")
    body = json.loads(source.body_json)
    for path, param in source.body_params:
        cursor = body
        for part in path[:-1]:
            cursor = cursor[part]
        cursor[path[-1]] = supplied[param.name]
    return json_body_to_string(body)


def _validate_source_param_scalar(value: object, *, name: str) -> None:
    """Validate one runtime source parameter scalar (not a list)."""
    if isinstance(value, str | int | bool):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"JSON source parameter {name!r} must be a finite float.")
        return
    raise TypeError(
        f"JSON source parameter {name!r} must be str, int, float, or bool, or a flat list of these."
    )


def _validate_source_param_value(value: object, *, name: str) -> None:
    """Validate one runtime source parameter value: a scalar or a flat, non-empty scalar list."""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        if not value:
            raise ValueError(f"JSON source parameter {name!r} must not be an empty list.")
        for element in value:
            if isinstance(element, Sequence) and not isinstance(element, str | bytes | bytearray):
                raise TypeError(
                    f"JSON source parameter {name!r} list values must be flat (no nested lists)."
                )
            _validate_source_param_scalar(element, name=name)
        return
    _validate_source_param_scalar(value, name=name)


def json_source_url(
    source: JsonSourceIR,
    source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
) -> str:
    """Resolve one JSON source URL from fixed and required query parameters."""
    supplied = normalize_json_source_params(source, source_params)

    parts = urlsplit(source.path)
    existing = parse_qsl(parts.query, keep_blank_values=True)
    existing_names = {name for name, _ in existing}
    declared_names = {name for name, _ in source.query_params}
    duplicates = tuple(sorted(existing_names & declared_names))
    if duplicates:
        raise ValueError(
            "JSON source query parameters must not be declared both in path and "
            f"query_params: {duplicates!r}."
        )
    resolved: list[tuple[str, str | list[str]]] = [(name, val) for name, val in existing]
    for name, value in source.query_params:
        actual = _resolve_query_param_value(value, supplied)
        resolved.append((name, _query_value(actual)))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(cast("list[tuple[str, str]]", resolved), doseq=True),
            parts.fragment,
        )
    )


def normalize_json_source_params(
    source: JsonSourceIR,
    source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None,
) -> dict[str, QueryParamScalar | QueryParamScalarList]:
    """Validate and declaration-order one JSON source runtime binding."""
    if source_params is not None and not isinstance(source_params, Mapping):
        raise TypeError("JSON source parameters must be a mapping.")
    supplied = dict(source_params or {})
    for name, value in supplied.items():
        if not isinstance(name, str):
            raise TypeError("JSON source parameter names must be strings.")
        _validate_source_param_value(value, name=name)
    ordered_required = json_source_param_names(source)
    required = set(ordered_required)
    missing = tuple(sorted(required - supplied.keys()))
    extra = tuple(sorted(supplied.keys() - required))
    if missing or extra:
        raise ValueError(
            f"JSON source parameter binding mismatch: missing={missing!r}, extra={extra!r}."
        )
    return {name: supplied[name] for name in ordered_required}


def read_json_source(
    backend: object,
    source: JsonSourceIR,
    *,
    source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
) -> ir.Table:
    """Read a typed JSON source and optionally unpack a wrapped record array."""
    apply_json_http_settings(backend, source)
    supplied = normalize_json_source_params(source, source_params)
    url = json_source_url(source, supplied)

    if source.method == "POST":
        envelope = _post_json_envelope(backend, url, _resolved_json_body(source, supplied))
        records_json: Any = envelope["__marivo_json_response"]
        if source.records_path is not None:
            for part in source.records_path.removeprefix("$.").split("."):
                records_json = records_json[part]
        return _unpack_json_records(backend, envelope, records_json, source)

    reader = getattr(backend, "read_json", None)
    if not callable(reader):
        raise RuntimeError("datasource backend does not expose read_json()")
    options: dict[str, object] = {}
    if source.format != "auto":
        options["format"] = source.format
    if source.records_path is None:
        options["columns"] = dict(source.schema)
        return cast("ir.Table", reader(url, **options))

    path_parts = source.records_path.removeprefix("$.").split(".")
    options["columns"] = {path_parts[0]: "json"}
    envelope = cast("ir.Table", reader(url, **options))
    records_json = envelope[path_parts[0]]
    for part in path_parts[1:]:
        records_json = records_json[part]
    return _unpack_json_records(backend, envelope, records_json, source)
