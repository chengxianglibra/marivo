"""Private Frame entry builders sharing the current pure source capture."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Literal

from ibis.common.deferred import Deferred

from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate
from marivo._grouping import Grouping
from marivo._temporal import PeriodCalendarSnapshotV1, TimeScope
from marivo.analysis.core.artifact_data import DataColumn
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_calendar import FrozenSourceTime, freeze_calendar, freeze_interval
from marivo.analysis.core.frame_data import LiteralFrameBinding, SourceFrameBinding
from marivo.analysis.core.frame_key_inputs import (
    FrameKeyInput,
    collect_frame_key_inputs,
    merge_frame_key_inputs,
)
from marivo.analysis.core.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    PendingFrameAxis,
    value_name,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import ResultQuantity, reject
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.population import (
    Population,
)
from marivo.analysis.dsl.source_capture import (
    ValueRow,
    capture_metrics,
    capture_population,
    capture_read,
    capture_values,
)
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.datasource.runtime import DatasourceConnectionConfig
from marivo.semantic._compiled_state import CompiledSemanticState


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
                key=DataColumn(f"key.{offset}", name, ScalarDataType("int64"), False),
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
            at = _utc(axis.at)
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


def observe(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    metrics: Mapping[str, Deferred],
    *,
    by: tuple[Deferred | Grouping | TimeScope, ...] | Population = (),
    where: Deferred | None = None,
    memberships: tuple[Grouping, ...] = (),
    timezone: str = "UTC",
    calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
) -> AnalysisFrame:
    for name in metrics:
        value_name(name, location="frame.observe")
    frame_inputs = collect_frame_key_inputs(
        *metrics.values(),
        where,
        by if isinstance(by, Population) else None,
        owner=local.owner,
    )
    graph = capture_metrics(
        metrics,
        local.owner,
        by=by,
        where=where,
        memberships=memberships,
        timezone=timezone,
        state=state,
        calendar_snapshots=calendar_snapshots,
    )
    return _capture(local, graph, state, connection, frame_inputs)


def read(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    population: Population,
    fields: Mapping[str, Deferred],
) -> AnalysisFrame:
    graph = capture_read(population, fields, local.owner, state)
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


def values(
    local: LocalFrameServices, *, columns: Mapping[str, str | None], rows: tuple[ValueRow, ...]
) -> AnalysisFrame:
    """Capture zero-axis literal values without source configuration or scope."""
    if not isinstance(columns, Mapping) or not columns:
        reject(
            "named columns and finite typed literal rows",
            repr((columns, rows)),
            "Provide at least one literal row to establish value kinds; filter the Frame when an empty typed result is needed.",
            "Session.values",
        )
    for name in columns:
        value_name(name, location="frame.values")
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
    if not isinstance(columns, Mapping) or not columns:
        reject(
            "named columns and finite typed literal rows",
            repr((columns, rows)),
            "Provide at least one literal row to establish value kinds; filter the Frame when an empty typed result is needed.",
            "Session.values",
        )
    for name in columns:
        value_name(name, location="frame.values")
    return _capture(
        local,
        capture_values(by=by, columns=columns, rows=rows, owner=local.owner),
        state,
        connection,
    )
