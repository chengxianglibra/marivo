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
from marivo._temporal import TimeScope
from marivo.analysis.binding.lineage import observation_contexts
from marivo.analysis.binding.time_guidance import (
    REPORT_CLOCK_GUIDANCE,
    partition_hour_candidates,
    partition_hour_guidance,
)
from marivo.analysis.binding.value_time import observation_time_semantics
from marivo.analysis.core.artifact_data import DataColumn
from marivo.analysis.core.calendar_keys import time_key_type
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_calendar import (
    calendar_descriptor,
    freeze_interval,
)
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
    DimensionExpression,
    FieldExpression,
    MetricExpression,
    NumericMetric,
    RowExpression,
    RowPredicate,
)
from marivo.semantic.catalog import TimeDimension


def _utc(value: datetime | None) -> datetime | None:
    return (
        None
        if value is None
        else datetime.fromisoformat(value.astimezone(timezone.utc).isoformat())
    )


def pending_schema(graph: BoundGraph, state: CompiledSemanticState | None) -> PendingDataSchema:
    """Capture static structure without freezing native result positions."""
    axes: list[PendingFrameAxis] = []
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
            axes.append(interval.axis)
            offset += len(interval.axis.columns)
            continue
        if isinstance(axis, CalendarCoordinate):
            name = f"calendar:{axis.grain}"
            frozen_axis = calendar_descriptor(
                axis,
                token=f"axis.{index}",
                name=name,
                key=DataColumn(f"key.{offset}", name, time_key_type(axis), False),
            )
            axes.append(frozen_axis)
            offset += len(frozen_axis.columns)
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
        value_time_semantics=observation_time_semantics(graph, state),
    )


def _capture(
    local: LocalFrameServices,
    graph: BoundGraph,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    frame_key_inputs: tuple[FrameKeyInput, ...] = (),
) -> AnalysisFrame:
    schema = pending_schema(graph, state)
    return AnalysisFrame._from_expression(
        local,
        schema,
        SourceFrameBinding(
            graph,
            state,
            connection,
            merge_frame_key_inputs(frame_key_inputs, owner=local.owner),
        ),
    )


def _source_by(
    by: tuple[Deferred | DimensionExpression | Grouping, ...] | Population,
    state: CompiledSemanticState,
    *,
    project_root: Path,
    owner: Owner,
) -> tuple[Deferred | Grouping, ...] | Population:
    """Adapt typed public values only inside a source boundary with explicit scope."""
    if not isinstance(by, (tuple, Population)):
        reject(
            "an ordered tuple of classifications and optional CalendarAxis, or a complete Population",
            type(by).__name__,
            "Pass by=(scope.by(mv.grain('day')), field) or by=population.",
            "Session.observe.by",
            canonical_id="session.observe",
        )
    values = (by,) if isinstance(by, Population) else by
    result: list[Deferred | Grouping] = []
    for value in values:
        if isinstance(value, RowExpression):
            if value._role != "dimension":
                candidates = (
                    partition_hour_candidates(state.registry, value.path)
                    if isinstance(value, TimeDimension)
                    else ()
                )
                repair = "Use a Dimension for classification or scope.by(grain) for report time."
                if value._role == "time":
                    repair += (
                        " A TimeDimension supplies time= on a source reduction or a field predicate; "
                        "it is not an ordinary classification. "
                        + REPORT_CLOCK_GUIDANCE
                        + partition_hour_guidance(candidates)
                    )
                reject(
                    "ordinary classification dimensions",
                    repr(value),
                    repair,
                    "Session.observe.by",
                    canonical_id="session.observe",
                    candidates=candidates,
                )
            result.append(bind_field_in_state(value, state, project_root=project_root, owner=owner))
        elif isinstance(value, Grouping) or (isinstance(value, Population) and value is by):
            validate_context(value._context, project_root=project_root, owner=owner)
            for identity, keys in value._identity_keys:
                entity = state.registry.entities.get(identity.removeprefix("entity:"))
                if entity is None or tuple(entity.primary_key) != keys:
                    reject(
                        "the captured complete primary-key interpretation",
                        identity,
                        "Rebuild the coordinate domain with the receiving scope's complete identity.",
                        "source capture",
                        canonical_id="session.observe",
                    )
            if isinstance(value, Grouping):
                result.append(value)
        elif isinstance(value, Deferred):
            result.append(value)
        else:
            reject(
                "classification fields or grouping rules and at most one CalendarAxis",
                type(value).__name__,
                "Put a report TimeScope in where and pass an entire Population directly in by.",
                "Session.observe.by",
                canonical_id="session.observe",
            )
    return by if isinstance(by, Population) else tuple(result)


def observe(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    metrics: Mapping[str, Deferred | NumericMetric] | _MetricInputs,
    *,
    by: tuple[Deferred | DimensionExpression | Grouping, ...] | Population = (),
    where: Deferred | RowPredicate | None = None,
    scope: TimeScope | None = None,
    timezone: str = "UTC",
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
        bound_where,
        by if isinstance(by, Population) else None,
        owner=local.owner,
    )
    graph = capture_metrics(
        inputs,
        local.owner,
        by=bound_by,
        where=bound_where,
        timezone=timezone,
        state=state,
        scope=scope,
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
    schema = pending_schema(graph, None)
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
