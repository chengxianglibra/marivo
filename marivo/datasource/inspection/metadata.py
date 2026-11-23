"""Datasource table metadata inspection DTOs and backend adapters."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
from ibis.backends import BaseBackend

from marivo.datasource import credentials as cr
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.engines.base import MetadataInspectRequest
from marivo.datasource.errors import (
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
    DatasourceError,
    DatasourceMetadataError,
    DatasourceObservedEffects,
    _backend_failure_summary,
    _BackendFailureSummary,
    repair,
)
from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    EntitySourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
    source_name,
)
from marivo.datasource.metadata_types import (
    MetadataWarning,
    TableMetadata,
    schema_only_metadata,
)
from marivo.datasource.runtime.adapters import SourceSession
from marivo.datasource.runtime.connections import DatasourceConnectionService
from marivo.datasource.runtime.json_source import read_json_source
from marivo.datasource.store import declarations as _store


class _TableMetadataUnavailableError(Exception):
    """Internal classified table-resolution failure for projected inspection."""

    def __init__(self, failure: _BackendFailureSummary) -> None:
        self.failure = failure
        super().__init__(failure.message)


def _with_primary_key_capability_warning(metadata: TableMetadata) -> TableMetadata:
    """Append ``primary_keys_unavailable`` for backends that do not expose PK metadata.

    DuckDB (duckdb_constraints), PostgreSQL
    (pg_constraint), MySQL (SHOW INDEX) and Trino (information_schema table
    constraints) populate primary keys through the provider statement channel
    and are left alone. ClickHouse has no primary-key concept; its absence is
    disclosed with a single capability warning so it is never silent.
    """
    if metadata.backend_type in {"duckdb", "postgres", "mysql", "trino"}:
        return metadata
    if metadata.primary_keys:
        return metadata
    if any(warning.kind == "primary_keys_unavailable" for warning in metadata.warnings):
        return metadata
    return replace(
        metadata,
        warnings=(
            *metadata.warnings,
            MetadataWarning(
                kind="primary_keys_unavailable",
                message=f"{metadata.backend_type} primary key metadata is not exposed by this adapter",
            ),
        ),
    )


def inspect_table(
    datasource: str,
    *,
    table: str,
    database: str | tuple[str, ...] | None = None,
    include_partitions: bool = True,
    project_root: Path | None = None,
    connections: DatasourceConnectionService | None = None,
) -> TableMetadata:
    if connections is None:
        with DatasourceConnectionService(project_root).operation() as owned:
            return inspect_table(
                datasource,
                table=table,
                database=database,
                include_partitions=include_partitions,
                project_root=project_root,
                connections=owned,
            )
    datasource_ir = _store.load_one(datasource, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMetadataError(
            message=f"datasource {datasource!r} is not configured",
            expected="a registered project datasource",
            received=datasource,
            location="models/datasources/",
            repair=repair(
                kind="register",
                canonical_id="register",
                action="Register the datasource before inspecting it.",
                candidates=tuple(_store.list_names(project_root)),
            ),
        )

    return _inspect_table_from_ir(
        datasource_ir,
        table=table,
        database=database,
        include_partitions=include_partitions,
        project_root=project_root,
        connections=connections,
    )


def _inspect_table_from_ir(
    datasource_ir: DatasourceIR,
    *,
    table: str,
    database: str | tuple[str, ...] | None = None,
    include_partitions: bool = True,
    project_root: Path | None = None,
    connections: DatasourceConnectionService | None = None,
) -> TableMetadata:
    if connections is None:
        with DatasourceConnectionService(project_root).operation() as owned:
            return _inspect_table_from_ir(
                datasource_ir,
                table=table,
                database=database,
                include_partitions=include_partitions,
                project_root=project_root,
                connections=owned,
            )
    datasource = datasource_ir.name
    profile = require_profile_for_backend_type(datasource_ir.backend_type)
    backend: BaseBackend | None = None
    session: SourceSession | None = None
    try:
        try:
            backend = connections.backend_for(datasource_ir)
            if isinstance(backend, BaseBackend):
                session = SourceSession(profile, datasource_ir, backend, owns_backend=False)
        except (DatasourceCredentialError, DatasourceCredentialScopeError):
            raise
        except Exception as exc:
            exc = cr.safe_backend_exception(exc, backend)
            failure = _backend_failure_summary(exc)
            raise DatasourceMetadataError(
                message=(
                    f"failed to connect while inspecting datasource table "
                    f"{datasource!r}.{table!r}: {failure.message}"
                ),
                _backend_failure=failure,
                expected="an inspectable datasource table",
                received=failure.identity,
                location=f"md.inspect({datasource!r}, {table!r})",
                repair=repair(
                    kind="reconnect",
                    canonical_id="inspect",
                    action="Verify the datasource connection and table name before retrying.",
                ),
            ) from exc

        try:
            table_expr = (
                session.bind_source(
                    TableSourceIR(table, database=database),
                    source_identity=f"metadata:{datasource}:{database}:{table}",
                ).relation
                if session is not None
                else (
                    backend.table(table)
                    if database is None
                    else backend.table(table, database=database)
                )
            )
        except (DatasourceCredentialError, DatasourceCredentialScopeError):
            raise
        except Exception as exc:
            unavailable = profile.metadata.classify_table_resolution_failure(exc)
            exc = cr.safe_backend_exception(exc, backend)
            failure = _backend_failure_summary(exc)
            if unavailable == "metadata_unavailable":
                raise _TableMetadataUnavailableError(failure) from exc
            raise DatasourceMetadataError(
                message=(
                    f"failed to resolve datasource table {datasource!r}.{table!r}: "
                    f"{failure.message}"
                ),
                _backend_failure=failure,
                expected="an inspectable datasource table",
                received=failure.identity,
                location=f"md.inspect({datasource!r}, {table!r})",
                repair=repair(
                    kind="reconnect",
                    canonical_id="inspect",
                    action="Verify the datasource connection and table name before retrying.",
                ),
            ) from exc

        try:
            metadata = profile.metadata.inspect_table(
                MetadataInspectRequest(
                    datasource=datasource,
                    backend=backend,
                    table=table,
                    database=database,
                    table_expr=table_expr,
                    include_partitions=include_partitions,
                    datasource_ir=datasource_ir,
                )
            )
        except DatasourceMetadataError:
            raise
        except Exception as exc:
            metadata = schema_only_metadata(
                datasource=datasource,
                table=table,
                database=database,
                backend_type=datasource_ir.backend_type,
                table_expr=table_expr,
                warnings=(
                    MetadataWarning(
                        kind="metadata_query_failed",
                        message=f"{datasource_ir.backend_type} metadata query failed: {cr.safe_backend_exception(exc, backend)}",
                    ),
                ),
            )
    finally:
        # The temporary source owner releases its handles; the batch connection
        # owner releases the borrowed backend after all inspection work.
        if session is not None:
            session.close()
    return _with_primary_key_capability_warning(metadata)


def _inspect_source(
    datasource: str,
    *,
    source: EntitySourceIR,
    include_partitions: bool = True,
    project_root: Path | None = None,
    connections: DatasourceConnectionService | None = None,
) -> TableMetadata:
    if connections is None:
        with DatasourceConnectionService(project_root).operation() as owned:
            return _inspect_source(
                datasource,
                source=source,
                include_partitions=include_partitions,
                project_root=project_root,
                connections=owned,
            )
    datasource_ir = _store.load_one(datasource, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMetadataError(
            message=f"datasource {datasource!r} is not configured",
            expected="a registered project datasource",
            received=datasource,
            location="models/datasources/",
            repair=repair(
                kind="register",
                canonical_id="register",
                action="Register the datasource before inspecting it.",
                candidates=tuple(_store.list_names(project_root)),
            ),
        )
    return _inspect_source_from_ir(
        datasource_ir,
        source=source,
        include_partitions=include_partitions,
        project_root=project_root,
        connections=connections,
    )


def _inspect_source_from_ir(
    datasource_ir: DatasourceIR,
    *,
    source: EntitySourceIR,
    include_partitions: bool = True,
    project_root: Path | None = None,
    connections: DatasourceConnectionService | None = None,
) -> TableMetadata:
    if connections is None:
        with DatasourceConnectionService(project_root).operation() as owned:
            return _inspect_source_from_ir(
                datasource_ir,
                source=source,
                include_partitions=include_partitions,
                project_root=project_root,
                connections=owned,
            )
    datasource = datasource_ir.name
    if isinstance(source, TableSourceIR):
        return _inspect_table_from_ir(
            datasource_ir,
            table=str(source.table),
            database=source.database,
            include_partitions=include_partitions,
            project_root=project_root,
            connections=connections,
        )
    if not isinstance(source, (ParquetSourceIR, CsvSourceIR, JsonSourceIR)):
        raise DatasourceMetadataError(
            message=f"unsupported datasource source kind {getattr(source, 'kind', None)!r}",
            expected="a table, parquet, CSV, or JSON datasource source",
            received=str(getattr(source, "kind", None)),
            location=f"datasource {datasource!r}",
            repair=repair(
                kind="reauthor",
                canonical_id="inspect",
                action="Use a supported datasource source kind.",
            ),
        )

    if isinstance(source, CsvSourceIR) and datasource_ir.backend_type != "duckdb":
        raise DatasourceMetadataError(
            message="CSV source type discovery requires a DuckDB datasource",
            expected="a DuckDB datasource for CSV source inspection",
            received=datasource_ir.backend_type,
            location=f"md.inspect({datasource!r}, {source.path!r})",
            effect_observed=DatasourceObservedEffects(query_executed=False),
            repair=repair(
                kind="reconnect",
                canonical_id="inspect",
                action="Register a DuckDB datasource to inspect a CSV source.",
            ),
        )
    try:
        backend = connections.backend_for(datasource_ir)
    except DatasourceError:
        raise
    except Exception as exc:
        raise DatasourceMetadataError(
            message=f"failed to inspect datasource file source {datasource!r}.{source.path!r}: {exc}",
            expected="an inspectable datasource file source",
            received=str(exc),
            location=f"md.inspect({datasource!r}, {source.path!r})",
            repair=repair(
                kind="reconnect",
                canonical_id="inspect",
                action="Verify the datasource connection and file source before retrying.",
            ),
        ) from exc

    try:
        table_expr = _file_metadata_expression(backend, source)
    except Exception as exc:
        raise DatasourceMetadataError(
            message=f"failed to inspect datasource file source {datasource!r}.{source.path!r}: {exc}",
            expected="an inspectable file datasource source",
            received=str(exc),
            location=f"md.inspect({datasource!r}, {source.path!r})",
            repair=repair(
                kind="reconnect",
                canonical_id="inspect",
                action="Verify the datasource connection and file source before retrying.",
            ),
        ) from exc

    result = _with_primary_key_capability_warning(
        schema_only_metadata(
            datasource=datasource,
            table=source_name(source),
            database=None,
            backend_type=datasource_ir.backend_type,
            table_expr=table_expr,
            is_view=False,
            warnings=(
                MetadataWarning(
                    kind="comments_unavailable",
                    message="file source comments are not available",
                ),
                MetadataWarning(
                    kind="nullable_unavailable",
                    message="file source nullable flags are not available",
                ),
                MetadataWarning(
                    kind="partitions_unavailable",
                    message="file source partition metadata is not available",
                ),
            ),
        )
    )
    if isinstance(source, JsonSourceIR) and source.path.lower().startswith(("http://", "https://")):
        result = replace(
            result,
            columns=tuple(replace(column, type="unknown") for column in result.columns),
        )
    return result


def _file_metadata_expression(
    backend: BaseBackend, source: ParquetSourceIR | CsvSourceIR | JsonSourceIR
) -> ir.Table:
    kwargs: dict[str, object] = {}
    if isinstance(source, ParquetSourceIR):
        reader = getattr(backend, "read_parquet", None)
        if reader is None:
            raise AttributeError("backend has no read_parquet()")
        if source.hive_partitioning:
            kwargs["hive_partitioning"] = source.hive_partitioning
        if source.columns is not None:
            kwargs["columns"] = list(source.columns)
        table_expr = reader(source.path, **kwargs)
    elif isinstance(source, CsvSourceIR):
        reader = getattr(backend, "read_csv", None)
        if reader is None:
            raise AttributeError("backend has no read_csv()")
        if not source.header:
            kwargs["header"] = source.header
        if source.delimiter != ",":
            kwargs["delimiter"] = source.delimiter
        table_expr = reader(source.path, **kwargs)
        if source.columns:
            table_expr = table_expr.select(
                *(
                    table_expr[source_name].name(output_name)
                    for output_name, source_name in source.columns
                )
            )
    else:
        if source.path.lower().startswith(("http://", "https://")):
            # Inspection must not fetch a remote response only to rediscover types.
            table_expr = ibis.table(
                {output: dt.null for output, _path in source.columns},
                name=source_name(source),
            )
        else:
            table_expr = read_json_source(backend, source)
    return table_expr
