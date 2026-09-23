"""Shared inferred JSON source lowering for DuckDB-backed file and HTTP sources."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from math import isfinite
from typing import cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pyarrow as pa

from marivo.datasource.backends import (
    apply_json_http_settings,
    json_http_headers,
)
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

_POST_TIMEOUT_SECONDS = 30
_MISSING = object()


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


_POST_OPENER = build_opener(_NoRedirectHandler())


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
            item_value = _resolve_query_param_value(item, supplied)
            if isinstance(item_value, Sequence) and not isinstance(
                item_value, str | bytes | bytearray
            ):
                resolved.extend(item_value)
            else:
                resolved.append(cast("QueryParamScalar", item_value))
        return resolved
    return cast("QueryParamScalar", value)


def _validate_source_param_scalar(value: object, *, name: str) -> None:
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
    resolved: list[tuple[str, str | list[str]]] = [(name, value) for name, value in existing]
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


def _request_payload(
    backend: object,
    source: JsonSourceIR,
    supplied: Mapping[str, QueryParamScalar | QueryParamScalarList],
) -> object:
    url = json_source_url(source, supplied)
    headers = {"Accept": "application/json", **json_http_headers(backend, url)}
    body: bytes | None = None
    if source.method == "POST":
        body = _resolved_json_body(source, supplied).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=source.method)
    opener = _POST_OPENER if source.method == "POST" else build_opener()
    with opener.open(request, timeout=_POST_TIMEOUT_SECONDS) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        raw = response.read().decode(encoding)
    try:
        if source.format == "newline_delimited":
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        return json.loads(raw)
    except json.JSONDecodeError:
        if source.format == "auto":
            try:
                return [json.loads(line) for line in raw.splitlines() if line.strip()]
            except json.JSONDecodeError:
                pass
        raise ValueError(
            "JSON source response is not valid JSON for the requested format."
        ) from None


def _path_value(value: object, segments: tuple[tuple[str, object], ...]) -> object:
    current = value
    for kind, arg in segments:
        if kind == "member":
            if not isinstance(current, Mapping) or cast("str", arg) not in current:
                return _MISSING
            current = current[cast("str", arg)]
        elif kind == "index":
            index = cast("int", arg)
            if not isinstance(current, Sequence) or isinstance(current, str | bytes | bytearray):
                return _MISSING
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            raise ValueError("Array traversal is resolved by the JSON row projector.")
    return current


def _records(payload: object, source: JsonSourceIR) -> list[object]:
    if source.records_path is not None:
        path = source.records_path.removeprefix("$").removeprefix(".")
        value = _path_value(payload, _parse_json_path(path))
        if not isinstance(value, list):
            raise ValueError(
                f"md.json records_path {source.records_path!r} did not resolve to an array; "
                "verify the response envelope and API authentication."
            )
        return value
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        return [payload]
    raise ValueError("JSON source records must be an object or array of objects.")


def _project_rows(payload: object, source: JsonSourceIR) -> list[dict[str, object]]:
    records = _records(payload, source)
    if not source.columns:
        if any(not isinstance(record, Mapping) for record in records):
            raise ValueError("JSON records must be objects when no columns projection is supplied.")
        return [dict(cast("Mapping[str, object]", record)) for record in records]

    parsed = [(output, path, _parse_json_path(path)) for output, path in source.columns]
    traversal = next(
        (
            segments
            for _output, _path, segments in parsed
            if any(k == "traverse" for k, _ in segments)
        ),
        None,
    )
    traverse_at = (
        next(i for i, (kind, _arg) in enumerate(traversal) if kind == "traverse")
        if traversal is not None
        else None
    )
    traversal_prefix = () if traversal is None or traverse_at is None else traversal[:traverse_at]
    rows: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("JSON records must be objects when columns are projected.")
        elements: Sequence[object] = (None,)
        if traversal is not None and traverse_at is not None:
            array = _path_value(record, traversal_prefix)
            if array is _MISSING or array is None:
                continue
            if not isinstance(array, list):
                raise ValueError("A traversed JSON field path did not resolve to an array.")
            elements = array
        for element in elements:
            row: dict[str, object] = {}
            for output, _path, segments in parsed:
                if traversal is not None and any(kind == "traverse" for kind, _ in segments):
                    assert traverse_at is not None
                    value = _path_value(element, segments[traverse_at + 1 :])
                else:
                    value = _path_value(record, segments)
                row[output] = None if value is _MISSING else value
            rows.append(row)
    return rows


def _arrow_table(
    backend: object,
    rows: list[dict[str, object]],
    source: JsonSourceIR,
) -> ir.Table:
    table = pa.Table.from_pylist(rows)
    if source.columns:
        for output, _path in source.columns:
            field = table.schema.get_field_index(output)
            if field < 0 or pa.types.is_null(table.schema.field(field).type):
                raise ValueError(
                    f"Cannot infer a physical type for required JSON column {output!r}; "
                    "the field is missing or null in every returned record."
                )
    create_table = getattr(backend, "create_table", None)
    if not callable(create_table):
        raise RuntimeError("HTTP JSON sources require a DuckDB backend with Arrow registration")
    name = "_marivo_json_" + uuid4().hex
    return cast("ir.Table", create_table(name, obj=table, temp=True))


def _apply_path(value: ir.Table | ir.Value, segments: tuple[tuple[str, object], ...]) -> ir.Value:
    for kind, arg in segments:
        if kind == "member":
            member = cast("str", arg)
            if isinstance(value, ir.Table):
                if member not in value.columns:
                    raise ValueError(f"JSON field {member!r} is missing from the inferred source.")
                value = value[member]
            else:
                if not isinstance(value.type(), dt.Struct):
                    raise ValueError(
                        f"JSON field {member!r} does not resolve through an object value."
                    )
                value = value[member]
        elif kind == "index":
            if not isinstance(value.type(), dt.Array):
                raise ValueError(
                    f"JSON field index {arg!r} does not resolve through an array value."
                )
            value = value[cast("int", arg)]
        else:
            raise AssertionError("Array traversal must be expanded before path projection.")
    return value


def _inferred_table(backend: object, source: JsonSourceIR) -> ir.Table:
    reader = getattr(backend, "read_json", None)
    if not callable(reader):
        raise RuntimeError("datasource backend does not expose read_json()")
    options: dict[str, object] = {}
    if source.format != "auto":
        options["format"] = source.format
    table = cast("ir.Table", reader(source.path, **options))
    if source.records_path is None:
        if not source.columns:
            return table
        parsed = [(output, _parse_json_path(path)) for output, path in source.columns]
        traversal = next(
            (segments for _output, segments in parsed if any(k == "traverse" for k, _ in segments)),
            None,
        )
        element_name: str | None = None
        if traversal is not None:
            traverse_at = next(i for i, (kind, _arg) in enumerate(traversal) if kind == "traverse")
            array = _apply_path(table, traversal[:traverse_at])
            if not isinstance(array.type(), dt.Array):
                raise ValueError("A traversed JSON field path did not resolve to an array.")
            element_name = "__marivo_json_element"
            while element_name in table.columns:
                element_name = "_" + element_name
            table = table.select(
                *[table[name] for name in table.columns],
                array.unnest().name(element_name),
            )
        fields: list[ir.Value] = []
        for output, segments in parsed:
            if any(kind == "traverse" for kind, _ in segments):
                assert traversal is not None and element_name is not None
                traverse_at = next(
                    i for i, (kind, _arg) in enumerate(segments) if kind == "traverse"
                )
                value = _apply_path(table[element_name], segments[traverse_at + 1 :])
            else:
                value = _apply_path(table, segments)
            fields.append(value.name(output))
        return table.select(*fields)

    record_path = _parse_json_path(source.records_path.removeprefix("$").removeprefix("."))
    try:
        records_value = _apply_path(table, record_path)
    except ValueError as exc:
        raise ValueError(
            f"md.json records_path {source.records_path!r} did not resolve to an array; "
            "verify the response envelope and API authentication."
        ) from exc
    if not isinstance(records_value.type(), dt.Array):
        raise ValueError(
            f"md.json records_path {source.records_path!r} did not resolve to an array; "
            "verify the response envelope and API authentication."
        )
    record_name = "__marivo_json_record"
    while record_name in table.columns:
        record_name = "_" + record_name
    records = table.select(records_value.unnest().name(record_name))
    record_type = records[record_name].type()
    if not isinstance(record_type, dt.Struct):
        if source.columns:
            output = source.columns[0][0]
            raise ValueError(
                f"Cannot infer a physical type for required JSON column {output!r}; "
                "the field is missing or null in every returned record."
            )
        raise ValueError("JSON records must be objects to infer their columns.")
    if not source.columns:
        return records.select(
            *(records[record_name][name].name(name) for name in record_type.fields)
        )

    parsed = [(output, _parse_json_path(path)) for output, path in source.columns]
    traversal = next(
        (segments for _output, segments in parsed if any(k == "traverse" for k, _ in segments)),
        None,
    )
    records_element_name: str | None = None
    if traversal is not None:
        traverse_at = next(i for i, (kind, _arg) in enumerate(traversal) if kind == "traverse")
        array = _apply_path(records[record_name], traversal[:traverse_at])
        if not isinstance(array.type(), dt.Array):
            raise ValueError("A traversed JSON field path did not resolve to an array.")
        records_element_name = "__marivo_json_element"
        while records_element_name in records.columns:
            records_element_name = "_" + records_element_name
        records = records.select(
            *[records[name] for name in records.columns],
            array.unnest().name(records_element_name),
        )
    record_fields: list[ir.Value] = []
    for output, segments in parsed:
        if any(kind == "traverse" for kind, _ in segments):
            assert traversal is not None and records_element_name is not None
            traverse_at = next(i for i, (kind, _arg) in enumerate(segments) if kind == "traverse")
            value = _apply_path(records[records_element_name], segments[traverse_at + 1 :])
        else:
            value = _apply_path(records[record_name], segments)
        record_fields.append(value.name(output))
    return records.select(*record_fields)


def read_json_source(
    backend: object,
    source: JsonSourceIR,
    *,
    source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
) -> ir.Table:
    """Read JSON once and infer projected physical types from returned values."""
    apply_json_http_settings(backend, source)
    supplied = normalize_json_source_params(source, source_params)
    if source.path.lower().startswith(("http://", "https://")):
        payload = _request_payload(backend, source, supplied)
        return _arrow_table(backend, _project_rows(payload, source), source)
    return _inferred_table(backend, source)
