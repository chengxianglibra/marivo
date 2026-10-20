"""Immutable current Frame structures, lazy operations and fixed-data lifecycles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from json import dumps
from typing import Literal, TypeAlias
from uuid import uuid4

import pandas as pd

from marivo._definition.correspondence import CalendarCorrespondence
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    PairStatistic,
    PresenceSlot,
    ReduceValues,
    ResultQuery,
    SelectValues,
    Statistic,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo._definition.relations import ReadQuery
from marivo._definition.types import DefinitionGraphError, LiteralValue, check_literal
from marivo.analysis._scope import scope_summary
from marivo.analysis.core.artifact_data import ArtifactData, DataSchema, SavedFrameMethodFacts
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
from marivo.analysis.core.frame_expression import (
    AlignFrameValues,
    AttributeContributionValues,
    AttributeFrameValues,
    AttributeInputsValues,
    AttributeProjectValues,
    CompareFrameValues,
    FrameUnaryOperationKind,
    NamedFrameComparison,
    infer_frame_combine_schema,
    infer_frame_compare_schema,
    infer_frame_schema,
)
from marivo.analysis.core.frame_method_facts import FrameMethodFacts
from marivo.analysis.core.frame_structure import (
    PendingDataSchema,
    axis_role,
    column_kind,
    require_complete_structure,
)
from marivo.analysis.core.model import reject
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.dsl.column import Column, _Statistic
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.materialization.frame_services import LocalFrameServices

FrameBacking: TypeAlias = ExpressionFrameBacking | MemoryFrameBacking | StoredFrameBacking
FrameSchema: TypeAlias = DataSchema | PendingDataSchema


@dataclass(frozen=True, init=False, repr=False, eq=False)
class AnalysisFrame:
    """One immutable data structure with expression, complete-memory or stored backing.

    Acquire through Session methods and Frame operators. Compute and persist return
    fixed results without mutating the original object.
    """

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
        method_facts: tuple[SavedFrameMethodFacts, ...] = (),
    ) -> AnalysisFrame:
        backing = StoredFrameBacking(artifact_id, data, metadata, method_facts)
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
        """Return complete ordered value names, excluding coordinates and presence.

        Args: None.
        Returns: A tuple of exact public value paths joined by dots.
        Example: ``names = frame.output_names``.
        Constraints: Metadata only; does not prepare schema or read rows.
        """
        return tuple(".".join(path) for path in self._schema.value_paths)

    @property
    def presence_names(self) -> tuple[str, ...]:
        """Return ordered names of independent nonnullable boolean presence columns.

        Args: None.
        Returns: Exact current presence paths joined by dots.
        Example: ``missing = frame.where(~frame['right_present'])``.
        Constraints: Reads metadata only; value NA does not imply missing rows.
        """
        return tuple(".".join(path) for path in self._schema.presence_paths)

    @property
    def axes(self) -> tuple[FrameAxis, ...]:
        """Return ordered complete axis handles bound to this exact input.

        Args: None.
        Returns: A tuple of FrameAxis handles; composite identities remain grouped.
        Example: ``axis = frame.axes[0]``.
        Constraints: Metadata only; handles from another input do not authorize operations here.
        """
        schema = self._schema
        return tuple(
            FrameAxis._from_frame(
                self, axis.token, axis.name, axis_role(axis), tuple(c.name for c in axis.columns)
            )
            for axis in schema.axes
        )

    def axis(self, name: str) -> FrameAxis:
        """Resolve one exact current display name to its bound complete axis.

        Args: name: Exact display name in the current structure.
        Returns: The unique matching FrameAxis.
        Example: ``region = frame.axis('sales.order.region')``.
        Constraints: Missing/ambiguous names reject with actual candidates; no cross-input inference.
        """
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
        """Return a bound value or presence Column by its exact current path.

        Args: name: Exact value/presence name.
        Returns: A Column bound to this Frame input.
        Example: ``column = frame['left.revenue']``.
        Constraints: No row reads, suffix/role guessing or coordinate lookup; use coord for coordinates.
        """
        if type(name) is str:
            for index, path in enumerate(self._schema.value_paths):
                if name == ".".join(path):
                    column = self._schema.values[index]
                    return Column(
                        self._input_id,
                        ValueSlot(index),
                        column.unit,
                        column_kind(column),
                        _frame=self,
                        _name=name,
                    )
            for path in self._schema.presence_paths:
                if name == ".".join(path):
                    return Column(
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
        """Return a coordinate Column for one complete single-component axis.

        Args: axis: A FrameAxis from this exact input.
        Returns: A bound coordinate Column.
        Example: ``key = frame.coord(frame.axes[0])``.
        Constraints: Composite axes remain legal by/on inputs but cannot be split by coord.
        """
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
        return Column(
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
            and isinstance(statistic.definition, Statistic)
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

    def _derive(self, operation: FrameUnaryOperationKind) -> AnalysisFrame:
        structure = infer_frame_schema(self._schema, operation)
        return self._from_expression(
            self._local, structure, OperationFrameBinding(operation, (self,))
        )

    def where(self, predicate: Column) -> AnalysisFrame:
        """Filter current rows by a same-input boolean Column.

        Args: predicate: A boolean Column bound to this exact input.
        Returns: A lazy AnalysisFrame with the same axes and presence.
        Example: ``positive = frame.where(frame['revenue'] > 0)``.
        Constraints: Keeps True, excludes False and NA, and preserves relative row order.
        """
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
        """Name current Columns or non-null supported literals, preserving the row domain.

        Args: values: Nonempty ordered mapping of user name segments to bound Columns/literals.
        Returns: A lazy Frame retaining axes and all presence, with only the selected values.
        Example: ``selected = frame.select({'change': frame['reference.absolute.revenue']})``.
        Constraints: Does not recover removed values or endpoint inputs; no source reads.
        """
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
        """Select global Top-N current rows with complete-key tie breaking.

        Args:
            n: Nonnegative integer excluding bool.
            by: Same-input ranking Column.
            descending: Boolean rank direction, default True.

        Returns:
            A lazy globally ranked AnalysisFrame.

        Example:
            ``leaders = frame.top(5, by=frame['revenue'])``.

        Constraints: NA last; no partition parameter or source regrouping.
        """
        if type(n) is not int or n < 0 or type(descending) is not bool:
            reject(
                "a nonnegative integer and boolean descending",
                repr((n, descending)),
                "Choose a finite global Top-N selection.",
                "frame.top",
            )
        self._require_column(by)
        return self._derive(TopValues(n, by._expression, descending))

    def reduce(
        self, values: Mapping[str, _Statistic], *, by: tuple[FrameAxis, ...] = ()
    ) -> AnalysisFrame:
        """Aggregate current rows by retained complete axes, removing all presence.

        Args:
            values: Nonempty ordered named unfinished-statistic mapping.
            by: Tuple of complete same-input FrameAxis handles, default ().

        Returns:
            A lazy current-row aggregate AnalysisFrame.

        Example:
            ``mean = frame.reduce({'mean': frame['revenue'].mean()})``.

        Constraints: Each named statistic produces one column with its independent
        NA mask. Use correlate for a coefficient and its matching pair count;
        corr/pair_count compose with sum/mean/row_count without inferred pairing.
        No original Metric regrouping or calendar coarsening.
        """
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

    def correlate(
        self,
        x: Column,
        y: Column,
        *,
        method: Literal["pearson", "spearman"],
        by: tuple[FrameAxis, ...] = (),
    ) -> AnalysisFrame:
        """Correlate two numeric expressions over this Frame's current rows.

        Args:
            x: Numeric Column bound to this exact Frame/Session input.
            y: Numeric Column on the same input; its unit may differ from x.
            method: Required explicit pearson or spearman.
            by: Tuple of complete same-input axes; () produces one scalar row.

        Returns:
            A lazy ordinary Frame with coefficient (nullable float64, unit 1)
            and n_pairs (nonnullable int64, no unit), in that order.

        Example:
            ``frame.correlate(frame['spend'], frame['revenue'], method='pearson').compute()``.

        Constraints: Both expressions are checked before a common non-NA mask;
        Duration is rejected, including after physical type preparation. Retains
        zero-pair groups, removes presence, and returns NA for fewer than two
        pairs or a constant endpoint. Construction and show read no rows. Use
        reduce with corr/pair_count to compose several named statistics.
        """
        if method not in ("pearson", "spearman"):
            reject(
                "pearson or spearman",
                repr(method),
                "Choose an explicit supported correlation method.",
                "frame.correlate",
            )
        for label, column in (("x", x), ("y", y)):
            if not isinstance(column, Column) or (
                column._input_id != self._input_id
                or column._frame is None
                or column._frame._local.owner != self._local.owner
            ):
                reject(
                    f"{label}: a Column from this exact Store/Session/Frame input",
                    repr(column),
                    "Acquire both Columns on this Frame; align independent Frames first.",
                    "frame.correlate",
                )
        if type(by) is not tuple:
            reject(
                "a tuple of complete same-input FrameAxis handles",
                repr(by),
                "Use by=() or by=(frame.axes[0],).",
                "frame.correlate",
            )
        for axis in by:
            if not isinstance(axis, FrameAxis) or (
                axis._input_id != self._input_id or axis._frame._local.owner != self._local.owner
            ):
                reject(
                    "complete axes from this exact Frame/Session input",
                    repr(axis),
                    "Acquire by axes from this Frame.",
                    "frame.correlate",
                )
        positions = tuple(self._axis_index(axis) for axis in by)
        if len(set(positions)) != len(positions):
            reject(
                "distinct complete current axes",
                repr(positions),
                "Use each complete axis once.",
                "frame.correlate",
            )
        coefficient = x._pair_statistic(y, method, location="frame.correlate")
        count = x._pair_statistic(y, "pair_count", location="frame.correlate")
        return self._derive(
            ReduceValues(
                (("coefficient", coefficient.definition), ("n_pairs", count.definition)), positions
            )
        )

    def _axis_pairs(
        self,
        other: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None,
        match: CalendarCorrespondence | None,
    ) -> tuple[tuple[int, int], ...]:
        if (match is not None and not isinstance(match, CalendarCorrespondence)) or (
            on is not None
            and (
                type(on) is not tuple
                or any(
                    type(pair) is not tuple
                    or len(pair) != 2
                    or any(not isinstance(axis, FrameAxis) for axis in pair)
                    for pair in on
                )
            )
        ):
            reject(
                "closed binary policies and a tuple of FrameAxis pairs",
                repr((on, match)),
                "Use complete FrameAxis pairs and exact/outer, null/error policies.",
                "frame.combine",
            )
        if not isinstance(other, AnalysisFrame) or other._local.owner != self._local.owner:
            reject(
                "Frame inputs from the same Store and Session",
                repr(other),
                "Use two inputs owned by the same Session.",
                "frame.combine",
            )
        left_axes, right_axes = self.axes, other.axes
        if any(axis.role == "paired" for axis in (*left_axes, *right_axes)):
            reject(
                "inputs without paired axes",
                repr((left_axes, right_axes)),
                "Reduce away paired axes or construct new source inputs.",
                "frame.combine",
            )
        if len(left_axes) != len(right_axes):
            reject(
                "equal complete axis counts",
                repr((len(left_axes), len(right_axes))),
                "Reduce or reconstruct inputs with compatible axes.",
                "frame.combine",
            )
        if on is None:
            if (
                len(left_axes) == 1
                and left_axes[0].role == right_axes[0].role
                and left_axes[0].role in ("calendar", "interval")
            ):
                on = ((left_axes[0], right_axes[0]),)
            elif left_axes:
                reject(
                    "a complete explicit axis bijection",
                    repr((left_axes, right_axes)),
                    "Pass on with every complete axis exactly once; only zero axes or a sole calendar axis on each side can be inferred.",
                    "frame.combine",
                )
            else:
                on = ()
        if (
            len(on) != len(left_axes)
            or {a.token for a, _ in on} != {a.token for a in left_axes}
            or {b.token for _, b in on} != {b.token for b in right_axes}
        ):
            reject(
                "a complete explicit axis bijection",
                repr(on),
                f"Bind each complete axis once from {left_axes!r} to {right_axes!r}.",
                "frame.combine",
            )
        return tuple((self._axis_index(a), other._axis_index(b)) for a, b in on)

    def align(
        self,
        other: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None = None,
        match: CalendarCorrespondence | None = None,
        how: Literal["exact", "outer"] = "exact",
    ) -> AnalysisFrame:
        """Align all current values by complete axes, adding independent row presence.

        Args:
            other: Same-Session Frame.
            on: Complete FrameAxis-pair bijection or None.
            match: Optional closed calendar rule.
            how: 'exact' or ordered 'outer'.

        Returns:
            A lazy Frame containing all left/right values and presence.

        Example:
            ``paired = a.align(b, on=((a.axes[0], b.axes[0]),))``.

        Constraints: Only zero axes or a sole calendar axis per side infer on; ordinary axes
        require explicit mapping. Paired inputs reject. No Catalog or row reads.
        """
        pairs = self._axis_pairs(other, on=on, match=match)
        operation = AlignFrameValues(pairs, how, match)
        schema = infer_frame_combine_schema(self._schema, other._schema, operation)
        return self._from_expression(
            self._local, schema, OperationFrameBinding(operation, (self, other))
        )

    def compare(
        self,
        baselines: Mapping[str, AnalysisFrame],
        *,
        on: Mapping[str, tuple[tuple[FrameAxis, FrameAxis], ...]] | None = None,
        match: Mapping[str, CalendarCorrespondence] | None = None,
        how: Literal["exact", "outer"] = "exact",
        zero_denominator: Literal["null", "error"] = "null",
    ) -> AnalysisFrame:
        """Compare current values with one or more explicitly named baseline Frames.

        Args:
            baselines: Nonempty ordered mapping of same-Session Frames; current is reserved.
            on: Per-alias complete axis bijections. Omitted aliases require unique inference.
            match: Per-alias closed time rules, consistent with any frozen direct relation.
            how: Exact joint actual domains or an ordered outer union within the current time domain.
            zero_denominator: Null or error for valid pairs with a zero baseline.

        Returns:
            A lazy ordinary Frame with current values, named baseline/difference groups and presence.

        Example:
            ``change = current.compare({"reference": baseline}, match={"reference": mv.ordinal()})``.

        Constraints: Equal nonempty numeric value paths and units; no paired inputs. Relative
        change is (current-baseline)/abs(baseline). Missing rows and NA precede zero policy.
        No source replay or date inference from alias names.
        """
        from marivo.analysis.core.frame_structure import validate_paths

        if not isinstance(baselines, Mapping) or not baselines:
            reject(
                "a nonempty named baseline Frame mapping",
                repr(baselines),
                "Pass compare({'reference': baseline}) even for one baseline.",
                "frame.compare",
            )
        names = tuple(baselines)
        validate_paths(tuple((name,) for name in names), location="frame.compare.aliases")
        if "current" in names:
            reject(
                "baseline aliases other than current",
                repr(names),
                "Rename the baseline alias.",
                "frame.compare",
            )
        if how not in ("exact", "outer") or zero_denominator not in ("null", "error"):
            reject(
                "exact/outer and null/error comparison policies",
                repr((how, zero_denominator)),
                "Use how='exact' or 'outer' and zero_denominator='null' or 'error'.",
                "frame.compare",
            )
        for mapping in (on, match):
            if mapping is not None and (
                not isinstance(mapping, Mapping) or set(mapping) - set(names)
            ):
                reject(
                    "on/match mappings containing only supplied baseline aliases",
                    repr(mapping),
                    f"Use the actual baseline aliases {names!r}.",
                    "frame.compare",
                )
        if on is not None and any(type(pairs) is not tuple for pairs in on.values()):
            reject(
                "a complete axis-pair tuple for every explicitly supplied on alias",
                repr(on),
                "Omit an alias to request inference, or supply its complete axis-pair tuple.",
                "frame.compare",
            )
        if match is not None and any(
            type(rule) is not CalendarCorrespondence for rule in match.values()
        ):
            reject(
                "a closed calendar rule for every explicitly supplied match alias",
                repr(match),
                "Omit an alias for direct inference or equality, or supply ordinal/offset.",
                "frame.compare",
            )
        bindings = tuple(
            NamedFrameComparison(
                name,
                self._axis_pairs(
                    baseline,
                    on=None if on is None else on.get(name),
                    match=None if match is None else match.get(name),
                ),
                None if match is None else match.get(name),
            )
            for name, baseline in baselines.items()
        )
        operation = CompareFrameValues(bindings, how, zero_denominator)
        inputs = (self, *baselines.values())
        schema = infer_frame_compare_schema(tuple(frame._schema for frame in inputs), operation)
        return self._from_expression(self._local, schema, OperationFrameBinding(operation, inputs))

    def compute(self) -> AnalysisFrame:
        """Complete explicit inputs without publishing or returning partial data.

        Args: None.

        Returns:
            Self for complete memory; otherwise a new independent complete-memory Frame.

        Example:
            ``fixed = frame.compute()``.

        Constraints: Reads only explicit inputs, closes sources before success, and creates no
        persistent Run, Artifact or result files. Stored loading preserves result facts.
        """
        if isinstance(self._backing, MemoryFrameBacking):
            return self
        if isinstance(self._backing, StoredFrameBacking):
            return self._from_memory(self._local, self._local.read_complete(self._backing))
        from marivo.analysis.materialization.frame_evaluation import evaluate_frame

        return self._from_memory(self._local, evaluate_frame(self))

    @property
    def method_facts(self) -> tuple[FrameMethodFacts, ...]:
        """Read the complete immutable facts explaining original decompositions.

        Returns: An ordered tuple of self-contained FrameMethodFacts.
        Example: ``facts = fixed.method_facts``.
        Read static type details with marivo.help("analysis.frame_method_facts").
        Constraints: Pending expressions require compute/persist. Stored reads validate
        all owned method payloads, without reading current data or loading sources/models.
        Inherited facts explain their original result identities, not derived values.
        """
        if isinstance(self._backing, ExpressionFrameBacking):
            reject(
                "an evaluated memory or stored Frame",
                "pending expression",
                "Call compute() or persist() before reading method_facts.",
                "frame.method_facts",
            )
        if isinstance(self._backing, MemoryFrameBacking):
            return self._backing.method_facts
        return self._local.read_method_facts(self._backing)

    def persist(self) -> AnalysisFrame:
        """Publish a completed result; exact stored roots return themselves.

        Args: None.
        Returns: A stored AnalysisFrame owned by the same original local service.
        Example: ``saved = frame.compute().persist()``.
        Constraints: Pending evaluation completes before publication admission;
        memory publication does not reevaluate, and stored idempotence reads metadata only.
        """
        if isinstance(self._backing, StoredFrameBacking):
            selected = self._local.open_frame(self._backing.artifact_id)
            if selected != self._backing:
                reject(
                    "the exact original committed descriptor",
                    self._backing.artifact_id,
                    "Preserve the selected Artifact metadata.",
                    "frame.persist",
                )
            return self
        completed = self.compute()
        assert isinstance(completed._backing, MemoryFrameBacking)
        backing = self._local.publish(completed._backing.result)
        return self._from_stored(
            self._local,
            backing.data,
            artifact_id=backing.artifact_id,
            metadata=backing.metadata,
            method_facts=backing.method_facts,
        )

    @property
    def artifact(self) -> Artifact:
        """Open commit and audit metadata for this exact stored root.

        Returns: The Artifact audit view for this committed Frame.
        Example: ``saved.artifact.show()``.
        Constraints: Memory and derived expressions require persist first; no payload read.
        """
        if not isinstance(self._backing, StoredFrameBacking):
            reject(
                "an exact stored Frame root",
                type(self._backing).__name__,
                "Call persist() and use the returned Frame's artifact.",
                "frame.artifact",
            )
        selected = self._local.open_frame(self._backing.artifact_id)
        if selected != self._backing:
            reject(
                "the exact original committed descriptor",
                self._backing.artifact_id,
                "Preserve the selected Artifact metadata.",
                "frame.artifact",
            )
        return self._local.artifact(self._backing.artifact_id)

    def to_pandas(self) -> pd.DataFrame:
        """Export all fixed data as isolated typed arrays and inert attrs.

        Args: None.
        Returns: A complete pandas DataFrame with raw coordinate MultiIndex columns.
        Example: ``rows = saved.to_pandas()``.
        Constraints: Pending expressions require compute/persist; saved reads fully validate.
        """
        from marivo.analysis.materialization.pandas_export import export_frame

        if isinstance(self._backing, ExpressionFrameBacking):
            reject(
                "a complete memory or stored Frame",
                "expression",
                "Call compute() or persist() before exporting.",
                "frame.to_pandas",
            )
        data = (
            self._local.read_data(self._backing)
            if isinstance(self._backing, StoredFrameBacking)
            else self._backing.data
        )
        return export_frame(data.schema, data.copy_rows(), data.copy_attachments())

    def show(self, *, n: int = 20) -> None:
        """Display current metadata and a bounded fixed-data prefix without evaluation.

        Args:
            n: Nonnegative integer excluding bool, default 20; zero is metadata only.

        Returns:
            None; prints the structure and available memory/stored preview.

        Example:
            ``saved.show(n=0)``.

        Constraints: Pending display never prepares schema; positive stored previews bound
        decoded output, not receipt hashing or Parquet IO. No Run, full-export/head, ancestor
        rows, complete-result cache or fallback after a read error. Method facts
        show only counts/origins and a .method_facts pointer; no method payload read.
        """
        if type(n) is not int or n < 0:
            reject(
                "a nonnegative integer preview count",
                repr(n),
                "Use n=0 for metadata only.",
                "frame.show",
            )
        from marivo.render import Card

        available = (
            (".compute()", ".persist()")
            if isinstance(self._backing, ExpressionFrameBacking)
            else (".to_pandas()", ".persist()")
            if isinstance(self._backing, MemoryFrameBacking)
            else (".to_pandas()", ".artifact")
        )
        card = Card(
            identity=repr(self),
            available=available,
            omission_recovery="use .output_names and .axes for captured structure; compute() for data"
            if isinstance(self._backing, ExpressionFrameBacking)
            else "use .output_names for all names and .to_pandas() for complete fixed data",
        )
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
                    if isinstance(node, ReadQuery)
                    else "literal"
                    if isinstance(node, ResultQuery)
                    else "observe"
                    if root.graph.signature.quantities
                    else "frame"
                )
                card.field("operation", operation).field("inputs", "source")
            else:
                card.field("operation", "literal").field("inputs", "literal")
            sources = _pending_source_inputs(self)
            if sources:
                scopes = tuple(
                    dict.fromkeys(
                        scope_summary(source.graph.root, source.graph.signature.observation)
                        for _, source in sources
                    )
                )
                card.field(
                    "scope",
                    _bounded("; ".join(scopes), 1800)
                    if scopes
                    else "no captured filter or member restriction",
                )
                anchors = tuple(
                    (frame._input_id, axis.token, axis.at.isoformat())
                    for frame, _ in sources
                    for axis in frame._schema.axes
                    if axis.at is not None
                )
                if anchors:
                    card.field("anchors", _bounded(repr(anchors)))
            card.field("rows", "pending; no input rows read")
            card.field(
                "types",
                "pending source physical types"
                if isinstance(self._schema, PendingDataSchema)
                else "captured concrete data types",
            )
        else:
            from marivo.analysis.materialization.pandas_export import preview_text

            if isinstance(self._backing, MemoryFrameBacking):
                total = self._backing.data.row_count
            else:
                selected = self._local.open_frame(self._backing.artifact_id)
                if selected != self._backing:
                    reject(
                        "the exact original committed descriptor",
                        self._backing.artifact_id,
                        "Preserve the selected Artifact metadata.",
                        "frame.show",
                    )
                total = self._backing.data.rows.receipt.realized_row_count
            card.field("result_id", self._backing.metadata.result_id)
            facts = self._backing.method_facts
            card.field("method_facts", str(len(facts)))
            if facts:
                card.field(
                    "method_origins", _bounded(repr(tuple(item.origin_result_id for item in facts)))
                )
                card.field(
                    "method_facts_read", "use .method_facts for complete original explanation facts"
                )
            card.field("evaluated_at", self._backing.metadata.evaluated_at.isoformat())
            if isinstance(self._backing, StoredFrameBacking):
                card.field("artifact", self._backing.artifact_id)
            card.field("rows", str(total))
            if n:
                count = min(n, 20)
                if isinstance(self._backing, MemoryFrameBacking):
                    rows = self._backing.data.preview_rows(count)
                    attachments = self._backing.data.copy_attachments()
                else:
                    prefix = self._local.preview(self._backing, count)
                    rows, attachments = prefix.rows, prefix.attachments
                assert isinstance(self._schema, DataSchema)
                card.field("preview", _bounded(preview_text(self._schema, rows, attachments), 4000))
                if total > count:
                    card.field("omitted", f"{total - count} rows; preview/card budget")
            else:
                card.field("preview", "omitted by n=0")
        print(card.render())


def _pending_source_inputs(
    frame: AnalysisFrame,
) -> tuple[tuple[AnalysisFrame, SourceFrameBinding], ...]:
    """Collect explicit current source leaves; fixed inputs never recover history."""
    pending = [frame]
    seen: set[str] = set()
    sources: list[tuple[AnalysisFrame, SourceFrameBinding]] = []
    while pending:
        current = pending.pop()
        if current._input_id in seen:
            continue
        seen.add(current._input_id)
        backing = current._backing
        if not isinstance(backing, ExpressionFrameBacking):
            continue
        root = backing.root
        if isinstance(root, SourceFrameBinding):
            sources.append((current, root))
        elif isinstance(root, OperationFrameBinding):
            pending.extend(reversed(root.inputs))
    return tuple(sources)


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
    if isinstance(operation, CompareFrameValues):
        return f"compare(baselines={operation.baselines!r}, how={operation.how!r}, zero_denominator={operation.zero_denominator!r})"
    if isinstance(operation, AlignFrameValues):
        match = f", match={operation.match!r}" if operation.match is not None else ""
        return f"align(on={operation.pairs!r}, how={operation.how!r}{match})"
    if isinstance(operation, AttributeContributionValues):
        return f"attribute(parent_how={operation.mapping.how!r}, contribution=True)"
    if isinstance(operation, AttributeInputsValues):
        return "private Attribute input preparation"
    if isinstance(operation, AttributeFrameValues):
        return f"attribute(parent_how={operation.how!r}, contribution=False)"
    assert not isinstance(operation, AttributeProjectValues)
    statistics = tuple(
        (
            name,
            statistic.method,
            (_value_summary(statistic.left, source), _value_summary(statistic.right, source))
            if isinstance(statistic, PairStatistic)
            else None
            if statistic.value is None
            else _value_summary(statistic.value, source),
        )
        for name, statistic in operation.values
    )
    axes = tuple(source._schema.axes[position].token for position in operation.positions)
    return f"reduce(values={statistics!r}, by={axes!r})"
