"""Private unified Frame structure and exact handles, before public cutover."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from json import dumps
from typing import TypeAlias
from uuid import uuid4

import pandas as pd

from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    LiteralValues,
    PresenceSlot,
    ReadFields,
    ReduceValues,
    ResultQuery,
    SelectValues,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo._definition.types import DefinitionGraphError, LiteralValue, check_literal
from marivo.analysis.core.artifact_data import ArtifactData, DataSchema
from marivo.analysis.core.frame_audit import FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameResult,
    ExpressionFrameBacking,
    FrameExpressionRoot,
    LiteralFrameBinding,
    MemoryFrameBacking,
    OperationFrameBinding,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_expression import FrameOperationKind, infer_frame_schema
from marivo.analysis.core.frame_source_facts import captured_source_scopes
from marivo.analysis.core.frame_structure import (
    PendingDataSchema,
    axis_role,
    column_kind,
    require_complete_structure,
)
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
        cls, local: LocalFrameServices, schema: FrameSchema, root: FrameExpressionRoot
    ) -> AnalysisFrame:
        if (
            isinstance(root, (SourceFrameBinding, LiteralFrameBinding))
            and root.graph.signature.owner != local.owner
        ):
            reject(
                "a source graph from this Store and Session",
                repr(root.graph.signature.owner),
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
        return cls._bind(local, schema, ExpressionFrameBacking(root), "frame_" + uuid4().hex)

    @classmethod
    def _from_memory(
        cls,
        local: LocalFrameServices,
        result: CompletedFrameResult,
    ) -> AnalysisFrame:
        backing = MemoryFrameBacking(result)
        return cls._bind(local, result.data.schema, backing, "frame_" + uuid4().hex)

    @classmethod
    def _from_stored(
        cls,
        local: LocalFrameServices,
        data: ArtifactData,
        *,
        artifact_id: str,
        metadata: FrameResultMetadata,
    ) -> AnalysisFrame:
        backing = StoredFrameBacking(artifact_id, data, metadata)
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
        return f"<AnalysisFrame input={self._input_id[:16]} backing={kind} outputs={len(self._schema.values)}; use .show()>"

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

    def _derive(self, operation: FrameOperationKind) -> AnalysisFrame:
        structure = infer_frame_schema(self._schema, operation)
        return self._from_expression(
            self._local, structure, OperationFrameBinding(operation, (self,))
        )

    def where(self, predicate: Column) -> AnalysisFrame:
        """Filter current rows by a same-input boolean Column; NA is excluded."""
        self._require_column(predicate)
        if predicate._kind != "boolean":
            reject(
                "a boolean Column",
                predicate._kind,
                "Compare current values to construct a predicate.",
                "frame.where",
            )
        return self._derive(FilterValues(predicate._expression))

    def select(self, values: Mapping[str, Column | LiteralValue]) -> AnalysisFrame:
        """Name current Columns or literals while preserving axes and presence."""
        if not isinstance(values, Mapping) or not values:
            reject(
                "a nonempty named value mapping",
                type(values).__name__,
                "Pass select({'name': column}).",
                "frame.select",
            )
        expressions: list[tuple[str, ValueExpression]] = []
        from marivo.analysis.core.frame_structure import value_name

        for name, value in values.items():
            value_name(name, location="frame.select")
            if isinstance(value, Column):
                self._require_column(value)
                expression = value._expression
            else:
                try:
                    literal = check_literal(value, role="Frame select value")
                except DefinitionGraphError as error:
                    reject(
                        "an immutable finite literal or current Column",
                        str(error),
                        "Select a current Column or finite typed literal.",
                        "frame.select",
                    )
                if literal is None:
                    reject(
                        "a non-null literal",
                        "None",
                        "Select a nullable Column or derive NA with a typed operation.",
                        "frame.select",
                    )
                if isinstance(literal, datetime):
                    if literal.utcoffset() is None:
                        reject(
                            "an aware timestamp literal",
                            repr(literal),
                            "Use an aware datetime or a date value.",
                            "frame.select",
                        )
                    literal = literal.astimezone(timezone.utc)
                expression = ValueLiteral(literal)
            expressions.append((name, expression))
        return self._derive(SelectValues(tuple(expressions)))

    def top(self, n: int, *, by: Column, descending: bool = True) -> AnalysisFrame:
        """Select the first global n ranked rows, with complete-key tie breaking."""
        if type(n) is not int or n < 0 or type(descending) is not bool:
            reject(
                "a nonnegative integer and boolean descending",
                repr((n, descending)),
                "Choose a finite global Top-N selection.",
                "frame.top",
            )
        self._require_column(by)
        return self._derive(TopValues(n, by._expression, descending, ()))

    def reduce(
        self, values: Mapping[str, _Statistic], *, by: tuple[FrameAxis, ...] = ()
    ) -> AnalysisFrame:
        """Aggregate current values by retained complete axes, deleting presence."""
        if not isinstance(values, Mapping) or not values or type(by) is not tuple:
            reject(
                "a nonempty statistic mapping and tuple of current axes",
                type(values).__name__,
                "Pass reduce({'rows': row_count()}, by=(frame.axes[0],)).",
                "frame.reduce",
            )
        positions = tuple(self._axis_index(axis) for axis in by)
        if len(set(positions)) != len(positions):
            reject(
                "distinct complete current axes",
                repr(positions),
                "Use each complete axis once.",
                "frame.reduce",
            )
        from marivo.analysis.core.frame_structure import value_name

        statistics = []
        for name, value in values.items():
            value_name(name, location="frame.reduce")
            statistics.append((name, self._bind_statistic(value).definition))
        return self._derive(ReduceValues(tuple(statistics), positions))

    def compute(self) -> AnalysisFrame:
        """Evaluate explicit inputs without publishing; complete memory returns itself."""
        if isinstance(self._backing, MemoryFrameBacking):
            return self
        if isinstance(self._backing, StoredFrameBacking):
            reject(
                "the complete stored Frame reader",
                self._backing.artifact_id,
                "Stored compute is integrated with the Store 12 reader before public cutover.",
                "frame.compute",
            )
        from marivo.analysis.materialization.frame_evaluation import evaluate_frame

        return self._from_memory(self._local, evaluate_frame(self))

    def to_pandas(self) -> pd.DataFrame:
        """Export isolated completed data; pending expressions require compute."""
        if not isinstance(self._backing, MemoryFrameBacking):
            reject(
                "a complete memory Frame",
                type(self._backing).__name__,
                "Call compute() and export the returned Frame.",
                "frame.to_pandas",
            )
        return _export(self._backing.data.copy_rows(), self._schema)

    def show(self, *, n: int = 20) -> None:
        """Display current metadata and a bounded completed-memory preview without execution."""
        if type(n) is not int or n < 0:
            reject(
                "a nonnegative integer preview count",
                repr(n),
                "Use n=0 for metadata only.",
                "frame.show",
            )
        from marivo.render import Card

        card = Card(identity=repr(self), available=(".axes", ".output_names", ".presence_names"))
        card.field("values", repr(self.output_names)).field("presence", repr(self.presence_names))
        card.field(
            "axes",
            repr(tuple((axis.token, axis.name, axis.role, axis.key_names) for axis in self.axes)),
        )
        if isinstance(self._backing, ExpressionFrameBacking):
            root = self._backing.root
            if isinstance(root, OperationFrameBinding):
                card.field("operation", _bounded(_operation_summary(root)))
                card.field(
                    "inputs",
                    _bounded(
                        repr(
                            tuple(
                                (child._input_id, _input_kind(child._backing))
                                for child in root.inputs
                            )
                        )
                    ),
                )
            elif isinstance(root, SourceFrameBinding):
                node = root.graph.root
                operation = (
                    "read"
                    if isinstance(node, ResultQuery) and isinstance(node.operation, ReadFields)
                    else "literal"
                    if isinstance(node, ResultQuery) and isinstance(node.operation, LiteralValues)
                    else "observe"
                    if root.graph.signature.quantities
                    else "frame"
                )
                card.field("operation", operation).field("inputs", "source")
                scopes = captured_source_scopes(root.graph)
                card.field(
                    "scope",
                    _bounded("; ".join(scopes), 1800)
                    if scopes
                    else "no captured filter or member restriction",
                )
                observation = root.graph.signature.observation
                if observation is not None and observation.period is not None:
                    card.field("period", _bounded(repr(observation.period)))
                anchors = tuple(
                    (axis.token, axis.at.isoformat())
                    for axis in self._schema.axes
                    if axis.at is not None
                )
                if anchors:
                    card.field("anchors", _bounded(repr(anchors)))
            else:
                card.field("operation", "literal").field("inputs", "literal")
            card.field("rows", "pending; no input rows read")
            card.field(
                "types",
                "pending source physical types"
                if isinstance(self._schema, PendingDataSchema)
                else "captured concrete data types",
            )
        elif isinstance(self._backing, MemoryFrameBacking):
            card.field("rows", str(self._backing.data.row_count))
            if n:
                count = min(n, 20)
                preview = self._backing.data.preview_rows(count)
                card.field(
                    "preview",
                    preview.to_string(index=False, max_cols=8, max_colwidth=60)
                    if len(preview)
                    else "empty result",
                )
                if self._backing.data.row_count > count:
                    card.field(
                        "omitted",
                        f"{self._backing.data.row_count - count} rows; preview/card budget",
                    )
            else:
                card.field("preview", "omitted by n=0")
        else:
            card.field("rows", str(self._backing.data.rows.receipt.realized_row_count))
            if n:
                reject(
                    "the stored Frame preview reader",
                    self._backing.artifact_id,
                    "Stored previews join the Store 12 reader before public cutover; n=0 inspects metadata.",
                    "frame.show",
                )
        print(card.render())


def _bounded(text: str, limit: int = 900) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    prefix = encoded[:limit].decode("utf-8", errors="ignore")
    return f"{prefix}... omitted (display budget; {len(encoded)} total bytes)"


def _input_kind(backing: FrameBacking) -> str:
    if isinstance(backing, MemoryFrameBacking):
        return "memory"
    if isinstance(backing, StoredFrameBacking):
        return "stored"
    if isinstance(backing.root, SourceFrameBinding):
        return "expression(source)"
    if isinstance(backing.root, LiteralFrameBinding):
        return "expression(literal)"
    return "expression"


def _value_summary(value: object, source: AnalysisFrame, depth: int = 0) -> str:
    if depth >= 8:
        return "... omitted (expression depth)"
    if isinstance(value, ValueSlot):
        return f"value[{source.output_names[value.index]!r}]"
    if isinstance(value, CoordinateSlot):
        return f"coordinate[{source._schema.axes[value.index].token!r}]"
    if isinstance(value, PresenceSlot):
        return f"presence[{'.'.join(value.path)!r}]"
    if isinstance(value, ValueLiteral):
        return repr(value.value)
    if isinstance(value, ValueUnary):
        return f"{value.operation}({_value_summary(value.operand, source, depth + 1)})"
    if isinstance(value, ValueBinary):
        policy = (
            f", zero_denominator={value.zero_denominator!r}" if value.operation == "div" else ""
        )
        return (
            f"{value.operation}({_value_summary(value.left, source, depth + 1)}, "
            f"{_value_summary(value.right, source, depth + 1)}{policy})"
        )
    return type(value).__name__


def _operation_summary(binding: OperationFrameBinding) -> str:
    operation, source = binding.operation, binding.inputs[0]
    if isinstance(operation, SelectValues):
        values = tuple((name, _value_summary(value, source)) for name, value in operation.values)
        return f"select(values={values!r})"
    if isinstance(operation, FilterValues):
        return f"where(predicate={_value_summary(operation.predicate, source)})"
    if isinstance(operation, TopValues):
        return (
            f"top(n={operation.n}, by={_value_summary(operation.score, source)}, "
            f"descending={operation.descending})"
        )
    statistics = tuple(
        (
            name,
            statistic.method,
            None if statistic.value is None else _value_summary(statistic.value, source),
        )
        for name, statistic in operation.values
    )
    axes = tuple(source._schema.axes[position].token for position in operation.positions)
    return f"reduce(values={statistics!r}, by={axes!r})"


def _export(rows: pd.DataFrame, structure: FrameSchema) -> pd.DataFrame:
    if not isinstance(structure, DataSchema):
        reject(
            "a complete data structure",
            type(structure).__name__,
            "Compute the Frame first.",
            "frame.export",
        )
    series: list[pd.Series] = []
    names: list[tuple[str, str]] = []
    for axis in structure.axes:
        if len(axis.columns) == 1:
            series.append(rows[axis.columns[0].slot_id].reset_index(drop=True))
        else:
            series.append(
                pd.Series(
                    tuple(
                        rows[[c.slot_id for c in axis.columns]].itertuples(index=False, name=None)
                    )
                )
            )
        names.append(("coordinate", axis.token))
    for role, columns in (("value", structure.values), ("presence", structure.presence)):
        for column in columns:
            series.append(rows[column.slot_id].reset_index(drop=True))
            names.append((role, column.name))
    result = pd.concat(series, axis=1) if series else pd.DataFrame(index=pd.RangeIndex(len(rows)))
    result.columns = (
        pd.MultiIndex.from_tuples(names) if names else pd.MultiIndex.from_arrays([[], []])
    )
    result.attrs = {
        "axes": tuple(
            {
                "token": axis.token,
                "name": axis.name,
                "role": axis_role(axis),
                "key_names": tuple(c.name for c in axis.columns),
                "at": axis.at,
            }
            for axis in structure.axes
        ),
        "units": tuple((column.name, column.unit) for column in structure.values),
    }
    return result
