"""Capture source Frame structure from admitted graph facts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    IntervalCoordinate,
    RowAttributeTime,
)
from marivo.analysis.binding.calendar_keys import time_key_type
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.binding.signature import ResultQuantity
from marivo.analysis.binding.value_time import observation_time_semantics
from marivo.analysis.core.source_lineage import observation_contexts
from marivo.analysis.model.calendar_schema import calendar_descriptor, interval_descriptor
from marivo.analysis.model.data_types import DataColumn, ScalarDataType
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    PendingFrameAxis,
)
from marivo.analysis.model.lineage import SemanticObjectIdentity
from marivo.analysis.model.time_facts import FrameRowAttributeTime
from marivo.analysis.model.value_names import value_name
from marivo.semantic.spi import (
    CompiledSemanticState,
)


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
            interval = interval_descriptor(
                axis,
                token=f"axis.{index}",
                name=name,
                key=DataColumn(f"key.{offset}", name, ScalarDataType("int64"), False),
            )
            axes.append(interval)
            offset += len(interval.columns)
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
