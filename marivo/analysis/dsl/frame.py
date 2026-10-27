"""Immutable current Frame structures, lazy operations and fixed-data lifecycles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from json import dumps
from typing import TYPE_CHECKING, Literal, TypeAlias, overload
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
from marivo.analysis.core.frame_audit import (
    FrameResultMetadata,
    MemoryInput,
    SourceInput,
    StoredInput,
)
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    FrameExpressionRoot,
    LiteralFrameBinding,
    MemoryFrameBacking,
    OperationFrameBinding,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_derivation import (
    FrameDerivation,
    complete_declaration,
    derive_declaration,
)
from marivo.analysis.core.frame_expression import (
    AlignFrameValues,
    AttributeContributionValues,
    AttributeInputsValues,
    AttributeProjectValues,
    CompareFrameValues,
    FrameUnaryOperationKind,
    NamedFrameComparison,
    infer_frame_combine_schema,
    infer_frame_compare_schema,
    infer_frame_schema,
)
from marivo.analysis.core.frame_fix import FixFrameValues
from marivo.analysis.core.frame_method_facts import FrameMethodFacts
from marivo.analysis.core.frame_structure import (
    PendingDataSchema,
    axis_role,
    column_kind,
    require_complete_structure,
)
from marivo.analysis.core.model import reject
from marivo.analysis.dsl.baseline import ComparisonBaseline
from marivo.analysis.dsl.baseline import baseline as declare_baseline
from marivo.analysis.dsl.column import Column, _Statistic
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.evidence._dataset_types import ArtifactDigest, FindingPage
from marivo.analysis.materialization.frame_services import LocalFrameServices

FrameBacking: TypeAlias = ExpressionFrameBacking | MemoryFrameBacking | StoredFrameBacking
FrameSchema: TypeAlias = DataSchema | PendingDataSchema

if TYPE_CHECKING:
    from ibis.common.deferred import Deferred

    from marivo._grouping import Grouping
    from marivo.analysis.dsl.population import Population


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
    _derivation: FrameDerivation | None

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
        return cls._bind(
            local,
            schema,
            ExpressionFrameBacking(root),
            "frame_" + uuid4().hex,
            derive_declaration(schema, root),
        )

    @classmethod
    def _from_memory(
        cls,
        local: LocalFrameServices,
        result: CompletedFrameResult,
        derivation: FrameDerivation | None = None,
    ) -> AnalysisFrame:
        backing = MemoryFrameBacking(result)
        return cls._bind(local, result.data.schema, backing, "frame_" + uuid4().hex, derivation)

    @classmethod
    def _from_stored(
        cls,
        local: LocalFrameServices,
        data: ArtifactData,
        *,
        artifact_id: str,
        metadata: FrameResultMetadata,
        method_facts: tuple[SavedFrameMethodFacts, ...] = (),
        derivation: FrameDerivation | None = None,
    ) -> AnalysisFrame:
        backing = StoredFrameBacking(artifact_id, data, metadata, method_facts)
        identity = dumps(
            (local.store.store_id, local.session_ref, artifact_id), separators=(",", ":")
        )
        input_id = "stored_" + sha256(identity.encode()).hexdigest()
        return cls._bind(local, data.schema, backing, input_id, derivation)

    @classmethod
    def _bind(
        cls,
        local: LocalFrameServices,
        schema: FrameSchema,
        backing: FrameBacking,
        input_id: str,
        derivation: FrameDerivation | None = None,
    ) -> AnalysisFrame:
        result = object.__new__(cls)
        object.__setattr__(result, "_local", local)
        object.__setattr__(result, "_input_id", input_id)
        object.__setattr__(result, "_schema", schema)
        object.__setattr__(result, "_backing", backing)
        object.__setattr__(result, "_derivation", derivation)
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
                canonical_id="analysis_frame.axis",
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
            canonical_id="analysis_frame.__getitem__",
        )

    def _axis_index(
        self,
        axis: FrameAxis,
        *,
        location: str = "frame.axis",
        canonical_id: str | None = None,
    ) -> int:
        if not isinstance(axis, FrameAxis) or (
            axis._input_id != self._input_id or axis._frame._local.owner != self._local.owner
        ):
            reject(
                "an axis from this exact Frame input",
                repr(axis),
                "Acquire the axis from this Frame's axes.",
                location,
                canonical_id=canonical_id,
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
            location,
            canonical_id=canonical_id,
        )

    def coord(self, axis: FrameAxis) -> Column:
        """Return a coordinate Column for one complete single-component axis.

        Args: axis: A FrameAxis from this exact input.
        Returns: A bound coordinate Column.
        Example: ``key = frame.coord(frame.axes[0])``.
        Constraints: Composite axes remain legal by/on inputs but cannot be split by coord.
        """
        index = self._axis_index(axis, location="frame.coord", canonical_id="analysis_frame.coord")
        columns = self._schema.axes[index].columns
        if len(columns) != 1:
            reject(
                "a single physical component coordinate",
                repr(axis.key_names),
                "Use the complete axis in by/on; do not split a composite identity.",
                "frame.coord",
                canonical_id="analysis_frame.coord",
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

    def _require_column(
        self,
        column: Column,
        *,
        location: str = "frame.column",
        canonical_id: str | None = None,
    ) -> None:
        if not isinstance(column, Column) or (
            column._input_id != self._input_id
            or column._frame is None
            or column._frame._local.owner != self._local.owner
        ):
            reject(
                "a Column from this exact Store/Session/Frame input",
                repr(column),
                "Locate the Column on this Frame, or align the two Frames first.",
                location,
                canonical_id=canonical_id,
            )

    def _bind_statistic(
        self,
        statistic: _Statistic,
        *,
        location: str = "frame.reduce",
        canonical_id: str | None = None,
    ) -> _Statistic:
        if not isinstance(statistic, _Statistic):
            reject(
                "an unfinished Column statistic or row_count()",
                type(statistic).__name__,
                "Construct the statistic from this Frame.",
                location,
                canonical_id=canonical_id,
            )
        if (
            statistic._input_id is None
            and isinstance(statistic._definition, Statistic)
            and statistic._definition.method == "row_count"
            and statistic._definition.value is None
        ):
            return _Statistic(self._input_id, statistic._definition, statistic.unit, self)
        if (
            statistic._input_id != self._input_id
            or statistic._frame is None
            or statistic._frame._local.owner != self._local.owner
        ):
            reject(
                "a statistic from this exact Frame input",
                repr(statistic._input_id),
                "Reacquire the Column on this Frame before constructing the statistic.",
                location,
                canonical_id=canonical_id,
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
        self._require_column(predicate, location="frame.where", canonical_id="analysis_frame.where")
        if predicate._kind != "boolean":
            reject(
                "a boolean Column",
                predicate._kind,
                "Compare current values to construct a predicate.",
                "frame.where",
                canonical_id="analysis_frame.where",
            )
        return self._derive(FilterValues(predicate._expression))

    def fix(self, axis: FrameAxis, key: LiteralValue | tuple[LiteralValue, ...]) -> AnalysisFrame:
        """Fix one complete ordinary coordinate and remove its axis without aggregation.

        Args:
            axis: A field or identity axis from this exact Frame.
            key: A scalar for one component or a complete tuple in key_names order.
        Returns: A lazy Frame preserving selected values, presence and relative row order.
        Example: ``bi1 = frame.fix(frame.axis('cluster'), 'bi1')``.
        Constraints: None selects a nullable NULL key. Absent members yield an empty Frame,
        including after removing the final axis. Time and paired axes cannot be fixed.
        Remaining axes use the documented default pairing or a complete explicit on.
        """
        position = self._axis_index(axis, location="frame.fix", canonical_id="analysis_frame.fix")
        single = len(self._schema.axes[position].columns) == 1
        if single == isinstance(key, tuple):
            reject(
                "a scalar key" if single else "a complete tuple key",
                repr(key),
                "Use axis.key_names to supply every component in order.",
                "frame.fix",
                canonical_id="analysis_frame.fix",
            )
        keys = key if isinstance(key, tuple) else (key,)
        return self._derive(FixFrameValues(position, keys))

    def select(self, values: Mapping[str, Column | LiteralValue]) -> AnalysisFrame:
        """Name current Columns or non-null supported literals, preserving the row domain.

        Args: values: Ordered mapping of user name segments to bound Columns/literals; may be empty.
        Returns: A lazy Frame retaining axes and all presence, with only the selected values.
        Example: ``selected = frame.select({'change': frame['reference.absolute.revenue']})``.
        Constraints: An empty mapping removes all values while retaining rows, axes and presence.
        Does not recover removed values or endpoint inputs; no source reads during capture.
        """
        if not isinstance(values, Mapping):
            reject(
                "a named value mapping (possibly empty)",
                type(values).__name__,
                "Pass select({'name': column}) or select({}) to retain only coordinates and presence.",
                "frame.select",
                canonical_id="analysis_frame.select",
            )
        expressions: list[tuple[str, ValueExpression]] = []
        from marivo.analysis.core.frame_structure import value_name

        for name, value in values.items():
            value_name(name, location="frame.select", canonical_id="analysis_frame.select")
            if isinstance(value, Column):
                self._require_column(
                    value, location="frame.select", canonical_id="analysis_frame.select"
                )
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
                        canonical_id="analysis_frame.select",
                    )
                if literal is None:
                    reject(
                        "a non-null literal",
                        "None",
                        "Select a nullable Column or derive NA with a typed operation.",
                        "frame.select",
                        canonical_id="analysis_frame.select",
                    )
                if isinstance(literal, datetime):
                    if literal.utcoffset() is None:
                        reject(
                            "an aware timestamp literal",
                            repr(literal),
                            "Use an aware datetime or a date value.",
                            "frame.select",
                            canonical_id="analysis_frame.select",
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
                canonical_id="analysis_frame.top",
            )
        self._require_column(by, location="frame.top", canonical_id="analysis_frame.top")
        return self._derive(TopValues(n, by._expression, descending))

    def reduce(
        self,
        values: _Statistic | tuple[_Statistic, ...] | Mapping[str, _Statistic],
        *,
        by: tuple[FrameAxis, ...] = (),
    ) -> AnalysisFrame:
        """Aggregate current rows by retained complete axes, removing all presence.

        Args:
            values: One statistic, a nonempty ordered tuple, or a named statistic mapping.
            by: Tuple of complete same-input FrameAxis handles, default ().

        Returns:
            A lazy current-row aggregate AnalysisFrame.

        Example:
            ``summary = frame.reduce((frame['revenue'].sum(), mv.row_count()))``.

        Constraints: Each named statistic produces one column with its independent
        NA mask. Use correlate for a coefficient and its matching pair count;
        corr/pair_count compose with sum/mean/row_count without inferred pairing.
        Sum/mean retain the current expression's unit without certifying original Metric
        additivity or rollup over removed axes. No original Metric regrouping or calendar coarsening.
        Automatic names are <value>_sum, <value>_mean for direct single-segment value
        paths, and row_count. Expressions, pair statistics and generated paths require
        an explicit Mapping. Duplicate automatic names require distinct explicit names.
        """
        if isinstance(values, Mapping) and values:
            named = True
            items = tuple(values.items())
        elif isinstance(values, _Statistic):
            named = False
            items = (("", values),)
        elif type(values) is tuple and values:
            named = False
            items = tuple(("", statistic) for statistic in values)
        else:
            reject(
                "one statistic, a nonempty statistic tuple, or a nonempty named mapping",
                type(values).__name__,
                "Pass reduce(row_count()) or reduce({'your_name': statistic}).",
                "frame.reduce",
                canonical_id="analysis_frame.reduce",
            )
        if type(by) is not tuple:
            reject(
                "a tuple of complete current axes",
                type(by).__name__,
                "Use by=() for a scalar result or a tuple of this Frame's complete axes.",
                "frame.reduce",
                canonical_id="analysis_frame.reduce",
            )
        positions = tuple(
            self._axis_index(axis, location="frame.reduce", canonical_id="analysis_frame.reduce")
            for axis in by
        )
        if len(set(positions)) != len(positions):
            reject(
                "distinct complete current axes",
                repr(positions),
                "Use each complete axis once.",
                "frame.reduce",
                canonical_id="analysis_frame.reduce",
            )
        from marivo.analysis.core.frame_structure import value_name

        statistics: list[tuple[str, Statistic | PairStatistic]] = []
        candidates: dict[str, int] = {}
        for position, (name, value) in enumerate(items, 1):
            if named:
                value_name(name, location="frame.reduce", canonical_id="analysis_frame.reduce")
            bound = self._bind_statistic(
                value, location="frame.reduce", canonical_id="analysis_frame.reduce"
            )
            if not named:
                definition = bound._definition
                if isinstance(definition, Statistic) and definition.method == "row_count":
                    name = "row_count"
                elif isinstance(definition, Statistic) and isinstance(definition.value, ValueSlot):
                    path = self._schema.value_paths[definition.value.index]
                    if len(path) != 1:
                        reject(
                            "a direct single-segment value path for an automatic statistic name",
                            f"input {position}: {'.'.join(path)!r}",
                            "Give this multi-segment path a single-segment output name with "
                            "reduce({'your_name': statistic}).",
                            "frame.reduce",
                            canonical_id="analysis_frame.reduce",
                        )
                    name = f"{path[0]}_{definition.method}"
                else:
                    reject(
                        "row_count() or sum/mean of a direct single-segment value Column",
                        f"input {position}: {type(definition).__name__}",
                        "Give this statistic an explicit name with "
                        "reduce({'your_name': statistic}).",
                        "frame.reduce",
                        canonical_id="analysis_frame.reduce",
                    )
                value_name(name, location="frame.reduce", canonical_id="analysis_frame.reduce")
            if name in candidates:
                reject(
                    "distinct statistic output names",
                    f"{name!r} at input positions {candidates[name]} and {position}",
                    "Give each statistic a distinct explicit name with reduce({'your_name': statistic}).",
                    "frame.reduce",
                    canonical_id="analysis_frame.reduce",
                )
            candidates[name] = position
            statistics.append((name, bound._definition))
        return self._derive(ReduceValues(tuple(statistics), positions))

    def attribute(self, *, by: tuple[Deferred | Grouping, ...] | Population) -> AnalysisFrame:
        """Explain selected source targets with contributions on new business axes.

        Args:
            by: Nonempty tuple of new semantic classifications, or one complete
                Population retaining its correlated keys and allocation rules.

        Returns:
            A lazy AnalysisFrame with original parent axes, new fine axes,
            contributions, fresh totals, observed totals, shares and fine diagnostics.

        Example:
            ``report = selected.attribute(by=(channel,)).compute()``.

        Constraints: Requires retained process-local source declarations for every
        direct Metric or admitted comparison target. Select and rename direct outputs;
        define ratio/linear Metrics before observation. Fixed division retains its typed
        divisor and division order; Metric rescale also declares the result unit.
        Pending components expose their own units. Completion freezes parent keys
        and old values; parent and fine inputs are read independently without reranking
        or a shared-snapshot guarantee. Semantic identity, cardinality and version-grain
        declarations are trusted, including non-null primary keys even when physical
        metadata marks them nullable. No full-source NULL or uniqueness precheck is
        added; declaration violations are not guaranteed to fail. Existing identity
        output checks remain. Operation-specific membership/Population coverage,
        overlap, distinct-value partitions, numeric validity and complete time
        correspondence remain checked. Float64 method facts preserve captured values
        without arithmetic recomputation. Only requested final targets must close within
        their closure policy. Float64 allows the greater of 1% of abs(total) and the
        numeric rounding budget; zero totals retain only that budget. Integer/count,
        Decimal and Duration policies are unchanged. Qualified physical tables use the native backend
        subject to its method, type and source restrictions. Independent
        reopen has no declaration; rebuild explicit source inputs using saved joint keys.
        """
        from marivo.analysis.binding.frame_attribute import attribute

        return attribute(self, by=by)

    def over(self, statistic: _Statistic, *, by: tuple[FrameAxis, ...] = ()) -> Column:
        """Reduce each current partition and broadcast its statistic to the same rows.

        Args:
            statistic: A same-input sum, mean, corr, pair_count, or unbound row_count.
            by: Distinct complete axes retained as partition keys; () uses all rows.
        Returns: A lazy Column bound to this exact Frame, retaining row order.
        Example: ``frame.select({'total': frame.over(frame['revenue'].sum())})``.
        Constraints: Uses current data and reduce's numeric/sample rules. Empty input
        stays empty. Nested statistics count repeated broadcast values. Filtering after
        over does not change its partition; no source regrouping or calendar matching.
        """
        from marivo._definition.query import ValuePartition

        if type(by) is not tuple:
            reject(
                "a tuple of complete current axes",
                type(by).__name__,
                "Pass over(statistic, by=(frame.axes[0],)).",
                "frame.over",
                canonical_id="analysis_frame.over",
            )
        requested = tuple(
            self._axis_index(axis, location="frame.over", canonical_id="analysis_frame.over")
            for axis in by
        )
        if len(set(requested)) != len(requested):
            reject(
                "distinct complete current axes",
                repr(requested),
                "Use each complete axis once.",
                "frame.over",
                canonical_id="analysis_frame.over",
            )
        positions = tuple(index for index in range(len(self.axes)) if index in requested)
        bound = self._bind_statistic(
            statistic, location="frame.over", canonical_id="analysis_frame.over"
        )
        schema = infer_frame_schema(
            self._schema, ReduceValues((("statistic", bound._definition),), positions)
        )
        return Column(
            self._input_id,
            ValuePartition(bound._definition, positions),
            bound.unit,
            column_kind(schema.values[0]),
            self,
        )

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
                canonical_id="analysis_frame.correlate",
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
                    canonical_id="analysis_frame.correlate",
                )
        if type(by) is not tuple:
            reject(
                "a tuple of complete same-input FrameAxis handles",
                repr(by),
                "Use by=() or by=(frame.axes[0],).",
                "frame.correlate",
                canonical_id="analysis_frame.correlate",
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
                    canonical_id="analysis_frame.correlate",
                )
        positions = tuple(
            self._axis_index(
                axis, location="frame.correlate", canonical_id="analysis_frame.correlate"
            )
            for axis in by
        )
        if len(set(positions)) != len(positions):
            reject(
                "distinct complete current axes",
                repr(positions),
                "Use each complete axis once.",
                "frame.correlate",
                canonical_id="analysis_frame.correlate",
            )
        coefficient = x._pair_statistic(
            y, method, location="frame.correlate", canonical_id="analysis_frame.correlate"
        )
        count = x._pair_statistic(
            y, "pair_count", location="frame.correlate", canonical_id="analysis_frame.correlate"
        )
        return self._derive(
            ReduceValues(
                (("coefficient", coefficient._definition), ("n_pairs", count._definition)),
                positions,
            )
        )

    def _axis_pairs(
        self,
        other: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None,
        match: CalendarCorrespondence | None,
        location: str,
        canonical_id: str,
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
                "a tuple of complete FrameAxis pairs and an optional CalendarCorrespondence",
                repr((on, match)),
                "Pass on as a tuple pairing every complete axis exactly once; pass match as a CalendarCorrespondence or omit it.",
                location,
                canonical_id=canonical_id,
            )
        if not isinstance(other, AnalysisFrame) or other._local.owner != self._local.owner:
            reject(
                "Frame inputs from the same Store and Session",
                repr(other),
                "Use two inputs owned by the same Session.",
                location,
                canonical_id=canonical_id,
            )
        left_axes, right_axes = self.axes, other.axes
        if any(axis.role == "paired" for axis in (*left_axes, *right_axes)):
            reject(
                "inputs without paired axes",
                repr((left_axes, right_axes)),
                "Reduce away paired axes or construct new source inputs.",
                location,
                canonical_id=canonical_id,
            )
        if len(left_axes) != len(right_axes):
            reject(
                "equal complete axis counts",
                repr((len(left_axes), len(right_axes))),
                "Reduce or reconstruct inputs with compatible axes.",
                location,
                canonical_id=canonical_id,
            )
        if on is None:
            time_roles = ("calendar", "interval")
            left_time = tuple(axis for axis in left_axes if axis.role in time_roles)
            right_time = tuple(axis for axis in right_axes if axis.role in time_roles)
            if location == "frame.compare" and (len(left_time) > 1 or len(right_time) > 1):
                reject(
                    "at most one compatible time axis pair",
                    f"current={len(left_time)}, baseline={len(right_time)}",
                    "Reduce or reconstruct each input with at most one time axis before compare.",
                    location,
                    canonical_id=canonical_id,
                )
            left_ordinary = tuple(axis for axis in left_axes if axis.role not in time_roles)
            right_ordinary = tuple(axis for axis in right_axes if axis.role not in time_roles)
            pairs: list[tuple[FrameAxis, FrameAxis]] = []
            valid = (
                len(left_time) == len(right_time)
                and len(left_time) <= 1
                and (not left_time or left_time[0].role == right_time[0].role)
            )
            for axis in left_axes:
                if axis.role in time_roles:
                    if valid:
                        pairs.append((axis, right_time[0]))
                    continue
                left_matches = tuple(
                    candidate for candidate in left_ordinary if candidate.name == axis.name
                )
                right_matches = tuple(
                    candidate for candidate in right_ordinary if candidate.name == axis.name
                )
                if (
                    not axis.name
                    or len(left_matches) != 1
                    or len(right_matches) != 1
                    or axis.role != right_matches[0].role
                    or axis.key_names != right_matches[0].key_names
                ):
                    valid = False
                else:
                    pairs.append((axis, right_matches[0]))
            if not valid or len(pairs) != len(left_axes):
                left_description = tuple((a.name, a.role, a.key_names) for a in left_axes)
                right_description = tuple((a.name, a.role, a.key_names) for a in right_axes)
                reject(
                    "a complete explicit axis bijection",
                    f"current={left_description!r}, other={right_description!r}",
                    "Pass on with every complete axis exactly once. Automatic pairing requires unique same-name ordinary axes with matching roles and complete key names, and at most one same-kind time axis per side.",
                    location,
                    canonical_id=canonical_id,
                )
            on = tuple(pairs)
        if (
            len(on) != len(left_axes)
            or {a.token for a, _ in on} != {a.token for a in left_axes}
            or {b.token for _, b in on} != {b.token for b in right_axes}
        ):
            reject(
                "a complete explicit axis bijection",
                repr(on),
                f"Bind each complete axis once from {left_axes!r} to {right_axes!r}.",
                location,
                canonical_id=canonical_id,
            )
        return tuple(
            (
                self._axis_index(a, location=location, canonical_id=canonical_id),
                other._axis_index(b, location=location, canonical_id=canonical_id),
            )
            for a, b in on
        )

    def align(
        self,
        other: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None = None,
        match: CalendarCorrespondence | None = None,
    ) -> AnalysisFrame:
        """Align all current values by complete axes, adding independent row presence.

        Args:
            other: Same-Session Frame.
            on: Complete FrameAxis-pair bijection or None.
            match: Optional closed calendar rule.

        Returns:
            A lazy Frame containing all left/right values and presence.

        Example:
            ``paired = a.align(b, on=((a.axes[0], b.axes[0]),))``.

        Constraints: Omitted on pairs unique same-name ordinary axes with equal roles
        and key names, and at most one same-kind calendar/interval axis per side.
        This declares caller business correspondence; validation
        does not check original Entity/Field equality. Paired inputs reject. No Catalog or row reads.
        """
        pairs = self._axis_pairs(
            other,
            on=on,
            match=match,
            location="frame.align",
            canonical_id="analysis_frame.align",
        )
        operation = AlignFrameValues(pairs, match)
        schema = infer_frame_combine_schema(self._schema, other._schema, operation)
        return self._from_expression(
            self._local, schema, OperationFrameBinding(operation, (self, other))
        )

    @overload
    def compare(
        self,
        baselines: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None = None,
        match: CalendarCorrespondence | None = None,
    ) -> AnalysisFrame: ...

    @overload
    def compare(self, baselines: Mapping[str, ComparisonBaseline]) -> AnalysisFrame: ...

    def compare(
        self,
        baselines: AnalysisFrame | Mapping[str, ComparisonBaseline],
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None = None,
        match: CalendarCorrespondence | None = None,
    ) -> AnalysisFrame:
        """Compare current values with one Frame or named baseline declarations.

        Args:
            baselines: One same-Session Frame, or a nonempty ordered mapping of
                mv.baseline(...) declarations. A bare Frame uses alias reference;
                current is reserved.
            on: Complete axis-pair tuple for a bare Frame only. For named inputs,
                put on inside mv.baseline(...). Omission pairs unique same-name
                ordinary axes with equal roles and ordered key names, plus at most
                one same-kind time axis per side.
            match: Closed time rule for a bare Frame only. For named inputs, put
                match inside mv.baseline(...). Must agree with a frozen direct relation.

        Returns:
            A lazy ordinary Frame with current values, named baseline/difference
            groups and presence.

        Example:
            ``change = current.compare({'prior': mv.baseline(previous, match=mv.ordinal())})``.

        Constraints: Equal nonempty value paths with compatible numeric or matching-tick
        Duration endpoints and matching units; no paired inputs. Relative change is
        (current-baseline)/abs(baseline). Missing rows and NA propagate; zero baselines
        return NA. Outer correspondence preserves missing-side rows. Ordinary-axis on
        declares caller business correspondence; omitting on accepts that same-name
        convention. Neither checks original Entity/Field equality. Equal value names select pairs, not Metric identity or business
        comparability. No source replay or date inference from alias names. Select,
        name and prepare inputs before compare. The reference alias retains full
        reference.baseline/absolute/relative paths.
        """
        from marivo.analysis.core.frame_structure import validate_paths

        declarations: tuple[tuple[str, ComparisonBaseline], ...]
        if isinstance(baselines, AnalysisFrame):
            declarations = (("reference", declare_baseline(baselines, on=on, match=match)),)
        elif isinstance(baselines, Mapping) and baselines:
            if on is not None or match is not None:
                reject(
                    "correspondence rules inside each named baseline declaration",
                    repr((on, match)),
                    'Use compare({"alias": mv.baseline(frame, on=..., match=...)}).',
                    "frame.compare",
                    canonical_id="analysis_frame.compare",
                )
            if any(type(value) is not ComparisonBaseline for value in baselines.values()):
                reject(
                    "a ComparisonBaseline declaration for every named baseline",
                    repr(tuple(type(value).__name__ for value in baselines.values())),
                    'Use compare({"alias": mv.baseline(frame, on=..., match=...)}).',
                    "frame.compare",
                    canonical_id="analysis_frame.compare",
                )
            declarations = tuple(baselines.items())
        else:
            reject(
                "one baseline Frame or a nonempty named ComparisonBaseline mapping",
                type(baselines).__name__,
                'Use compare(frame), or compare({"alias": mv.baseline(frame)}).',
                "frame.compare",
                canonical_id="analysis_frame.compare",
            )
        names = tuple(name for name, _ in declarations)
        validate_paths(
            tuple((name,) for name in names),
            location="frame.compare.aliases",
            canonical_id="analysis_frame.compare",
        )
        if "current" in names:
            reject(
                "baseline aliases other than current",
                repr(names),
                "Rename the baseline alias.",
                "frame.compare",
                canonical_id="analysis_frame.compare",
            )
        bindings = tuple(
            NamedFrameComparison(
                name,
                self._axis_pairs(
                    declaration.frame,
                    on=declaration.on,
                    match=declaration.match,
                    location="frame.compare",
                    canonical_id="analysis_frame.compare",
                ),
                declaration.match,
            )
            for name, declaration in declarations
        )
        operation = CompareFrameValues(bindings)
        inputs = (self, *(declaration.frame for _, declaration in declarations))
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
        Artifact or result files. Stored loading preserves result facts. Retain the returned
        Frame in this Python process. If another process needs these values, persist and
        output/save Session and Artifact IDs before exit. One-shot work needs no save.
        Source UInt64 uses exact Decimal(20,0) before semantic arithmetic. Source and
        local kernels retain native numeric behavior without extra overflow checks,
        finite scans or protective widening. Exact Decimal transport must avoid float. Naive
        timestamp identity preserves wall-clock values and precision independently
        of TimeDimension parser and Session timezones.
        """
        if isinstance(self._backing, MemoryFrameBacking):
            return self
        if isinstance(self._backing, StoredFrameBacking):
            return self._from_memory(
                self._local, self._local.read_complete(self._backing), self._derivation
            )
        from marivo.analysis.materialization.frame_evaluation import evaluate_frame

        keys: dict[str, CompletedFrameData] = {}
        needs_key_checkpoint = self._derivation is not None and any(
            output.target.current.binding.frame_key_inputs
            or (
                output.target.baseline is not None
                and output.target.baseline.binding.frame_key_inputs
            )
            for output in self._derivation.outputs
        )
        result = evaluate_frame(self, declaration_keys=keys if needs_key_checkpoint else None)
        return self._from_memory(
            self._local, result, complete_declaration(self._derivation, keys, result.metadata)
        )

    @property
    def method_facts(self) -> tuple[FrameMethodFacts, ...]:
        """Read the complete immutable facts explaining original decompositions.

        Returns: An ordered tuple of self-contained FrameMethodFacts.
        Example: ``facts = fixed.method_facts``.
        Read static type details with marivo.help("analysis.frame_method_facts").
        Constraints: Pending expressions require compute/persist. Stored reads validate
        all owned method payloads, without reading current data or loading sources/models.
        Float64 values are captured facts without arithmetic recomputation; structural,
        type, unit, finite-value and storage-integrity checks remain. Non-floating
        arithmetic retains exact validation. Inherited facts explain original result
        identities, not derived values.
        """
        if isinstance(self._backing, ExpressionFrameBacking):
            reject(
                "an evaluated memory or stored Frame",
                "pending expression",
                "Call compute() or persist() before reading method_facts.",
                "frame.method_facts",
                canonical_id="analysis_frame.method_facts",
            )
        if isinstance(self._backing, MemoryFrameBacking):
            return self._backing.method_facts
        return self._local.read_method_facts(self._backing)

    def persist(self) -> AnalysisFrame:
        """Publish a completed result; exact stored roots return themselves.

        Args: None.
        Returns: A stored AnalysisFrame owned by the same original local service.
        Example: ``saved = frame.persist()``;
        ``print({'session_id': saved.session_id, 'artifact_id': saved.artifact_id})``.
        Constraints: Pending evaluation completes before publication admission;
        memory publication does not reevaluate, and stored idempotence reads metadata only.
        For another process, output/save the returned Session and Artifact IDs before exit;
        no show call is required. An existing saved reference needs no new publication.
        Lost return references require explicit
        resubmission, which may duplicate saved outputs or read changed sources.
        """
        if isinstance(self._backing, StoredFrameBacking):
            selected = self._local.open_frame(self._backing.artifact_id)
            if selected != self._backing:
                reject(
                    "the exact original committed descriptor",
                    self._backing.artifact_id,
                    "Preserve the selected Artifact metadata.",
                    "frame.persist",
                    canonical_id="analysis_frame.persist",
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
            derivation=completed._derivation,
        )

    @property
    def session_id(self) -> str:
        """Return this Frame's captured owning Session ID.

        Args: None.
        Returns: The exact Session ID for every Frame carrier.
        Example: ``session = mv.session.resume(frame.session_id)``.
        Constraints: Read-only metadata; no Store, result or source reads.
        """
        return self._local.session_ref

    def _require_stored(self, member: str) -> StoredFrameBacking:
        if not isinstance(self._backing, StoredFrameBacking):
            reject(
                "an exact stored Frame root",
                type(self._backing).__name__,
                "Call persist() and use the returned Frame.",
                f"frame.{member}",
                canonical_id=f"analysis_frame.{member}",
            )
        return self._backing

    @property
    def artifact_id(self) -> str:
        """Return the captured identity of this exact Frame commit.

        Args: None.
        Returns: The saved ID accepted by session.persisted_frame(id).
        Example: ``reopened = session.persisted_frame(saved.artifact_id)``.
        Constraints: Only stored roots have a commit ID. Reads metadata without Store,
        payload or source access; derived and computed memory Frames require persist.
        """
        return self._require_stored("artifact_id").artifact_id

    def findings(self, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read a bounded page of this committed Frame's method Findings.

        Args: limit: Page size from 1 through 100. cursor: Previous page cursor or None.
        Returns: The commit-scoped FindingPage with immutable items and next_cursor.
        Example: ``page = saved.findings(limit=20)``.
        Constraints: Requires a stored root. Reads audit records without result rows,
        coordinate attachments, method payloads, semantic definitions or sources.
        The cursor belongs to this exact commit and selection.
        """
        backing = self._require_stored("findings")
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (cursor is not None and type(cursor) is not str)
        ):
            reject(
                "limit 1..100 and a cursor string or None",
                repr((limit, cursor)),
                "Use the preceding page cursor with the same limit.",
                "frame.findings",
                canonical_id="analysis_frame.findings",
            )
        return self._local.findings(backing, limit=limit, cursor=cursor)

    def evidence_digest(self) -> ArtifactDigest:
        """Read all committed Evidence facts and collection digests for this Frame.

        Args: None.
        Returns: ArtifactDigest containing every saved Evidence fact in output order.
        Example: ``digest = saved.evidence_digest()``.
        Constraints: Requires a stored root. No result rows, coordinate attachments,
        method payloads, current semantic checks or source-freshness claims.
        """
        return self._local.evidence_digest(self._require_stored("evidence_digest"))

    def to_pandas(self, *, axis_names: Mapping[FrameAxis, str] | None = None) -> pd.DataFrame:
        """Export complete fixed data as a readable, isolated flat table.

        Args:
            axis_names: Optional names for axes from this exact Frame input. Names
                must be nonempty printable strings without surrounding whitespace.
                Defaults to the complete captured axis names.

        Returns:
            A pandas DataFrame with string columns: readable coordinates, full
            output names and independent boolean presence. Composite identities
            remain tuples. Calendar coordinates include labels, actual_start,
            actual_end and timezone; intervals include start/end/timezone. Paired
            coordinates use left/right prefixes. Exact bounds are UTC timestamps.
            attrs retain isolated axes, units, comparisons and frozen_coordinates.

        Example:
            ``fixed = frame.compute(); rows = fixed.to_pandas()``;
            ``amounts = rows["amount"]``. Rename an axis with
            ``fixed.to_pandas(axis_names={fixed.axes[0]: "region"})``.

        Constraints:
            Pending expressions require compute/persist, including derivatives of
            fixed data; use the returned Frame. Column names must be unique; resolve
            collisions with axis_names or select(). Saved reads fully validate current
            data without Catalog, source or method-payload reads. Value types, NA,
            presence and row order are preserved; no float coercion or zero filling.
            Exported data and attrs grant no typed computation authority or stable
            deserialization protocol. Continue typed analysis from the Frame.
            Retain that Frame for typed operations; a DataFrame does not retain it.
            For another process, persist and retain exact IDs before the producer exits.
        """
        from marivo.analysis.materialization.pandas_export import export_frame, export_names

        if isinstance(self._backing, ExpressionFrameBacking):
            reject(
                "a complete memory or stored Frame",
                "expression",
                "Call compute() or persist() before exporting.",
                "frame.to_pandas",
                canonical_id="analysis_frame.to_pandas",
            )
        if axis_names is not None and not isinstance(axis_names, Mapping):
            reject(
                "a mapping from current Frame axes to export names",
                type(axis_names).__name__,
                "Pass axis_names={frame.axes[0]: 'name'} or omit axis_names.",
                "frame.to_pandas",
                canonical_id="analysis_frame.to_pandas",
            )
        names = [axis.name for axis in self.axes]
        for axis, name in (axis_names or {}).items():
            index = self._axis_index(
                axis, location="frame.to_pandas", canonical_id="analysis_frame.to_pandas"
            )
            if type(name) is not str or not name or name != name.strip() or not name.isprintable():
                reject(
                    "a nonempty printable export name without surrounding whitespace",
                    repr(name),
                    "Assign a distinct readable name to each renamed axis.",
                    "frame.to_pandas",
                    canonical_id="analysis_frame.to_pandas",
                )
            names[index] = name
        assert isinstance(self._schema, DataSchema)
        export_names(self._schema, tuple(names))
        data = (
            self._local.read_data(self._backing)
            if isinstance(self._backing, StoredFrameBacking)
            else self._backing.data
        )
        return export_frame(
            data.schema, data.copy_rows(), data.copy_attachments(), axis_names=tuple(names)
        )

    def show(self, *, n: int = 20, max_output_bytes: int | None = 8192) -> None:
        """Display current metadata and a bounded fixed-data prefix without evaluation.

        Args:
            n: Nonnegative integer excluding bool, default 20; zero is metadata only.
            max_output_bytes: Positive UTF-8 budget including the printed newline, or None.

        Returns:
            None; prints structure, bounded audit summaries and an available fixed-data preview.

        Example:
            ``saved.show(n=0)``.

        Constraints: Pending display never prepares schema; positive stored previews bound
        decoded output, not receipt hashing or Parquet IO. n=0 reads only metadata,
        including commit time for stored roots. Audit summaries never read complete Findings or Evidence. No publication, full-export/head, ancestor
        rows, complete-result cache or fallback after a read error. Method facts
        show only counts/origins and a .method_facts pointer; no method payload read.
        """
        if type(n) is not int or n < 0:
            reject(
                "a nonnegative integer preview count",
                repr(n),
                "Use n=0 for metadata only.",
                "frame.show",
                canonical_id="analysis_frame.show",
            )
        if max_output_bytes is not None and (
            type(max_output_bytes) is not int or max_output_bytes < 1
        ):
            raise ValueError("max_output_bytes must be a positive integer or None")
        from marivo.render import Card

        available = (
            (".compute()", ".persist()")
            if isinstance(self._backing, ExpressionFrameBacking)
            else (".to_pandas()", ".persist()")
            if isinstance(self._backing, MemoryFrameBacking)
            else (".to_pandas()",)
            + ((".findings(limit=20)",) if self._backing.metadata.findings else ())
            + (
                (".evidence_digest()",)
                if self._backing.metadata.audit.source_facts
                or self._backing.metadata.audit.evaluation.checks
                else ()
            )
        )
        card = Card(
            identity=repr(self),
            available=available,
            omission_recovery="pass max_output_bytes=None; use .output_names and .axes for structure; compute() for data"
            if isinstance(self._backing, ExpressionFrameBacking)
            else "pass max_output_bytes=None; use .output_names for names and .to_pandas() for complete fixed data",
        )
        card.field("values", repr(self.output_names)).field("presence", repr(self.presence_names))
        declaration = self._derivation
        if declaration is None:
            card.field(
                "attribute",
                "no retained source declaration; rebuild an explicit source observation with saved joint keys",
            )
        elif tuple(output.path for output in declaration.outputs) != self._schema.value_paths:
            card.field(
                "attribute",
                "some targets have unsupported producers; select direct Metric or comparison outputs",
            )
        else:
            card.field(
                "attribute",
                "source declaration retained; attribute(by=...) checks rules and reads fresh source facts",
            )
        card.field(
            "axes",
            repr(tuple((axis.token, axis.name, axis.role, axis.key_names) for axis in self.axes)),
        )
        if isinstance(self._backing, ExpressionFrameBacking):
            root = self._backing.root
            if isinstance(root, OperationFrameBinding):
                card.field("operation", _operation_summary(root))
                card.field(
                    "inputs",
                    repr(
                        tuple(
                            (child._input_id, _input_kind(child._backing)) for child in root.inputs
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
                    "; ".join(scopes) if scopes else "no captured filter or member restriction",
                )
                anchors = tuple(
                    (frame._input_id, axis.token, axis.at.isoformat())
                    for frame, _ in sources
                    for axis in frame._schema.axes
                    if axis.at is not None
                )
                if anchors:
                    card.field("anchors", repr(anchors))
            card.field("rows", "pending; no input rows read")
            card.field(
                "types",
                "pending source physical types"
                if isinstance(self._schema, PendingDataSchema)
                else "captured concrete data types",
            )
        else:
            from marivo.analysis.materialization.pandas_export import preview_table

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
                        canonical_id="analysis_frame.show",
                    )
                total = self._backing.data.rows.receipt.realized_row_count
            card.field("result_id", self._backing.metadata.result_id)
            facts = self._backing.method_facts
            card.field("method_facts", str(len(facts)))
            if facts:
                card.field("method_origins", repr(tuple(item.origin_result_id for item in facts)))
                card.field(
                    "method_facts_read", "use .method_facts for complete original explanation facts"
                )
            card.field("evaluated_at", self._backing.metadata.evaluated_at.isoformat())
            if isinstance(self._backing, StoredFrameBacking):
                card.field("session_id", self.session_id)
                card.field("artifact_id", self.artifact_id)
                card.field("committed_at", self._local.committed_at(self._backing).isoformat())
            audit = self._backing.metadata.audit
            input_kinds = tuple(
                "stored"
                if isinstance(item, StoredInput)
                else "memory"
                if isinstance(item, MemoryInput)
                else "source"
                if isinstance(item, SourceInput)
                else "literal"
                for item in audit.evaluation.inputs
            )
            card.field("inputs", repr(input_kinds))
            card.field("operations", repr(audit.evaluation.operations))
            card.field("source_facts", str(len(audit.source_facts)))
            card.field("checks", str(len(audit.evaluation.checks)))
            card.field("findings", str(len(self._backing.metadata.findings)))
            card.field("rows", str(total))
            if n and total:
                count = min(n, total)
                if isinstance(self._backing, MemoryFrameBacking):
                    rows = self._backing.data.preview_rows(count)
                    attachments = self._backing.data.copy_attachments()
                else:
                    prefix = self._local.preview(self._backing, count)
                    rows, attachments = prefix.rows, prefix.attachments
                assert isinstance(self._schema, DataSchema)
                columns, display_rows = preview_table(self._schema, rows, attachments)
                if total > count:
                    card.field("row_limit", f"n={n}; use .show(n={total}) to request all rows")
                card.table(
                    columns,
                    display_rows,
                    row_count=total,
                    show_omission_counts=True,
                    report_rows=True,
                    bounded_row_limit=count,
                    recovery=f".show(n={total}, max_output_bytes=None)",
                )
            elif n:
                card.status("preview rows: total=0 displayed=0 omitted=0; empty result")
                card.field("preview", "empty result")
            else:
                card.status(f"preview rows: total={total} displayed=0 omitted={total}; n=0")
                card.field("preview", f"omitted by n=0; shown=0; omitted={total}")
        print(card.render(max_output_bytes=max_output_bytes, _include_newline=True))


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
        return (
            f"{value.operation}({_value_summary(value.left, source, depth + 1)}, "
            f"{_value_summary(value.right, source, depth + 1)})"
        )
    return type(value).__name__


def _operation_summary(binding: OperationFrameBinding) -> str:
    from marivo.analysis.core.frame_attribute_merge import AttributeMergeValues

    operation, source = binding.operation, binding.inputs[0]
    if isinstance(operation, AttributeMergeValues):
        return "attribute(report_merge)"
    if isinstance(operation, SelectValues):
        values = tuple((name, _value_summary(value, source)) for name, value in operation.values)
        return f"select(values={values!r})"
    if isinstance(operation, FixFrameValues):
        return f"fix(axis={source._schema.axes[operation.position].name!r}, key={operation.key!r})"
    if isinstance(operation, FilterValues):
        return f"where(predicate={_value_summary(operation.predicate, source)})"
    if isinstance(operation, TopValues):
        return (
            f"top(n={operation.n}, by={_value_summary(operation.score, source)}, "
            f"descending={operation.descending})"
        )
    if isinstance(operation, CompareFrameValues):
        return f"compare(baselines={operation.baselines!r})"
    if isinstance(operation, AlignFrameValues):
        match = f", match={operation.match!r}" if operation.match is not None else ""
        return f"align(on={operation.pairs!r}{match})"
    if isinstance(operation, AttributeContributionValues):
        target = operation.target
        return (
            f"attribute(target={target.path!r}, kind={target.mode!r})"
            if target is not None
            else "attribute(source_contribution)"
        )
    if isinstance(operation, AttributeInputsValues):
        return "private Attribute input preparation"
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
