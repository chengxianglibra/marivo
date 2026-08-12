"""Shared JSON source lowering for DuckDB-backed file sources."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
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
    JsonSourceIR,
    QueryParamScalar,
    SourceParamIR,
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


def _unpack_json_records(
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
    record_type = dt.Struct.from_tuples(source.schema)
    records = records_json.unwrap_as(dt.Array(record_type))
    record_column = "__marivo_json_record"
    while record_column in dict(source.schema):
        record_column = f"_{record_column}"
    return envelope.select(records.unnest().name(record_column)).unpack(record_column)


def _query_value(value: QueryParamScalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON source query parameter floats must be finite.")
    return str(value)


def _resolved_json_body(
    source: JsonSourceIR,
    supplied: Mapping[str, QueryParamScalar],
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


def json_source_url(
    source: JsonSourceIR,
    source_params: Mapping[str, QueryParamScalar] | None = None,
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
    resolved = [*existing]
    for name, value in source.query_params:
        actual = supplied[value.name] if isinstance(value, SourceParamIR) else value
        resolved.append((name, _query_value(actual)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(resolved), parts.fragment))


def normalize_json_source_params(
    source: JsonSourceIR,
    source_params: Mapping[str, QueryParamScalar] | None,
) -> dict[str, QueryParamScalar]:
    """Validate and declaration-order one JSON source runtime binding."""
    if source_params is not None and not isinstance(source_params, Mapping):
        raise TypeError("JSON source parameters must be a mapping.")
    supplied = dict(source_params or {})
    for name, value in supplied.items():
        if not isinstance(name, str):
            raise TypeError("JSON source parameter names must be strings.")
        if not isinstance(value, str | int | float | bool):
            raise TypeError(f"JSON source parameter {name!r} must be str, int, float, or bool.")
        if isinstance(value, float) and not isfinite(value):
            raise ValueError(f"JSON source parameter {name!r} must be a finite float.")
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
    source_params: Mapping[str, QueryParamScalar] | None = None,
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
        return _unpack_json_records(envelope, records_json, source)

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
    return _unpack_json_records(envelope, records_json, source)
