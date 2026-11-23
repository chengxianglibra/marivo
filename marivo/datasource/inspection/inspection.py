"""Metadata-only source inspection for datasource authoring."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from marivo._kernel.project import resolve_project_root
from marivo._kernel.refs import DatasourceKind, Ref, SemanticKind
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.symbolic import (
    DatasourceIdentityHandle,
    DatasourceSymbol,
    native_symbol_ref,
)
from marivo._kernel.validation import has_exact_type
from marivo.datasource import credentials as cr
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import (
    DatasourceMetadataError,
    repair,
)
from marivo.datasource.inspection.details import (
    Partitioning,
    _execution_capabilities,
    _physical_extent,
)
from marivo.datasource.inspection.errors import _authoring_error
from marivo.datasource.inspection.metadata import (
    _inspect_source_from_ir,
    _TableMetadataUnavailableError,
)
from marivo.datasource.inspection.partitions import _captured_partitioning, _project_partitioning
from marivo.datasource.inspection.projection import (
    _declared_only_table_metadata,
    _project_physical_schema,
    _project_table_metadata,
    _unprojected_table,
)
from marivo.datasource.inspection.source import SourceInspection
from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.datasource.metadata_types import (
    ColumnMetadata,
    MetadataWarning,
    TableMetadata,
    _schema_columns,
)
from marivo.datasource.runtime.connections import DatasourceConnectionService
from marivo.datasource.source import (
    TableSource,
)
from marivo.datasource.store import declarations as _store
from marivo.datasource.store.authoring import _loaded_datasource_ref, _storage_name
from marivo.telemetry import traced


def _parquet_metadata(
    datasource_ir: DatasourceIR,
    source: ParquetSourceIR,
    connections: DatasourceConnectionService,
) -> TableMetadata:
    backend = connections.backend_for(datasource_ir)
    try:
        reader = getattr(backend, "read_parquet", None)
        if not callable(reader):
            raise _authoring_error(
                code="source_mismatch",
                stage="inspect",
                expected="a DuckDB backend with Parquet footer inspection",
                received=datasource_ir.backend_type,
                reason="the datasource backend cannot inspect Parquet footer schema",
                scope_state=None,
            )
        options: dict[str, object] = {}
        if source.hive_partitioning:
            options["hive_partitioning"] = True
        table_expr = reader(source.path, **options)
        if source.columns is not None:
            table_expr = table_expr.select(*source.columns)
        return TableMetadata(
            datasource=datasource_ir.name,
            table=source.path,
            database=None,
            backend_type=datasource_ir.backend_type,
            comment=None,
            columns=_schema_columns(table_expr),
            partitions=(),
            partition_state="unknown" if source.hive_partitioning else "none",
            warnings=(),
        )
    except Exception as exc:
        if cr.injected_values(backend):
            raise cr.safe_backend_exception(exc, backend) from None
        raise


def _structured_inspection_warnings(
    *,
    metadata: TableMetadata,
    partitioning: Partitioning,
    partition_warnings: tuple[MetadataWarning, ...],
) -> tuple[MetadataWarning, ...]:
    warnings = (*metadata.warnings, *partition_warnings)
    if partitioning.state == "unknown":
        warnings = (
            *warnings,
            MetadataWarning(
                kind="partition_state_unknown",
                message="partition state is unknown",
                columns=tuple(field.name for field in partitioning.fields),
            ),
        )
    return warnings


@traced(surface="datasource", operation="inspect", stage="connect")
def inspect(
    datasource: DatasourceSymbol | DatasourceIdentityHandle,
    source: TableSource,
    *,
    workspace_dir: str | Path | None = None,
) -> SourceInspection:
    """Inspect a physical source through metadata and system-catalog hooks only.

    Args:
        datasource: Typed datasource reference from ``ms.ref.datasource(...)``.
        source: Typed table, Parquet, CSV, or JSON source descriptor.
        workspace_dir: Optional exact project root. When omitted, resolves from
            MARIVO_PROJECT_ROOT, the nearest ancestor manifest, or cwd.

    Returns:
        A metadata-only ``SourceInspection`` with schema, cost, partition, and
        execution-capability evidence.

    Example:
        >>> inspection = md.inspect(
        ...     ms.ref.datasource("warehouse"),
        ...     md.table("orders"),
        ... )
        >>> inspection.show()
        >>> md.inspect(ms.ref.datasource("warehouse"), md.table("orders"), workspace_dir=".")

    Constraints:
        Does not execute a user-data query. Tables use metadata hooks and
        Parquet reads footer metadata. The backend may open local CSV and JSON
        files to discover their columns and observed types. Remote HTTP JSON is
        not fetched. ``datasource`` must be a loaded Datasource or native datasource selector.
        Reads local and configured external model roots. One call uses one
        loaded datasource definition; later calls read current declarations.
    """
    project_root = (
        resolve_project_root() if workspace_dir is None else Path(workspace_dir).resolve()
    )
    reference = _loaded_datasource_ref(datasource, project_root=project_root)
    if reference is None:
        symbolic = native_symbol_ref(datasource)
        reference = (
            ref_factory.datasource(symbolic.path)
            if symbolic is not None and symbolic.kind is SemanticKind.DATASOURCE
            else None
        )
    if reference is None:
        received = "bare string" if isinstance(datasource, str) else type(datasource).__name__
        raise TypeError(
            "datasource must be a loaded Datasource or a native datasource lookup symbol "
            "from spec.ref or ms.ref.datasource('warehouse'); "
            f"received {received}."
        )
    return _inspect_in_project(
        ref_factory.datasource(reference.path), source, project_root=project_root
    )


def _inspect_in_project(
    datasource: Ref[DatasourceKind],
    source: TableSource,
    *,
    project_root: Path,
    connections: DatasourceConnectionService | None = None,
) -> SourceInspection:
    if connections is None:
        with DatasourceConnectionService(project_root).operation() as owned:
            return _inspect_in_project(
                datasource, source, project_root=project_root, connections=owned
            )
    with connections.resolution_context(), cr.operation_context(project_root=project_root):
        return _inspect_bound_source(
            datasource, source, project_root=project_root, connections=connections
        )


def _inspect_bound_source(
    datasource: Ref[DatasourceKind],
    source: TableSource,
    *,
    project_root: Path,
    connections: DatasourceConnectionService,
) -> SourceInspection:
    """Inspect one source against an already-resolved project root."""
    if not has_exact_type(datasource, Ref) or datasource.kind is not SemanticKind.DATASOURCE:
        received = type(datasource).__name__
        if isinstance(datasource, str):
            raise TypeError(
                "datasource must be Ref[datasource], got a bare string. Use "
                'md.inspect(ms.ref.datasource("warehouse"), md.table("orders")).'
            )
        raise TypeError(
            "datasource must be Ref[datasource] from a datasource spec's .ref or "
            f"ms.ref.datasource('warehouse'); got {received}."
        )
    if not isinstance(source, TableSourceIR | ParquetSourceIR | CsvSourceIR | JsonSourceIR):
        raise TypeError("source must be built by md.table, md.parquet, md.csv, or md.json.")

    datasource_name = _storage_name(datasource)
    datasource_ir = _store.load_one(datasource_name, project_root=project_root)
    if datasource_ir is None:
        raise _authoring_error(
            code="datasource_missing",
            stage="project",
            expected=f"registered datasource {datasource.path}",
            received="missing datasource",
            reason=f"datasource {datasource.path!r} is not registered in the active project",
            scope_state=None,
        )
    return _inspect_resolved_source(
        datasource_ir, source, project_root=project_root, connections=connections
    )


def _inspect_resolved_source(
    datasource_ir: DatasourceIR,
    source: TableSource,
    *,
    project_root: Path,
    connections: DatasourceConnectionService,
) -> SourceInspection:
    """Inspect the caller's captured datasource without reloading declarations."""
    datasource_name = datasource_ir.name
    datasource = ref_factory.datasource(datasource_name)
    profile = require_profile_for_backend_type(datasource_ir.backend_type)

    projected_metadata_unavailable = False
    physical_schema: tuple[ColumnMetadata, ...] = ()
    base_partitioning: Partitioning | None = None
    partition_warnings: tuple[MetadataWarning, ...] = ()

    if isinstance(source, CsvSourceIR | JsonSourceIR):
        metadata = _inspect_source_from_ir(
            datasource_ir,
            source=source,
            include_partitions=False,
        )
    elif isinstance(source, ParquetSourceIR):
        if datasource_ir.backend_type != "duckdb":
            raise _authoring_error(
                code="source_mismatch",
                stage="inspect",
                expected="a DuckDB datasource for Parquet sources",
                received=datasource_ir.backend_type,
                reason="Parquet source descriptors require a DuckDB datasource",
                scope_state=None,
            )
        metadata = _parquet_metadata(datasource_ir, source, connections)
    else:
        try:
            base_metadata = _inspect_source_from_ir(
                datasource_ir,
                source=_unprojected_table(source),
                include_partitions=True,
                project_root=project_root,
                connections=connections,
            )
        except _TableMetadataUnavailableError as exc:
            if not source.columns:
                raise DatasourceMetadataError(
                    message=(
                        "base table metadata is unavailable and an unprojected source has no "
                        f"declared schema fallback: {exc.failure.message}"
                    ),
                    _backend_failure=exc.failure,
                    expected="an inspectable datasource table",
                    received=exc.failure.identity,
                    location=f"md.inspect({datasource.path!r}, {source.table!r})",
                    repair=repair(
                        kind="reconnect",
                        canonical_id="inspect",
                        action="Restore base table metadata access before retrying inspection.",
                    ),
                ) from exc
            projected_metadata_unavailable = True
            metadata = _declared_only_table_metadata(
                datasource_name=datasource_name,
                datasource_ir=datasource_ir,
                source=source,
                failure=exc.failure,
            )
        else:
            if source.columns:
                physical_schema = _project_physical_schema(base_metadata, source)
            metadata = (
                _project_table_metadata(base_metadata, source) if source.columns else base_metadata
            )
            base_partitioning, partition_warnings = _captured_partitioning(
                metadata=base_metadata,
                datasource_ir=datasource_ir,
                source=_unprojected_table(source),
                profile=profile,
                connections=connections,
            )

    if base_partitioning is None:
        partitioning, partition_warnings = _captured_partitioning(
            metadata=metadata,
            datasource_ir=datasource_ir,
            source=source,
            profile=profile,
            connections=connections,
        )
    elif isinstance(source, TableSourceIR) and source.columns:
        partitioning = _project_partitioning(base_partitioning, source, metadata)
    else:
        partitioning = base_partitioning

    structured_warnings = _structured_inspection_warnings(
        metadata=metadata,
        partitioning=partitioning,
        partition_warnings=partition_warnings,
    )
    warnings = tuple(warning.message for warning in structured_warnings)
    capabilities = _execution_capabilities(profile)
    if projected_metadata_unavailable or any(
        warning.kind == "projected_partition_unavailable" for warning in metadata.warnings
    ):
        capabilities = replace(capabilities, partition_predicate_supported=False)
    return SourceInspection(
        _datasource=datasource,
        source=source,
        physical_extent=_physical_extent(metadata.physical_profile),
        partitioning=partitioning,
        execution_capabilities=capabilities,
        schema=metadata.columns,
        warnings=warnings,
        _project_root=project_root,
        projectable_columns=metadata.projectable_columns,
        _backend_type=metadata.backend_type,
        _physical_schema=physical_schema,
    )
