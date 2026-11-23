"""Datasource source Help specifications."""

from marivo._authoring.model import AuthoringCapability
from marivo.datasource._help_specs.builders import (
    _capability,
    _inputs,
    _optional_input,
    _target,
)

SPECS: tuple[AuthoringCapability, ...] = (
    _capability(
        "table",
        "marivo.datasource.source.table",
        (
            "Build a table source descriptor, not a datasource. Pass the table name as "
            "the sole positional argument; a dot is part of the name. Use the keyword "
            "database= for a namespace string or tuple; omit it to leave namespace "
            "resolution to the backend. Namespace interpretation depends on the backend. "
            "columns maps output names to physical "
            "column names (Mapping[str, str]); it does not declare types. Types come from "
            "source metadata when needed at execution; md.inspect exposes current physical "
            "names and types. Values must be physical names, not objects describing "
            "a column or its type."
        ),
        output="TableSource",
        inputs=(
            *_inputs(("subject", "TableName")),
            _optional_input("dependency", "ProjectionColumns"),
        ),
        constraints=("projected_source_runtime_evidence",),
        example=(
            'catalog_source = md.table("dwd_orders", database="iceberg_inf")\n'
            'projected_source = md.table("events", columns={"event_time": "event.timestamp"})'
        ),
        see_also=(_target("inspect"), _target("raw_sql")),
    ),
    _capability(
        "parquet",
        "marivo.datasource.source.parquet",
        "Build a Parquet file source descriptor.",
        output="TableSource",
        inputs=_inputs(("subject", "SourcePath")),
        example='md.parquet("data/orders.parquet")',
    ),
    _capability(
        "csv",
        "marivo.datasource.source.csv",
        "Build a CSV source descriptor with inferred types and an optional projection.",
        output="TableSource",
        inputs=(
            *_inputs(("subject", "SourcePath")),
            _optional_input("dependency", "ProjectionColumns"),
        ),
        example='md.csv("data/orders.csv", columns={"order_id": "Order ID"})',
    ),
    _capability(
        "source_param",
        "marivo.datasource.source.source_param",
        "Declare a JSON request value for datasource sampling or Semantic preview.",
        output="SourceParameter",
        inputs=_inputs(("subject", "SourceParameterName")),
        example='md.source_param("start")',
    ),
    _capability(
        "json",
        "marivo.datasource.source.json",
        "Build a JSON source with inferred types, JSON-path projections, and GET/POST request values. "
        "records_path selects an object-member path to an array of records; a missing or non-array "
        "value fails. POST requires an HTTP(S) path, a JSON object body and format='auto'; GET does not accept a body. Query lists encode repeated keys; "
        "source_param placeholders are bound only by datasource sampling or Semantic preview.",
        output="TableSource",
        inputs=(
            *_inputs(("subject", "SourcePath")),
            _optional_input("dependency", "JsonProjectionColumns"),
            _optional_input("dependency", "SourceParameter"),
        ),
        constraints=("json_request_shape",),
        example=(
            'md.json("https://api.example/orders", '
            'columns={"order_id": "id", "app_name": "apps[].name"}, '
            'records_path="$.data", '
            'query_params={"app": ["app-1", "app-2"]})'
        ),
    ),
    _capability(
        "partition",
        "marivo.datasource.source.partition",
        "Build an explicitly partitioned acquisition scope.",
        output="PartitionScope",
        inputs=_inputs(
            ("mapping_key", "PartitionValues"),
            ("scope", "PositiveRowGuard"),
            ("scope", "PositiveTimeoutGuard"),
        ),
        example='md.partition({"dt": "20260710"}, max_rows=1000, timeout_seconds=30)',
    ),
    _capability(
        "time_range",
        "marivo.datasource.source.time_range",
        "Build a half-open temporal scope. Boundaries must match the physical column: "
        "aware datetimes for aware timestamps, naive datetimes or dates for naive "
        "timestamps, and dates for DATE. Datasource defaults do not fill boundary awareness.",
        output="PartitionScope",
        inputs=_inputs(
            ("subject", "TemporalColumn"),
            ("scope", "TemporalBound"),
            ("scope", "PositiveRowGuard"),
            ("scope", "PositiveTimeoutGuard"),
        ),
        example=(
            'md.time_range("timestamp", start="2026-08-01", end="2026-08-02", '
            "max_rows=1000, timeout_seconds=30)"
        ),
        see_also=(_target("partition"), _target("SourceInspection.sample")),
    ),
    _capability(
        "unpruned",
        "marivo.datasource.source.unpruned",
        "Build an explicitly unpruned acquisition scope.",
        output="UnprunedScope",
        inputs=_inputs(("scope", "PositiveRowGuard"), ("scope", "PositiveTimeoutGuard")),
        example="md.unpruned(max_rows=1000, timeout_seconds=30)",
    ),
)
