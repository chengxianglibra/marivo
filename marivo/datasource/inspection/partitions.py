"""Metadata-only source inspection for datasource authoring."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

from marivo._kernel.refs import DatasourceKind, Ref
from marivo.datasource import credentials as cr
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.engines.base import (
    EngineProfile,
    PartitionProbeRequest,
    PartitionProbeResult,
)
from marivo.datasource.errors import (
    DatasourceConnectionError,
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
)
from marivo.datasource.inspection.details import _PARTITION_VALUE_LIMIT, Partitioning
from marivo.datasource.inspection.errors import _authoring_error
from marivo.datasource.inspection.projection import _unprojected_table
from marivo.datasource.ir import (
    DatasourceIR,
    TableSourceIR,
)
from marivo.datasource.metadata_types import MetadataWarning, PartitionMetadata, TableMetadata
from marivo.datasource.runtime.adapters import SourceSession
from marivo.datasource.runtime.connections import DatasourceConnectionService
from marivo.datasource.source import (
    TableSource,
)
from marivo.datasource.store import declarations as _store
from marivo.datasource.store.authoring import _storage_name


def _project_partitioning(
    partitioning: Partitioning,
    source: TableSourceIR,
    effective_metadata: TableMetadata,
) -> Partitioning:
    if effective_metadata.partition_state == "unknown" and partitioning.state == "known":
        return Partitioning(
            state="unknown",
            fields=(),
            value_source=None,
            values=(),
            values_complete=False,
            truncated=False,
        )
    if partitioning.state != "known":
        return replace(partitioning, fields=effective_metadata.partitions)

    source_to_output = {source_name: output for output, source_name in source.columns}
    return replace(
        partitioning,
        fields=effective_metadata.partitions,
        values=tuple(
            tuple((source_to_output[name], value) for name, value in row)
            for row in partitioning.values
        ),
    )


def _captured_partitioning(
    *,
    metadata: TableMetadata,
    datasource_ir: DatasourceIR,
    source: TableSource,
    profile: EngineProfile,
    connections: DatasourceConnectionService,
    limit: int = _PARTITION_VALUE_LIMIT,
    order: Literal["asc", "desc"] = "desc",
) -> tuple[Partitioning, tuple[MetadataWarning, ...]]:
    return _captured_partitioning_for_fields(
        state=metadata.partition_state,
        fields=metadata.partitions,
        datasource_ir=datasource_ir,
        source=source,
        profile=profile,
        connections=connections,
        limit=limit,
        order=order,
    )


def _captured_partitioning_for_fields(
    *,
    state: Literal["known", "none", "unknown"],
    fields: tuple[PartitionMetadata, ...],
    datasource_ir: DatasourceIR,
    source: TableSource,
    profile: EngineProfile,
    connections: DatasourceConnectionService,
    limit: int,
    order: Literal["asc", "desc"],
) -> tuple[Partitioning, tuple[MetadataWarning, ...]]:
    if state == "none":
        return (
            Partitioning(
                state="none",
                fields=(),
                value_source=None,
                values=(),
                values_complete=True,
                truncated=False,
            ),
            (),
        )
    if state != "known" or not isinstance(source, TableSourceIR):
        return (
            Partitioning(
                state=state,
                fields=fields,
                value_source=None,
                values=(),
                values_complete=False,
                truncated=False,
            ),
            (),
        )

    if any(field.transform is not None for field in fields):
        return (
            Partitioning(
                state="known",
                fields=fields,
                value_source=None,
                values=(),
                values_complete=False,
                truncated=False,
            ),
            (
                MetadataWarning(
                    kind="partitions_unavailable",
                    message="transformed partition values are not safely expressible in V1",
                    columns=tuple(field.name for field in fields),
                ),
            ),
        )

    hook = profile.inspect_partition_values
    if hook is None:
        return (
            Partitioning(
                state="known",
                fields=fields,
                value_source=None,
                values=(),
                values_complete=False,
                truncated=False,
            ),
            (
                MetadataWarning(
                    kind="partitions_unavailable",
                    message=(
                        "partition values are unavailable from metadata without scanning user data"
                    ),
                    columns=tuple(field.name for field in fields),
                ),
            ),
        )

    backend = connections.backend_for(datasource_ir)
    try:
        result = hook(
            PartitionProbeRequest(
                open_reader=lambda: SourceSession(
                    profile, datasource_ir, backend, owns_backend=False
                ),
                datasource_ir=datasource_ir,
                source=source,
                partition_columns=tuple(field.name for field in fields),
                limit=limit + 1,
                order=order,
            )
        )
        return _partition_probe_values(result, fields, limit)
    except (DatasourceCredentialError, DatasourceCredentialScopeError):
        raise
    except Exception as exc:
        message = exc.message if isinstance(exc, DatasourceConnectionError) else str(exc)
        message = cr.redact(message, cr.injected_values(backend))
        return (
            Partitioning(
                state="known",
                fields=fields,
                value_source=None,
                values=(),
                values_complete=False,
                truncated=False,
            ),
            (
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"partition metadata value hook failed: {message}",
                    columns=tuple(field.name for field in fields),
                ),
            ),
        )


def _listed_partitioning(
    *,
    datasource: Ref[DatasourceKind],
    source: TableSource,
    partitioning: Partitioning,
    project_root: Path,
    limit: int,
    order: Literal["asc", "desc"],
) -> tuple[Partitioning, tuple[MetadataWarning, ...]]:
    """Read one alternate bounded partition edge for an existing inspection."""
    if partitioning.state != "known" or not isinstance(source, TableSourceIR):
        return partitioning, ()

    datasource_name = _storage_name(datasource)
    datasource_ir = _store.load_one(datasource_name, project_root=project_root)
    if datasource_ir is None:
        raise _authoring_error(
            code="datasource_missing",
            stage="project",
            expected=f"registered datasource {datasource.path}",
            received="missing datasource",
            reason=(f"datasource {datasource.path!r} was removed after source inspection"),
            scope_state=partitioning.state,
        )
    profile = require_profile_for_backend_type(datasource_ir.backend_type)
    physical_source = source
    physical_fields = partitioning.fields
    source_to_output: dict[str, str] = {}
    if source.columns:
        output_to_source = dict(source.columns)
        if any(field.name not in output_to_source for field in physical_fields):
            return partitioning, (
                MetadataWarning(
                    kind="projected_partition_unavailable",
                    message="projected partition fields cannot be mapped back to physical names",
                    columns=tuple(field.name for field in physical_fields),
                ),
            )
        physical_fields = tuple(
            replace(field, name=output_to_source[field.name]) for field in physical_fields
        )
        source_to_output = {source: output for output, source in output_to_source.items()}
        physical_source = _unprojected_table(source)

    with DatasourceConnectionService(project_root).operation() as connections:
        captured, warnings = _captured_partitioning_for_fields(
            state=partitioning.state,
            fields=physical_fields,
            datasource_ir=datasource_ir,
            source=physical_source,
            profile=profile,
            connections=connections,
            limit=limit,
            order=order,
        )
    if not source_to_output or captured.state != "known":
        return captured, warnings
    return (
        replace(
            captured,
            fields=partitioning.fields,
            values=tuple(
                tuple((source_to_output[name], value) for name, value in row)
                for row in captured.values
            ),
        ),
        warnings,
    )


def _partition_probe_values(
    result: PartitionProbeResult, fields: tuple[PartitionMetadata, ...], limit: int
) -> tuple[Partitioning, tuple[MetadataWarning, ...]]:
    rows = result.rows[: limit + 1]
    truncated = len(rows) > limit
    complete_values: list[tuple[tuple[str, str], ...]] = []
    omitted_incomplete = 0
    for row in rows[:limit]:
        if any(row.get(field.name) is None for field in fields):
            omitted_incomplete += 1
            continue
        complete_values.append(tuple((field.name, str(row[field.name])) for field in fields))
    warnings: tuple[MetadataWarning, ...] = ()
    if omitted_incomplete:
        warnings = (
            MetadataWarning(
                kind="partitions_unavailable",
                message=f"incomplete partition metadata rows omitted={omitted_incomplete}",
                columns=tuple(field.name for field in fields),
            ),
        )
    return (
        Partitioning(
            state="known",
            fields=fields,
            value_source=result.value_source,
            values=tuple(complete_values),
            values_complete=not truncated and omitted_incomplete == 0,
            truncated=truncated,
        ),
        warnings,
    )
