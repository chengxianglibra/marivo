"""Closed HTTP route declarations and generated OpenAPI 3.1 schemas."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

from marivo import __version__
from marivo._temporal import InstantAnchor
from marivo.analysis.core import frame_interpretation as frame
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_schema import CoordinateExportColumn, FieldExportColumn, FrameSchema
from marivo.analysis.core.frame_source_reads import FrameSourceRead
from marivo.analysis.core.lineage import FrameLineage
from marivo.analysis.session._lazy_read_model import SessionSummary
from marivo.datasource.ir import AiContextIR
from marivo.refs import SemanticKind
from marivo.semantic import catalog_entries as entries
from marivo.semantic import ir as semantic
from marivo.server.catalog_projection import DEFINITION_FIELDS
from marivo.server.frame_projection import _FIELDS
from marivo.server.wire import API_VERSION, STATUS, JsonObject, JsonValue

KINDS = tuple(kind.value for kind in SemanticKind)


@dataclass(frozen=True)
class Route:
    name: str
    path: str
    response: str
    summary: str
    query: tuple[str, ...] = ()
    method: Literal["GET", "POST"] = "GET"
    description: str = ""

    def match(self, path: str) -> dict[str, str] | None:
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", self.path)
        match = re.fullmatch(pattern, path)
        return None if match is None else match.groupdict()


_PAGE = ("limit", "cursor")
ROUTES = (
    Route("info", "/v2/info", "Info", "Read service, Store and Catalog status"),
    Route("openapi", "/v2/openapi.json", "OpenAPI", "Read the HTTP protocol schema"),
    Route(
        "objects",
        "/v2/semantic/objects",
        "ObjectDirectory",
        "List current semantic objects",
        ("kind", "domain", *_PAGE),
    ),
    Route(
        "object",
        "/v2/semantic/objects/{id}",
        "SemanticObject",
        "Read a current semantic definition",
    ),
    Route(
        "related",
        "/v2/semantic/objects/{id}/related",
        "RelatedDirectory",
        "List semantic dependencies or dependents",
        ("relation", *_PAGE),
    ),
    Route(
        "reload",
        "/v2/semantic/reload",
        "Reload",
        "Reload the semantic Catalog",
        method="POST",
        description=(
            "Send an empty body. Start a new Catalog load; an active load returns "
            "409 catalog_load_in_progress. The service owns the load, which continues "
            "after a client disconnect until its deadline. Reload does not change saved results."
        ),
    ),
    Route("sessions", "/v2/sessions", "SessionDirectory", "List saved Sessions", _PAGE),
    Route(
        "frames", "/v2/sessions/{session_id}/frames", "FrameDirectory", "List saved Frames", _PAGE
    ),
    Route(
        "frame",
        "/v2/stores/{store_id}/sessions/{session_id}/frames/{artifact_id}",
        "FrameReport",
        "Read one exact saved AnalysisFrame result",
    ),
    Route(
        "dataframe",
        "/v2/dataframes/{result_id}",
        "DataFrameReport",
        "Read one exact saved DataFrame result",
    ),
)

STRING: JsonObject = {"type": "string"}
INT: JsonObject = {"type": "integer"}
BOOL: JsonObject = {"type": "boolean"}
NULL: JsonObject = {"type": "null"}
NUMBER: JsonObject = {"type": "number"}
DATETIME: JsonObject = {"type": "string", "format": "date-time"}
TICKS: JsonObject = {"type": "string", "pattern": r"^-?[0-9]+$"}
DECIMAL: JsonObject = {"type": "string", "pattern": r"^-?[0-9]+(?:\.[0-9]+)?$"}
UNITS: JsonObject = {"enum": ["s", "ms", "us", "ns"]}


def ref(name: str) -> JsonObject:
    return {"$ref": "#/components/schemas/" + name}


def arr(item: JsonObject) -> JsonObject:
    return {"type": "array", "items": item}


def mapping(item: JsonObject) -> JsonObject:
    return {"type": "object", "additionalProperties": item}


def either(*variants: JsonObject) -> JsonObject:
    return {"anyOf": list(variants)}


def nullable(item: JsonObject) -> JsonObject:
    return either(item, NULL)


def literal(value: str) -> JsonObject:
    return {"type": "string", "const": value}


def record(**fields: JsonObject) -> JsonObject:
    return {
        "type": "object",
        "properties": dict(fields.items()),
        "required": list(fields),
        "additionalProperties": False,
    }


_NESTED: dict[type[object], str] = {
    **_FIELDS,
    **DEFINITION_FIELDS,
    frame.FrameFixedAxis: "axis key origins",
    AiContextIR: "business_definition guardrails aliases",
    entries.DependencyContext: "id business_definition guardrails",
    entries.MetricTimeFieldBinding: "branch source field path anchor",
    entries.MetricNoTimeBinding: "branch source root reason",
    semantic.SampleIntervalIR: "count unit",
    semantic.DateParse: "kind timezone",
    semantic.TimestampParse: "kind timezone",
    semantic.StrptimeParse: "kind format timezone",
    semantic.HourPrefixParse: "kind prefix",
    semantic.SnapshotVersioningIR: "kind partition_field timezone",
    semantic.ValidityVersioningIR: "kind valid_from valid_to interval open_end",
}


def _annotation(owner: type[object], name: str) -> object:
    annotation = get_type_hints(owner).get(name)
    if annotation is None:
        getter = getattr(owner, name).fget
        annotation = get_type_hints(getter)["return"]
    return annotation


def _type(annotation: object) -> JsonObject:
    if isinstance(annotation, type) and annotation in _NESTED:
        return ref(annotation.__name__)
    if annotation is frame.FrameAxisSchema:
        return either(
            *(
                ref(variant.__name__)
                for variant in (
                    frame.FrameOrdinaryAxisSchema,
                    frame.FrameCalendarAxisSchema,
                    frame.FrameIntervalAxisSchema,
                    frame.FramePairedAxisSchema,
                )
            )
        )
    if annotation in (ScalarDataType, DecimalDataType, TimestampDataType, DurationDataType):
        return ref(annotation.__name__)
    if annotation in (datetime, InstantAnchor):
        return DATETIME
    if annotation is date:
        return {"type": "string", "format": "date"}
    if annotation is timedelta:
        return record(unit=literal("us"), ticks=TICKS)
    if annotation is str:
        return STRING
    if annotation is int:
        return INT
    if annotation is bool:
        return BOOL
    if annotation is float:
        return NUMBER
    if annotation is type(None):
        return NULL
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        return either(*(_type(arg) for arg in args))
    if origin is Literal:
        return {"enum": list(args)}
    if origin is Mapping and args[0] is str:
        return mapping(_type(args[1]))
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return arr(_type(args[0]))
        return {
            "type": "array",
            "prefixItems": [_type(arg) for arg in args],
            "minItems": len(args),
            "maxItems": len(args),
        }
    raise TypeError(f"Missing v2 schema for {annotation!r}")


def _nested() -> dict[str, JsonObject]:
    result = {}
    roles: dict[type[object], JsonObject] = {
        frame.FrameOrdinaryAxisSchema: {"enum": ["field", "identity"]},
        frame.FrameCalendarAxisSchema: literal("calendar"),
        frame.FrameIntervalAxisSchema: literal("interval"),
        frame.FramePairedAxisSchema: literal("paired"),
    }
    for owner, names in _NESTED.items():
        fields: dict[str, JsonObject] = {}
        for name in names.split():
            if owner is frame.FrameField and name == "dtype":
                fields[name] = _type(DataType)
            elif owner is frame.FrameFixedAxis and name == "key":
                fields[name] = arr(ref("ScalarValue"))
            elif name == "role" and owner in roles:
                fields[name] = roles[owner]
            else:
                fields[name] = _type(_annotation(owner, name))
        if owner is entries.EntityDefinition:
            fields["source"] = either(
                record(
                    kind=literal("table"),
                    table=nullable(STRING),
                    database=either(STRING, arr(STRING), NULL),
                ),
                record(kind={"enum": ["csv", "parquet", "json"]}),
            )
        result[owner.__name__] = record(**fields)
    return result


def _directory(item: str, **extra: JsonObject) -> JsonObject:
    return record(
        items=arr(ref(item)), limit=INT, has_more=BOOL, next_cursor=nullable(STRING), **extra
    )


def _schemas() -> dict[str, JsonObject]:
    layout = arr(either(ref(CoordinateExportColumn.__name__), ref(FieldExportColumn.__name__)))
    scalar_kinds = ("boolean", "string", "int64", "float64", "date")
    scalar_values = (BOOL, STRING, TICKS, either(NUMBER, ref("NonFiniteFloat")), _type(date))
    types = {
        "ScalarDataType": record(kind={"enum": list(scalar_kinds)}),
        "DecimalDataType": record(kind=literal("decimal"), precision=INT, scale=INT),
        "TimestampDataType": record(
            kind=literal("timestamp"), unit=UNITS, timezone=nullable(STRING)
        ),
        "DurationDataType": record(kind=literal("duration"), unit=UNITS),
    }
    typed_scalar = either(
        *(
            record(dtype=record(kind=literal(kind)), value=nullable(value))
            for kind, value in zip(scalar_kinds, scalar_values, strict=True)
        ),
        record(dtype=ref("DecimalDataType"), value=nullable(DECIMAL)),
        record(dtype=ref("TimestampDataType"), value=nullable(STRING)),
        record(dtype=ref("DurationDataType"), value=nullable(TICKS)),
    )
    key = arr(ref("TypedScalar"))
    endpoint = either(ref("CalendarPeriod"), ref("IntervalPeriod"))
    summary = {
        "id": STRING,
        "path": STRING,
        "name": STRING,
        "domain": nullable(STRING),
        "business_definition": nullable(STRING),
    }
    definitions = {
        "datasource": entries.DatasourceDefinition,
        "domain": entries.DomainDefinition,
        "entity": entries.EntityDefinition,
        "metric": entries.MetricDefinition,
        "relationship": entries.RelationshipDefinition,
        "dimension": entries.FieldDefinition,
        "measure": entries.FieldDefinition,
        "time_dimension": entries.FieldDefinition,
    }
    provenance = record(source_file=nullable(STRING), source_line=INT, python_symbol=STRING)
    frame_summary = {
        "ref": ref("FrameRef"),
        "description": nullable(STRING),
        "committed_at": DATETIME,
        "schema": ref(FrameSchema.__name__),
    }
    session_fields = {
        name: _type(_annotation(SessionSummary, name))
        for name in [
            "name",
            "question",
            "timezone",
            "timezone_origin",
            "created_at",
            "updated_at",
            "artifact_count",
        ]
    }
    return {
        **_nested(),
        **types,
        "FrameRef": record(
            schema=literal("marivo.frame_ref/v2"),
            store_id=STRING,
            session_id=STRING,
            artifact_id=STRING,
        ),
        "FrameSummary": record(**frame_summary),
        "FrameReport": record(
            kind=literal("analysis_frame"),
            **frame_summary,
            lineage=ref(FrameLineage.__name__),
            source_reads=arr(ref(FrameSourceRead.__name__)),
            data=record(
                layout=layout,
                coordinates=mapping(arr(ref("ScalarValue"))),
                rows=arr(arr(ref("Cell"))),
                row_count=INT,
            ),
            periods=arr(
                record(
                    target=either(
                        record(kind=literal("axis"), axis_token=STRING),
                        record(kind=literal("comparison"), comparison_id=STRING),
                    ),
                    items=arr(
                        either(
                            ref("CalendarPeriod"),
                            ref("IntervalPeriod"),
                            ref("PairedPeriod"),
                            ref("ComparisonPeriod"),
                        )
                    ),
                )
            ),
        ),
        "DataFrameRef": record(
            schema=literal("marivo.dataframe_ref/v2"), project_root=STRING, result_id=STRING
        ),
        "DataFrameType": either(
            record(
                kind={
                    "enum": [
                        "null",
                        "boolean",
                        "string",
                        "date",
                        "int8",
                        "int16",
                        "int32",
                        "int64",
                        "uint8",
                        "uint16",
                        "uint32",
                        "uint64",
                        "float32",
                        "float64",
                    ]
                }
            ),
            record(kind=literal("decimal"), precision=INT, scale=INT),
            record(kind=literal("timestamp"), unit=UNITS, timezone=nullable(STRING)),
            record(kind=literal("duration"), unit=UNITS),
        ),
        "DataFrameReport": record(
            kind=literal("dataframe"),
            ref=ref("DataFrameRef"),
            committed_at=DATETIME,
            schema=record(columns=arr(record(name=STRING, dtype=ref("DataFrameType")))),
            data=record(layout=arr(STRING), rows=arr(arr(ref("ScalarValue"))), row_count=INT),
        ),
        "NonFiniteFloat": record(float={"enum": ["nan", "inf", "-inf"]}),
        "ScalarValue": either(STRING, BOOL, NUMBER, NULL, ref("NonFiniteFloat")),
        "TypedScalar": typed_scalar,
        "Cell": either(ref("ScalarValue"), ref("TypedScalar"), arr(ref("ScalarValue"))),
        "CalendarPeriod": record(
            kind=literal("calendar"),
            key=key,
            timezone=STRING,
            label=ref("TypedScalar"),
            ordinal=INT,
            nominal_ordinal=INT,
            selection={"enum": ["full", "prefix", "partial", "unavailable"]},
            cutoff=nullable(STRING),
        ),
        "IntervalPeriod": record(
            kind=literal("interval"), key=key, timezone=STRING, start=DATETIME, end=DATETIME
        ),
        "PairedPeriod": record(
            kind=literal("paired"), key=key, left=nullable(endpoint), right=nullable(endpoint)
        ),
        "ComparisonPeriod": record(
            kind=literal("comparison"),
            key=key,
            current=endpoint,
            baseline=nullable(endpoint),
            absent_reason=nullable({"enum": ["period_absent", "cutoff_absent", "unmatched"]}),
        ),
        "Info": record(
            api_version={"type": "integer", "const": API_VERSION},
            marivo_version=STRING,
            store_id=nullable(STRING),
            catalog=record(loaded=BOOL, loaded_at=nullable(DATETIME)),
        ),
        "Reload": record(loaded_at=DATETIME, object_count=INT),
        "SessionSummary": record(session_id=STRING, **session_fields),
        "SessionDirectory": _directory("SessionSummary"),
        "FrameDirectory": _directory("FrameSummary"),
        "SemanticSummary": record(**summary, kind={"enum": list(KINDS)}),
        "SemanticObject": either(
            *(
                record(
                    **summary,
                    kind=literal(kind),
                    definition=ref(definition.__name__),
                    provenance=provenance,
                )
                for kind, definition in definitions.items()
            )
        ),
        "ObjectDirectory": _directory("SemanticSummary", kinds=arr(STRING), domains=arr(STRING)),
        "RelatedDirectory": _directory("SemanticSummary", outside_scope=arr(STRING)),
        **{
            f"Error{status}": record(
                error=record(
                    code={"enum": [code for code, value in STATUS.items() if value == status]},
                    message=STRING,
                    location=STRING,
                    repair=STRING,
                    target=nullable(STRING),
                )
            )
            for status in sorted(set(STATUS.values()))
        },
    }


QUERY_SCHEMAS: dict[str, JsonObject] = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
    "cursor": {"type": "string", "minLength": 1, "maxLength": 16384},
    "kind": {"enum": list(KINDS)},
    "domain": {"type": "string", "minLength": 1},
    "relation": {"enum": ["dependencies", "dependents"]},
}


def openapi() -> JsonObject:
    schemas = _schemas()
    paths: JsonObject = {}
    for route in ROUTES:
        params: list[JsonValue] = [
            {"name": name, "in": "path", "required": True, "schema": STRING}
            for name in re.findall(r"\{(\w+)\}", route.path)
        ]
        params.extend(
            {
                "name": name,
                "in": "query",
                "required": name == "relation",
                "schema": QUERY_SCHEMAS[name],
            }
            for name in route.query
        )
        success: JsonObject = (
            {"$ref": "https://spec.openapis.org/oas/3.1/schema/2022-10-07"}
            if route.name == "openapi"
            else ref(route.response)
        )
        responses: JsonObject = {
            "200": {"description": "Success", "content": {"application/json": {"schema": success}}},
            **{
                str(status): {
                    "description": "Structured service error",
                    "content": {"application/json": {"schema": ref(f"Error{status}")}},
                }
                for status in sorted(set(STATUS.values()))
            },
        }
        paths[route.path] = {
            route.method.lower(): {
                "operationId": route.name,
                "summary": route.summary,
                **({"description": route.description} if route.description else {}),
                "parameters": params,
                "responses": responses,
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Marivo project data service", "version": __version__},
        "security": [{"Bearer": []}],
        "components": {
            "securitySchemes": {"Bearer": {"type": "http", "scheme": "bearer"}},
            "schemas": dict(schemas.items()),
        },
        "paths": paths,
    }
