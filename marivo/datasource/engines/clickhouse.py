"""ClickHouse engine profile."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    PartitionProbeRequest,
    PartitionProbeResult,
    QuantileCapability,
    TableRefRequest,
    decode_cursor_frame,
    identity_str,
    quote_identifier,
    require_field,
)
from marivo.datasource.ir import DatasourceIR, TableSourceIR

if TYPE_CHECKING:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        PartitionMetadata,
        TableMetadata,
        TablePhysicalProfile,
    )


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host")
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
    settings["access_mode"] = "read_only"
    out["settings"] = settings
    return out


_CH_DISTRIBUTED_ENGINE_RE = re.compile(r"^Distributed\('([^']+)',\s*'([^']+)',\s*'([^']+)'")

_CH_PARTITION_FUNC_RE = re.compile(r"^(\w+)\((\w+)\)$")
_CH_PARTITION_BARE_RE = re.compile(r"^(\w+)$")


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _nullable_from_clickhouse(is_nullable_value: object, type_str: str) -> bool | None:
    from marivo.datasource.metadata import _bool_from_nullable

    result = _bool_from_nullable(is_nullable_value)
    if result is not None:
        return result
    return bool(type_str.startswith("Nullable("))


def clickhouse_database(source: TableSourceIR, datasource_ir: DatasourceIR) -> str:
    if source.database is not None and not isinstance(source.database, tuple):
        return str(source.database)
    database = datasource_ir.fields.get("database")
    return str(database) if database is not None else "default"


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    return (clickhouse_database(request.source, request.datasource_ir), request.source.table)


def clickhouse_system_parts_target(
    backend: BaseBackend,
    datasource_ir: DatasourceIR,
    source: TableSourceIR,
) -> tuple[str, str]:
    database = clickhouse_database(source, datasource_ir)
    sql = (
        "SELECT engine, engine_full FROM system.tables "
        f"WHERE name = {_quote_sql_literal(source.table)} "
        f"AND database = {_quote_sql_literal(database)} LIMIT 1"
    )
    try:
        frame = decode_cursor_frame(backend.raw_sql(sql), include_types=False, max_rows=None)
        rows = frame.rows
    except Exception:
        return database, source.table
    if not rows:
        return database, source.table
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
    database, table = clickhouse_system_parts_target(
        request.backend, request.datasource_ir, request.source
    )
    sql = (
        f"SELECT partition AS {quote_identifier(column, PROFILE)} "
        "FROM system.parts "
        "WHERE active "
        f"AND database = {_quote_sql_literal(database)} "
        f"AND table = {_quote_sql_literal(table)} "
        "GROUP BY partition "
        "ORDER BY partition DESC "
        f"LIMIT {request.limit}"
    )
    frame = decode_cursor_frame(request.backend.raw_sql(sql), include_types=False, max_rows=None)
    return PartitionProbeResult(rows=frame.rows, value_source="system_catalog")


def _parse_clickhouse_partition_key(
    partition_key: str,
    catalog_columns: Mapping[str, ColumnMetadata],
) -> tuple[PartitionMetadata, ...]:
    from marivo.datasource.metadata import PartitionMetadata

    if not partition_key or partition_key == "tuple()":
        return ()
    # Split on commas only outside parentheses (depth-aware).
    elements: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(partition_key):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            elements.append(partition_key[start:i].strip())
            start = i + 1
    elements.append(partition_key[start:].strip())
    parts: list[PartitionMetadata] = []
    for element in elements:
        func_match = _CH_PARTITION_FUNC_RE.match(element)
        if func_match:
            func_name, col_name = func_match.group(1), func_match.group(2)
            col = catalog_columns.get(col_name)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=col_name,
                        type=col.type,
                        transform=func_name,
                        comment=None,
                    )
                )
            continue
        bare_match = _CH_PARTITION_BARE_RE.match(element)
        if bare_match:
            col_name = bare_match.group(1)
            col = catalog_columns.get(col_name)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=col_name,
                        type=col.type,
                        transform=None,
                        comment=None,
                    )
                )
            continue
        # Unparseable expression: store full raw string as transform.
        # Try to identify a column name from identifier-like tokens,
        # stripping commas so inner args like "uid," in intDiv(uid, 100) match.
        for token in element.replace("(", " ").replace(")", " ").replace(",", " ").split():
            col = catalog_columns.get(token)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=token,
                        type=col.type,
                        transform=element,
                        comment=None,
                    )
                )
                break
    return tuple(parts)


def _dereference_clickhouse_distributed(
    backend: Any,
    engine_full: str,
    ch_database: str,
    warnings: list[MetadataWarning],
) -> str:
    from marivo.datasource.metadata import (
        MetadataWarning,
        _query_rows,
        _quote_literal,
    )

    match = _CH_DISTRIBUTED_ENGINE_RE.match(engine_full)
    if not match:
        return ""
    local_database, local_table = match.group(2), match.group(3)
    try:
        local_rows = _query_rows(
            backend,
            "SELECT partition_key FROM system.tables "
            f"WHERE name = {_quote_literal(local_table)} "
            f"AND database = {_quote_literal(local_database)} LIMIT 1",
        )
        if local_rows:
            return str(local_rows[0].get("partition_key") or "")
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"clickhouse distributed table dereference failed: {exc}",
            )
        )
    return ""


def _clickhouse_physical_profile(
    *,
    backend: Any,
    database: str,
    table: str,
    engine: str,
    engine_full: str,
    warnings: list[MetadataWarning],
) -> TablePhysicalProfile | None:
    from marivo.datasource.metadata import (
        MetadataWarning,
        TablePhysicalProfile,
        _int_or_none,
        _query_rows,
        _quote_literal,
    )

    profile_database = database
    profile_table = table
    notes: tuple[str, ...] = ()
    if engine == "Distributed":
        match = _CH_DISTRIBUTED_ENGINE_RE.match(engine_full)
        if not match:
            warnings.append(
                MetadataWarning(
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
        notes = (
            f"resolved Distributed table to {profile_database}.{profile_table}; "
            "profile is not cluster-wide",
        )
    try:
        rows = _query_rows(
            backend,
            "SELECT sum(rows) AS row_count, sum(bytes_on_disk) AS size_bytes "
            "FROM system.parts "
            "WHERE active "
            f"AND database = {_quote_literal(profile_database)} "
            f"AND table = {_quote_literal(profile_table)}",
        )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"clickhouse physical profile query failed: {exc}",
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
    return TablePhysicalProfile(
        row_count=row_count,
        row_count_kind="metadata" if row_count is not None else "unknown",
        size_bytes=size_bytes,
        size_kind="on_disk" if size_bytes is not None else "unknown",
        source="clickhouse.system_parts",
        notes=notes,
    )


def _inspect_clickhouse(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
) -> TableMetadata:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        TableMetadata,
        _database_label,
        _empty_to_none,
        _merge_columns,
        _query_rows,
        _quote_literal,
        _schema_columns,
    )

    schema_columns = _schema_columns(table_expr)
    ch_database = _database_label(database) or "default"
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None
    partition_key = ""
    engine = ""
    engine_full = ""
    physical_profile: TablePhysicalProfile | None = None

    try:
        table_rows = _query_rows(
            backend,
            "SELECT comment, partition_key, engine, engine_full FROM system.tables "
            f"WHERE name = {_quote_literal(table)} "
            f"AND database = {_quote_literal(ch_database)} LIMIT 1",
        )
        if table_rows:
            table_comment = _empty_to_none(table_rows[0].get("comment"))
            partition_key = str(table_rows[0].get("partition_key") or "")
            engine = str(table_rows[0].get("engine") or "")
            engine_full = str(table_rows[0].get("engine_full") or "")
    except Exception:
        try:
            table_rows = _query_rows(
                backend,
                "SELECT comment FROM system.tables "
                f"WHERE name = {_quote_literal(table)} "
                f"AND database = {_quote_literal(ch_database)} LIMIT 1",
            )
            if table_rows:
                table_comment = _empty_to_none(table_rows[0].get("comment"))
        except Exception as exc2:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"clickhouse table metadata query failed: {exc2}",
                )
            )

    catalog_columns: dict[str, ColumnMetadata] = {}
    try:
        column_rows = _query_rows(
            backend,
            "SELECT name, type, is_nullable, comment, position "
            "FROM system.columns "
            f"WHERE table = {_quote_literal(table)} "
            f"AND database = {_quote_literal(ch_database)} "
            "ORDER BY position",
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
            column_rows = _query_rows(
                backend,
                "SELECT name, type, comment, position "
                "FROM system.columns "
                f"WHERE table = {_quote_literal(table)} "
                f"AND database = {_quote_literal(ch_database)} "
                "ORDER BY position",
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
                    message=f"clickhouse column metadata query failed: {exc2}",
                )
            )

    partitions: tuple[PartitionMetadata, ...] = ()
    if include_partitions:
        if engine == "Distributed":
            local_pk = _dereference_clickhouse_distributed(
                backend,
                engine_full,
                ch_database,
                warnings,
            )
            if local_pk:
                partitions = _parse_clickhouse_partition_key(local_pk, catalog_columns)
        elif partition_key and partition_key != "tuple()":
            partitions = _parse_clickhouse_partition_key(partition_key, catalog_columns)

    columns = _merge_columns(schema_columns, catalog_columns)
    if not any(column.comment for column in columns) and table_comment is None:
        warnings.append(
            MetadataWarning(
                kind="comments_unavailable",
                message="clickhouse table and column comments are unavailable for this table",
            )
        )

    is_view = engine in ("View", "MaterializedView")
    view_definition: str | None = None
    if is_view:
        try:
            def_rows = _query_rows(
                backend,
                "SELECT create_table_query FROM system.tables "
                f"WHERE name = {_quote_literal(table)} "
                f"AND database = {_quote_literal(ch_database)} LIMIT 1",
            )
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("create_table_query"))
        except Exception as exc:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"clickhouse view metadata query failed: {exc}",
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

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="clickhouse",
        comment=table_comment,
        columns=columns,
        partitions=partitions,
        warnings=tuple(warnings),
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


_DATETRUNC_TO_NATIVE: dict[str, str] = {
    "second": "toStartOfSecond",
    "minute": "toStartOfMinute",
    "hour": "toStartOfHour",
    "day": "toStartOfDay",
    "week": "toMonday",
    "month": "toStartOfMonth",
    "quarter": "toStartOfQuarter",
    "year": "toStartOfYear",
}


def postprocess_sql(sql: str) -> str:
    """Replace dateTrunc with native ClickHouse toStartOf* functions.

    Ibis 12.0.0 generates dateTrunc('DAY', col) etc. for ClickHouse, but
    dateTrunc is unsupported or unreliable in ClickHouse 22.3. Native
    ClickHouse functions (toStartOfDay, toStartOfHour, toMonday, etc.)
    work in all versions.

    This transforms:
        dateTrunc('DAY', col)   -> toStartOfDay(col)
        dateTrunc('HOUR', col)  -> toStartOfHour(col)
        dateTrunc('WEEK', col)  -> toMonday(col)
        etc.
    Any surrounding CAST wrapper is preserved.
    """

    def _replace_unit(match: re.Match[str]) -> str:
        unit = match.group(1).lower()
        native = _DATETRUNC_TO_NATIVE.get(unit)
        if native is None:
            return match.group(0)
        return f"{native}("

    return re.sub(r"dateTrunc\('([A-Za-z]+)',\s*", _replace_unit, sql)


PROFILE = EngineProfile(
    name="clickhouse",
    aliases=(),
    authoring_func="clickhouse",
    required_modules=("ibis.backends.clickhouse",),
    connect=connect,
    apply_read_only_kwargs=apply_read_only_kwargs,
    timezone_probe_sql="select timezone() as timezone",
    identifier_quote="`",
    table_name_parts=table_name_parts,
    inspect_partition_values=inspect_partition_values,
    readonly_tx_start=None,
    metadata=EngineMetadataIntrospection(inspect_table=inspect_table),
    translate_strptime_format=identity_str,
    postprocess_sql=postprocess_sql,
    datetime_decode_policy="utc_naive_instant",
    quantile=QuantileCapability(mode="approximate", method="reservoir_sampling"),
    percentile_uses_approx_quantile=False,
)
