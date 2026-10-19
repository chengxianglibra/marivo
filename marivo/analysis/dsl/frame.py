"""Private unified Frame structure and exact handles, before public cutover."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from json import dumps
from typing import TypeAlias
from uuid import uuid4

from marivo._definition.query import CoordinateSlot, PresenceSlot, ValueSlot
from marivo.analysis.core.artifact_data import ArtifactData, DataSchema
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    ExpressionFrameBacking,
    MemoryFrameBacking,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_structure import (
    PendingDataSchema,
    axis_role,
    column_kind,
    require_complete_structure,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject
from marivo.analysis.dsl.column import Column, ColumnPredicate, _Statistic
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.materialization.frame_services import LocalFrameServices

FrameBacking: TypeAlias = ExpressionFrameBacking | MemoryFrameBacking | StoredFrameBacking
FrameSchema: TypeAlias = DataSchema | PendingDataSchema


@dataclass(frozen=True, init=False, repr=False, eq=False)
class AnalysisFrame:
    """One current structure, exact binding, and closed private backing."""

    _local: LocalFrameServices
    _input_id: str
    _schema: FrameSchema
    _backing: FrameBacking

    def __init__(self) -> None:
        raise TypeError("Use Session methods or Frame operations.")

    @classmethod
    def _from_expression(
        cls, local: LocalFrameServices, schema: FrameSchema, graph: BoundGraph
    ) -> AnalysisFrame:
        if graph.signature.owner != local.owner:
            reject(
                "a source graph from this Store and Session",
                repr(graph.signature.owner),
                "Use the owning Session.",
                "frame.expression",
            )
        if isinstance(schema, DataSchema):
            require_complete_structure(schema)
        elif type(schema) is not PendingDataSchema:
            reject(
                "a current complete or pending schema",
                type(schema).__name__,
                "Capture the current structure.",
                "frame.expression",
            )
        return cls._bind(local, schema, ExpressionFrameBacking(graph), "frame_" + uuid4().hex)

    @classmethod
    def _from_memory(
        cls,
        local: LocalFrameServices,
        data: CompletedFrameData,
        *,
        result_id: str,
        evaluated_at: datetime,
    ) -> AnalysisFrame:
        backing = MemoryFrameBacking(data, result_id, evaluated_at)
        return cls._bind(local, data.schema, backing, "frame_" + uuid4().hex)

    @classmethod
    def _from_stored(
        cls,
        local: LocalFrameServices,
        data: ArtifactData,
        *,
        artifact_id: str,
        result_id: str,
        evaluated_at: datetime,
    ) -> AnalysisFrame:
        backing = StoredFrameBacking(artifact_id, data, result_id, evaluated_at)
        identity = dumps(
            (local.store.store_id, local.session_ref, artifact_id), separators=(",", ":")
        )
        input_id = "stored_" + sha256(identity.encode()).hexdigest()
        return cls._bind(local, data.schema, backing, input_id)

    @classmethod
    def _bind(
        cls, local: LocalFrameServices, schema: FrameSchema, backing: FrameBacking, input_id: str
    ) -> AnalysisFrame:
        result = object.__new__(cls)
        object.__setattr__(result, "_local", local)
        object.__setattr__(result, "_input_id", input_id)
        object.__setattr__(result, "_schema", schema)
        object.__setattr__(result, "_backing", backing)
        return result

    def __repr__(self) -> str:
        kind = (
            "expression"
            if isinstance(self._backing, ExpressionFrameBacking)
            else "memory"
            if isinstance(self._backing, MemoryFrameBacking)
            else "stored"
        )
        return f"<AnalysisFrame input={self._input_id[:16]} backing={kind} outputs={len(self._schema.values)}; inspect .axes/.output_names>"

    @property
    def output_names(self) -> tuple[str, ...]:
        return tuple(".".join(path) for path in self._schema.value_paths)

    @property
    def presence_names(self) -> tuple[str, ...]:
        return tuple(".".join(path) for path in self._schema.presence_paths)

    @property
    def axes(self) -> tuple[FrameAxis, ...]:
        schema = self._schema
        if isinstance(schema, PendingDataSchema):
            return tuple(
                FrameAxis._from_frame(
                    self, axis.token, axis.name, axis.role, tuple(c.name for c in axis.columns)
                )
                for axis in schema.axes
            )
        return tuple(
            FrameAxis._from_frame(
                self, axis.token, axis.name, axis_role(axis), tuple(c.name for c in axis.columns)
            )
            for axis in schema.axes
        )

    def axis(self, name: str) -> FrameAxis:
        matches = tuple(axis for axis in self.axes if axis.name == name)
        if type(name) is not str or len(matches) != 1:
            reject(
                "one exact current axis display name",
                repr(name),
                f"Choose an explicit handle from frame.axes: {tuple((a.token, a.name) for a in self.axes)!r}.",
                "frame.axis",
            )
        return matches[0]

    def __getitem__(self, name: str) -> Column:
        if type(name) is str:
            for index, path in enumerate(self._schema.value_paths):
                if name == ".".join(path):
                    column = self._schema.values[index]
                    cls = ColumnPredicate if column_kind(column) == "boolean" else Column
                    return cls(
                        self._input_id,
                        ValueSlot(index),
                        column.unit,
                        column_kind(column),
                        _frame=self,
                        _name=name,
                    )
            for path in self._schema.presence_paths:
                if name == ".".join(path):
                    return ColumnPredicate(
                        self._input_id, PresenceSlot(path), None, "boolean", _frame=self, _name=name
                    )
        reject(
            "an exact current value or presence name",
            repr(name),
            f"Choose from {(*self.output_names, *self.presence_names)!r}; use frame.coord for coordinates.",
            "frame.__getitem__",
        )

    def _axis_index(self, axis: FrameAxis) -> int:
        if not isinstance(axis, FrameAxis) or (
            axis._input_id != self._input_id or axis._frame._local.owner != self._local.owner
        ):
            reject(
                "an axis from this exact Frame input",
                repr(axis),
                "Acquire the axis from this Frame's axes.",
                "frame.axis",
            )
        for index, current in enumerate(self.axes):
            if (current.token, current.name, current.role, current.key_names) == (
                axis.token,
                axis.name,
                axis.role,
                axis.key_names,
            ):
                return index
        reject(
            "a complete current axis handle",
            repr(axis),
            "Acquire the axis from this Frame's axes.",
            "frame.axis",
        )

    def coord(self, axis: FrameAxis) -> Column:
        index = self._axis_index(axis)
        columns = self._schema.axes[index].columns
        if len(columns) != 1:
            reject(
                "a single physical component coordinate",
                repr(axis.key_names),
                "Use the complete axis in by/on; do not split a composite identity.",
                "frame.coord",
            )
        column = columns[0]
        cls = ColumnPredicate if column_kind(column) == "boolean" else Column
        return cls(
            self._input_id,
            CoordinateSlot(index),
            column.unit,
            column_kind(column),
            _frame=self,
            _name=axis.name,
        )

    def _require_column(self, column: Column) -> None:
        if not isinstance(column, Column) or (
            column._input_id != self._input_id
            or column._frame is None
            or column._frame._local.owner != self._local.owner
        ):
            reject(
                "a Column from this exact Store/Session/Frame input",
                repr(column),
                "Locate the Column on this Frame, or align the two Frames first.",
                "frame.column",
            )

    def _bind_statistic(self, statistic: _Statistic) -> _Statistic:
        if not isinstance(statistic, _Statistic):
            reject(
                "an unfinished Column statistic or row_count()",
                type(statistic).__name__,
                "Construct the statistic from this Frame.",
                "frame.reduce",
            )
        if (
            statistic.input_id is None
            and statistic.definition.method == "row_count"
            and statistic.definition.value is None
        ):
            return _Statistic(self._input_id, statistic.definition, statistic.unit, self)
        if (
            statistic.input_id != self._input_id
            or statistic._frame is None
            or statistic._frame._local.owner != self._local.owner
        ):
            reject(
                "a statistic from this exact Frame input",
                repr(statistic.input_id),
                "Reacquire the Column on this Frame before constructing the statistic.",
                "frame.reduce",
            )
        return statistic
