"""Metadata-only source inspection for datasource authoring."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import ibis

from marivo._authoring.model import AuthoringContract
from marivo.config import find_project_root
from marivo.datasource import backends as _backends
from marivo.datasource import store as _store
from marivo.datasource._capabilities.contracts import (
    contract_for_partition_inspection,
    contract_for_source_inspection,
    repair_for_authoring_code,
)
from marivo.datasource.authoring import _storage_name
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.engines.base import EngineProfile, PartitionProbeRequest
from marivo.datasource.errors import (
    DatasourceAuthoringError,
    DatasourceMetadataError,
    DatasourceObservedEffects,
    repair,
)
from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
    _format_database_identity,
)
from marivo.datasource.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
    TablePhysicalProfile,
    UniqueConstraintMetadata,
    _inspect_source,
    _schema_columns,
    _TableMetadataUnavailableError,
)
from marivo.datasource.snapshot import DiscoverySnapshot, acquire_snapshot
from marivo.datasource.source import (
    AuthoringScope,
    PartitionScope,
    TableSource,
    UnprunedScope,
)
from marivo.refs import DatasourceKind, Ref, SemanticKind
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
    datasource: Ref[DatasourceKind]
    source: TableSource
    partitioning: Partitioning
    status: Literal["complete", "incomplete"]
    issues: tuple[str, ...]

    def _repr_identity(self) -> str:
        return (
            f"PartitionInspection datasource={self.datasource.path} "
            f"state={self.partitioning.state} status={self.status}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".contract()", ".show()"),
        ).field(
            label="partition fields",
            value=", ".join(field.name for field in self.partitioning.fields) or "none",
        )
        card.field("value source", self.partitioning.value_source or "none")
        card.field("values complete", str(self.partitioning.values_complete))
        card.field("values truncated", str(self.partitioning.truncated))
        range_field = _time_range_partition_field(self.partitioning)
        if range_field is not None:
            card.field(
                "scope template",
                (
                    f"md.time_range({range_field.name!r}, start=<inclusive ISO boundary>, "
                    "end=<exclusive ISO boundary>, max_rows=<positive int>, "
                    "timeout_seconds=<positive int>)"
                ),
            )
        elif self.partitioning.values:
            card.table(
                columns=("captured partition",),
                rows=((repr(dict(value)),) for value in self.partitioning.values),
                row_count=len(self.partitioning.values),
                label="captured partition values",
                show_omission_counts=True,
            )
            card.field(
                "scope template",
                (
                    f"md.partition({dict(self.partitioning.values[0])!r}, "
                    "max_rows=<positive int>, timeout_seconds=<positive int>)"
                ),
            )
        elif self.partitioning.state == "known" and self.partitioning.value_source is None:
            card.field(
                "scope template",
                "md.unpruned(max_rows=<positive int>, timeout_seconds=<positive int>) "
                "because the partition-value hook captured no values",
            )
        elif self.partitioning.state == "known":
            card.field(
                "scope template",
                "unavailable: supply one complete mapping for "
                + ", ".join(field.name for field in self.partitioning.fields),
            )
        if self.issues:
            card.listing("issues", self.issues)
        return card

    def contract(self) -> AuthoringContract:
        """Return factual scope constructors for this captured partition state."""
        return contract_for_partition_inspection(
            datasource_id=self.datasource.path,
            source=self.source,
            partition_state=self.partitioning.state,
            partition_fields=tuple(field.name for field in self.partitioning.fields),
            time_range_available=any(
                field.type is not None and _is_temporal_type(field.type)
                for field in self.partitioning.fields
            ),
        )


@dataclass(frozen=True, repr=False)
class SourceInspection(RenderableResult):
    datasource: Ref[DatasourceKind]
    source: TableSource
    physical_extent: PhysicalExtent
    partitioning: Partitioning
    execution_capabilities: ExecutionCapabilities
    schema: tuple[ColumnMetadata, ...]
    warnings: tuple[str, ...]
    _project_root: Path
    projectable_columns: tuple[ColumnMetadata, ...] = ()

    def _repr_identity(self) -> str:
        return (
            f"SourceInspection datasource={self.datasource.path} source={self.source.kind} "
            f"columns={len(self.schema)} projectable={len(self.projectable_columns)} "
            f"partition_state={self.partitioning.state}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(
                ".contract()",
                ".partitions()",
                ".sample(...)",
                ".show()",
            ),
        )
        if isinstance(self.source, TableSourceIR) and self.source.columns:
            card.field("source kind", "projected table")
            card.field("base table", self.source.table)
            card.field("database", _format_database_identity(self.source.database))
            card.field("projected columns", str(len(self.source.columns)))
            card.field("full source", ".source.to_dict()")
            card.table(
                columns=("output alias", "physical source", "declared type"),
                rows=(
                    (output_name, binding.source, binding.data_type)
                    for output_name, binding in self.source.columns
                ),
                row_count=len(self.source.columns),
                label="column bindings",
                show_omission_counts=True,
            )
        else:
            card.field(
                label="source descriptor",
                value=json.dumps(self.source.to_dict(), sort_keys=True, separators=(",", ":")),
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
        if self.physical_extent.notes:
            card.listing("physical extent notes", self.physical_extent.notes)
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
        card.field(
            label="focused acquisition help",
            value='marivo.help("datasource.SourceInspection.sample")',
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
        if self.projectable_columns:
            card.table(
                columns=("physical column", "ibis type", "nullable", "binding"),
                rows=(
                    (
                        column.name,
                        column.type,
                        "?" if column.nullable is None else ("Y" if column.nullable else "N"),
                        f"md.source_column({column.name!r}, data_type={column.type!r})",
                    )
                    for column in self.projectable_columns
                ),
                row_count=len(self.projectable_columns),
                label="projectable physical columns",
                show_omission_counts=True,
            )
        if self.warnings:
            card.listing("warnings", self.warnings)
        return card

    def contract(self) -> AuthoringContract:
        """Return factual scope and acquisition transitions for this inspection."""
        return contract_for_source_inspection(
            datasource_id=self.datasource.path,
            source=self.source,
            partition_state=self.partitioning.state,
            partition_fields=tuple(field.name for field in self.partitioning.fields),
            time_range_available=(
                self.execution_capabilities.partition_predicate_supported
                and any(_is_temporal_type(column.type) for column in self.schema)
            ),
        )

    def partitions(self) -> PartitionInspection:
        """Return partition evidence already captured by ``md.inspect(...)``."""
        issues = _partition_issues(self.partitioning)
        return PartitionInspection(
            datasource=self.datasource,
            source=self.source,
            partitioning=self.partitioning,
            status="complete" if not issues else "incomplete",
            issues=issues,
        )

    def sample(
        self,
        *,
        scope: AuthoringScope,
        columns: tuple[str, ...],
        persist_values: bool = False,
        refresh: bool = False,
        source_params: (
            Mapping[
                str,
                str | int | float | bool | Sequence[str | int | float | bool],
            ]
            | None
        ) = None,
    ) -> DiscoverySnapshot:
        """Acquire a bounded snapshot after metadata preflight.

        ``source_params`` supplies the exact non-secret runtime values declared
        by ``md.source_param(...)`` on a JSON source. Missing or extra values
        fail before acquisition and the normalized values participate in the
        persisted snapshot identity.
        """
        _preflight_sample(self, scope=scope, columns=columns)
        return acquire_snapshot(
            self,
            scope=scope,
            columns=columns,
            persist_values=persist_values,
            refresh=refresh,
            source_params=source_params,
        )


def _authoring_error(
    *,
    code: str,
    stage: Literal["inspect", "preflight", "acquire", "cache", "project"],
    expected: str,
    received: str,
    reason: str,
    scope_state: Literal["known", "none", "unknown"] | None,
) -> DatasourceAuthoringError:
    return DatasourceAuthoringError(
        code=code,
        stage=stage,
        expected=expected,
        received=received,
        reason=reason,
        effect_observed=DatasourceObservedEffects(query_executed=False, scope_state=scope_state),
        repair=repair_for_authoring_code(code),
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
            )
    if not isinstance(scope, PartitionScope | UnprunedScope):
        raise TypeError("scope must be md.PartitionScope or md.UnprunedScope.")
    _validate_scope_values(scope)

    if state == "unknown" and isinstance(scope, PartitionScope) and scope._time_range is None:
        raise _authoring_error(
            code="partition_state_unknown",
            stage="preflight",
            expected="an explicit unpruned scope acknowledging unknown partition state",
            received="partition scope",
            reason="metadata could not prove whether the source is partitioned",
            scope_state=state,
        )

    transformed = tuple(
        field.name for field in inspection.partitioning.fields if field.transform is not None
    )
    time_predicate = scope._time_range if isinstance(scope, PartitionScope) else None
    if transformed and time_predicate is None:
        raise _authoring_error(
            code="transformed_partition_unsupported",
            stage="preflight",
            expected="untransformed partition fields expressible by the V1 adapter contract",
            received=", ".join(transformed),
            reason="transformed partition fields cannot be expressed safely in V1",
            scope_state=state,
        )

    if time_predicate is not None:
        time_column = next(
            (column for column in inspection.schema if column.name == time_predicate.column),
            None,
        )
        if time_column is None:
            raise _authoring_error(
                code="unknown_source_column",
                stage="preflight",
                expected="a temporal output column from the inspected source schema",
                received=time_predicate.column,
                reason=(
                    f"time-range column {time_predicate.column!r} is not exposed by the "
                    "inspected source; add its projected output binding before acquisition"
                ),
                scope_state=state,
            )
        if not _is_temporal_type(time_column.type):
            raise _authoring_error(
                code="unknown_source_column",
                stage="preflight",
                expected="an inspected date or timestamp column",
                received=f"{time_predicate.column}: {time_column.type}",
                reason="md.time_range(...) can only filter a date or timestamp column",
                scope_state=state,
            )
        if not inspection.execution_capabilities.partition_predicate_supported:
            raise _authoring_error(
                code="partition_predicate_unsupported",
                stage="preflight",
                expected="an adapter with predicate pushdown",
                received="range predicate unsupported",
                reason="the adapter cannot push down the requested time-range predicate",
                scope_state=state,
            )
    elif isinstance(scope, PartitionScope):
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
            )
        if not inspection.execution_capabilities.partition_predicate_supported:
            raise _authoring_error(
                code="partition_predicate_unsupported",
                stage="preflight",
                expected="an adapter with partition predicate pushdown",
                received="partition predicate unsupported",
                reason="the adapter cannot push down the requested partition predicate",
                scope_state=state,
            )
    elif state == "known" and inspection.partitioning.value_source is not None:
        # Partition values were captured from metadata (possibly incomplete or
        # truncated), so a bounded unpruned scope is not the intended path: the
        # author should rescope with the captured partition evidence.
        raise _authoring_error(
            code="incomplete_partition_fields",
            stage="preflight",
            expected=", ".join(field.name for field in inspection.partitioning.fields),
            received="unpruned scope",
            reason="known partition fields require an explicit complete partition scope",
            scope_state=state,
        )

    if not inspection.execution_capabilities.timeout_enforced:
        raise _authoring_error(
            code="timeout_not_enforceable",
            stage="preflight",
            expected="an adapter-enforced acquisition timeout",
            received=f"timeout_seconds={scope.timeout_seconds}",
            reason="the datasource adapter cannot enforce the requested acquisition timeout",
            scope_state=state,
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
    if scope._time_range is not None:
        if scope.values:
            raise ValueError("PartitionScope cannot combine partition values and a time range.")
        predicate = scope._time_range
        if not predicate.column:
            raise ValueError("PartitionScope time-range column must be non-empty.")
        if type(predicate.start) is not type(predicate.end) or predicate.start >= predicate.end:
            raise ValueError("PartitionScope time-range boundaries are invalid.")
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


def _partition_issues(partitioning: Partitioning) -> tuple[str, ...]:
    issues: list[str] = []
    if partitioning.state == "unknown":
        issues.append("partition state is unknown")
    if partitioning.state == "known" and not partitioning.values_complete:
        issues.append("partition values are incomplete")
    if (
        any(field.transform is not None for field in partitioning.fields)
        and _time_range_partition_field(partitioning) is None
    ):
        issues.append("transformed partition fields are not expressible in V1")
    return tuple(issues)


def _is_temporal_type(type_name: str) -> bool:
    try:
        dtype = ibis.dtype(type_name)
    except (TypeError, ValueError, RuntimeError):
        try:
            from ibis.backends.sql.datatypes import ClickHouseType

            dtype = ClickHouseType.from_string(type_name)
        except (TypeError, ValueError, RuntimeError):
            return False
    return bool(dtype.is_date() or dtype.is_timestamp())


def _time_range_partition_field(partitioning: Partitioning) -> PartitionMetadata | None:
    transformed = tuple(
        field
        for field in partitioning.fields
        if field.transform is not None and field.type is not None and _is_temporal_type(field.type)
    )
    return transformed[0] if len(transformed) == 1 and len(partitioning.fields) == 1 else None


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


def _unprojected_table(source: TableSourceIR) -> TableSourceIR:
    return TableSourceIR(table=source.table, database=source.database)


def _declared_only_table_metadata(
    *,
    datasource_name: str,
    datasource_ir: DatasourceIR,
    source: TableSourceIR,
    failure: _TableMetadataUnavailableError,
) -> TableMetadata:
    warnings = [
        MetadataWarning(
            kind="base_table_metadata_unavailable",
            message=(
                "base table metadata is unavailable for this projected source; "
                f"backend={failure.identity} reason={failure.message}; use an explicit "
                "bounded md.unpruned(...) acquisition to prove the table and identifiers"
            ),
        )
    ]
    columns: list[ColumnMetadata] = []
    for position, (output_name, binding) in enumerate(source.columns, start=1):
        columns.append(
            ColumnMetadata(
                name=output_name,
                type=binding.data_type,
                nullable=None,
                comment=None,
                ordinal_position=position,
            )
        )
        warnings.append(
            MetadataWarning(
                kind="declared_column_unverified",
                message=(
                    f"projected column output={output_name!r} source={binding.source!r} "
                    f"declared_type={binding.data_type!r} is declared only; bounded runtime "
                    "acquisition is required to prove queryability"
                ),
                columns=(output_name,),
            )
        )
    return TableMetadata(
        datasource=datasource_name,
        table=source.table,
        database=source.database,
        backend_type=datasource_ir.backend_type,
        comment=None,
        columns=tuple(columns),
        partitions=(),
        partition_state="unknown",
        warnings=tuple(warnings),
    )


def _canonical_catalog_type(type_name: str, *, backend_type: str) -> str:
    try:
        return str(ibis.dtype(type_name))
    except (TypeError, ValueError, RuntimeError):
        if backend_type == "clickhouse":
            try:
                from ibis.backends.sql.datatypes import ClickHouseType

                return str(ClickHouseType.from_string(type_name).copy(nullable=True))
            except (TypeError, ValueError, RuntimeError):
                pass
        return type_name


def _declared_type_mismatch(
    *,
    datasource_name: str,
    source: TableSourceIR,
    output_name: str,
    physical_name: str,
    declared_type: str,
    observed_type: str,
    scope_state: Literal["known", "none", "unknown"],
) -> DatasourceAuthoringError:
    snippet = f"md.source_column({physical_name!r}, data_type={observed_type!r})"
    return DatasourceAuthoringError(
        code="declared_type_mismatch",
        stage="inspect",
        expected=f"catalog type {observed_type!r} for projected output {output_name!r}",
        received=(
            f"datasource={datasource_name!r} table={source.table!r} "
            f"database={source.database!r} output={output_name!r} "
            f"source={physical_name!r} declared_type={declared_type!r}"
        ),
        reason=(
            f"projected table {datasource_name!r}.{source.table!r} column {output_name!r} "
            f"declares {declared_type!r}, but catalog metadata reports {observed_type!r} "
            f"for physical source {physical_name!r}"
        ),
        effect_observed=DatasourceObservedEffects(
            query_executed=False,
            scope_state=scope_state,
        ),
        repair=repair(
            kind="reauthor",
            canonical_id="source_column",
            action=(
                f"Correct md.table(columns=...)[{output_name!r}] to use the observed "
                "catalog type, or bind a different physical source."
            ),
            snippet=snippet,
            preserves_evidence=False,
        ),
    )


def _mapped_constraint_columns(
    columns: tuple[str, ...],
    source_to_output: Mapping[str, str],
) -> tuple[str, ...] | None:
    if any(column not in source_to_output for column in columns):
        return None
    return tuple(source_to_output[column] for column in columns)


def _project_table_metadata(metadata: TableMetadata, source: TableSourceIR) -> TableMetadata:
    catalog_columns = {
        column.name: column for column in (*metadata.columns, *metadata.projectable_columns)
    }
    source_to_output = {binding.source: output for output, binding in source.columns}
    columns: list[ColumnMetadata] = []
    warnings = list(metadata.warnings)

    for position, (output_name, binding) in enumerate(source.columns, start=1):
        catalog_column = catalog_columns.get(binding.source)
        if catalog_column is None:
            columns.append(
                ColumnMetadata(
                    name=output_name,
                    type=binding.data_type,
                    nullable=None,
                    comment=None,
                    ordinal_position=position,
                )
            )
            warnings.append(
                MetadataWarning(
                    kind="declared_column_unverified",
                    message=(
                        f"projected column output={output_name!r} source={binding.source!r} "
                        f"declared_type={binding.data_type!r} is absent from base metadata; "
                        "bounded runtime acquisition is required to prove queryability"
                    ),
                    columns=(output_name,),
                )
            )
            continue

        observed_type = _canonical_catalog_type(
            catalog_column.type,
            backend_type=metadata.backend_type,
        )
        if observed_type != binding.data_type:
            raise _declared_type_mismatch(
                datasource_name=metadata.datasource,
                source=source,
                output_name=output_name,
                physical_name=binding.source,
                declared_type=binding.data_type,
                observed_type=observed_type,
                scope_state=metadata.partition_state,
            )
        columns.append(
            ColumnMetadata(
                name=output_name,
                type=binding.data_type,
                nullable=catalog_column.nullable,
                comment=catalog_column.comment,
                ordinal_position=position,
            )
        )

    primary_keys = _mapped_constraint_columns(metadata.primary_keys, source_to_output)
    if metadata.primary_keys and primary_keys is None:
        missing = tuple(
            column for column in metadata.primary_keys if column not in source_to_output
        )
        warnings.append(
            MetadataWarning(
                kind="projected_constraint_incomplete",
                message=(
                    "projected source omits physical primary-key columns "
                    f"{missing!r}; the effective source does not claim that constraint"
                ),
                columns=missing,
            )
        )

    unique_constraints: list[UniqueConstraintMetadata] = []
    for constraint in metadata.unique_constraints:
        mapped = _mapped_constraint_columns(constraint.columns, source_to_output)
        if mapped is None:
            missing = tuple(
                column for column in constraint.columns if column not in source_to_output
            )
            warnings.append(
                MetadataWarning(
                    kind="projected_constraint_incomplete",
                    message=(
                        f"projected source omits physical {constraint.kind} constraint "
                        f"columns {missing!r}; constraint={constraint.name!r} is not exposed"
                    ),
                    columns=missing,
                )
            )
            continue
        unique_constraints.append(replace(constraint, columns=mapped))

    partitions: list[PartitionMetadata] = []
    partition_state = metadata.partition_state
    if metadata.partition_state == "known":
        missing_partitions = tuple(
            field.name for field in metadata.partitions if field.name not in source_to_output
        )
        if missing_partitions:
            partition_state = "unknown"
            warnings.append(
                MetadataWarning(
                    kind="projected_partition_unavailable",
                    message=(
                        "base table partition fields are not fully exposed by the projected "
                        f"source; missing={missing_partitions!r}; add bindings or use explicit "
                        "bounded md.unpruned(...) acquisition"
                    ),
                    columns=missing_partitions,
                )
            )
        else:
            partitions.extend(
                replace(field, name=source_to_output[field.name]) for field in metadata.partitions
            )

    return replace(
        metadata,
        columns=tuple(columns),
        partitions=tuple(partitions),
        partition_state=partition_state,
        warnings=tuple(warnings),
        primary_keys=primary_keys or (),
        unique_constraints=tuple(unique_constraints),
    )


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

    source_to_output = {binding.source: output for output, binding in source.columns}
    return replace(
        partitioning,
        fields=effective_metadata.partitions,
        values=tuple(
            tuple((source_to_output[name], value) for name, value in captured)
            for captured in partitioning.values
        ),
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
) -> tuple[Partitioning, tuple[MetadataWarning, ...]]:
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
            (
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"partition metadata value hook failed: {exc}",
                    columns=tuple(field.name for field in fields),
                ),
            ),
        )
    finally:
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
            with suppress(Exception):
                disconnect()


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


def inspect(datasource: Ref[DatasourceKind], source: TableSource) -> SourceInspection:
    """Inspect a physical source through metadata and system-catalog hooks only.

    Args:
        datasource: Typed datasource reference from ``ms.ref.datasource(...)``.
        source: Typed table, Parquet, CSV, or JSON source descriptor.

    Returns:
        A metadata-only ``SourceInspection`` with schema, cost, partition, and
        execution-capability evidence.

    Example:
        >>> inspection = md.inspect(
        ...     ms.ref.datasource("warehouse"),
        ...     md.table("orders"),
        ... )
        >>> inspection.show()

    Constraints:
        Executes no user-data query. CSV and JSON paths are never opened and
        use only the authored schema. Parquet reads footer schema only.
        ``datasource`` is the typed ref itself; do not call ``md.connect``
        before inspection.
    """
    if type(datasource) is not Ref or datasource.kind is not SemanticKind.DATASOURCE:
        received = type(datasource).__name__
        if received == "DatasourceConnection":
            raise TypeError(
                "datasource must be Ref[datasource], got DatasourceConnection. "
                "md.inspect does not require md.connect; use "
                'md.inspect(ms.ref.datasource("warehouse"), md.table("orders")).'
            )
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

    project_root = find_project_root() or Path.cwd()
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
    profile = require_profile_for_backend_type(datasource_ir.backend_type)

    projected_metadata_unavailable = False
    base_partitioning: Partitioning | None = None
    partition_warnings: tuple[MetadataWarning, ...] = ()

    if isinstance(source, CsvSourceIR | JsonSourceIR):
        if datasource_ir.backend_type != "duckdb":
            raise _authoring_error(
                code="source_mismatch",
                stage="inspect",
                expected="a DuckDB datasource for CSV or JSON sources",
                received=datasource_ir.backend_type,
                reason="CSV and JSON source descriptors require a DuckDB datasource",
                scope_state=None,
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
            )
        metadata = _parquet_metadata(datasource_ir, source)
    else:
        try:
            base_metadata = _inspect_source(
                datasource_name,
                source=_unprojected_table(source),
                include_partitions=True,
                project_root=project_root,
            )
        except _TableMetadataUnavailableError as exc:
            if not source.columns:
                raise DatasourceMetadataError(
                    message=(
                        "base table metadata is unavailable and an unprojected source has no "
                        f"declared schema fallback: {exc.message}"
                    ),
                    expected="an inspectable datasource table",
                    received=exc.identity,
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
                failure=exc,
            )
        else:
            metadata = (
                _project_table_metadata(base_metadata, source) if source.columns else base_metadata
            )
            base_partitioning, partition_warnings = _captured_partitioning(
                metadata=base_metadata,
                datasource_ir=datasource_ir,
                source=_unprojected_table(source),
                profile=profile,
            )

    if base_partitioning is None:
        partitioning, partition_warnings = _captured_partitioning(
            metadata=metadata,
            datasource_ir=datasource_ir,
            source=source,
            profile=profile,
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
        datasource=datasource,
        source=source,
        physical_extent=_physical_extent(metadata.physical_profile),
        partitioning=partitioning,
        execution_capabilities=capabilities,
        schema=metadata.columns,
        warnings=warnings,
        _project_root=project_root,
        projectable_columns=metadata.projectable_columns,
    )
