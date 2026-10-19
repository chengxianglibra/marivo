"""Private Frame entry builders sharing the current pure source capture."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from ibis.common.deferred import Deferred

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.types import AxisBinding, CalendarCoordinate
from marivo._temporal import PeriodCalendarSnapshotV1, TimeScope
from marivo.analysis.core.artifact_data import DataColumn
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_calendar import freeze_calendar
from marivo.analysis.core.frame_data import LiteralFrameBinding, SourceFrameBinding
from marivo.analysis.core.frame_structure import (
    AxisRole,
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    value_name,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import ResultQuantity, reject
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.population import (
    MemberAxis,
    MembershipAxis,
    Population,
    PopulationPredicate,
)
from marivo.analysis.dsl.source_capture import (
    ValueRow,
    capture_metrics,
    capture_population,
    capture_read,
    capture_values,
)
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.datasource.runtime import DatasourceConnectionConfig
from marivo.semantic._compiled_state import CompiledSemanticState


def _utc(value: datetime | None) -> datetime | None:
    return (
        None
        if value is None
        else datetime.fromisoformat(value.astimezone(timezone.utc).isoformat())
    )


def pending_schema(graph: BoundGraph, state: CompiledSemanticState | None) -> PendingDataSchema:
    axes: list[PendingDataAxis] = []
    offset = 0
    role: AxisRole
    components: tuple[str, ...]
    for index, axis in enumerate(graph.signature.axes):
        if isinstance(axis, CalendarCoordinate):
            name = f"calendar:{axis.grain}"
            components = (name,)
            role = "calendar"
            at = None
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
                False if role in ("identity", "calendar") else None,
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
    )


def _capture(
    local: LocalFrameServices,
    graph: BoundGraph,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
) -> AnalysisFrame:
    schema = pending_schema(graph, state)
    calendars = tuple(
        freeze_calendar(
            axis,
            token=captured.token,
            name=captured.name,
            key=DataColumn(
                captured.columns[0].slot_id,
                captured.columns[0].name,
                ScalarDataType("int64"),
                False,
            ),
        )
        for axis, captured in zip(graph.signature.axes, schema.axes, strict=True)
        if isinstance(axis, CalendarCoordinate)
    )
    return AnalysisFrame._from_expression(
        local, schema, SourceFrameBinding(graph, state, connection, calendars)
    )


def observe(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    metrics: Mapping[str, Deferred],
    *,
    during: TimeScope | CalendarAxis | None = None,
    by: tuple[Deferred | MembershipAxis | MemberAxis | ComparisonAxis, ...] | Population = (),
    where: Deferred | PopulationPredicate | None = None,
    members: Population | None = None,
    timezone: str = "UTC",
    calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
) -> AnalysisFrame:
    for name in metrics:
        value_name(name, location="frame.observe")
    graph = capture_metrics(
        metrics,
        local.owner,
        during=during,
        by=by,
        where=where,
        members=members,
        timezone=timezone,
        state=state,
        calendar_snapshots=calendar_snapshots,
    )
    return _capture(local, graph, state, connection)


def read(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    population: Population,
    fields: Mapping[str, Deferred],
) -> AnalysisFrame:
    for name in fields:
        value_name(name, location="frame.read")
    return _capture(local, capture_read(population, fields, local.owner, state), state, connection)


def frame(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    population: Population,
) -> AnalysisFrame:
    return _capture(local, capture_population(population, local.owner), state, connection)


def values(
    local: LocalFrameServices, *, columns: Mapping[str, str | None], rows: tuple[ValueRow, ...]
) -> AnalysisFrame:
    """Capture zero-axis literal values without source configuration or scope."""
    for name in columns:
        value_name(name, location="frame.values")
    graph = capture_values(by=(), columns=columns, rows=rows, owner=local.owner)
    return AnalysisFrame._from_expression(
        local, pending_schema(graph, None), LiteralFrameBinding(graph)
    )


def coordinate_values(
    local: LocalFrameServices,
    state: CompiledSemanticState,
    connection: DatasourceConnectionConfig,
    *,
    by: tuple[Deferred | AxisBinding, ...],
    columns: Mapping[str, str | None],
    rows: tuple[ValueRow, ...],
) -> AnalysisFrame:
    for name in columns:
        value_name(name, location="frame.values")
    return _capture(
        local,
        capture_values(by=by, columns=columns, rows=rows, owner=local.owner),
        state,
        connection,
    )
