"""Immutable current Frame structures, lazy operations and fixed-data lifecycles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pandas as pd
from typing_extensions import overload as _public_overload

from marivo._definition.correspondence import CalendarCorrespondence
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    PresenceSlot,
    TopValues,
    ValueSlot,
)
from marivo._definition.types import LiteralValue
from marivo.analysis.core.frame_data import (
    CompletedFrameResult,
    ExpressionFrameBacking,
    FrameBacking,
    FrameDerivation,
    FrameExpressionRoot,
    MemoryFrameBacking,
    OperationFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_expression import (
    infer_frame_combine_schema,
    infer_frame_compare_schema,
)
from marivo.analysis.core.frame_interpretation import capture_value_inputs
from marivo.analysis.core.frame_method_facts import FrameMethodFacts
from marivo.analysis.core.frame_operations import (
    AlignFrameValues,
    CompareFrameValues,
    FixFrameValues,
    FrameUnaryOperationKind,
    NamedFrameComparison,
)
from marivo.analysis.core.frame_value_analysis import infer_frame_schema
from marivo.analysis.dsl import frame_derivation
from marivo.analysis.dsl._frame_attribute_api import attribute
from marivo.analysis.dsl._frame_lifecycle import (
    capture_lineage,
    complete_frame,
    require_stored,
    stored_backing,
)
from marivo.analysis.dsl._frame_ops import (
    bind_axis_pairs,
    bind_correlation,
    bind_partition,
    bind_reduction,
    bind_selection,
    validate_pairing,
)
from marivo.analysis.dsl._frame_ownership import (
    axis_index,
    require_column,
    validate_expression_owner,
)
from marivo.analysis.dsl._frame_view import (
    export_axis_names,
    export_fixed_frame,
    read_fixed_page,
    show_frame,
    validate_time_details,
)
from marivo.analysis.dsl.baseline import (
    named_baselines,
    show_baseline,
    validate_baseline_aliases,
    validate_baseline_options,
)
from marivo.analysis.dsl.column import Column, _Statistic
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.materialization.carriers.frame_page import FramePage, FramePeriodPage
from marivo.analysis.materialization.frame_paging import read_frame_periods
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.pandas_export import export_names
from marivo.analysis.model.artifact_data import ArtifactData, SavedFrameMethodFacts
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_contracts import FrameIdentity
from marivo.analysis.model.frame_integrity import share_contexts, validate_current_facts
from marivo.analysis.model.frame_metadata import StoredFrameMetadata
from marivo.analysis.model.frame_schema import (
    FrameAxisSchema,
    FrameSchema,
    LogicalSchema,
    capture_schema,
)
from marivo.analysis.model.frame_source_reads import FrameSourceRead
from marivo.analysis.model.frame_structure import (
    axis_role,
    column_kind,
)
from marivo.analysis.model.layout_facts import FrameComparison
from marivo.analysis.model.lineage import FrameLineage, all_origins
from marivo.analysis.model.result_reference import FrameReference
from marivo.telemetry import traced

if TYPE_CHECKING:
    from pathlib import Path

    from marivo._kernel.grouping import Grouping
    from marivo.analysis.dsl.population import Population
    from marivo.semantic.spi import FieldExpression


@dataclass(frozen=True, init=False, repr=False, eq=False)
class AnalysisFrame(FrameIdentity):
    """One immutable data structure with expression, complete-memory or stored backing.

    Acquire through Session methods and Frame operators. Compute and persist return
    fixed results without mutating the original object.
    """

    _local: LocalFrameServices
    _input_id: str
    _schema: LogicalSchema
    _backing: FrameBacking
    _derivation: FrameDerivation | None
    _lineage: FrameLineage

    def __init__(self) -> None:
        raise TypeError("Use Session methods or Frame operations.")

    @classmethod
    def _from_expression(
        cls, local: LocalFrameServices, schema: LogicalSchema, root: FrameExpressionRoot
    ) -> AnalysisFrame:
        validate_expression_owner(local.owner, schema, root)
        return cls._bind(
            local,
            schema,
            ExpressionFrameBacking(root),
            "frame_" + uuid4().hex,
            frame_derivation.derive_declaration(schema, root),
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
        metadata: StoredFrameMetadata,
        method_facts: tuple[SavedFrameMethodFacts, ...] = (),
        description: str | None = None,
        derivation: FrameDerivation | None = None,
    ) -> AnalysisFrame:
        backing, input_id = stored_backing(
            local, data, artifact_id, metadata, method_facts, description
        )
        return cls._bind(local, data.schema, backing, input_id, derivation)

    @classmethod
    def _bind(
        cls,
        local: LocalFrameServices,
        schema: LogicalSchema,
        backing: FrameBacking,
        input_id: str,
        derivation: FrameDerivation | None = None,
    ) -> AnalysisFrame:
        schema = share_contexts(schema)
        validate_current_facts(schema)
        result = object.__new__(cls)
        object.__setattr__(result, "_local", local)
        object.__setattr__(result, "_input_id", input_id)
        object.__setattr__(result, "_schema", schema)
        object.__setattr__(result, "_backing", backing)
        object.__setattr__(result, "_derivation", derivation)
        lineage = capture_lineage(schema, backing, input_id)
        object.__setattr__(result, "_lineage", lineage)
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
    def schema(self) -> FrameSchema:
        """Return immutable captured output structure without payload or Store reads.

        Args: None.
        Returns: FrameSchema with full fields, axes, slots and default export layout.
        Example: ``field = frame.schema.values['revenue']``.
        Constraints: Pending types may be explicitly unresolved. Identities apply
        only to this exact input; schema grants no computation or freshness proof.
        """
        return capture_schema(self._input_id, self._schema)

    @property
    def lineage(self) -> FrameLineage:
        """Return captured semantic origins for every value, complete axis and presence.

        Args: None. Returns: Immutable FrameLineage directories.
        Example: ``frame.lineage.values["revenue"].show()``.
        Constraints: Metadata only on every carrier; no evaluation, row read or Catalog lookup.
        Static dependencies do not grant source or semantic execution authority.
        """
        return self._lineage

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

    @traced(surface="analysis", operation="analysis_frame.axis", capture="short")
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

    @traced(surface="analysis", operation="analysis_frame.__getitem__", capture="short")
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
        return axis_index(
            axis,
            self.axes,
            self._input_id,
            self._local.owner,
            location=location,
            canonical_id=canonical_id,
        )

    @traced(surface="analysis", operation="analysis_frame.coord", capture="short")
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
        require_column(
            column,
            self._input_id,
            self._local.owner,
            location=location,
            canonical_id=canonical_id,
        )

    def _derive(self, operation: FrameUnaryOperationKind) -> AnalysisFrame:
        structure = capture_value_inputs(
            self._schema, self.lineage, infer_frame_schema(self._schema, operation), operation
        )
        return self._from_expression(
            self._local, structure, OperationFrameBinding(operation, (self,))
        )

    @traced(surface="analysis", operation="analysis_frame.where", capture="short")
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

    @traced(surface="analysis", operation="analysis_frame.fix", capture="short")
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
        return self._derive(
            FixFrameValues(
                position, keys, all_origins(self.lineage.axes[self._schema.axes[position].token])
            )
        )

    @traced(surface="analysis", operation="analysis_frame.select", capture="short")
    def select(self, values: Mapping[str, Column | LiteralValue]) -> AnalysisFrame:
        """Name current Columns or non-null supported literals, preserving the row domain.

        Args: values: Ordered mapping of user name segments to bound Columns/literals; may be empty.
        Returns: A lazy Frame retaining axes and all presence, with only the selected values.
        Example: ``selected = frame.select({'change': frame['reference.absolute.revenue']})``.
        Constraints: An empty mapping removes all values while retaining rows, axes and presence.
        Does not recover removed values or endpoint inputs; no source reads during capture.
        """
        return self._derive(bind_selection(self, values))

    @traced(surface="analysis", operation="analysis_frame.top", capture="short")
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
            Proven complete input keys avoid duplicate scans, including n=0;
            unknown input keys and retained NULL/numeric obligations are checked
            before cropping. Use explicit source_check to audit declarations.
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

    @traced(surface="analysis", operation="analysis_frame.reduce", capture="short")
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
        return self._derive(bind_reduction(self, values, by))

    @traced(surface="analysis", operation="analysis_frame.attribute", capture="short")
    def attribute(
        self,
        *,
        by: tuple[FieldExpression | Grouping, ...] | Population,
        permutations: int = 1024,
        seed: int = 0,
    ) -> AnalysisFrame:
        """Explain selected source targets with contributions on new business axes.

        Args:
            by: Nonempty tuple of new semantic classifications, or one complete
                Population retaining its correlated keys and allocation rules.
            permutations: Complete sampled permutations for replacement-game targets,
                at least two; default 1024. Rejects bool. Exact targets do not sample.
            seed: Nonnegative integer seed, excluding bool; default zero. Controls the
                versioned permutation stream for a fixed parent and player domain.

        Returns:
            A lazy AnalysisFrame with original parent axes, new fine axes,
            contributions, fresh totals, observed totals, shares and fine diagnostics.

        Example:
            ``report = selected.attribute(by=(channel,)).compute()``.

        Constraints: Requires retained process-local source declarations for every
        direct Metric or admitted comparison target. Select and rename direct outputs.
        Supply a CalendarAxis to retain endpoints for attribution.
        Define ratio/linear Metrics before observation. Rules bind per target before
        business reads. Admitted paired nonlinear formulas use whole-Metric Shapley
        permutation sampling. Qualified state routes prove the complete domain;
        native-query routes validate visited values using the original reducers,
        preserving exact or approximate accuracy, including approximate distinct.
        The execution route binds before reads. Level retains admitted share rules,
        without a zero baseline.
        Fixed division retains its typed
        divisor and division order; Metric rescale also declares the result unit.
        Pending components expose their own units. Completion freezes parent keys
        and old values; parent and fine inputs are read independently without reranking
        or a shared-snapshot guarantee. Semantic identity, cardinality and version-grain
        declarations are trusted, including non-null primary keys even when physical
        metadata marks them nullable. No full-source NULL or uniqueness precheck is
        added; declaration violations are not guaranteed to fail. MySQL/MariaDB grouping
        and membership retain native source collations without requiring table changes;
        completed Frame alignment uses actual returned strings. Existing identity
        output checks remain. Operation-specific membership/Population coverage,
        overlap, distinct-value partitions, numeric validity and complete time
        correspondence remain checked. Float64 method facts preserve captured values
        without arithmetic recomputation. Paired rate/mix adds effect.within,
        effect.mix and effect.entry_exit; mixed additive/rate formulas also expose
        effect.additive. Effects sum to each group's contribution. Group denominators
        and effective pair weights must be nonnegative, parent denominators positive,
        and parent/fine components and original group rates consistent. These checks
        use numeric budgets without a business percentage allowance. Unchanged
        additive/level Float64 closure allows the greater of 1% of abs(total) and its
        numeric rounding budget. Integer/count, Decimal and Duration retain their typed
        policies. Relative contributions and effects use one absolute fresh parent
        baseline. Failures never change rules or redistribute residuals.
        Shapley reports contribution_std_error: permutation error conditional on captured states, excluding summary bias,
        observed marginal variation for native queries without isolating input changes; exact
        targets have no synthetic error column. Contributions and standard errors
        retain the target unit, with common parent normalization for relative targets.
        Optimized state capture does not grow with permutations; exact value-frequency states
        can transfer O(N) entries. For n players, cached coalition evaluations are
        bounded by min(2**n, 2 + permutations*(n-1)); this is not a fixed accuracy or
        latency guarantee. No row sampling, player truncation, adaptive sample count,
        or partial publication applies. Float SUM/native AVG use group-summary reconstruction
        without a bias bound; endpoints and total explain that reconstructed Metric.
        Without qualified state, native coalition queries preserve original reducers and may increase source work with coalition count.
        Each coalition and empty/full endpoint is evaluated once and cached. No shared
        snapshot, temporary materialization or drift audit is required; only visited
        native coalition values are validated.
        Remote identity keys admit direct integer, string and date32 carriers.
        ClickHouse Enum8/Enum16 decoded equality and filters are supported;
        Attribute excludes FixedString; Query retains decoded string NUL padding.
        Distributed queries compile
        GLOBAL IN/JOIN across shards, preserving complete subquery inputs, the
        declared table and target View filters without changing server settings.
        A zero fresh relative baseline reports the target, value and parent key.
        Qualified physical tables use the native backend
        subject to its method, type and source restrictions. Independent
        reopen has no declaration; rebuild explicit source inputs using saved joint keys.
        """
        return attribute(self, by=by, permutations=permutations, seed=seed)

    @traced(surface="analysis", operation="analysis_frame.over", capture="short")
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
        return bind_partition(self, statistic, by)

    @traced(surface="analysis", operation="analysis_frame.correlate", capture="short")
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
        return self._derive(bind_correlation(self, x, y, method, by))

    def _axis_pairs(
        self,
        other: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None,
        match: CalendarCorrespondence | None,
        location: str,
        canonical_id: str,
    ) -> tuple[tuple[int, int], ...]:
        validate_pairing(on, match, location=location, canonical_id=canonical_id)
        if not isinstance(other, AnalysisFrame) or other._local.owner != self._local.owner:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
            reject(
                "Frame inputs from the same Store and Session",
                repr(other),
                "Use two inputs owned by the same Session.",
                location,
                canonical_id=canonical_id,
            )
        return bind_axis_pairs(self, other, on, location=location, canonical_id=canonical_id)

    @traced(surface="analysis", operation="analysis_frame.align", capture="short")
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

    @_public_overload
    def compare(
        self,
        baselines: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None = None,
        match: CalendarCorrespondence | None = None,
    ) -> AnalysisFrame: ...

    @_public_overload
    def compare(self, baselines: Mapping[str, ComparisonBaseline]) -> AnalysisFrame: ...

    @traced(surface="analysis", operation="analysis_frame.compare", capture="short")
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
                match inside mv.baseline(...). Must agree with a consumed direct relation
                when current is its origin; otherwise declares a new endpoint match.

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
        declarations: tuple[tuple[str, ComparisonBaseline], ...]
        if isinstance(baselines, AnalysisFrame):
            declarations = (("reference", baseline(baselines, on=on, match=match)),)
        else:
            declarations = named_baselines(baselines, ComparisonBaseline, on=on, match=match)
        validate_baseline_aliases(tuple(name for name, _ in declarations))
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
        operation = CompareFrameValues(bindings, unnamed=isinstance(baselines, AnalysisFrame))
        inputs = (self, *(declaration.frame for _, declaration in declarations))
        schema = infer_frame_compare_schema(tuple(frame._schema for frame in inputs), operation)
        return self._from_expression(self._local, schema, OperationFrameBinding(operation, inputs))

    @traced(surface="analysis", operation="analysis_frame.compute", stage="execute", capture="long")
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
        result, declaration = complete_frame(self, self._backing, _bind_key_input)
        return self._from_memory(self._local, result, declaration)

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

    @traced(surface="analysis", operation="analysis_frame.persist", stage="execute", capture="long")
    def persist(self, *, description: str | None = None) -> AnalysisFrame:
        """Publish a completed result; exact stored roots return themselves.

        Args: description: Optional caller-supplied purpose, saved unchanged with this commit.
        Returns: A stored AnalysisFrame owned by the same original local service.
        Example: ``saved = frame.persist(description='Monthly revenue by region')``;
        ``print({'session_id': saved.session_id, 'artifact_id': saved.artifact_id})``.
        Constraints: Pending evaluation completes before publication admission;
        memory publication does not reevaluate, and stored idempotence reads metadata only.
        For another process, output/save the returned Session and Artifact IDs before exit;
        no show call is required. An existing saved reference needs no new publication.
        Discover lost commit IDs through session.persisted_frames(). A stored root
        retains its description when omitted or None; a different non-None description
        fails because commits are immutable. Descriptions do not certify data meaning.
        """
        if description is not None and type(description) is not str:
            reject(
                "a description string or None",
                type(description).__name__,
                "Pass a purpose string or omit description.",
                "frame.persist.description",
                canonical_id="analysis_frame.persist",
            )
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
            if description is not None and description != selected.description:
                reject(
                    "the immutable saved description or None",
                    repr(description),
                    "Omit description to retain the commit; use compute().persist(description=...) "
                    "to publish a new commit with another description.",
                    "frame.persist.description",
                    canonical_id="analysis_frame.persist",
                )
            return self
        completed = self.compute()
        assert isinstance(completed._backing, MemoryFrameBacking)
        backing = self._local.publish(completed._backing.result, description=description)
        return self._from_stored(
            self._local,
            backing.data,
            artifact_id=backing.artifact_id,
            metadata=backing.metadata,
            method_facts=backing.method_facts,
            description=backing.description,
            derivation=completed._derivation,
        )

    @property
    def ref(self) -> FrameReference:
        """Export the exact stored result identity without reading rows.

        Returns: A fixed FrameReference including the owning Store identity.
        Example: ``frame.persist().ref.to_dict()``.
        Constraints: Only persisted Frames have references; explicitly persist
        pending or memory data first. Never executes, saves or queries a source.
        """
        backing = self._require_stored("ref")
        return FrameReference._create(
            self._local.store.store_id, self.session_id, backing.artifact_id
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

    @property
    def _project_root(self) -> Path:
        return self._local.store.project_root

    def _require_stored(self, member: str) -> StoredFrameBacking:
        return require_stored(self._backing, member)

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

    @property
    def description(self) -> str | None:
        """Return the caller-supplied purpose of this exact saved commit.

        Args: None.
        Returns: The original description, or None when none was supplied.
        Example: ``purpose = saved.description``.
        Constraints: Stored roots only; metadata only, without Store or payload reads.
        The text helps identify candidates and does not certify their data meaning.
        """
        return self._require_stored("description").description

    @property
    def source_reads(self) -> tuple[FrameSourceRead, ...]:
        """Return captured actual source reads consumed by this saved result.

        Args: None.
        Returns: Immutable reads in stable inherited-input and evaluation order.
        Example: ``reads = pending.persist().source_reads``.
        Constraints: Stored roots only; call persist() for pending or memory Frames.
        Reads captured metadata without data, method, Catalog or source access.
        Periods retain known original time restrictions, not complete historical
        filters or the current derived domain. None means no captured period.
        """
        return self._require_stored("source_reads").metadata.source_reads

    def read_page(
        self,
        *,
        columns: tuple[str, ...] | None = None,
        limit: int = 1000,
        cursor: str | None = None,
        axis_names: Mapping[str, str] | None = None,
        time_details: bool = False,
    ) -> FramePage:
        """Read one projected ordered page from complete fixed data.

        Args:
            columns: Full value/presence names in output order; None selects all,
                an empty tuple selects coordinates only.
            limit: Integer 1..10000, excluding bool; default 1000.
            cursor: Previous page cursor or None; repeat the same request parameters.
            axis_names: Optional full axis name or token to export name mapping.
            time_details: Include calendar labels; default False.
                Calendar axes export one actual key; interval axes export .start/.end.
                Ordinary TimeFields export one value. axis_names renames prefixes.
        Returns: Immutable FramePage with complete keys, layout and read statistics.
        Example: ``page = frame.compute().read_page(columns=('revenue',), limit=100)``.
        Constraints: Does not execute or persist expressions. Stored pages validate
        consumed receipts and selected data/coordinates, not unread semantic data
        or source-read metadata. Page exports are isolated and have no attrs.
        """
        return read_fixed_page(
            self,
            self._local,
            self.axes,
            columns=columns,
            limit=limit,
            cursor=cursor,
            axis_names=axis_names,
            time_details=time_details,
        )

    def read_periods(
        self,
        target: FrameAxis | FrameComparison,
        *,
        limit: int = 1000,
        cursor: str | None = None,
    ) -> FramePeriodPage:
        """Read one bounded page of captured time periods or consumed correspondences.

        Args:
            target: A time axis handle or current time comparison from this exact Frame.
            limit: Integer 1..10000, excluding bool; default 1000.
            cursor: Previous period-page cursor or None; repeat the same target and limit.
        Returns: Immutable FramePeriodPage with complete keys, saved endpoints and read stats.
        Example: ``fixed = time_frame.compute(); page = fixed.read_periods(fixed.axes[0])``.
        Constraints: Requires complete memory or stored data; does not execute or persist. Ordinary axes and comparisons reject. Period records need not have current rows; use complete keys to join them to ``read_page().coordinates``. No source, Catalog, method-file or whole attachment buffer is retained or reconstructed.
        """
        if isinstance(self._backing, ExpressionFrameBacking):
            reject(
                "a complete memory or stored Frame",
                "expression",
                "Call compute() or persist() and read the returned Frame.",
                "frame.read_periods",
                canonical_id="analysis_frame.read_periods",
            )
        if isinstance(target, FrameAxis):
            selected: FrameAxisSchema | FrameComparison = self.schema.axes[
                self._axis_index(
                    target,
                    location="frame.read_periods",
                    canonical_id="analysis_frame.read_periods",
                )
            ]
        elif isinstance(target, FrameComparison):
            selected = target
        else:
            reject(
                "a time FrameAxis or current FrameComparison from this exact Frame",
                type(target).__name__,
                "Choose a time axis or comparison from this Frame's schema.",
                "frame.read_periods",
                canonical_id="analysis_frame.read_periods",
            )
        assert isinstance(self._schema, DataSchema)
        return read_frame_periods(
            input_id=self._input_id,
            schema=self._schema,
            backing=self._backing,
            local=self._local,
            target=selected,
            limit=limit,
            cursor=cursor,
        )

    @traced(
        surface="analysis", operation="analysis_frame.to_pandas", stage="execute", capture="long"
    )
    def to_pandas(
        self, *, axis_names: Mapping[str, str] | None = None, time_details: bool = False
    ) -> pd.DataFrame:
        """Export complete data as an isolated flat table, evaluating pending inputs.

        Args:
            axis_names: Optional full axis name or token to export name mapping. Names
                must be nonempty printable strings without surrounding whitespace.
                Defaults to the final path component of each axis name.
            time_details: Include calendar labels; default False.

        Returns:
            A pandas DataFrame with string columns: readable coordinates, full
            output names and independent boolean presence. Composite identities
            remain tuples. Calendar coordinates export one actual key timestamp; interval
            coordinates export start/end timestamps in their captured timezone.
            Mixed interval zones and sub-minute fixed offsets use UTC.
            Ordinary TimeField coordinates remain one value column.
            Calendar details add label. Actual keys do not describe complete bucket
            membership intervals; read_periods() exposes saved selection progress.
            axis_names renames prefixes only.
            Paired coordinates use left/right prefixes.
            attrs are empty; read axes, units and comparisons from frame.schema.

        Example:
            ``rows = frame.top(10, by=frame['amount']).to_pandas()``;
            ``amounts = rows["amount"]``. Rename an axis with
            ``frame.to_pandas(axis_names={"sales.region": "region"})``.

        Constraints:
            Pending expressions evaluate once per call, including derivatives of fixed
            data, without mutating this Frame, caching results or persisting. Retain
            compute()'s returned Frame to reuse fixed values across typed operations.
            Validate export options and name collisions before evaluation or data reads;
            resolve collisions with axis_names or select(). Saved reads fully validate current
            data without Catalog, source or method-payload reads. Value types, NA,
            presence and row order are preserved; no float coercion or zero filling.
            Large int64[pyarrow] values mixed with floats may raise ArrowInvalid.
            For approximate arithmetic, use ``rows["value"].astype("float64") / 3600.0``;
            this may round integers and represent pd.NA as NaN.
            Exported data grants no typed computation authority. Continue typed
            analysis from the Frame.
            Retain that Frame for typed operations; a DataFrame does not retain it.
            For another process, persist and retain exact IDs before the producer exits.
        """
        validate_time_details(time_details, "to_pandas")
        names = export_axis_names(self.axes, self._input_id, axis_names, "to_pandas")
        export_names(self._schema, tuple(names), time_details=time_details)
        if isinstance(self._backing, ExpressionFrameBacking):
            return self.compute().to_pandas(axis_names=axis_names, time_details=time_details)
        return export_fixed_frame(
            self._backing, self._local, tuple(names), time_details=time_details
        )

    def show(self) -> None:
        """Display current metadata and complete fixed data without evaluation.

        Returns:
            None; prints structure, captured value-column metadata, source-read
            summaries and available fixed data using pandas formatting.

        Example:
            ``saved.show()``.

        Constraints: Pending display never evaluates or prepares schema. Stored data is
        read in full; source-read summaries use captured metadata. No publication,
        ancestor rows, complete-result cache or fallback after a read error. Method
        facts show only counts/origins and a .method_facts pointer; no method payload read.
        """
        show_frame(self, self._local, self.axes)


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class ComparisonBaseline:
    """One immutable Frame and its requested comparison correspondence.

    Args: No public constructor; use mv.baseline(frame, on=..., match=...).
    Returns: A declaration with read-only frame, on and match fields.
    Example: ``current.compare({'prior': mv.baseline(previous)})``.
    Constraints: Captures inputs without reading data or binding a current Frame.
    Direct construction and subclass extension are unsupported.
    """

    frame: AnalysisFrame
    on: tuple[tuple[FrameAxis, FrameAxis], ...] | None
    match: CalendarCorrespondence | None

    def __init__(self) -> None:
        raise TypeError("Use mv.baseline(frame, on=..., match=...).")

    def __repr__(self) -> str:
        axes = "inferred" if self.on is None else str(len(self.on))
        rule = "inferred" if self.match is None else self.match.kind
        return (
            f"<ComparisonBaseline input={self.frame._input_id[:16]} "
            f"axes={axes} match={rule}; use .show()>"
        )

    def show(self) -> None:
        """Print bounded captured baseline metadata without reading data.

        Args: None.
        Returns: None; prints the input identity, axis pairs and time rule.
        Example: ``mv.baseline(previous, match=mv.ordinal()).show()``.
        Constraints: Does not call Frame.show, evaluate inputs or bind correspondence.
        """
        show_baseline(repr(self), self.on, self.match)


def baseline(
    frame: AnalysisFrame,
    *,
    on: tuple[tuple[FrameAxis, FrameAxis], ...] | None = None,
    match: CalendarCorrespondence | None = None,
) -> ComparisonBaseline:
    """Capture one named comparison's Frame and optional correspondence rules.

    Args:
        frame: Baseline Frame; the consuming compare requires the same Session.
        on: Complete (current axis, baseline axis) pairs, or None for inference.
        match: Closed calendar rule, or None for direct-relation/equality inference.
    Returns: An immutable ComparisonBaseline declaration.
    Example: ``current.compare({'prior': mv.baseline(previous, match=mv.ordinal())})``.
    Constraints: Checks input shapes only, with no reads. compare binds ownership,
    complete axes and temporal compatibility against its current Frame.
    """
    if not isinstance(frame, AnalysisFrame):
        reject(
            "an AnalysisFrame baseline",
            type(frame).__name__,
            "Pass mv.baseline(frame, on=..., match=...).",
            "baseline",
            canonical_id="baseline",
        )
    validate_baseline_options(on, match)
    result = object.__new__(ComparisonBaseline)
    object.__setattr__(result, "frame", frame)
    object.__setattr__(result, "on", on)
    object.__setattr__(result, "match", match)
    return result


def _bind_key_input(
    local: LocalFrameServices, result: CompletedFrameResult, input_id: str
) -> AnalysisFrame:
    return AnalysisFrame._bind(local, result.data.schema, MemoryFrameBacking(result), input_id)
