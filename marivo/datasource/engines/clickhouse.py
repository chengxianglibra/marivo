"""ClickHouse engine profile."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from functools import partial
from typing import Literal

import ibis
import ibis.expr.types as ir
from ibis.backends import BaseBackend
from ibis.backends.sql.datatypes import (
    ClickHouseType,
)
from ibis.expr.datatypes import (
    DataType,
)

from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    PartitionProbeRequest,
    PartitionProbeResult,
    PartitionReader,
    TableRefRequest,
    require_field,
    structured_exception_chain,
)
from marivo.datasource.engines.clickhouse_partitions import clickhouse_partitions
from marivo.datasource.engines.clickhouse_query import (
    inline_key_relation,
    query_dialect_profile,
    read_issued_query,
    validate_temporal_parsers,
)
from marivo.datasource.engines.query import EngineQueryPolicy
from marivo.datasource.engines.requirements import CLICKHOUSE_REQUIREMENT
from marivo.datasource.engines.statements import (
    ProviderStatement,
    execute_provider_statement,
    register_provider_statements,
)
from marivo.datasource.engines.strptime import python_to_mysql_strptime
from marivo.datasource.errors import (
    DatasourceSourceCapabilityError,
    _backend_failure_summary,
    repair,
)
from marivo.datasource.ir import DatasourceIR, TableSourceIR
from marivo.datasource.metadata_types import (
    ColumnMetadata,
    MetadataWarning,
    TableMetadata,
    TablePhysicalProfile,
    _bool_from_nullable,
    _database_label,
    _empty_to_none,
    _int_or_none,
    _merge_columns,
    _schema_columns,
)
from marivo.datasource.metadata_types import (
    MetadataWarning as _Warning,
)


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:

    host = require_field(name, kwargs, "host", help_target="clickhouse")
    connect_kwargs: dict[str, object] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = kwargs.get("database", "default")
    connect_kwargs.setdefault("autogenerate_session_id", False)
    # SourceSession owns transient resubmission with backoff. The driver's own
    # status retries are immediate and also resend gateway timeouts.
    connect_kwargs.setdefault("query_retries", 0)
    if "secure" in kwargs:
        connect_kwargs["secure"] = bool(kwargs["secure"])
    settings = kwargs.get("settings")
    connect_kwargs["settings"] = {
        **(settings if isinstance(settings, dict) else {}),
        "timeout_overflow_mode": "throw",
    }
    return ibis.clickhouse.connect(**connect_kwargs)


# Server capacity and inter-server connection failures that clear without
# changing the statement. Timeouts and memory limits are excluded.
_TRANSIENT_ERROR_CODES = frozenset(
    {
        202,  # TOO_MANY_SIMULTANEOUS_QUERIES
        203,  # NO_FREE_CONNECTION
        210,  # NETWORK_ERROR
        279,  # ALL_CONNECTION_TRIES_FAILED
    }
)
# Gateway responses without a ClickHouse exception. 504 is a timeout that the
# same statement would usually repeat, so it is not resubmitted.
_TRANSIENT_HTTP_STATUSES = frozenset({429, 502, 503})
_HTTP_STATUS_RE = re.compile(r"HTTP driver received HTTP status (\d{3})")


def transient_failure(error: Exception) -> bool:
    """Classify native failures that resubmitting a read-only statement can clear."""
    from clickhouse_connect.driver.exceptions import DatabaseError  # noqa: PLC0415  # Optional ClickHouse transport.
    from urllib3.exceptions import ConnectTimeoutError  # noqa: PLC0415  # Optional ClickHouse transport.

    if not isinstance(error, DatabaseError):
        return False
    code = getattr(error, "code", None)
    if code is not None:
        return code in _TRANSIENT_ERROR_CODES
    status = _HTTP_STATUS_RE.search(str(error))
    if status is not None:
        return int(status.group(1)) in _TRANSIENT_HTTP_STATUSES
    # A connection that was never established submitted no statement.
    seen: set[int] = set()
    cause: BaseException | None = error.__cause__
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, ConnectTimeoutError):
            return True
        seen.add(id(cause))
        cause = cause.__cause__ or cause.__context__
    return False


_CH_DISTRIBUTED_ENGINE_RE = re.compile(r"^Distributed\('([^']+)',\s*'([^']+)',\s*'([^']+)'")


def clickhouse_database(source: TableSourceIR, datasource_ir: DatasourceIR) -> str:
    if source.database is not None and not isinstance(source.database, tuple):
        return str(source.database)
    database = datasource_ir.fields.get("database")
    return str(database) if database is not None else "default"


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    return (clickhouse_database(request.source, request.datasource_ir), request.source.table)


def clickhouse_system_parts_target(
    session: PartitionReader,
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
    frame = session.collect_bounded(
        expression,
        source_identities=(identity,),
        purpose="datasource.partition_topology",
        max_rows=1,
    )
    rows: list[dict[str, object]] = [
        {str(name): value for name, value in row.items()} for row in frame.to_dict(orient="records")
    ]
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
    if len(request.partition_columns) != 1:
        raise RuntimeError(
            "clickhouse system.parts mapping only supports single bare partition columns"
        )
    column = request.partition_columns[0]
    with request.open_reader() as session:
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
        frame = session.collect_bounded(
            expression,
            source_identities=(identity,),
            purpose="datasource.partition_metadata",
            max_rows=request.limit,
        )
        rows: list[dict[str, object]] = [
            {str(name): value for name, value in row.items()}
            for row in frame.to_dict(orient="records")
        ]
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


register_provider_statements(
    "clickhouse",
    {
        "tables.full": ProviderStatement(
            statement_id="clickhouse.tables.full",
            template=(
                "SELECT comment, partition_key, engine, engine_full FROM system.tables "
                "WHERE name = {table} "
                "AND database = {database} LIMIT 1"
            ),
            literal_slots=frozenset({"table", "database"}),
            allowed_purposes=frozenset({"datasource.metadata.clickhouse"}),
        ),
        "tables.comment": ProviderStatement(
            statement_id="clickhouse.tables.comment",
            template=(
                "SELECT comment FROM system.tables "
                "WHERE name = {table} "
                "AND database = {database} LIMIT 1"
            ),
            literal_slots=frozenset({"table", "database"}),
            allowed_purposes=frozenset({"datasource.metadata.clickhouse"}),
        ),
        "tables.create_query": ProviderStatement(
            statement_id="clickhouse.tables.create_query",
            template=(
                "SELECT create_table_query FROM system.tables "
                "WHERE name = {table} "
                "AND database = {database} LIMIT 1"
            ),
            literal_slots=frozenset({"table", "database"}),
            allowed_purposes=frozenset({"datasource.metadata.clickhouse"}),
        ),
        "tables.local_partition_key": ProviderStatement(
            statement_id="clickhouse.tables.local_partition_key",
            template=(
                "SELECT partition_key FROM system.tables "
                "WHERE name = {table} "
                "AND database = {database} LIMIT 1"
            ),
            literal_slots=frozenset({"table", "database"}),
            allowed_purposes=frozenset({"datasource.metadata.clickhouse"}),
        ),
        "columns.full": ProviderStatement(
            statement_id="clickhouse.columns.full",
            template=(
                "SELECT name, type, is_nullable, comment, position "
                "FROM system.columns "
                "WHERE table = {table} "
                "AND database = {database} "
                "ORDER BY position"
            ),
            literal_slots=frozenset({"table", "database"}),
            allowed_purposes=frozenset({"datasource.metadata.clickhouse"}),
        ),
        "columns.fallback": ProviderStatement(
            statement_id="clickhouse.columns.fallback",
            template=(
                "SELECT name, type, comment, position "
                "FROM system.columns "
                "WHERE table = {table} "
                "AND database = {database} "
                "ORDER BY position"
            ),
            literal_slots=frozenset({"table", "database"}),
            allowed_purposes=frozenset({"datasource.metadata.clickhouse"}),
        ),
        "parts.profile": ProviderStatement(
            statement_id="clickhouse.parts.profile",
            template=(
                "SELECT sum(rows) AS row_count, sum(bytes_on_disk) AS size_bytes "
                "FROM system.parts "
                "WHERE active "
                "AND database = {database} "
                "AND table = {table}"
            ),
            literal_slots=frozenset({"database", "table"}),
            allowed_purposes=frozenset({"datasource.metadata.clickhouse"}),
        ),
        "parts_columns.active": ProviderStatement(
            statement_id="clickhouse.parts_columns.active",
            template=(
                "SELECT column, type FROM system.parts_columns "
                "WHERE active "
                "AND database = {database} "
                "AND table = {table} "
                "GROUP BY column, type ORDER BY column, type"
            ),
            literal_slots=frozenset({"database", "table"}),
            allowed_purposes=frozenset({"datasource.metadata.clickhouse"}),
        ),
    },
)


def _clickhouse_rows(
    backend: BaseBackend,
    statement_id: str,
    *,
    values: Mapping[str, object] = {},
) -> tuple[dict[str, object], ...]:
    return execute_provider_statement(
        backend, PROFILE, statement_id, values=values, purpose="datasource.metadata.clickhouse"
    )


def table_engine(
    backend: BaseBackend, datasource: DatasourceIR, source: TableSourceIR
) -> str | None:
    """Read a named source's engine through the registered metadata statement."""
    rows = _clickhouse_rows(
        backend,
        "clickhouse.tables.full",
        values={"table": source.table, "database": clickhouse_database(source, datasource)},
    )
    return str(rows[0]["engine"]) if len(rows) == 1 and rows[0].get("engine") else None


def decoded_string_columns(
    backend: BaseBackend, datasource: DatasourceIR, source: TableSourceIR, *, enums: bool = False
) -> frozenset[str]:
    """Find decoded carriers; Enum equality is safe, but its native order is numeric."""
    rows = _clickhouse_rows(
        backend,
        "clickhouse.columns.fallback",
        values={"table": source.table, "database": clickhouse_database(source, datasource)},
    )
    return frozenset(
        str(row["name"])
        for row in rows
        if str(row.get("type")) in ("String", "Nullable(String)", "LowCardinality(String)")
        or (enums and re.fullmatch(r"(?:Nullable\()?Enum(?:8|16)\(.*\)\)?", str(row.get("type"))))
    )


def table_join_issue(
    backend: BaseBackend, datasource: DatasourceIR, source: TableSourceIR
) -> str | None:
    """Qualify a declared table's topology without substituting its remote target."""
    rows = _clickhouse_rows(
        backend,
        "clickhouse.tables.full",
        values={"table": source.table, "database": clickhouse_database(source, datasource)},
    )
    if len(rows) != 1 or not rows[0].get("engine"):
        return f"{source.table}: physical table metadata is unavailable"
    engine = str(rows[0]["engine"])
    if engine.endswith("MergeTree") or engine == "Distributed":
        return None
    return f"{source.table}: engine={engine} does not establish its physical join topology"


def _nullable_from_clickhouse(is_nullable_value: object, type_str: str) -> bool | None:
    result = _bool_from_nullable(is_nullable_value)
    if result is not None:
        return result
    return bool(type_str.startswith("Nullable("))


def _dereference_clickhouse_distributed(
    backend: BaseBackend,
    engine_full: str,
    ch_database: str,
    warnings: list[MetadataWarning],
) -> str:
    match = _CH_DISTRIBUTED_ENGINE_RE.match(engine_full)
    if not match:
        return ""
    local_database, local_table = match.group(2), match.group(3)
    try:
        local_rows = _clickhouse_rows(
            backend,
            "clickhouse.tables.local_partition_key",
            values={"table": local_table, "database": local_database},
        )
        if local_rows:
            return str(local_rows[0].get("partition_key") or "")
    except Exception as exc:
        warnings.append(
            _Warning(
                kind="metadata_query_failed",
                message=(
                    "clickhouse distributed table dereference failed: "
                    f"{_backend_failure_summary(exc).message}"
                ),
            )
        )
    return ""


def _clickhouse_physical_profile(
    *,
    backend: BaseBackend,
    database: str,
    table: str,
    engine: str,
    engine_full: str,
    warnings: list[MetadataWarning],
) -> TablePhysicalProfile | None:
    profile_database = database
    profile_table = table
    distributed_local_table: str | None = None
    if engine == "Distributed":
        match = _CH_DISTRIBUTED_ENGINE_RE.match(engine_full)
        if not match:
            warnings.append(
                _Warning(
                    kind="metadata_query_failed",
                    message=(
                        "clickhouse distributed physical profile dereference failed: "
                        "could not resolve local table from engine_full"
                    ),
                )
            )
            return None
        profile_database = match.group(2)
        profile_table = match.group(3)
        distributed_local_table = f"{profile_database}.{profile_table}"
    try:
        rows = _clickhouse_rows(
            backend,
            "clickhouse.parts.profile",
            values={"database": profile_database, "table": profile_table},
        )
    except Exception as exc:
        warnings.append(
            _Warning(
                kind="metadata_query_failed",
                message=(
                    "clickhouse physical profile query failed: "
                    f"{_backend_failure_summary(exc).message}"
                ),
            )
        )
        return None
    if not rows:
        return None
    row = rows[0]
    row_count = _int_or_none(row.get("row_count"))
    size_bytes = _int_or_none(row.get("size_bytes"))
    if row_count is None and size_bytes is None:
        return None
    if distributed_local_table is not None:
        return TablePhysicalProfile(
            row_count=None,
            row_count_kind="unknown",
            size_bytes=None,
            size_kind="unknown",
            source="clickhouse.system_parts.local_node",
            notes=(
                "scope=local_node_only",
                f"resolved_local_table={distributed_local_table}",
                f"local_row_count={row_count}"
                if row_count is not None
                else "local_row_count=unknown",
                f"local_size_bytes={size_bytes}"
                if size_bytes is not None
                else "local_size_bytes=unknown",
            ),
        )
    return TablePhysicalProfile(
        row_count=row_count,
        row_count_kind="metadata" if row_count is not None else "unknown",
        size_bytes=size_bytes,
        size_kind="on_disk" if size_bytes is not None else "unknown",
        source="clickhouse.system_parts",
        notes=(),
    )


def _clickhouse_projectable_columns(
    *,
    backend: BaseBackend,
    database: str,
    table: str,
    engine: str,
    engine_full: str,
    standard_column_names: Collection[str],
    warnings: list[MetadataWarning],
) -> tuple[ColumnMetadata, ...]:
    target_database = database
    target_table = table
    if engine == "Distributed":
        match = _CH_DISTRIBUTED_ENGINE_RE.match(engine_full)
        if match is None:
            warnings.append(
                _Warning(
                    kind="projectable_columns_unavailable",
                    message=(
                        "clickhouse projectable-column discovery could not resolve the "
                        "Distributed local table from engine_full"
                    ),
                )
            )
            return ()
        target_database = match.group(2)
        target_table = match.group(3)
    try:
        rows = _clickhouse_rows(
            backend,
            "clickhouse.parts_columns.active",
            values={"database": target_database, "table": target_table},
        )
    except Exception as exc:
        failure = _backend_failure_summary(exc)
        warnings.append(
            _Warning(
                kind="projectable_columns_unavailable",
                message=(
                    "clickhouse projectable-column discovery failed: "
                    f"{failure.exception_type}: {failure.message}"
                ),
            )
        )
        return ()

    raw_types_by_column: dict[str, set[str]] = {}
    for row in rows:
        name = str(row.get("column") or "")
        raw_type = str(row.get("type") or "")
        if not name or not raw_type or name in standard_column_names:
            continue
        raw_types_by_column.setdefault(name, set()).add(raw_type)

    discovered: list[ColumnMetadata] = []
    for name in sorted(raw_types_by_column):
        raw_types = tuple(sorted(raw_types_by_column[name]))
        parsed: list[DataType] = []
        try:
            parsed = [ClickHouseType.from_string(raw_type) for raw_type in raw_types]
            if any(bool(dtype.is_unknown()) for dtype in parsed):
                raise ValueError("ClickHouse type parser returned an unknown data type")
        except Exception as exc:
            warnings.append(
                _Warning(
                    kind="projectable_column_type_unparsed",
                    message=(
                        f"clickhouse physical column {name!r} has an unparseable type; "
                        f"observed_types={raw_types!r}; reason={exc}"
                    ),
                    columns=(name,),
                )
            )
            continue
        canonical_types = {
            str(dtype.copy(nullable=True)) for dtype in parsed if hasattr(dtype, "copy")
        }
        if len(canonical_types) != 1:
            warnings.append(
                _Warning(
                    kind="projectable_column_type_conflict",
                    message=(
                        f"clickhouse physical column {name!r} has incompatible types across "
                        f"active parts: {raw_types!r}"
                    ),
                    columns=(name,),
                )
            )
            continue
        canonical_type = next(iter(canonical_types))
        discovered.append(
            ColumnMetadata(
                name=name,
                type=canonical_type,
                nullable=any(bool(getattr(dtype, "nullable", True)) for dtype in parsed),
                comment=None,
                ordinal_position=None,
            )
        )
    return tuple(discovered)


def _inspect_clickhouse(
    *,
    datasource: str,
    backend: BaseBackend,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: ir.Table,
    include_partitions: bool,
) -> TableMetadata:
    schema_columns = _schema_columns(table_expr)
    ch_database = _database_label(database) or "default"
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None
    partition_key = ""
    engine = ""
    engine_full = ""
    table_metadata_available = False
    physical_profile: TablePhysicalProfile | None = None

    try:
        table_rows = _clickhouse_rows(
            backend,
            "clickhouse.tables.full",
            values={"table": table, "database": ch_database},
        )
        if table_rows:
            table_metadata_available = True
            table_comment = _empty_to_none(table_rows[0].get("comment"))
            partition_key = str(table_rows[0].get("partition_key") or "")
            engine = str(table_rows[0].get("engine") or "")
            engine_full = str(table_rows[0].get("engine_full") or "")
    except Exception:
        try:
            table_rows = _clickhouse_rows(
                backend,
                "clickhouse.tables.comment",
                values={"table": table, "database": ch_database},
            )
            if table_rows:
                table_comment = _empty_to_none(table_rows[0].get("comment"))
        except Exception as exc2:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=(
                        "clickhouse table metadata query failed: "
                        f"{_backend_failure_summary(exc2).message}"
                    ),
                )
            )

    catalog_columns: dict[str, ColumnMetadata] = {}
    try:
        column_rows = _clickhouse_rows(
            backend,
            "clickhouse.columns.full",
            values={"table": table, "database": ch_database},
        )
        for row in column_rows:
            name = str(row.get("name"))
            type_str = str(row.get("type") or "")
            ordinal = row.get("position")
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=type_str,
                nullable=_nullable_from_clickhouse(row.get("is_nullable"), type_str),
                comment=_empty_to_none(row.get("comment")),
                ordinal_position=int(str(ordinal)) if ordinal is not None else None,
            )
    except Exception:
        try:
            column_rows = _clickhouse_rows(
                backend,
                "clickhouse.columns.fallback",
                values={"table": table, "database": ch_database},
            )
            for row in column_rows:
                name = str(row.get("name"))
                type_str = str(row.get("type") or "")
                ordinal = row.get("position")
                catalog_columns[name] = ColumnMetadata(
                    name=name,
                    type=type_str,
                    nullable=_nullable_from_clickhouse(None, type_str),
                    comment=_empty_to_none(row.get("comment")),
                    ordinal_position=int(str(ordinal)) if ordinal is not None else None,
                )
        except Exception as exc2:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=(
                        "clickhouse column metadata query failed: "
                        f"{_backend_failure_summary(exc2).message}"
                    ),
                )
            )

    local_partition_key = (
        _dereference_clickhouse_distributed(backend, engine_full, ch_database, warnings)
        if include_partitions and engine == "Distributed"
        else None
    )
    partitions, partition_state = clickhouse_partitions(
        engine,
        partition_key,
        catalog_columns,
        local_partition_key,
        table_metadata_available=table_metadata_available,
        include_partitions=include_partitions,
    )

    columns = _merge_columns(schema_columns, catalog_columns)
    if not any(column.comment for column in columns) and table_comment is None:
        warnings.append(
            MetadataWarning(
                kind="comments_unavailable",
                message="clickhouse table and column comments are unavailable for this table",
            )
        )
    # ClickHouse has no primary-key concept; the absence is disclosed here so
    # both the dispatcher path and direct provider calls surface it.
    warnings.append(
        MetadataWarning(
            kind="primary_keys_unavailable",
            message="clickhouse primary key metadata is not exposed by this adapter",
        )
    )

    is_view = engine in ("View", "MaterializedView") if table_metadata_available else None
    view_definition: str | None = None
    if is_view:
        try:
            def_rows = _clickhouse_rows(
                backend,
                "clickhouse.tables.create_query",
                values={"table": table, "database": ch_database},
            )
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("create_table_query"))
        except Exception as exc:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=(
                        "clickhouse view metadata query failed: "
                        f"{_backend_failure_summary(exc).message}"
                    ),
                )
            )

    if not is_view:
        physical_profile = _clickhouse_physical_profile(
            backend=backend,
            database=ch_database,
            table=table,
            engine=engine,
            engine_full=engine_full,
            warnings=warnings,
        )

    projectable_columns = (
        ()
        if is_view
        else _clickhouse_projectable_columns(
            backend=backend,
            database=ch_database,
            table=table,
            engine=engine,
            engine_full=engine_full,
            standard_column_names=tuple(column.name for column in columns),
            warnings=warnings,
        )
    )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="clickhouse",
        comment=table_comment,
        columns=columns,
        partitions=partitions,
        partition_state=partition_state,
        warnings=tuple(warnings),
        projectable_columns=projectable_columns,
        is_view=is_view,
        view_definition=view_definition,
        physical_profile=physical_profile,
    )


def inspect_table(request: MetadataInspectRequest) -> TableMetadata:
    ch_database = (
        request.database
        if request.database is not None
        else request.datasource_ir.fields.get("database", "default")
    )
    return _inspect_clickhouse(
        datasource=request.datasource,
        backend=request.backend,
        table=request.table,
        database=ch_database,
        table_expr=request.table_expr,
        include_partitions=request.include_partitions,
    )


def classify_table_resolution_failure(exc: Exception) -> Literal["metadata_unavailable"] | None:
    """Classify ClickHouse catalog access denial from native structured fields."""
    for candidate in structured_exception_chain(exc):
        if getattr(candidate, "code", None) == 497:
            return "metadata_unavailable"
        if getattr(candidate, "name", None) == "ACCESS_DENIED":
            return "metadata_unavailable"
    return None


def validate_query_size(backend: BaseBackend, sql: str) -> None:
    """Check the known native parser byte limit without another metadata read."""
    connection = getattr(backend, "con", None)
    getter = getattr(connection, "get_client_setting", None)
    value = getter("max_query_size") if callable(getter) else None
    if value is None:
        settings = getattr(connection, "server_settings", None)
        if isinstance(settings, dict):
            value = getattr(settings.get("max_query_size"), "value", None)
    # Missing connection facts retain the native provider error boundary. They
    # do not qualify an unlimited request. Zero explicitly disables this limit.
    if not isinstance(value, (str, int)) or not str(value).isdecimal():
        return
    limit = int(value)
    size = len((sql + "\n FORMAT Native").encode("utf-8"))
    if limit and size > limit:
        raise DatasourceSourceCapabilityError(
            message="The ClickHouse source request exceeds its native parser capacity.",
            expected=f"a UTF-8 query within max_query_size={limit} bytes",
            received=f"{size} query bytes including FORMAT Native",
            repair=repair(
                kind="reconnect",
                canonical_id="clickhouse",
                action="Reduce the selected key domain or query size, or increase max_query_size in the datasource settings and reconnect.",
            ),
        )


@contextmanager
def native_query_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
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


def _validate_query(backend: BaseBackend, sql: str) -> None:
    validate_temporal_parsers(backend, sql)
    validate_query_size(backend, sql)


PROFILE = EngineProfile(
    dialect=query_dialect_profile(
        original_string_columns=partial(decoded_string_columns, enums=True),
        query_string_columns=decoded_string_columns,
    ),
    read_issued_query=read_issued_query,
    issued_query_only=True,
    query_policy=EngineQueryPolicy(
        key_fusion="numeric",
        sample_fusion=True,
        inline_key_relation=inline_key_relation,
        accepts_nonfinite_inline_time_keys=True,
    ),
    name="clickhouse",
    aliases=(),
    authoring_func="clickhouse",
    required_modules=(CLICKHOUSE_REQUIREMENT,),
    connect=connect,
    table_engine=table_engine,
    table_join_issue=table_join_issue,
    validate_query=_validate_query,
    identifier_quote="`",
    table_name_parts=table_name_parts,
    inspect_partition_values=inspect_partition_values,
    metadata=EngineMetadataIntrospection(
        inspect_table=inspect_table,
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
    native_query_timeout=native_query_timeout,
    transient_failure=transient_failure,
)
