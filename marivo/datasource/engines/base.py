"""Engine profile foundation: dataclasses, generic profile, and cursor decode."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import ibis.expr.types as ir
from ibis.backends import BaseBackend

from marivo.datasource.ir import DatasourceIR, TableSourceIR

if TYPE_CHECKING:
    from marivo.datasource.metadata import MetadataWarning, TableMetadata

type BackendDatetimeDecodePolicy = Literal["local_naive_label", "utc_naive_instant"]
type PartitionValueSource = Literal["metadata", "system_catalog"]


@dataclass(frozen=True)
class QuantileCapability:
    mode: Literal["exact", "approximate"]
    method: str


@dataclass(frozen=True)
class CursorFrame:
    columns: tuple[str, ...]
    rows: tuple[dict[str, object], ...]
    types: dict[str, str]


@dataclass(frozen=True)
class TableRefRequest:
    source: TableSourceIR
    datasource_ir: DatasourceIR


@dataclass(frozen=True)
class PartitionProbeRequest:
    backend: BaseBackend
    datasource_ir: DatasourceIR
    source: TableSourceIR
    partition_columns: tuple[str, ...]
    limit: int


@dataclass(frozen=True)
class PartitionProbeResult:
    rows: tuple[dict[str, object], ...]
    value_source: PartitionValueSource


@dataclass(frozen=True)
class MetadataInspectRequest:
    datasource: str
    backend: BaseBackend
    table: str
    database: str | tuple[str, ...] | None
    table_expr: ir.Table
    include_partitions: bool
    datasource_ir: DatasourceIR


@dataclass(frozen=True)
class EngineMetadataIntrospection:
    inspect_table: Callable[[MetadataInspectRequest], TableMetadata]
    schema_only_warnings: tuple[MetadataWarning, ...] = ()


@dataclass(frozen=True)
class EngineProfile:
    name: str
    aliases: tuple[str, ...]
    authoring_func: str
    required_modules: tuple[str, ...]
    connect: Callable[[str, Mapping[str, object]], BaseBackend]
    apply_read_only_kwargs: Callable[[Mapping[str, object]], dict[str, object]]
    timezone_probe_sql: str | None
    identifier_quote: str
    table_name_parts: Callable[[TableRefRequest], tuple[str, ...]]
    inspect_partition_values: Callable[[PartitionProbeRequest], PartitionProbeResult] | None
    readonly_tx_start: str | None
    metadata: EngineMetadataIntrospection
    translate_strptime_format: Callable[[str], str]
    postprocess_sql: Callable[[str], str]
    datetime_decode_policy: BackendDatetimeDecodePolicy
    quantile: QuantileCapability | None
    percentile_uses_approx_quantile: bool


def identity_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    return dict(kwargs)


def identity_str(value: str) -> str:
    return value


def default_table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    database = request.source.database
    if database is None:
        return (request.source.table,)
    if isinstance(database, tuple):
        return (*tuple(str(part) for part in database), request.source.table)
    return (str(database), request.source.table)


def quote_identifier(value: str, profile: EngineProfile) -> str:
    """Quote *value* as a SQL identifier using *profile*'s quote character."""
    quote = profile.identifier_quote
    escaped = value.replace(quote, quote + quote)
    return f"{quote}{escaped}{quote}"


def require_field(name: str, kwargs: Mapping[str, object], key: str) -> object:
    from marivo.datasource.errors import DatasourceFieldInvalidError

    if key not in kwargs:
        raise DatasourceFieldInvalidError(
            message=f"datasource {name!r} missing required field {key!r}",
            details={"datasource": name, "field": key, "reason": "required field missing"},
        )
    return kwargs[key]


def _generic_connect_unsupported(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    from marivo.datasource.errors import DatasourceBackendTypeUnsupportedError

    raise DatasourceBackendTypeUnsupportedError(
        message=f"datasource {name!r} cannot connect through GENERIC_PROFILE",
        details={"datasource": name, "backend_type": "generic"},
    )


def generic_metadata_inspect(request: MetadataInspectRequest) -> TableMetadata:
    from marivo.datasource.metadata import _schema_only

    return _schema_only(
        datasource=request.datasource,
        table=request.table,
        database=request.database,
        backend_type=GENERIC_PROFILE.name,
        table_expr=request.table_expr,
        warnings=GENERIC_PROFILE.metadata.schema_only_warnings,
    )


def decode_cursor_frame(
    cursor: object,
    *,
    include_types: bool,
    max_rows: int | None,
) -> CursorFrame:
    row_limit = max_rows + 1 if max_rows is not None else None
    description = getattr(cursor, "description", None)
    fetchall = getattr(cursor, "fetchall", None)
    if description is not None and callable(fetchall):
        columns = tuple(str(item[0]) for item in description)
        types = {str(item[0]): str(item[1]) for item in description} if include_types else {}
        fetchmany = getattr(cursor, "fetchmany", None)
        raw_rows = (
            fetchmany(row_limit) if row_limit is not None and callable(fetchmany) else fetchall()
        )
        if row_limit is not None:
            raw_rows = raw_rows[:row_limit]
        return CursorFrame(
            columns=columns,
            rows=tuple(dict(zip(columns, row, strict=True)) for row in raw_rows),
            types=types,
        )
    column_names = getattr(cursor, "column_names", None)
    result_rows = getattr(cursor, "result_rows", None)
    if column_names and result_rows is not None:
        columns = tuple(str(name) for name in column_names)
        raw_rows = result_rows[:row_limit] if row_limit is not None else result_rows
        return CursorFrame(
            columns=columns,
            rows=tuple(dict(zip(columns, row, strict=True)) for row in raw_rows),
            types={},
        )
    return CursorFrame(columns=(), rows=(), types={})


GENERIC_PROFILE = EngineProfile(
    name="generic",
    aliases=(),
    authoring_func="",
    required_modules=(),
    connect=_generic_connect_unsupported,
    apply_read_only_kwargs=identity_read_only_kwargs,
    timezone_probe_sql=None,
    identifier_quote='"',
    table_name_parts=default_table_name_parts,
    inspect_partition_values=None,
    readonly_tx_start=None,
    metadata=EngineMetadataIntrospection(inspect_table=generic_metadata_inspect),
    translate_strptime_format=identity_str,
    postprocess_sql=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=None,
    percentile_uses_approx_quantile=False,
)
