"""ClickHouse engine profile."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal

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
    require_field,
    schema_only_metadata_inspect,
    structured_exception_chain,
)
from marivo.datasource.ir import DatasourceIR, TableSourceIR
from marivo.datasource.strptime import python_to_mysql_strptime

if TYPE_CHECKING:
    from marivo.datasource.adapters import SourceSession


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host", help_target="clickhouse")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = kwargs.get("database", "default")
    connect_kwargs.setdefault("autogenerate_session_id", False)
    if "secure" in kwargs:
        connect_kwargs["secure"] = bool(kwargs["secure"])
    if "settings" in kwargs and isinstance(kwargs["settings"], dict):
        connect_kwargs["settings"] = dict(kwargs["settings"])
    return ibis.clickhouse.connect(**connect_kwargs)


def apply_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    out = dict(kwargs)
    raw_settings = out.get("settings")
    settings: dict[str, object] = dict(raw_settings) if isinstance(raw_settings, dict) else {}
    settings["readonly"] = 1
    out["settings"] = settings
    return out


_CH_DISTRIBUTED_ENGINE_RE = re.compile(r"^Distributed\('([^']+)',\s*'([^']+)',\s*'([^']+)'")


def clickhouse_database(source: TableSourceIR, datasource_ir: DatasourceIR) -> str:
    if source.database is not None and not isinstance(source.database, tuple):
        return str(source.database)
    database = datasource_ir.fields.get("database")
    return str(database) if database is not None else "default"


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    return (clickhouse_database(request.source, request.datasource_ir), request.source.table)


def clickhouse_system_parts_target(
    session: SourceSession,
    datasource_ir: DatasourceIR,
    source: TableSourceIR,
) -> tuple[str, str]:
    database = clickhouse_database(source, datasource_ir)
    identity = "clickhouse:system.tables"
    relation = session.bind(
        TableSourceIR("tables", database="system"), source_identity=identity
    ).relation
    expression = (
        relation.filter((relation.name == source.table) & (relation.database == database))
        .select("engine", "engine_full")
        .limit(1)
    )
    rows = session.collect_bounded(
        expression,
        source_identities=(identity,),
        purpose="datasource.partition_topology",
        max_rows=1,
    ).to_pylist()
    if not rows:
        raise RuntimeError("ClickHouse system.tables cannot resolve the selected source")
    engine = str(rows[0].get("engine") or "")
    if engine != "Distributed":
        return database, source.table
    engine_full = str(rows[0].get("engine_full") or "")
    match = _CH_DISTRIBUTED_ENGINE_RE.match(engine_full)
    if not match:
        return database, source.table
    return match.group(2), match.group(3)


def inspect_partition_values(request: PartitionProbeRequest) -> PartitionProbeResult:
    from marivo.datasource.adapters import SourceSession

    if len(request.partition_columns) != 1:
        raise RuntimeError(
            "clickhouse system.parts mapping only supports single bare partition columns"
        )
    column = request.partition_columns[0]
    with SourceSession(
        PROFILE, request.datasource_ir, request.backend, owns_backend=False
    ) as session:
        database, table = clickhouse_system_parts_target(
            session, request.datasource_ir, request.source
        )
        identity = "clickhouse:system.parts"
        relation = session.bind(
            TableSourceIR("parts", database="system"), source_identity=identity
        ).relation
        expression = _system_parts_projection(
            relation, database, table, column, request.order, request.limit
        )
        rows = session.collect_bounded(
            expression,
            source_identities=(identity,),
            purpose="datasource.partition_metadata",
            max_rows=request.limit,
        ).to_pylist()
    return PartitionProbeResult(rows=tuple(rows), value_source="system_catalog")


def _system_parts_projection(
    relation: ir.Table,
    database: str,
    table: str,
    column: str,
    order: Literal["asc", "desc"],
    limit: int,
) -> ir.Table:
    matching = relation.filter(
        (relation.active == 1) & (relation.database == database) & (relation.table == table)
    )
    values = matching.select(relation.partition.name(column)).distinct()
    sort_key = values[column].asc() if order == "asc" else values[column].desc()
    return values.order_by(sort_key).limit(limit)


def classify_table_resolution_failure(exc: Exception) -> Literal["metadata_unavailable"] | None:
    """Classify ClickHouse catalog access denial from native structured fields."""
    for candidate in structured_exception_chain(exc):
        if getattr(candidate, "code", None) == 497:
            return "metadata_unavailable"
        if getattr(candidate, "name", None) == "ACCESS_DENIED":
            return "metadata_unavailable"
    return None


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    connection = getattr(backend, "con", None)
    params = getattr(connection, "params", None)
    if not isinstance(params, dict):
        raise RuntimeError("clickhouse backend does not expose mutable query settings")
    server_settings = getattr(connection, "server_settings", None)
    if isinstance(server_settings, dict):
        definition = server_settings.get("max_execution_time")
        if definition is not None and getattr(definition, "readonly", 0) == 1:
            raise RuntimeError("clickhouse max_execution_time setting is read only")
    marker = object()
    previous = params.get("max_execution_time", marker)
    try:
        params["max_execution_time"] = str(timeout_seconds)
    except BaseException:
        if previous is marker:
            params.pop("max_execution_time", None)
        else:
            params["max_execution_time"] = previous
        raise
    try:
        yield
    finally:
        if previous is marker:
            params.pop("max_execution_time", None)
        else:
            params["max_execution_time"] = previous


PROFILE = EngineProfile(
    name="clickhouse",
    aliases=(),
    authoring_func="clickhouse",
    required_modules=("ibis.backends.clickhouse",),
    connect=connect,
    apply_read_only_kwargs=apply_read_only_kwargs,
    identifier_quote="`",
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
    datetime_decode_policy="utc_naive_instant",
    quantile=QuantileCapability(mode="approximate", method="reservoir_sampling"),
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
)
