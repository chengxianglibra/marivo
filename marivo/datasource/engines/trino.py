"""Trino engine profile."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal

import ibis.expr.types as ir
from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    PartitionProbeRequest,
    PartitionProbeResult,
    QuantileCapability,
    TableRefRequest,
    identity_read_only_kwargs,
    require_field,
    schema_only_metadata_inspect,
    structured_exception_chain,
)
from marivo.datasource.strptime import python_to_mysql_strptime


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host", help_target="trino")
    catalog = require_field(name, kwargs, "catalog", help_target="trino")
    user = require_field(name, kwargs, "user", help_target="trino")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs.pop("catalog", None)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = catalog
    connect_kwargs["user"] = user
    if "client_tags" in kwargs:
        tags: Any = kwargs["client_tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        connect_kwargs["client_tags"] = list(tags)
    if "session_properties" in kwargs and isinstance(kwargs["session_properties"], dict):
        connect_kwargs["session_properties"] = dict(kwargs["session_properties"])
    backend = ibis.trino.connect(**connect_kwargs)
    backend._marivo_timezone_name = str(connect_kwargs.get("timezone", "UTC"))
    return backend


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    catalog = str(request.datasource_ir.fields["catalog"])
    schema_value = request.datasource_ir.fields.get("schema")
    catalog_name, schema_name = _trino_namespace(
        request.source.database,
        catalog=catalog,
        default_schema=str(schema_value) if schema_value is not None else None,
    )
    return (
        (catalog_name, request.source.table)
        if schema_name is None
        else (catalog_name, schema_name, request.source.table)
    )


def _trino_namespace(
    database: str | tuple[str, ...] | None,
    *,
    catalog: str,
    default_schema: str | None,
) -> tuple[str, str | None]:
    if isinstance(database, tuple):
        if len(database) >= 2:
            return str(database[0]), str(database[1])
        if len(database) == 1:
            return catalog, str(database[0])
        return catalog, None
    if database is not None:
        parts = str(database).split(".")
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]
        return catalog, str(database)
    return catalog, default_schema


def _partition_table_parts(request: PartitionProbeRequest) -> tuple[str, str | None, str]:
    catalog = str(request.datasource_ir.fields["catalog"])
    schema_value = request.datasource_ir.fields.get("schema")
    catalog_name, schema_name = _trino_namespace(
        request.source.database,
        catalog=catalog,
        default_schema=str(schema_value) if schema_value is not None else None,
    )
    return catalog_name, schema_name, request.source.table


def inspect_partition_values(request: PartitionProbeRequest) -> PartitionProbeResult:
    from marivo.datasource.adapters import SourceSession
    from marivo.datasource.ir import TableSourceIR

    catalog, schema_name, table_name = _partition_table_parts(request)
    if schema_name is None:
        raise RuntimeError("trino partition inspection requires database= or datasource schema")
    identity = f"partition-metadata:{catalog}.{schema_name}.{table_name}"
    with SourceSession(
        PROFILE, request.datasource_ir, request.backend, owns_backend=False
    ) as session:
        relation = session.bind(
            TableSourceIR(f"{table_name}$partitions", database=(catalog, schema_name)),
            source_identity=identity,
        ).relation
        expression = _partition_projection(
            relation, request.partition_columns, request.order, request.limit
        )
        rows = session.collect_bounded(
            expression,
            source_identities=(identity,),
            purpose="datasource.partition_metadata",
            max_rows=request.limit,
        ).to_pylist()
    return PartitionProbeResult(rows=tuple(rows), value_source="metadata")


def _partition_projection(
    relation: ir.Table,
    columns: tuple[str, ...],
    order: Literal["asc", "desc"],
    limit: int,
) -> ir.Table:
    nested = "partition" in relation.columns and not all(
        column in relation.columns for column in columns
    )
    values = relation.select(
        **{
            column: (relation["partition"][column] if nested else relation[column])
            for column in columns
        }
    )
    sort_keys = (
        value.asc() if order == "asc" else value.desc()
        for value in (values[column] for column in columns)
    )
    return values.order_by(*sort_keys).limit(limit)


def classify_table_resolution_failure(exc: Exception) -> Literal["metadata_unavailable"] | None:
    """Classify Trino metadata permission denial from the server error name."""
    for candidate in structured_exception_chain(exc):
        if getattr(candidate, "error_name", None) == "PERMISSION_DENIED":
            return "metadata_unavailable"
    return None


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    properties = getattr(backend.con, "session_properties", None)
    if (
        getattr(backend, "_marivo_terminal_timeout_seconds", None) != timeout_seconds
        or not isinstance(properties, dict)
        or properties.get("query_max_run_time") != f"{timeout_seconds}s"
    ):
        raise RuntimeError("trino terminal connection has no configured query timeout")
    yield


PROFILE = EngineProfile(
    name="trino",
    aliases=("presto",),
    authoring_func="trino",
    required_modules=("ibis.backends.trino",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
    identifier_quote='"',
    table_name_parts=table_name_parts,
    inspect_partition_values=inspect_partition_values,
    metadata=EngineMetadataIntrospection(
        inspect_table=schema_only_metadata_inspect,
        classify_table_resolution_failure=classify_table_resolution_failure,
    ),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=True,
    ),
    translate_strptime_format=python_to_mysql_strptime,
    datetime_decode_policy="local_naive_label",
    quantile=QuantileCapability(mode="approximate", method="approx_percentile"),
    percentile_uses_approx_quantile=True,
    authoring_timeout=authoring_timeout,
)
