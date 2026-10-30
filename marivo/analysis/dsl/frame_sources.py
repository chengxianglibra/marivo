"""Private Frame entry builders sharing the current pure source capture."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ibis.common.deferred import Deferred

from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    IntervalCoordinate,
    Owner,
    RowAttributeTime,
)
from marivo._grouping import Grouping
from marivo._temporal import PeriodCalendarSnapshotV2, TimeScope
from marivo.analysis.binding.lineage import observation_contexts
from marivo.analysis.core.artifact_data import DataColumn
from marivo.analysis.core.calendar_keys import time_key_type
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_calendar import FrozenSourceTime, freeze_calendar, freeze_interval
from marivo.analysis.core.frame_data import LiteralFrameBinding, SourceFrameBinding
from marivo.analysis.core.frame_interpretation import FrameRowAttributeTime
from marivo.analysis.core.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    PendingFrameAxis,
    value_name,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.lineage import SemanticObjectIdentity
from marivo.analysis.core.model import ResultQuantity, reject
from marivo.analysis.dsl._frame_inputs import (
    FrameKeyInput,
    collect_frame_key_inputs,
    merge_frame_key_inputs,
)
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.population import (
    Population,
)
from marivo.analysis.dsl.source_capture import (
    ValueRow,
    _MetricInputs,
    capture_metrics,
    capture_population,
    capture_read,
    capture_values,
)
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.datasource.runtime import DatasourceConnectionConfig
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._consumption import (
    bind_field_in_state,
    bind_metric_in_state,
    bind_predicate_in_state,
    validate_context,
)
from marivo.semantic._expression_values import (
    FieldExpression,
    MetricExpression,
    NumericMetric,
    RowExpression,
    RowPredicate,
)
from marivo.semantic.expressions import _Resolver


def _utc(value: datetime | None) -> datetime | None:
    return (
        None
        if value is None
        else datetime.fromisoformat(value.astimezone(timezone.utc).isoformat())
    )


def pending_schema(
    graph: BoundGraph, state: CompiledSemanticState | None
) -> tuple[PendingDataSchema, tuple[FrozenSourceTime, ...]]:
    """Capture current axis facts and freeze each source calendar exactly once."""
    axes: list[PendingFrameAxis] = []
    calendars: list[FrozenSourceTime] = []
    offset = 0
    role: Literal["field", "identity"]
    components: tuple[str, ...]
    for index, axis in enumerate(graph.signature.axes):
        if isinstance(axis, IntervalCoordinate):
            name = "interval"
            interval = freeze_interval(
                axis,
                token=f"axis.{index}",
                name=name,
                key=DataColumn(f"key.{offset}", name, ScalarDataType("int64"), False),
            )
            calendars.append(interval)
            axes.append(interval.axis)
            offset += len(interval.axis.columns)
            continue
        if isinstance(axis, CalendarCoordinate):
            name = f"calendar:{axis.grain}"
            frozen = freeze_calendar(
                axis,
                token=f"axis.{index}",
                name=name,
                key=DataColumn(f"key.{offset}", name, time_key_type(axis), False),
                calendar_ref=(
                    SemanticObjectIdentity(
                        "period_calendar",
                        axis.grain_spec._calendar.path,
                        _Resolver(state).exact(axis.grain_spec._calendar).version,
                    )
                    if state is not None
                    and axis.grain_spec.kind == "semantic"
                    and axis.grain_spec._calendar is not None
                    else None
                ),
            )
            calendars.append(frozen)
            axes.append(frozen.axis)
            offset += len(frozen.axis.columns)
            continue
        elif isinstance(axis, AxisBinding):
            name = axis.field.ref.path if axis.field is not None else axis.entity.ref.path
            if axis.field is None:
                if state is None:
                    reject(
                        "a captured complete Entity scope",
                        name,
                        "Supply the completed semantic scope for identity coordinates.",
                        "frame.source",
                    )
                components = state.registry.entities[axis.entity.ref.path].primary_key
                role = "identity"
            else:
                components = (name,)
                role = "field"
            at = (
                FrameRowAttributeTime(
                    SemanticObjectIdentity(
                        "time_dimension", axis.at.field.ref.path, axis.at.field.version
                    )
                )
                if isinstance(axis.at, RowAttributeTime)
                else _utc(axis.at)
            )
        else:
            reject(
                "an original field, identity or calendar source axis",
                type(axis).__name__,
                "Build source analyses before data-side pairing.",
                "frame.source",
            )
        columns = tuple(
            PendingDataColumn(
                f"key.{offset + component}",
                name,
                "unresolved",
                False if role == "identity" else None,
            )
            for component, name in enumerate(components)
        )
        axes.append(PendingDataAxis(f"axis.{index}", name, role, columns, at=at))
        offset += len(columns)
    values = tuple(
        PendingDataColumn(
            f"value.{index}",
            quantity.name,
            quantity.kind if isinstance(quantity, ResultQuantity) else "unresolved",
            None,
            quantity.unit,
        )
        for index, quantity in enumerate(graph.signature.quantities)
    )
    return PendingDataSchema(
        tuple(axes),
        values,
        tuple(value_name(value.name, location="frame.source") for value in values),
        value_contexts=observation_contexts(graph, tuple(axes), state),
    ), tuple(calendars)


def _capture(
    local: LocalFrameServices,
    graph: BoundGraph,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    frame_key_inputs: tuple[FrameKeyInput, ...] = (),
) -> AnalysisFrame:
    schema, calendars = pending_schema(graph, state)
    return AnalysisFrame._from_expression(
        local,
        schema,
        SourceFrameBinding(
            graph,
            state,
            connection,
            calendars,
            merge_frame_key_inputs(frame_key_inputs, owner=local.owner),
        ),
    )


def _source_by(
    by: tuple[Deferred | FieldExpression | Grouping | TimeScope | Population, ...] | Population,
    state: CompiledSemanticState,
    *,
    project_root: Path,
    owner: Owner,
) -> tuple[Deferred | Grouping | TimeScope | Population, ...] | Population:
    """Adapt typed public values only inside a source boundary with explicit scope."""
    values = (by,) if isinstance(by, Population) else by
    result: list[Deferred | Grouping | TimeScope | Population] = []
    for value in values:
        if isinstance(value, RowExpression):
            result.append(bind_field_in_state(value, state, project_root=project_root, owner=owner))
        else:
            if isinstance(value, (Grouping, Population)):
                validate_context(value._context, project_root=project_root, owner=owner)
                for identity, keys in value._identity_keys:
                    entity = state.registry.entities.get(identity.removeprefix("entity:"))
                    if entity is None or tuple(entity.primary_key) != keys:
                        reject(
                            "the captured complete primary-key interpretation",
                            identity,
                            "Rebuild the coordinate domain with the receiving scope's complete identity.",
                            "source capture",
                        )
            result.append(value)
    return by if isinstance(by, Population) else tuple(result)


def observe(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    metrics: Mapping[str, Deferred | NumericMetric] | _MetricInputs,
    *,
    by: tuple[Deferred | FieldExpression | Grouping | TimeScope | Population, ...]
    | Population = (),
    where: Deferred | RowPredicate | None = None,
    timezone: str = "UTC",
    calendar_snapshots: tuple[PeriodCalendarSnapshotV2, ...] = (),
    _parent_filter: Deferred | None = None,
    _complete_calendar: bool = True,
    _endpoint_scope: TimeScope | None = None,
    _captured_frame_inputs: tuple[FrameKeyInput, ...] = (),
) -> AnalysisFrame:
    selected = tuple(metrics.items()) if isinstance(metrics, Mapping) else metrics
    original_frame_inputs = collect_frame_key_inputs(
        *(value for _, value in selected), where, owner=local.owner
    )
    inputs = tuple(
        (
            name,
            bind_metric_in_state(
                value, state, project_root=connection.project_root, owner=local.owner
            )
            if isinstance(value, MetricExpression)
            else value,
        )
        for name, value in selected
    )
    bound_by = _source_by(by, state, project_root=connection.project_root, owner=local.owner)
    bound_where = (
        bind_predicate_in_state(
            where, state, project_root=connection.project_root, owner=local.owner
        )
        if isinstance(where, RowExpression)
        else where
    )
    frame_inputs = collect_frame_key_inputs(
        *(metric for _, metric in inputs),
        _parent_filter,
        bound_where,
        *(
            (by,)
            if isinstance(by, Population)
            else tuple(item for item in by if isinstance(item, Population))
            if isinstance(by, tuple)
            else ()
        ),
        owner=local.owner,
    )
    graph = capture_metrics(
        inputs,
        local.owner,
        by=bound_by,
        where=bound_where,
        timezone=timezone,
        state=state,
        calendar_snapshots=calendar_snapshots,
        _parent_filter=_parent_filter,
        _complete_calendar=_complete_calendar,
        _endpoint_scope=_endpoint_scope,
    )
    return _capture(
        local,
        graph,
        state,
        connection,
        merge_frame_key_inputs(
            _captured_frame_inputs, original_frame_inputs, frame_inputs, owner=local.owner
        ),
    )


def read(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    population: Population,
    fields: Mapping[str, Deferred | FieldExpression],
) -> AnalysisFrame:
    validate_context(population._context, project_root=connection.project_root, owner=local.owner)
    bound_fields = {
        name: bind_field_in_state(
            value, state, project_root=connection.project_root, owner=local.owner
        )
        if isinstance(value, RowExpression)
        else value
        for name, value in fields.items()
    }
    graph = capture_read(population, bound_fields, local.owner, state)
    for name in fields:
        value_name(name, location="frame.read")
    return _capture(
        local,
        graph,
        state,
        connection,
        population._frame_inputs,
    )


def population_needs_state(population: Population) -> bool:
    """Resolve a Catalog only when the captured domain consumes definitions."""
    from marivo._definition.graph import direct_refs

    return bool(direct_refs(population._definition))


def frame(
    local: LocalFrameServices,
    state: CompiledSemanticState | None,
    connection: DatasourceConnectionConfig,
    population: Population,
) -> AnalysisFrame:
    validate_context(population._context, project_root=connection.project_root, owner=local.owner)
    if state is None:
        from marivo.semantic._compiled_state import build_compiled_state
        from marivo.semantic._expression_binding import CompiledExpressionSidecar
        from marivo.semantic.validator import Registry

        state = build_compiled_state(
            registry=Registry(),
            sidecar=CompiledExpressionSidecar({}, {}, frozenset()),
            selected_root_roles=(),
            filtered_domains=(),
        )
    return _capture(
        local,
        capture_population(population, local.owner),
        state,
        connection,
        population._frame_inputs,
    )


def _validate_value_columns(columns: Mapping[str, str | None], rows: tuple[ValueRow, ...]) -> None:
    if not isinstance(columns, Mapping) or not columns:
        reject(
            "named columns and finite typed literal rows",
            repr((columns, rows)),
            "Provide at least one literal row to establish value kinds; filter the Frame when an empty typed result is needed.",
            "Session.values",
        )
    for name in columns:
        value_name(name, location="frame.values")


def values(
    local: LocalFrameServices, *, columns: Mapping[str, str | None], rows: tuple[ValueRow, ...]
) -> AnalysisFrame:
    """Capture zero-axis literal values without source configuration or scope."""
    _validate_value_columns(columns, rows)
    graph = capture_values(by=(), columns=columns, rows=rows, owner=local.owner)
    schema, _ = pending_schema(graph, None)
    return AnalysisFrame._from_expression(local, schema, LiteralFrameBinding(graph))


def coordinate_values(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    *,
    by: tuple[Deferred | Grouping, ...],
    columns: Mapping[str, str | None],
    rows: tuple[ValueRow, ...],
) -> AnalysisFrame:
    _validate_value_columns(columns, rows)
    return _capture(
        local,
        capture_values(by=by, columns=columns, rows=rows, owner=local.owner),
        state,
        connection,
    )
