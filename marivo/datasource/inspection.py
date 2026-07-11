"""Metadata-only source inspection for datasource authoring."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from marivo.config import find_project_root
from marivo.datasource import backends as _backends
from marivo.datasource import store as _store
from marivo.datasource.authoring import DatasourceRef, _storage_name
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.engines.base import EngineProfile, PartitionProbeRequest
from marivo.datasource.errors import DatasourceAuthoringError
from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.datasource.metadata import (
    ColumnMetadata,
    PartitionMetadata,
    TableMetadata,
    TablePhysicalProfile,
    _inspect_source,
    _schema_columns,
)
from marivo.datasource.snapshot import DiscoverySnapshot, acquire_snapshot
from marivo.datasource.source import (
    AuthoringScope,
    PartitionScope,
    TableSource,
    UnprunedScope,
)
from marivo.render import Card, RenderableResult

_PARTITION_VALUE_LIMIT = 100


@dataclass(frozen=True)
class PhysicalExtent:
    row_count: int | None
    row_count_kind: Literal["exact", "estimated", "unknown"]
    size_bytes: int | None
    size_kind: Literal["exact", "estimated", "unknown"]
    source: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Partitioning:
    state: Literal["known", "none", "unknown"]
    fields: tuple[PartitionMetadata, ...]
    value_source: str | None
    values: tuple[tuple[tuple[str, str], ...], ...]
    values_complete: bool
    truncated: bool


@dataclass(frozen=True)
class ExecutionCapabilities:
    partition_predicate_supported: bool
    transformed_partition_supported: bool
    timeout_enforced: bool
    byte_estimate_supported: bool


@dataclass(frozen=True, repr=False)
class PartitionInspection(RenderableResult):
    datasource: DatasourceRef
    source: TableSource
    partitioning: Partitioning
    status: Literal["complete", "incomplete"]
    issues: tuple[str, ...]
    next_calls: tuple[str, ...]

    def _repr_identity(self) -> str:
        return (
            f"PartitionInspection datasource={self.datasource.id} "
            f"state={self.partitioning.state} status={self.status}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".partitioning", ".render()", ".show()"),
        ).field(
            label="partition fields",
            value=", ".join(field.name for field in self.partitioning.fields) or "none",
        )
        if self.issues:
            card.listing("issues", self.issues)
        if self.next_calls:
            card.listing("next calls", self.next_calls)
        return card


@dataclass(frozen=True, repr=False)
class SourceInspection(RenderableResult):
    datasource: DatasourceRef
    source: TableSource
    physical_extent: PhysicalExtent
    partitioning: Partitioning
    execution_capabilities: ExecutionCapabilities
    schema: tuple[ColumnMetadata, ...]
    next_safe_action: str
    warnings: tuple[str, ...]
    _project_root: Path

    def _repr_identity(self) -> str:
        return (
            f"SourceInspection datasource={self.datasource.id} source={self.source.kind} "
            f"columns={len(self.schema)} partition_state={self.partitioning.state}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(
                ".partitions()",
                ".sample(scope=..., columns=(...))",
                ".render()",
                ".show()",
            ),
        )
        card.field(
            label="physical extent",
            value=(
                f"rows={self.physical_extent.row_count} "
                f"row_count_kind={self.physical_extent.row_count_kind} "
                f"size_bytes={self.physical_extent.size_bytes} "
                f"size_kind={self.physical_extent.size_kind} "
                f"source={self.physical_extent.source}"
            ),
        )
        card.field(
            label="partitioning",
            value=(
                f"state={self.partitioning.state} "
                f"fields={','.join(field.name for field in self.partitioning.fields) or 'none'} "
                f"values={len(self.partitioning.values)}"
            ),
        )
        card.field(
            label="execution capabilities",
            value=(
                "partition_predicate_supported="
                f"{self.execution_capabilities.partition_predicate_supported} "
                "transformed_partition_supported="
                f"{self.execution_capabilities.transformed_partition_supported} "
                f"timeout_enforced={self.execution_capabilities.timeout_enforced} "
                f"byte_estimate_supported={self.execution_capabilities.byte_estimate_supported}"
            ),
        )
        card.table(
            columns=("column", "type", "nullable"),
            rows=(
                (
                    column.name,
                    column.type,
                    "?" if column.nullable is None else ("Y" if column.nullable else "N"),
                )
                for column in self.schema
            ),
            row_count=len(self.schema),
            label="schema",
        )
        card.field(label="next safe action", value=self.next_safe_action)
        if self.warnings:
            card.listing("warnings", self.warnings)
        return card

    def partitions(self) -> PartitionInspection:
        """Return partition evidence already captured by ``md.inspect(...)``."""
        issues = _partition_issues(self.partitioning)
        transformed = any(field.transform is not None for field in self.partitioning.fields)
        next_calls: tuple[str, ...]
        if self.partitioning.state == "unknown":
            next_calls = (
                "inspection.show()",
                _unpruned_sample_call(tuple(column.name for column in self.schema)),
            )
        elif self.partitioning.state == "none" and self.execution_capabilities.timeout_enforced:
            next_calls = ("md.unpruned(max_rows=..., timeout_seconds=...)",)
        elif self.partitioning.values and not transformed:
            next_calls = ("md.partition({...}, max_rows=..., timeout_seconds=...)",)
        else:
            next_calls = ("md.inspect(...)",)
        return PartitionInspection(
            datasource=self.datasource,
            source=self.source,
            partitioning=self.partitioning,
            status="complete" if not issues else "incomplete",
            issues=issues,
            next_calls=next_calls,
        )

    def sample(
        self,
        *,
        scope: AuthoringScope,
        columns: tuple[str, ...],
        persist_values: bool = False,
        refresh: bool = False,
    ) -> DiscoverySnapshot:
        """Acquire a bounded snapshot through the Task 3 executor after preflight."""
        _preflight_sample(self, scope=scope, columns=columns)
        return acquire_snapshot(
            self,
            scope=scope,
            columns=columns,
            persist_values=persist_values,
            refresh=refresh,
        )


def _authoring_error(
    *,
    code: str,
    stage: Literal["inspect", "preflight", "acquire", "cache", "project"],
    expected: str,
    received: str,
    reason: str,
    scope_state: Literal["known", "none", "unknown"] | None,
    next_calls: tuple[str, ...],
) -> DatasourceAuthoringError:
    return DatasourceAuthoringError(
        code=code,
        stage=stage,
        expected=expected,
        received=received,
        reason=reason,
        query_executed=False,
        scope_state=scope_state,
        next_calls=next_calls,
    )


def _preflight_sample(
    inspection: SourceInspection,
    *,
    scope: AuthoringScope,
    columns: tuple[str, ...],
) -> None:
    state = inspection.partitioning.state
    if type(columns) is not tuple or any(not isinstance(column, str) for column in columns):
        raise TypeError("columns must be tuple[str, ...].")
    if not columns:
        raise _authoring_error(
            code="selected_columns_required",
            stage="preflight",
            expected="a non-empty tuple of inspected source columns",
            received="empty columns",
            reason="snapshot acquisition requires at least one selected source column",
            scope_state=state,
            next_calls=("md.inspect(...)",),
        )
    available = {column.name for column in inspection.schema}
    for column in columns:
        if column not in available:
            raise _authoring_error(
                code="unknown_source_column",
                stage="preflight",
                expected="columns from the inspected source schema",
                received=column,
                reason=(
                    f"selected column {column!r} is not present in the inspected source schema"
                ),
                scope_state=state,
                next_calls=("md.inspect(...)",),
            )
    if not isinstance(scope, PartitionScope | UnprunedScope):
        raise TypeError("scope must be md.PartitionScope or md.UnprunedScope.")
    _validate_scope_values(scope)

    if state == "unknown" and isinstance(scope, PartitionScope):
        raise _authoring_error(
            code="partition_state_unknown",
            stage="preflight",
            expected="an explicit unpruned scope acknowledging unknown partition state",
            received="partition scope",
            reason="metadata could not prove whether the source is partitioned",
            scope_state=state,
            next_calls=("inspection.show()", _unpruned_sample_call(columns)),
        )

    transformed = tuple(
        field.name for field in inspection.partitioning.fields if field.transform is not None
    )
    if transformed:
        raise _authoring_error(
            code="transformed_partition_unsupported",
            stage="preflight",
            expected="untransformed partition fields expressible by the V1 adapter contract",
            received=", ".join(transformed),
            reason="transformed partition fields cannot be expressed safely in V1",
            scope_state=state,
            next_calls=("inspection.partitions()",),
        )

    if isinstance(scope, PartitionScope):
        expected_fields = tuple(field.name for field in inspection.partitioning.fields)
        received_fields = tuple(name for name, _value in scope.values)
        if (
            state != "known"
            or len(received_fields) != len(expected_fields)
            or set(received_fields) != set(expected_fields)
        ):
            raise _authoring_error(
                code="incomplete_partition_fields",
                stage="preflight",
                expected=", ".join(expected_fields) or f"unpruned scope for {state} state",
                received=", ".join(received_fields) or "none",
                reason="partition scope must cover every known partition field exactly once",
                scope_state=state,
                next_calls=("inspection.partitions()",),
            )
        if not inspection.execution_capabilities.partition_predicate_supported:
            raise _authoring_error(
                code="partition_predicate_unsupported",
                stage="preflight",
                expected="an adapter with partition predicate pushdown",
                received="partition predicate unsupported",
                reason="the adapter cannot push down the requested partition predicate",
                scope_state=state,
                next_calls=("inspection.partitions()",),
            )
    elif state == "known":
        raise _authoring_error(
            code="incomplete_partition_fields",
            stage="preflight",
            expected=", ".join(field.name for field in inspection.partitioning.fields),
            received="unpruned scope",
            reason="known partition fields require an explicit complete partition scope",
            scope_state=state,
            next_calls=("inspection.partitions()",),
        )

    if not inspection.execution_capabilities.timeout_enforced:
        raise _authoring_error(
            code="timeout_not_enforceable",
            stage="preflight",
            expected="an adapter-enforced acquisition timeout",
            received=f"timeout_seconds={scope.timeout_seconds}",
            reason="the datasource adapter cannot enforce the requested acquisition timeout",
            scope_state=state,
            next_calls=("md.inspect(...)",),
        )


def _validate_scope_values(scope: AuthoringScope) -> None:
    for field, guard_value in (
        ("max_rows", scope.max_rows),
        ("timeout_seconds", scope.timeout_seconds),
    ):
        if type(guard_value) is not int or guard_value < 1:
            raise ValueError(f"{field} must be a positive integer.")
    if not isinstance(scope, PartitionScope):
        return
    if type(scope.values) is not tuple:
        raise TypeError("PartitionScope.values must be tuple[tuple[str, str], ...].")
    if not scope.values:
        raise ValueError("PartitionScope.values must contain at least one partition value.")
    for entry in scope.values:
        if type(entry) is not tuple or len(entry) != 2:
            raise TypeError("PartitionScope.values entries must be tuple[str, str].")
        name, partition_value = entry
        if not isinstance(name, str) or not isinstance(partition_value, str):
            raise TypeError("PartitionScope.values entries must be tuple[str, str].")
        if not name or not partition_value:
            raise ValueError("PartitionScope partition names and values must be non-empty.")


def _unpruned_sample_call(columns: tuple[str, ...]) -> str:
    return (
        "inspection.sample(scope=md.unpruned(max_rows=1000, timeout_seconds=30), "
        f"columns={columns!r})"
    )


def _partition_issues(partitioning: Partitioning) -> tuple[str, ...]:
    issues: list[str] = []
    if partitioning.state == "unknown":
        issues.append("partition state is unknown")
    if partitioning.state == "known" and not partitioning.values_complete:
        issues.append("partition values are incomplete")
    if any(field.transform is not None for field in partitioning.fields):
        issues.append("transformed partition fields are not expressible in V1")
    return tuple(issues)


def _physical_extent(profile: TablePhysicalProfile | None) -> PhysicalExtent:
    if profile is None:
        return PhysicalExtent(
            row_count=None,
            row_count_kind="unknown",
            size_bytes=None,
            size_kind="unknown",
            source="metadata_unavailable",
            notes=(),
        )
    row_count_kind: Literal["exact", "estimated", "unknown"]
    if profile.row_count_kind == "metadata":
        row_count_kind = "exact"
    elif profile.row_count_kind == "estimate":
        row_count_kind = "estimated"
    else:
        row_count_kind = "unknown"
    size_kind: Literal["exact", "estimated", "unknown"]
    if profile.size_kind == "on_disk":
        size_kind = "exact"
    elif profile.size_kind in {"data_plus_index", "table_stats"}:
        size_kind = "estimated"
    else:
        size_kind = "unknown"
    return PhysicalExtent(
        row_count=profile.row_count,
        row_count_kind=row_count_kind,
        size_bytes=profile.size_bytes,
        size_kind=size_kind,
        source=profile.source,
        notes=profile.notes,
    )


def _execution_capabilities(profile: EngineProfile) -> ExecutionCapabilities:
    capabilities = profile.authoring_capabilities
    return ExecutionCapabilities(
        partition_predicate_supported=capabilities.partition_predicate_supported,
        transformed_partition_supported=capabilities.transformed_partition_supported,
        timeout_enforced=profile.authoring_timeout is not None,
        byte_estimate_supported=capabilities.byte_estimate_supported,
    )


def _declared_schema(schema: tuple[tuple[str, str], ...]) -> tuple[ColumnMetadata, ...]:
    if not schema:
        raise _authoring_error(
            code="typed_schema_required",
            stage="inspect",
            expected="a non-empty authored schema mapping",
            received="empty schema",
            reason="CSV and JSON inspection requires an authored typed schema",
            scope_state=None,
            next_calls=("md.csv(..., schema={...})", "md.json(..., schema={...})"),
        )
    return tuple(
        ColumnMetadata(
            name=name,
            type=type_name,
            nullable=None,
            comment=None,
            ordinal_position=index,
        )
        for index, (name, type_name) in enumerate(schema, start=1)
    )


def _parquet_metadata(
    datasource_ir: DatasourceIR,
    source: ParquetSourceIR,
) -> TableMetadata:
    backend = _backends.build_backend(datasource_ir)
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
                next_calls=("md.duckdb(...) through md.register(...) next",),
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
    finally:
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
            with suppress(Exception):
                disconnect()


def _captured_partitioning(
    *,
    metadata: TableMetadata,
    datasource_ir: DatasourceIR,
    source: TableSource,
    profile: EngineProfile,
) -> tuple[Partitioning, tuple[str, ...]]:
    state = metadata.partition_state
    fields = metadata.partitions
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
            ("transformed partition values are not safely expressible in V1",),
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
            ("partition values are unavailable from metadata without scanning user data",),
        )

    backend = None
    try:
        backend = _backends.build_backend(datasource_ir)
        result = hook(
            PartitionProbeRequest(
                backend=backend,
                datasource_ir=datasource_ir,
                source=source,
                partition_columns=tuple(field.name for field in fields),
                limit=_PARTITION_VALUE_LIMIT + 1,
            )
        )
        rows = result.rows[: _PARTITION_VALUE_LIMIT + 1]
        truncated = len(rows) > _PARTITION_VALUE_LIMIT
        complete_values: list[tuple[tuple[str, str], ...]] = []
        omitted_incomplete = 0
        for row in rows[:_PARTITION_VALUE_LIMIT]:
            if any(row.get(field.name) is None for field in fields):
                omitted_incomplete += 1
                continue
            complete_values.append(tuple((field.name, str(row[field.name])) for field in fields))
        warnings = (
            (f"incomplete partition metadata rows omitted={omitted_incomplete}",)
            if omitted_incomplete
            else ()
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
    except Exception as exc:
        return (
            Partitioning(
                state="known",
                fields=fields,
                value_source=None,
                values=(),
                values_complete=False,
                truncated=False,
            ),
            (f"partition metadata value hook failed: {exc}",),
        )
    finally:
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
            with suppress(Exception):
                disconnect()


def inspect(datasource: DatasourceRef, source: TableSource) -> SourceInspection:
    """Inspect a physical source through metadata and system-catalog hooks only.

    Args:
        datasource: Typed datasource reference from ``md.ref(...)``.
        source: Typed table, Parquet, CSV, or JSON source descriptor.

    Returns:
        A metadata-only ``SourceInspection`` with schema, cost, partition, and
        execution-capability evidence.

    Example:
        ``md.inspect(md.ref("datasource.warehouse"), md.table("orders"))``

    Constraints:
        Executes no user-data query. CSV and JSON paths are never opened and
        use only the authored schema. Parquet reads footer schema only.
    """
    if not isinstance(datasource, DatasourceRef):
        raise TypeError('datasource must be md.DatasourceRef from md.ref("datasource.warehouse").')
    if not isinstance(source, TableSourceIR | ParquetSourceIR | CsvSourceIR | JsonSourceIR):
        raise TypeError("source must be built by md.table, md.parquet, md.csv, or md.json.")

    project_root = find_project_root() or Path.cwd()
    datasource_name = _storage_name(datasource)
    datasource_ir = _store.load_one(datasource_name, project_root=project_root)
    if datasource_ir is None:
        raise _authoring_error(
            code="datasource_not_found",
            stage="project",
            expected=f"registered datasource {datasource.id}",
            received="missing datasource",
            reason=f"datasource {datasource.id!r} is not registered in the active project",
            scope_state=None,
            next_calls=("md.register(...) next",),
        )
    profile = require_profile_for_backend_type(datasource_ir.backend_type)

    if isinstance(source, CsvSourceIR | JsonSourceIR):
        if datasource_ir.backend_type != "duckdb":
            raise _authoring_error(
                code="source_mismatch",
                stage="inspect",
                expected="a DuckDB datasource for CSV or JSON sources",
                received=datasource_ir.backend_type,
                reason="CSV and JSON source descriptors require a DuckDB datasource",
                scope_state=None,
                next_calls=("md.duckdb(...) through md.register(...) next",),
            )
        metadata = TableMetadata(
            datasource=datasource_name,
            table=source.path,
            database=None,
            backend_type=datasource_ir.backend_type,
            comment=None,
            columns=_declared_schema(source.schema),
            partitions=(),
            partition_state="none",
            warnings=(),
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
                next_calls=("md.duckdb(...) through md.register(...) next",),
            )
        metadata = _parquet_metadata(datasource_ir, source)
    else:
        metadata = _inspect_source(
            datasource_name,
            source=source,
            include_partitions=True,
            project_root=project_root,
        )

    partitioning, partition_warnings = _captured_partitioning(
        metadata=metadata,
        datasource_ir=datasource_ir,
        source=source,
        profile=profile,
    )
    warnings = tuple(warning.message for warning in metadata.warnings) + partition_warnings
    if partitioning.state == "unknown":
        warnings = (*warnings, "partition state is unknown")
    if partitioning.state != "none":
        next_safe_action = "inspection.partitions()"
    elif profile.authoring_capabilities.timeout_enforced:
        next_safe_action = "inspection.sample(scope=md.unpruned(...), columns=(...))"
    else:
        next_safe_action = "inspect execution_capabilities before sampling"
    return SourceInspection(
        datasource=datasource,
        source=source,
        physical_extent=_physical_extent(metadata.physical_profile),
        partitioning=partitioning,
        execution_capabilities=_execution_capabilities(profile),
        schema=metadata.columns,
        next_safe_action=next_safe_action,
        warnings=warnings,
        _project_root=project_root,
    )
