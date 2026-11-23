"""Metadata-only source inspection for datasource authoring."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import ibis

from marivo._kernel.refs import DatasourceKind, Ref
from marivo._kernel.render import Card, RenderableResult
from marivo._kernel.symbolic import (
    DatasourceSymbol,
    symbol_factory,
)
from marivo.datasource.errors import (
    DatasourceAuthoringError,
    DatasourceFieldInvalidError,
    DatasourceObservedEffects,
    repair,
)
from marivo.datasource.inspection.details import (
    _PARTITION_VALUE_LIMIT,
    ExecutionCapabilities,
    Partitioning,
    PartitionInspection,
    PhysicalExtent,
    _partition_issues,
)
from marivo.datasource.inspection.errors import _authoring_error
from marivo.datasource.inspection.partitions import _listed_partitioning
from marivo.datasource.inspection.projection import _canonical_catalog_type, _ibis_table_type
from marivo.datasource.inspection.snapshot import acquire_snapshot
from marivo.datasource.ir import (
    TableSourceIR,
    _format_database_identity,
)
from marivo.datasource.metadata_types import ColumnMetadata, MetadataWarning
from marivo.datasource.source import (
    AuthoringScope,
    PartitionScope,
    TableSource,
    UnprunedScope,
)
from marivo.datasource.store.evidence import DiscoveryEvidence


@dataclass(frozen=True, repr=False)
class SourceInspection(RenderableResult):
    _datasource: Ref[DatasourceKind]
    source: TableSource
    physical_extent: PhysicalExtent
    partitioning: Partitioning
    execution_capabilities: ExecutionCapabilities
    schema: tuple[ColumnMetadata, ...]
    warnings: tuple[str, ...]
    _project_root: Path
    projectable_columns: tuple[ColumnMetadata, ...] = ()
    _backend_type: str = ""
    _physical_schema: tuple[ColumnMetadata, ...] = ()

    @property
    def datasource(self) -> DatasourceSymbol:
        """Native lookup selector for the inspected datasource."""
        return symbol_factory.datasource(self._datasource.path)

    def _repr_identity(self) -> str:
        return (
            f"SourceInspection datasource={self._datasource.path} source={self.source.kind} "
            f"columns={len(self.schema)} projectable={len(self.projectable_columns)} "
            f"partition_state={self.partitioning.state}"
        )

    def _card(self) -> Card:
        available = (
            (".source_column(name)",) if isinstance(self.source, TableSourceIR) else ()
        ) + (".partitions()", ".sample(...)", ".show()")
        card = Card(
            identity=self._repr_identity(),
            available=available,
        )
        if isinstance(self.source, TableSourceIR) and self.source.columns:
            card.field("source kind", "projected table")
            card.field("base table", self.source.table)
            card.field("database", _format_database_identity(self.source.database))
            card.field("projected columns", str(len(self.source.columns)))
            card.field("full source", ".source.to_dict()")
            card.table(
                columns=("output alias", "physical source"),
                rows=(
                    (output_name, source_name) for output_name, source_name in self.source.columns
                ),
                row_count=len(self.source.columns),
                label="column projection",
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
            value='marivo.help("datasource.source_inspection.sample")',
        )
        if isinstance(self.source, TableSourceIR) and not self.source.columns:
            card.table(
                columns=("column", "physical type", "ibis type", "nullable", "projection"),
                rows=(self._table_schema_row(column) for column in self.schema),
                row_count=len(self.schema),
                label="schema",
                show_omission_counts=True,
            )
        else:
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
                columns=("physical column", "ibis type", "nullable", "projection"),
                rows=(
                    (
                        column.name,
                        column.type
                        if column._type_is_ibis
                        else (
                            _ibis_table_type(column.type, backend_type=self._backend_type)
                            or "unavailable"
                        ),
                        "?" if column.nullable is None else ("Y" if column.nullable else "N"),
                        self._binding_snippet(column),
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

    def _binding_snippet(self, column: ColumnMetadata) -> str:
        return f"{{{column.name!r}: {column.name!r}}}"

    def _table_schema_row(self, column: ColumnMetadata) -> tuple[str, ...]:
        ibis_type = (
            column.type
            if column._type_is_ibis
            else _ibis_table_type(column.type, backend_type=self._backend_type)
        )
        return (
            column.name,
            column.type,
            ibis_type or "unavailable",
            "?" if column.nullable is None else ("Y" if column.nullable else "N"),
            self._binding_snippet(column),
        )

    def source_column(self, name: str) -> str:
        """Return one inspected physical table column name.

        Args:
            name: Exact inspected column name, or an existing projected output alias.

        Returns:
            The physical source-column name for ``md.table(columns=...)``.

        Example:
            ``column = inspection.source_column("order_id")``

        Constraints:
            Only table inspections support source-column lookup. The method uses
            retained metadata and performs no query.
        """
        if not isinstance(self.source, TableSourceIR):
            raise _source_column_error(
                code="source_column_not_table",
                expected="an inspected table source",
                received=self.source.kind,
                action="Inspect a md.table(...) source before requesting a physical column name.",
            )
        if type(name) is not str or not name:
            raise _source_column_error(
                code="source_column_name_invalid",
                expected="a non-empty inspected column name",
                received=repr(name),
                action="Choose an exact column name from inspection.schema.",
            )
        for output, physical_name in self.source.columns:
            if output == name:
                return physical_name
        column = next(
            (item for item in (*self.schema, *self.projectable_columns) if item.name == name),
            None,
        )
        if column is None:
            raise _source_column_error(
                code="source_column_unknown",
                expected="a name in inspection.schema or inspection.projectable_columns",
                received=repr(name),
                action="Choose an exact inspected column name and retry.",
                candidates=tuple(item.name for item in (*self.schema, *self.projectable_columns))[
                    :8
                ],
            )
        return column.name

    def partitions(
        self,
        *,
        limit: int = _PARTITION_VALUE_LIMIT,
        order: Literal["asc", "desc"] = "desc",
    ) -> PartitionInspection:
        """Return one bounded edge of the source's partition metadata.

        Args:
            limit: Positive result bound no greater than 100.
            order: ``"asc"`` for the ascending physical-value edge or
                ``"desc"`` for the descending physical-value edge.

        Returns:
            A bounded ``PartitionInspection``. The default descending-edge
            request reuses values captured by ``md.inspect(...)``; another
            bound or order performs one metadata-only partition query. Its
            status is ``complete`` for an exhaustive edge, ``truncated`` when
            the requested bound cuts off more edge values, and ``incomplete``
            only when partition metadata is unavailable or invalid.

        Example:
            ``inspection.partitions(limit=1, order="asc").show()``

        Constraints:
            This method reads partition metadata, not user rows. It exposes
            bounded physical-value edges and does not interpret lexical or
            numeric ordering as chronological ordering.
        """
        if type(limit) is not int or not 1 <= limit <= _PARTITION_VALUE_LIMIT:
            raise _partition_listing_field_error(
                inspection=self,
                parameter="limit",
                expected="an integer within [1, 100]",
                received=repr(limit),
            )
        if type(order) is not str or order not in {"asc", "desc"}:
            raise _partition_listing_field_error(
                inspection=self,
                parameter="order",
                expected="'asc' or 'desc'",
                received=repr(order),
            )

        partitioning = self.partitioning
        listing_warnings: tuple[MetadataWarning, ...] = ()
        if limit != _PARTITION_VALUE_LIMIT or order != "desc":
            partitioning, listing_warnings = _listed_partitioning(
                datasource=self._datasource,
                source=self.source,
                partitioning=self.partitioning,
                project_root=self._project_root,
                limit=limit,
                order=order,
            )
        issues = tuple(
            dict.fromkeys(
                (
                    *_partition_issues(partitioning),
                    *(warning.message for warning in listing_warnings),
                )
            )
        )
        status: Literal["complete", "truncated", "incomplete"]
        if issues:
            status = "incomplete"
        elif partitioning.truncated:
            status = "truncated"
        else:
            status = "complete"
        return PartitionInspection(
            _datasource=self._datasource,
            source=self.source,
            partitioning=partitioning,
            limit=limit,
            order=order,
            status=status,
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
    ) -> DiscoveryEvidence:
        """Acquire a bounded snapshot after metadata preflight.

        Args:
            scope: One explicit partition, time-range, or unpruned acquisition scope.
            columns: Non-empty tuple of output column names in ``inspection.schema``.
                For a projected source, use its output aliases.
            persist_values: Set ``True`` to retain named rows and bounded profile
                values in plaintext project-local state. With ``False``, a fresh
                acquisition keeps profile values in the current process, but
                retained rows are unavailable even immediately after acquisition.
            refresh: Re-execute this exact bounded acquisition instead of reusing
                a matching cached snapshot.
            source_params: Exact non-secret runtime values declared by
                ``md.source_param(...)`` on a JSON source.

        Returns:
            One immutable ``DiscoveryEvidence`` whose local projections issue no query.

        Example:
            ``snapshot = inspection.sample(scope=scope, columns=("id", "region"))``

        Constraints:
            Missing or extra source parameters fail before acquisition. By default,
            a matching cached snapshot is reused even when stale; use ``refresh=True``
            to read the source again. Without value persistence, a cold process
            recovers profile statistics without min/max, top values, display
            samples or retained rows, and
            ``value_evidence_state`` is ``"value_evidence_unavailable"``. The
            retained row count describes rows used in profiling, not availability
            of ``retained_values``.
        """
        _preflight_sample(self, scope=scope, columns=columns)
        return acquire_snapshot(
            datasource=self._datasource,
            source=self.source,
            schema=self.schema,
            partitioning=self.partitioning,
            project_root=self._project_root,
            scope=scope,
            columns=columns,
            persist_values=persist_values,
            refresh=refresh,
            source_params=source_params,
        )


def _partition_listing_field_error(
    *,
    inspection: SourceInspection,
    parameter: Literal["limit", "order"],
    expected: str,
    received: str,
) -> DatasourceFieldInvalidError:
    return DatasourceFieldInvalidError(
        message=f"SourceInspection.partitions {parameter} is invalid",
        expected=expected,
        received=received,
        location=f"SourceInspection.partitions({parameter}=...)",
        effect_observed=DatasourceObservedEffects(
            query_executed=False,
            scope_state=inspection.partitioning.state,
        ),
        repair=repair(
            kind="retry",
            canonical_id="source_inspection.partitions",
            action="Use a bounded physical-value listing with a supported order.",
            snippet='inspection.partitions(limit=100, order="desc")',
            preserves_evidence=True,
        ),
    )


def _source_column_error(
    *,
    code: str,
    expected: str,
    received: str,
    action: str,
    candidates: tuple[str, ...] = (),
) -> DatasourceAuthoringError:
    return DatasourceAuthoringError(
        code=code,
        stage="inspect",
        expected=expected,
        received=received,
        reason="The inspected source does not expose this physical column for projection.",
        effect_observed=DatasourceObservedEffects(query_executed=False),
        repair=repair(
            kind="reauthor",
            canonical_id="source_inspection.source_column",
            action=action,
            candidates=candidates,
            preserves_evidence=True,
        ),
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
    _preflight_scope(inspection, scope=scope)


def _preflight_scope(inspection: SourceInspection, *, scope: AuthoringScope) -> None:
    """Validate physical pruning independently from snapshot column selection."""
    state = inspection.partitioning.state
    if not isinstance(scope, PartitionScope | UnprunedScope):
        raise TypeError(
            "scope must be produced by md.partition(), md.time_range(), or md.unpruned()."
        )
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
        physical_column = next(
            (column for column in inspection._physical_schema if column.name == time_column.name),
            time_column,
        )
        category = _physical_temporal_category(
            physical_column, backend_type=inspection._backend_type
        )
        if category is None:
            raise _authoring_error(
                code="unknown_source_column",
                stage="preflight",
                expected="an inspected date or timestamp column",
                received=f"{time_predicate.column}: {time_column.type}",
                reason="md.time_range(...) can only filter a date or timestamp column",
                scope_state=state,
            )
        _validate_time_range_category(
            column=time_predicate.column,
            category=category,
            start=time_predicate.start,
            end=time_predicate.end,
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
        # Captured partition evidence requires an explicitly pruned scope.
        # Unavailable values retain the deliberate unpruned fallback.
        raise _authoring_error(
            code="incomplete_partition_fields",
            stage="preflight",
            expected=", ".join(field.name for field in inspection.partitioning.fields),
            received="unpruned scope",
            reason="known partition fields require an explicit complete partition scope",
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
        if not isinstance(name, str) or not isinstance(partition_value, str):  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
            raise TypeError("PartitionScope.values entries must be tuple[str, str].")
        if not name or not partition_value:
            raise ValueError("PartitionScope partition names and values must be non-empty.")


def _physical_temporal_category(
    column: ColumnMetadata, *, backend_type: str
) -> Literal["aware_timestamp", "naive_timestamp", "date"] | None:
    """Classify native and reflected types without losing their physical meaning."""
    canonical = (
        column.type
        if column._type_is_ibis
        else _canonical_catalog_type(column.type, backend_type=backend_type)
    )
    try:
        dtype = ibis.dtype(canonical)
    except (TypeError, ValueError, RuntimeError):
        return None
    if dtype.is_date():
        return "date"
    if dtype.is_timestamp():
        # ClickHouse timestamps always identify instants, even when their native
        # type omits the server's display timezone. MySQL's native TIMESTAMP is
        # mapped to UTC-aware by its backend type mapper; DATETIME remains naive.
        if backend_type == "clickhouse" or dtype.timezone is not None:
            return "aware_timestamp"
        return "naive_timestamp"
    return None


def _validate_time_range_category(
    *,
    column: str,
    category: Literal["aware_timestamp", "naive_timestamp", "date"],
    start: date | datetime,
    end: date | datetime,
    scope_state: Literal["known", "none", "unknown"],
) -> None:
    is_datetime = isinstance(start, datetime)
    aware = (
        isinstance(start, datetime) and start.tzinfo is not None and start.utcoffset() is not None
    )
    if category == "aware_timestamp":
        allowed = aware
    elif category == "date":
        allowed = not is_datetime
    else:
        allowed = not aware
    if allowed:
        return
    expected, example_start, example_end = {
        "aware_timestamp": (
            "aware datetime boundaries for an aware timestamp column",
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ),
        "naive_timestamp": (
            "naive datetime or date boundaries for a naive timestamp column",
            "2026-01-01T00:00:00",
            "2026-01-02T00:00:00",
        ),
        "date": ("date boundaries for a DATE column", "2026-01-01", "2026-01-02"),
    }[category]
    raise DatasourceAuthoringError(
        code="time_range_boundary_mismatch",
        stage="preflight",
        expected=expected,
        received=f"column={column!r}, category={category}, start={start!r}, end={end!r}",
        reason="Time-range boundaries must match the inspected physical column category.",
        effect_observed=DatasourceObservedEffects(query_executed=False, scope_state=scope_state),
        repair=repair(
            kind="rescope",
            canonical_id="time_range",
            action=(
                f"Use {expected} for {column!r}; no datasource or connection timezone "
                "supplies boundary awareness. Replace the example dates with your intended range."
            ),
            snippet=(
                f"md.time_range({column!r}, start={example_start!r}, end={example_end!r}, "
                "max_rows=1000, timeout_seconds=30)"
            ),
            preserves_evidence=True,
        ),
    )
