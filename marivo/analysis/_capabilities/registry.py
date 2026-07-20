"""Immutable capability registry for ``marivo.analysis``.

This module owns the complete immutable capability table, callable/type
indexes, reverse edges, grouping-topic expansion, public type/member
allowlists, and generated type-algebra rows.

All names are private to ``marivo.analysis``.  Nothing is added to
``marivo/analysis/__init__.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from marivo.analysis._capabilities.model import (
    ARTIFACT_FAMILIES,
    BoundaryCapability,
    CapabilityDescriptor,
    ConstructorCapability,
    InputFamily,
    OperatorCapability,
    ReadCapability,
    RecoveryCapability,
    RootGroup,
    SameAsInputFamily,
)
from marivo.introspection.live.reflect import callable_identity

# ---------------------------------------------------------------------------
# Public type/member allowlists
# ---------------------------------------------------------------------------

PUBLIC_FRAME_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "BaseFrame": ("show", "contract", "to_pandas"),
        "MetricFrame": (
            "metric",
            "components",
            "coverage",
            "as_scalar",
            "as_time_series",
            "as_segmented",
            "as_panel",
        ),
        "DeltaFrame": (
            "components",
            "predicted_attribution_shape",
            "as_scalar",
            "as_time_series",
            "as_segmented",
            "as_panel",
        ),
        "AttributionFrame": ("as_sum", "as_ratio_mix", "as_weighted_mix"),
        "CandidateSet": (
            "select",
            "as_point_anomaly",
            "as_period_shift",
            "as_driver_axis",
            "as_slice",
            "as_window",
            "as_cross_sectional_outlier",
        ),
    }
)

PUBLIC_FRAME_PROPERTIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "BaseFrame": (
            "id",
            "ref",
            "kind",
            "lineage",
            "quality_summary",
            "evidence_status",
            "evidence_digest",
            "state",
            "shape",
            "columns",
        ),
        "MetricFrame": ("semantic_shape", "metrics", "arity", "value_columns", "transform"),
        "DeltaFrame": ("semantic_shape", "transform"),
        "AttributionFrame": ("attribution_shape", "attribution_mode"),
    }
)

PUBLIC_OBJECT_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "Session": (
            "render",
            "show",
            "jobs",
            "recent_jobs",
            "frame_summaries",
            "get_frame",
        ),
    }
)

PUBLIC_OBJECT_PROPERTIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "Session": (
            "id",
            "name",
            "question",
            "catalog",
            "created_at",
            "updated_at",
            "report_tz_name",
            "is_read_only",
        ),
        "FrameSummaryEntry": ("id",),
    }
)


# ---------------------------------------------------------------------------
# Type algebra row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeAlgebraRow:
    """A single rendered type-algebra edge.

    Parameters
    ----------
    help_target:
        Canonical help target for the capability.
    source_families:
        Frozen set of input families feeding this edge.
    output_family:
        Output family string (or ``"pandas.DataFrame"`` for the terminal).
    is_terminal:
        ``True`` for the single aggregate ``boundary.to_pandas`` row.
    """

    help_target: str
    source_families: frozenset[str]
    output_family: str
    is_terminal: bool = False

    def render(self) -> str:
        """Render the row as a single-line type-algebra edge string."""
        sources_text = (
            "all registered artifact families"
            if self.is_terminal
            else ", ".join(sorted(self.source_families))
        )
        suffix = " (terminal)" if self.is_terminal else ""
        return f"{sources_text} -> {self.help_target} -> {self.output_family}{suffix}"


# ---------------------------------------------------------------------------
# Grouping descriptors (queryable but not invokable)
# ---------------------------------------------------------------------------


def _make_grouping_descriptor(
    topic: str,
    summary: str,
    root_group: RootGroup,
) -> ConstructorCapability:
    """Create a non-invokable grouping descriptor for a collapsed topic."""
    return ConstructorCapability(
        id=topic,
        public_entrypoint=f'mv.help("{topic}")',
        help_target=topic,
        summary=summary,
        root_group=root_group,
        root_visibility="grouped",
        callable_path=None,
        output_type="",
    )


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRegistry:
    """Immutable registry of all analysis capabilities.

    Provides lookup by id, help_target, and callable identity, plus
    reverse-edge indexes and generated type-algebra rows.
    """

    _descriptors: tuple[CapabilityDescriptor, ...]
    _by_id: Mapping[str, CapabilityDescriptor] = field(default_factory=dict)
    _by_help_target: Mapping[str, CapabilityDescriptor] = field(default_factory=dict)
    _by_callable: Mapping[str, CapabilityDescriptor] = field(default_factory=dict)
    _constructor_consumers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _algebra_rows: tuple[TypeAlgebraRow, ...] = field(default_factory=tuple)

    @property
    def surface(self) -> Literal["analysis"]:
        """Return the owning help surface for the neutral registry protocol."""
        return "analysis"

    # -- Properties --------------------------------------------------------

    @property
    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        return self._descriptors

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(d.id for d in self._descriptors)

    @property
    def help_targets(self) -> tuple[str, ...]:
        return tuple(d.help_target for d in self._descriptors)

    @property
    def constructor_consumers(self) -> Mapping[str, tuple[str, ...]]:
        return self._constructor_consumers

    # -- Lookup -----------------------------------------------------------

    def by_id(self, capability_id: str) -> CapabilityDescriptor:
        """Return the descriptor with the given canonical id."""
        return self._by_id[capability_id]

    def canonical_ids(self) -> tuple[str, ...]:
        """Return canonical help targets in native registry order."""
        return self.help_targets

    def by_canonical_id(self, canonical_id: str) -> CapabilityDescriptor:
        """Resolve canonical help grammar, then the native capability id."""
        try:
            return self.by_help_target(canonical_id)
        except KeyError:
            return self.by_id(canonical_id)

    def by_help_target(self, help_target: str) -> CapabilityDescriptor:
        """Return the descriptor with the given help target."""
        return self._by_help_target[help_target]

    def by_callable(self, callable_obj: object) -> CapabilityDescriptor:
        """Return the descriptor registered for the given callable identity.

        Resolves bound methods through ``__func__`` so that
        ``Session.observe`` and ``session.observe`` resolve to the same
        descriptor.  The canonical identity is the ``callable_path`` string,
        not the function object's ``id()``.
        """
        key = self._callable_key(callable_obj)
        return self._by_callable[key]

    @staticmethod
    def _callable_key(callable_obj: object) -> str:
        """Return the canonical callable_path for a callable or type.

        Resolves bound methods through ``__func__`` and uses
        :func:`_module_path_for` to produce the dotted import path that
        matches the ``callable_path`` stored on descriptors.
        """
        return callable_identity(callable_obj)

    # -- Type algebra -----------------------------------------------------

    def type_algebra_rows(self) -> tuple[TypeAlgebraRow, ...]:
        """Return the generated type-algebra rows in deterministic order."""
        return self._algebra_rows


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------

_MF: frozenset[InputFamily] = frozenset({"MetricFrame"})
_DF: frozenset[InputFamily] = frozenset({"DeltaFrame"})
_MF_OR_DF: frozenset[InputFamily] = frozenset({"MetricFrame", "DeltaFrame"})
_CS: frozenset[InputFamily] = frozenset({"CandidateSet"})
_AF: frozenset[InputFamily] = frozenset({"AttributionFrame"})


def _build_registry() -> CapabilityRegistry:
    """Build the complete immutable capability registry."""

    # Late imports to avoid circular dependencies at module load time.
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.candidate import CandidateSet
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.policies import (
        SamplingPolicy,
        dow_aligned,
        holiday_aligned,
        holiday_and_dow_aligned,
        window_bucket,
    )
    from marivo.analysis.runtime_metric import aggregate, ratio, slice, weighted_mean
    from marivo.analysis.windows.spec import AbsoluteWindow, TimeScope

    all_artifact_families: frozenset[InputFamily] = frozenset(ARTIFACT_FAMILIES)

    descriptors: list[CapabilityDescriptor] = []

    # -- Session operators ------------------------------------------------

    descriptors.append(
        OperatorCapability(
            id="observe",
            public_entrypoint="session.observe(...)",
            help_target="observe",
            summary=(
                "Materialize exact catalog refs or closed runtime metric expressions "
                "through one bounded graph into a typed MetricFrame."
            ),
            root_group="artifact_production",
            root_visibility="direct",
            constraint_ids=("metric_expression_resolvable", "window_absolute_parseable"),
            callable_path="marivo.analysis.session.core.Session.observe",
            receiver="Session",
            accepted_inputs={
                "metric": frozenset({"MetricSemantic", "RuntimeMetricExpression"}),
                "time_scope": frozenset({"TimeScopeInput"}),
            },
            output_family="MetricFrame",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="compare",
            public_entrypoint="session.compare(...)",
            help_target="compare",
            summary="Compute the typed delta between two MetricFrames.",
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=(
                "frame_kind_compatible",
                "alignment_policy_shape",
                "cumulative_compare_compatible",
            ),
            callable_path="marivo.analysis.session.core.Session.compare",
            receiver="Session",
            accepted_inputs={
                "a": _MF,
                "b": _MF,
                "alignment": frozenset({"AlignmentPolicy"}),
                "sampling": frozenset({"SamplingPolicy"}),
            },
            output_family="DeltaFrame",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="attribute",
            public_entrypoint="session.attribute(...)",
            help_target="attribute",
            summary=(
                "Attribute a DeltaFrame's movement over explicit axes with "
                "reconciled contributions and explicit share denominators."
            ),
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=(
                "frame_kind_compatible",
                "attribution_additivity_compatible",
                "attribution_reconciliation",
                "cumulative_attribution_unsupported",
            ),
            callable_path="marivo.analysis.session.core.Session.attribute",
            receiver="Session",
            accepted_inputs={
                "frame": _DF,
                "axes": frozenset({"DimensionSemantic"}),
            },
            output_family="AttributionFrame",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="correlate",
            public_entrypoint="session.correlate(...)",
            help_target="correlate",
            summary="Measure the association between two MetricFrames.",
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=(
                "frame_kind_compatible",
                "alignment_policy_shape",
                "correlate_lag_semantics",
            ),
            callable_path="marivo.analysis.session.core.Session.correlate",
            receiver="Session",
            accepted_inputs={
                "a": _MF,
                "b": _MF,
                "alignment": frozenset({"AlignmentPolicy"}),
                "sampling": frozenset({"SamplingPolicy"}),
            },
            output_family="AssociationResult",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="hypothesis_test",
            public_entrypoint="session.hypothesis_test(...)",
            help_target="hypothesis_test",
            summary="Run a paired hypothesis test over two MetricFrames.",
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=("frame_kind_compatible", "alignment_policy_shape"),
            callable_path="marivo.analysis.session.core.Session.hypothesis_test",
            receiver="Session",
            accepted_inputs={
                "a": _MF,
                "b": _MF,
                "alignment": frozenset({"AlignmentPolicy"}),
            },
            output_family="HypothesisTestResult",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="forecast",
            public_entrypoint="session.forecast(...)",
            help_target="forecast",
            summary="Project a time_series or panel MetricFrame forward.",
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=("forecast_input_shape",),
            callable_path="marivo.analysis.session.core.Session.forecast",
            receiver="Session",
            accepted_inputs={
                "history": _MF,
            },
            output_family="ForecastFrame",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="assess_quality",
            public_entrypoint="session.assess_quality(...)",
            help_target="assess_quality",
            summary="Run quality checks over a MetricFrame.",
            root_group="typed_analysis",
            root_visibility="direct",
            constraint_ids=("quality_target_shape",),
            callable_path="marivo.analysis.session.core.Session.assess_quality",
            receiver="Session",
            accepted_inputs={
                "target": _MF,
            },
            output_family="QualityReport",
        )
    )

    # -- Discover operators -----------------------------------------------

    _discover_specs: tuple[
        tuple[str, str, frozenset[InputFamily], Mapping[str, frozenset[InputFamily]]], ...
    ] = (
        ("discover.point_anomalies", "Find time-series points with unusual values.", _MF, {}),
        ("discover.period_shifts", "Find period-shift candidates from a DeltaFrame.", _DF, {}),
        (
            "discover.driver_axes",
            "Find dimensions that explain a delta.",
            _DF,
            {"search_space": frozenset({"DimensionSemantic"})},
        ),
        (
            "discover.interesting_slices",
            "Find dimension slices with notable values.",
            _MF_OR_DF,
            {},
        ),
        ("discover.interesting_windows", "Find time windows with notable behavior.", _MF_OR_DF, {}),
        (
            "discover.cross_sectional_outliers",
            "Find segments that are outliers compared to their peers.",
            _MF,
            {},
        ),
    )

    for obj_id, summary, source_families, extra_inputs in _discover_specs:
        objective = obj_id.split(".", 1)[1]
        descriptors.append(
            OperatorCapability(
                id=obj_id,
                public_entrypoint=f"session.discover.{objective}(...)",
                help_target=obj_id,
                summary=summary,
                root_group="typed_analysis",
                root_visibility="grouped",
                constraint_ids=("discover_minimum_evidence", "frame_kind_compatible"),
                callable_path=f"marivo.analysis.session.core.SessionDiscoverNamespace.{objective}",
                receiver="SessionDiscoverNamespace",
                accepted_inputs={
                    "source": source_families,
                    **extra_inputs,
                },
                output_family="CandidateSet",
            )
        )

    # -- Transform operators ----------------------------------------------

    shared_transform_ops: tuple[
        tuple[str, str, frozenset[InputFamily], Mapping[str, frozenset[InputFamily]]], ...
    ] = (
        ("filter", "Filter rows using a boolean predicate.", _MF_OR_DF, {}),
        ("slice", "Filter rows by catalog-backed axis values.", _MF_OR_DF, {}),
        ("rollup", "Aggregate by dropping axes or re-bucketing time.", _MF_OR_DF, {}),
        ("topk", "Keep the largest rows ordered by a column.", _MF_OR_DF, {}),
        ("bottomk", "Keep the smallest rows ordered by a column.", _MF_OR_DF, {}),
        ("rank", "Add a rank column ordered by a value column.", _MF_OR_DF, {}),
        (
            "window",
            "Restrict to a half-open time window.",
            _MF_OR_DF,
            {"window": frozenset({"TimeScopeInput"})},
        ),
    )

    for op_name, summary, families, extra_inputs in shared_transform_ops:
        cap_id = f"transform.{op_name}"
        descriptors.append(
            OperatorCapability(
                id=cap_id,
                public_entrypoint=f"frame.transform.{op_name}(...)",
                help_target=cap_id,
                summary=summary,
                root_group="family_operations",
                root_visibility="grouped",
                constraint_ids=(
                    "transform_arguments",
                    "transform_frame_shape",
                    "transform_operator_supported",
                ),
                callable_path=f"marivo.analysis.frames.transforms._FrameTransforms.{op_name}",
                receiver="MetricFrameTransforms|DeltaFrameTransforms",
                accepted_inputs={
                    "receiver": families,
                    **extra_inputs,
                },
                output_family=SameAsInputFamily(parameter="receiver"),
            )
        )

    # normalize is MetricFrame-only
    descriptors.append(
        OperatorCapability(
            id="transform.normalize",
            public_entrypoint="frame.transform.normalize(...)",
            help_target="transform.normalize",
            summary="Normalize MetricFrame values.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=(
                "transform_arguments",
                "transform_frame_shape",
                "transform_operator_supported",
            ),
            callable_path="marivo.analysis.frames.transforms.MetricFrameTransforms.normalize",
            receiver="MetricFrameTransforms",
            accepted_inputs={
                "receiver": _MF,
            },
            output_family="MetricFrame",
        )
    )

    # -- Frame methods (operators / reads) --------------------------------

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.metric",
            public_entrypoint="frame.metric(...)",
            help_target="MetricFrame.metric",
            summary="Project one metric out of a multi-metric frame.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("frame_kind_compatible",),
            callable_path="marivo.analysis.frames.metric.MetricFrame.metric",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_family="MetricFrame",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.components",
            public_entrypoint="frame.components()",
            help_target="MetricFrame.components",
            summary="Load the recursive component graph persisted for a MetricFrame.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("component_frame_available",),
            callable_path="marivo.analysis.frames.metric.MetricFrame.components",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_family="ComponentFrame",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.coverage",
            public_entrypoint="frame.coverage()",
            help_target="MetricFrame.coverage",
            summary="Load the linked CoverageFrame for this metric frame.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path="marivo.analysis.frames.metric.MetricFrame.coverage",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_family="CoverageFrame",
        )
    )

    descriptors.append(
        OperatorCapability(
            id="DeltaFrame.components",
            public_entrypoint="frame.components()",
            help_target="DeltaFrame.components",
            summary="Load the linked ComponentFrame for component-aware deltas.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("component_frame_available",),
            callable_path="marivo.analysis.frames.delta.DeltaFrame.components",
            receiver="DeltaFrame",
            accepted_inputs={"receiver": _DF},
            output_family="ComponentFrame",
        )
    )

    descriptors.append(
        ReadCapability(
            id="CandidateSet.select",
            public_entrypoint="cands.select(...)",
            help_target="CandidateSet.select",
            summary="Return one closed shape-specific selection from a ranked candidate row.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=("frame_kind_compatible",),
            callable_path="marivo.analysis.frames.candidate.CandidateSet.select",
            receiver_family="CandidateSet",
            result_kind="defensive_copy",
            read_bound="bounded",
        )
    )

    # -- Shape-narrowing methods (reads) ----------------------------------

    for class_name, cls_obj, methods in (
        ("MetricFrame", MetricFrame, ("as_scalar", "as_time_series", "as_segmented", "as_panel")),
        ("DeltaFrame", DeltaFrame, ("as_scalar", "as_time_series", "as_segmented", "as_panel")),
        ("AttributionFrame", AttributionFrame, ("as_sum", "as_ratio_mix", "as_weighted_mix")),
        (
            "CandidateSet",
            CandidateSet,
            (
                "as_point_anomaly",
                "as_period_shift",
                "as_driver_axis",
                "as_slice",
                "as_window",
                "as_cross_sectional_outlier",
            ),
        ),
    ):
        family = class_name
        for method_name in methods:
            descriptors.append(
                ReadCapability(
                    id=f"{class_name}.{method_name}",
                    public_entrypoint=f"frame.{method_name}()",
                    help_target=f"{class_name}.{method_name}",
                    summary=f"Narrow {class_name} to its declared shape.",
                    root_group="family_operations",
                    root_visibility="grouped",
                    constraint_ids=("frame_kind_compatible",),
                    callable_path=_module_path_for(getattr(cls_obj, method_name)),
                    receiver_family=family,
                    result_kind="immutable_metadata",
                    read_bound="bounded",
                )
            )

    # Also register DeltaFrame.predicted_attribution_shape as a read
    descriptors.append(
        ReadCapability(
            id="DeltaFrame.predicted_attribution_shape",
            public_entrypoint="delta.predicted_attribution_shape()",
            help_target="DeltaFrame.predicted_attribution_shape",
            summary="Predict the AttributionFrame shape decompose will produce.",
            root_group="family_operations",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path="marivo.analysis.frames.delta.DeltaFrame.predicted_attribution_shape",
            receiver_family="DeltaFrame",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    # -- BaseFrame reads --------------------------------------------------

    descriptors.append(
        ReadCapability(
            id="BaseFrame.show",
            public_entrypoint="frame.show()",
            help_target="BaseFrame.show",
            summary="Bounded inspection of the artifact.",
            root_group="artifact_inspection",
            root_visibility="grouped",
            constraint_ids=("frame_read_bounds",),
            callable_path="marivo.analysis.frames.base.BaseFrame.show",
            receiver_family="BaseFrame",
            result_kind="terminal_text",
            read_bound="bounded",
        )
    )

    for method_name, summary in (
        ("render", "Return bounded session state as text without writing stdout."),
        ("show", "Print bounded session state for inspection."),
    ):
        descriptors.append(
            ReadCapability(
                id=f"Session.{method_name}",
                public_entrypoint=f"session.{method_name}()",
                help_target=f"Session.{method_name}",
                summary=summary,
                root_group="artifact_inspection",
                root_visibility="grouped",
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.Session.{method_name}",
                receiver_family="Session",
                result_kind="terminal_text",
                read_bound="bounded",
            )
        )

    descriptors.append(
        ReadCapability(
            id="BaseFrame.contract",
            public_entrypoint="frame.contract()",
            help_target="BaseFrame.contract",
            summary="Return the mechanical consumption contract for the artifact.",
            root_group="artifact_inspection",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path="marivo.analysis.frames.base.BaseFrame.contract",
            receiver_family="BaseFrame",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    # -- Boundaries -------------------------------------------------------

    descriptors.append(
        BoundaryCapability(
            id="boundary.to_pandas",
            public_entrypoint="frame.to_pandas()",
            help_target="boundary.to_pandas",
            summary="Terminal exit: return a defensive pandas DataFrame copy.",
            root_group="boundaries",
            root_visibility="direct",
            constraint_ids=("frame_immutable",),
            callable_path="marivo.analysis.frames.base.BaseFrame.to_pandas",
            direction="terminal_exit",
            accepted_inputs={
                "receiver": all_artifact_families,
            },
            output_family="pandas.DataFrame",
            preserves=(),
            does_not_preserve=("lineage", "meta", "session_ownership", "evidence"),
        )
    )

    # -- Constructors -----------------------------------------------------

    constructor_specs: tuple[tuple[str, str, str, str, object, str], ...] = (
        (
            "window_bucket",
            "mv.window_bucket()",
            "window_bucket",
            "Construct a window-bucket alignment policy.",
            window_bucket,
            "AlignmentPolicy",
        ),
        (
            "dow_aligned",
            "mv.dow_aligned(...)",
            "dow_aligned",
            "Construct a day-of-week calendar alignment policy.",
            dow_aligned,
            "AlignmentPolicy",
        ),
        (
            "holiday_aligned",
            "mv.holiday_aligned(...)",
            "holiday_aligned",
            "Construct a holiday calendar alignment policy.",
            holiday_aligned,
            "AlignmentPolicy",
        ),
        (
            "holiday_and_dow_aligned",
            "mv.holiday_and_dow_aligned(...)",
            "holiday_and_dow_aligned",
            "Construct a holiday-then-day-of-week alignment policy.",
            holiday_and_dow_aligned,
            "AlignmentPolicy",
        ),
        (
            "TimeScope",
            "mv.TimeScope(...)",
            "TimeScope",
            "Half-open time interval [start, end) for observe time_scope.",
            TimeScope,
            "TimeScope",
        ),
        (
            "AbsoluteWindow",
            "mv.AbsoluteWindow(...)",
            "AbsoluteWindow",
            "Half-open time interval [start, end) with optional grain.",
            AbsoluteWindow,
            "AbsoluteWindow",
        ),
        (
            "SamplingPolicy",
            "mv.SamplingPolicy(...)",
            "SamplingPolicy",
            "Sampling policy for compare and correlate.",
            SamplingPolicy,
            "SamplingPolicy",
        ),
    )

    for cap_id, entrypoint, target, summary, callable_obj, output_type in constructor_specs:
        descriptors.append(
            ConstructorCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group="policies_builders",
                root_visibility="direct",
                constraint_ids=(),
                callable_path=_module_path_for(callable_obj),
                output_type=output_type,
            )
        )

    runtime_metric_specs: tuple[tuple[str, str, object, str], ...] = (
        (
            "runtime_metric.aggregate",
            "mv.runtime_metric.aggregate(...) ",
            aggregate,
            "RuntimeAggregateExpr",
        ),
        (
            "runtime_metric.slice",
            "mv.runtime_metric.slice(...) ",
            slice,
            "RuntimeSliceExpr",
        ),
        (
            "runtime_metric.weighted_mean",
            "mv.runtime_metric.weighted_mean(...) ",
            weighted_mean,
            "RuntimeWeightedMeanExpr",
        ),
        (
            "runtime_metric.ratio",
            "mv.runtime_metric.ratio(...) ",
            ratio,
            "RuntimeRatioExpr",
        ),
    )
    for cap_id, entrypoint, callable_obj, output_type in runtime_metric_specs:
        descriptors.append(
            ConstructorCapability(
                id=cap_id,
                public_entrypoint=entrypoint.rstrip(),
                help_target=cap_id,
                summary="Build one frozen node in the closed runtime metric expression algebra.",
                root_group="policies_builders",
                root_visibility="grouped",
                constraint_ids=(
                    "runtime_metric_closed_algebra",
                    *(
                        ("runtime_weighted_mean_valid",)
                        if cap_id == "runtime_metric.weighted_mean"
                        else ()
                    ),
                ),
                callable_path=_module_path_for(callable_obj),
                output_type=output_type,
            )
        )

    descriptors.append(
        ConstructorCapability(
            id="AttributionMode",
            public_entrypoint='mode="joint" | mode="hierarchy"',
            help_target="AttributionMode",
            summary=(
                "Multi-axis row layout: joint emits one additive row per complete axis "
                "combination; hierarchy emits prefix rows and only its deepest level "
                "reconciles. Multi-axis calls have no default. Omit mode for one axis, "
                "where a supplied value has no effect. Mode is distinct from attribution "
                "method, so either layout can use weighted_mix."
            ),
            root_group="policies_builders",
            root_visibility="grouped",
            constraint_ids=(),
            callable_path=None,
            output_type='Literal["joint", "hierarchy"]',
        )
    )

    # -- Recovery / reads: session lifecycle ------------------------------

    recovery_specs: tuple[tuple[str, str, str, str, str, str, str], ...] = (
        (
            "session.get_or_create",
            "mv.session.get_or_create(...)",
            "session.get_or_create",
            "Attach to an existing session or create a new one.",
            "recovery",
            "Session",
            "session_name",
        ),
        (
            "session.current",
            "mv.session.current()",
            "session.current",
            "Return the current session or None.",
            "recovery",
            "Session",
            "none",
        ),
        (
            "session.list",
            "mv.session.list()",
            "session.list",
            "List sessions in the current project.",
            "session_state",
            "SessionSummary",
            "none",
        ),
        (
            "session.recent",
            "mv.session.recent()",
            "session.recent",
            "Return a bounded page of recently updated project sessions.",
            "recovery",
            "SessionSummaryPage",
            "none",
        ),
        (
            "session.inspect",
            "mv.session.inspect(name)",
            "session.inspect",
            "Read a bounded historical session metadata snapshot without resuming it.",
            "recovery",
            "SessionInspection",
            "session_name",
        ),
        (
            "session.delete",
            "mv.session.delete(name)",
            "session.delete",
            "Permanently delete a session and all its on-disk data.",
            "recovery",
            "Session",
            "session_name",
        ),
    )

    for cap_id, entrypoint, target, summary, group, restored, identity in recovery_specs:
        descriptors.append(
            RecoveryCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group=group,  # type: ignore[arg-type]  # group is a str from a tuple; validated at runtime
                root_visibility="direct",
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.{cap_id.split('.', 1)[1]}",
                identity_input=identity,
                restored_family=restored,
                query_behavior="none",
            )
        )

    # -- Session job/frame reads ------------------------------------------

    session_read_specs: tuple[tuple[str, str, str, str, str], ...] = (
        (
            "session.jobs",
            "session.jobs()",
            "session.jobs",
            "Return lightweight summaries for every recorded job.",
            "JobSummary",
        ),
        (
            "session.recent_jobs",
            "session.recent_jobs(limit=5)",
            "session.recent_jobs",
            "Return the most recent job summaries.",
            "JobSummary",
        ),
        (
            "session.job",
            "session.job(job_id)",
            "session.job",
            "Return the full record for a single job.",
            "dict",
        ),
        (
            "session.frame_summaries",
            "session.frame_summaries()",
            "session.frame_summaries",
            "Return rich metadata for each persisted frame.",
            "FrameSummaryPage",
        ),
        (
            "session.get_frame",
            "session.get_frame(ref)",
            "session.get_frame",
            "Load a persisted frame by ref or artifact_id.",
            "BaseFrame",
        ),
    )

    for cap_id, entrypoint, target, summary, restored in session_read_specs:
        method_name = cap_id.split(".", 1)[1]
        descriptors.append(
            RecoveryCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group="recovery",
                root_visibility="grouped",
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.Session.{method_name}",
                identity_input="session_id_or_frame_ref",
                restored_family=restored,
                query_behavior="none",
            )
        )

    # -- Evidence namespace reads -----------------------------------------

    evidence_specs: tuple[tuple[str, str, str, str], ...] = (
        (
            "session.evidence.digests",
            "session.evidence.digests(...) ",
            "session.evidence.digests",
            "Return a bounded newest-first page of persisted artifact digests.",
        ),
        (
            "session.evidence.findings",
            "session.evidence.findings(...)",
            "session.evidence.findings",
            "Return Surface 3 findings for this session.",
        ),
        (
            "session.evidence.finding",
            "session.evidence.finding(id)",
            "session.evidence.finding",
            "Return one canonical typed finding by identity.",
        ),
        (
            "session.evidence.digest",
            "session.evidence.digest(artifact_ref)",
            "session.evidence.digest",
            "Return one persisted artifact digest by identity.",
        ),
        (
            "session.evidence.trace",
            "session.evidence.trace(id)",
            "session.evidence.trace",
            "Trace one finding to its source fields and retained digest items.",
        ),
    )

    for cap_id, entrypoint, target, summary in evidence_specs:
        method_name = cap_id.split(".")[-1]
        descriptors.append(
            ReadCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group="recovery",
                root_visibility="grouped",
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.EvidenceNamespace.{method_name}",
                receiver_family="EvidenceNamespace",
                result_kind="immutable_metadata",
                read_bound="bounded",
            )
        )

    # -- help / help_text reads -------------------------------------------

    descriptors.append(
        ReadCapability(
            id="help",
            public_entrypoint="mv.help(target)",
            help_target="help",
            summary="Print bounded help text for a Marivo analysis symbol or semantic ref.",
            root_group="artifact_inspection",
            root_visibility="direct",
            constraint_ids=(),
            callable_path="marivo.analysis.help.help",
            receiver_family="module",
            result_kind="terminal_text",
            read_bound="bounded",
        )
    )

    descriptors.append(
        ReadCapability(
            id="help_text",
            public_entrypoint="mv.help_text(target)",
            help_target="help_text",
            summary="Return analysis help text as a string without printing.",
            root_group="artifact_inspection",
            root_visibility="direct",
            constraint_ids=(),
            callable_path="marivo.analysis.help.help_text",
            receiver_family="module",
            result_kind="terminal_text",
            read_bound="bounded",
        )
    )

    # -- Semantic catalog reads -------------------------------------------

    catalog_specs: tuple[tuple[str, str, str, str], ...] = (
        (
            "catalog.domains",
            "session.catalog.domains",
            "catalog.domains",
            "Browse catalog domains.",
        ),
        (
            "catalog.metrics",
            "session.catalog.metrics",
            "catalog.metrics",
            "Browse catalog metrics.",
        ),
        (
            "catalog.dimensions",
            "session.catalog.dimensions",
            "catalog.dimensions",
            "Browse catalog dimensions.",
        ),
        (
            "catalog.require",
            "session.catalog.require(ref)",
            "catalog.require",
            "Require one exact ref in the compiled catalog.",
        ),
        (
            "catalog.readiness",
            "session.catalog.readiness(refs=...)",
            "catalog.readiness",
            "Check semantic readiness for refs.",
        ),
    )

    for cap_id, entrypoint, target, summary in catalog_specs:
        descriptors.append(
            ReadCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                root_group="semantic_inputs",
                root_visibility="grouped",
                constraint_ids=(),
                callable_path=f"marivo.semantic.catalog.SemanticCatalog.{cap_id.split('.', 1)[1]}",
                receiver_family="SemanticCatalog",
                result_kind="immutable_metadata",
                read_bound="bounded",
            )
        )

    # -- Grouping descriptors (non-invokable) -----------------------------

    descriptors.append(
        _make_grouping_descriptor(
            "session",
            "Analysis session lifecycle and persistence helpers.",
            "session_state",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "catalog",
            "Browse semantic catalog domains, metrics, and dimensions.",
            "semantic_inputs",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "runtime_metric",
            "Closed recursive runtime metric expression constructors.",
            "policies_builders",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "discover",
            "Objective helpers for deterministic candidate discovery.",
            "typed_analysis",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "transform",
            "Family-preserving reshape of a MetricFrame or DeltaFrame.",
            "family_operations",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "recovery",
            "Cross-script frame and job recovery helpers.",
            "recovery",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "boundary",
            "Typed-flow boundary crossings.",
            "boundaries",
        )
    )

    descriptors.append(
        _make_grouping_descriptor(
            "artifacts",
            (
                "Read artifacts progressively: inspect bounded state, check mechanical "
                "compatibility, then cross a terminal boundary only for intentionally "
                "custom work."
            ),
            "artifact_inspection",
        )
    )

    # -- Finalize: build indexes ------------------------------------------

    return _finalize_registry(tuple(descriptors))


def _module_path_for(obj: object) -> str:
    """Return a best-effort dotted path for a callable or type.

    ``property`` objects lack ``__module__`` and ``__qualname__`` but their
    ``fget`` getter functions carry both, so we unwrap properties before
    computing the path.
    """
    if isinstance(obj, property):
        obj = obj.fget
    module: str | None = getattr(obj, "__module__", None)
    qualname: str | None = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    if module:
        return str(module)
    return str(qualname) if qualname else ""


def _finalize_registry(
    descriptors: tuple[CapabilityDescriptor, ...],
) -> CapabilityRegistry:
    """Build indexes, validate uniqueness, and generate type algebra rows."""

    # Validate no duplicate ids
    by_id: dict[str, CapabilityDescriptor] = {}
    for desc in descriptors:
        if desc.id in by_id:
            raise ValueError(f"duplicate capability id: {desc.id}")
        by_id[desc.id] = desc

    # Validate no duplicate help_targets
    by_help_target: dict[str, CapabilityDescriptor] = {}
    for desc in descriptors:
        if desc.help_target in by_help_target:
            raise ValueError(f"duplicate help_target: {desc.help_target}")
        by_help_target[desc.help_target] = desc

    # Build callable identity index keyed by callable_path (canonical string).
    # Reject duplicates: two descriptors with the same callable_path is an
    # error, not a silently-ignored collision.
    by_callable: dict[str, CapabilityDescriptor] = {}
    for desc in descriptors:
        if desc.callable_path is None:
            continue
        if desc.callable_path in by_callable:
            raise ValueError(
                f"duplicate callable_path: {desc.callable_path!r} "
                f"(shared by {by_callable[desc.callable_path].id!r} and {desc.id!r})"
            )
        by_callable[desc.callable_path] = desc

    # Build constructor consumer reverse index from all capabilities that
    # declare accepted_inputs (operators AND boundaries).
    constructor_consumers: dict[str, list[str]] = {}
    for desc in descriptors:
        if isinstance(desc, (OperatorCapability, BoundaryCapability)):
            for param_families in desc.accepted_inputs.values():
                for family in param_families:
                    constructor_consumers.setdefault(family, []).append(desc.id)

    constructor_consumers_frozen: dict[str, tuple[str, ...]] = {
        family: tuple(sorted(set(consumers))) for family, consumers in constructor_consumers.items()
    }

    # Generate type algebra rows
    algebra_rows = _generate_algebra_rows(descriptors, by_id)

    return CapabilityRegistry(
        _descriptors=descriptors,
        _by_id=MappingProxyType(by_id),
        _by_help_target=MappingProxyType(by_help_target),
        _by_callable=MappingProxyType(by_callable),
        _constructor_consumers=MappingProxyType(constructor_consumers_frozen),
        _algebra_rows=algebra_rows,
    )


def _generate_algebra_rows(
    descriptors: tuple[CapabilityDescriptor, ...],
    by_id: Mapping[str, CapabilityDescriptor],
) -> tuple[TypeAlgebraRow, ...]:
    """Generate the type algebra rows from the descriptor table.

    - Invokable operators produce one row each with their accepted input
      families.
    - discover.* and transform.* member edges collapse to the canonical
      ``discover`` / ``transform`` grouping topic in the root algebra.
    - Governed-entry boundary capabilities
      produce a row showing their accepted input families and output family.
    - The single terminal ``boundary.to_pandas`` aggregate row appears once.
    """

    rows: list[TypeAlgebraRow] = []

    # Collapse discover and transform members into their grouping topics.
    discover_source_families: set[str] = set()
    transform_source_families: set[str] = set()

    for desc in descriptors:
        if not isinstance(desc, OperatorCapability):
            continue
        if desc.id.startswith("discover."):
            for families in desc.accepted_inputs.values():
                discover_source_families.update(families)
            continue
        if desc.id.startswith("transform."):
            for families in desc.accepted_inputs.values():
                transform_source_families.update(families)
            continue

        # Non-collapsed operator: produce an individual row.
        source_families: set[str] = set()
        for families in desc.accepted_inputs.values():
            source_families.update(families)

        output_family = _output_family_str(desc)
        rows.append(
            TypeAlgebraRow(
                help_target=desc.help_target,
                source_families=frozenset(source_families),
                output_family=output_family,
                is_terminal=False,
            )
        )

    # Collapsed grouping rows
    if discover_source_families:
        rows.append(
            TypeAlgebraRow(
                help_target="discover",
                source_families=frozenset(discover_source_families),
                output_family="CandidateSet",
                is_terminal=False,
            )
        )

    if transform_source_families:
        rows.append(
            TypeAlgebraRow(
                help_target="transform",
                source_families=frozenset(transform_source_families),
                output_family="MetricFrame|DeltaFrame",
                is_terminal=False,
            )
        )

    # Governed-entry boundary rows.
    # These produce an artifact family from governed inputs and appear as
    # producer edges alongside the operator that produces the same family.
    for desc in descriptors:
        if not isinstance(desc, BoundaryCapability):
            continue
        if desc.direction != "governed_entry":
            continue
        source_families_gov: set[str] = set()
        for families in desc.accepted_inputs.values():
            source_families_gov.update(families)
        rows.append(
            TypeAlgebraRow(
                help_target=desc.help_target,
                source_families=frozenset(source_families_gov),
                output_family=desc.output_family,
                is_terminal=False,
            )
        )

    # Terminal boundary row (exactly once)
    to_pandas_desc = by_id.get("boundary.to_pandas")
    if to_pandas_desc is not None and isinstance(to_pandas_desc, BoundaryCapability):
        receiver_families = to_pandas_desc.accepted_inputs.get("receiver", frozenset())
        rows.append(
            TypeAlgebraRow(
                help_target="boundary.to_pandas",
                source_families=frozenset(receiver_families),
                output_family="pandas.DataFrame",
                is_terminal=True,
            )
        )

    return tuple(rows)


def _output_family_str(desc: OperatorCapability) -> str:
    """Return a string representation of an operator's output family."""
    output = desc.output_family
    if isinstance(output, SameAsInputFamily):
        return f"same as {output.parameter}"
    return str(output)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

REGISTRY: CapabilityRegistry = _build_registry()
