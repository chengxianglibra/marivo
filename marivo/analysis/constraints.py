"""Constraint catalog for ``marivo.analysis`` runtime and help surfaces."""

from __future__ import annotations

from enum import StrEnum

from marivo.introspection.constraints import Constraint, Phase

__all__ = [
    "CONSTRAINTS",
    "Constraint",
    "ConstraintId",
    "constraints_for_error_kind",
    "constraints_for_symbol",
    "default_constraint_for_error_kind",
    "default_hint_for_error_kind",
    "get_constraint",
    "iter_constraints",
]


class ConstraintId(StrEnum):
    """Stable identifiers for analysis constraints."""

    METRIC_EXPRESSION_RESOLVABLE = "metric_expression_resolvable"
    WINDOW_ABSOLUTE_PARSEABLE = "window_absolute_parseable"
    FRAME_KIND_COMPATIBLE = "frame_kind_compatible"
    DISCOVER_MINIMUM_EVIDENCE = "discover_minimum_evidence"
    ALIGNMENT_POLICY_SHAPE = "alignment_policy_shape"
    CORRELATE_LAG_SEMANTICS = "correlate_lag_semantics"
    TRANSFORM_ARGUMENTS = "transform_arguments"
    TRANSFORM_FRAME_SHAPE = "transform_frame_shape"
    TRANSFORM_OPERATOR_SUPPORTED = "transform_operator_supported"
    FORECAST_INPUT_SHAPE = "forecast_input_shape"
    QUALITY_TARGET_SHAPE = "quality_target_shape"
    FRAME_IMMUTABLE = "frame_immutable"
    FRAME_READ_BOUNDS = "frame_read_bounds"
    BACKEND_FACTORY_CONFIGURED = "backend_factory_configured"
    DATASOURCE_CONFIGURED = "datasource_configured"
    DATASOURCE_ENV_AVAILABLE = "datasource_env_available"
    DATASOURCE_BACKEND_SUPPORTED = "datasource_backend_supported"
    COMPONENT_FRAME_AVAILABLE = "component_frame_available"
    ATTRIBUTION_ADDITIVITY_COMPATIBLE = "attribution_additivity_compatible"
    ATTRIBUTION_AXIS_COLUMN_COMPATIBLE = "attribution_axis_column_compatible"
    ATTRIBUTION_RECONCILIATION = "attribution_reconciliation"
    CUMULATIVE_COMPARE_COMPATIBLE = "cumulative_compare_compatible"
    CUMULATIVE_ATTRIBUTION_UNSUPPORTED = "cumulative_attribution_unsupported"
    RUNTIME_METRIC_CLOSED_ALGEBRA = "runtime_metric_closed_algebra"
    RUNTIME_WEIGHTED_MEAN_VALID = "runtime_weighted_mean_valid"


_DATASOURCE_DOC = "site/src/content/docs/en/latest/concepts/semantic-layer.mdx"


def _constraint(
    id: ConstraintId,
    error_kind: str,
    phase: Phase,
    applies_to: tuple[str, ...],
    title: str,
    why: str,
    hint: str,
    *,
    example: str | None = None,
    docs_ref: str | None = None,
    help_target: str | None = None,
) -> Constraint:
    return Constraint(
        id=id.value,
        error_kind=error_kind,
        phase=phase,
        applies_to=applies_to,
        title=title,
        why=why,
        hint=hint,
        example=example,
        docs_ref=docs_ref,
        help_target=help_target,
    )


CONSTRAINTS: dict[ConstraintId, Constraint] = {
    ConstraintId.METRIC_EXPRESSION_RESOLVABLE: _constraint(
        ConstraintId.METRIC_EXPRESSION_RESOLVABLE,
        "MetricNotFound",
        "runtime",
        ("observe", "Ref[metric]", "RuntimeMetricExpr"),
        "Every metric-expression leaf must resolve to an analysis-ready governed ref.",
        "Catalog metric refs and runtime expressions share one graph planner; unresolved or unready leaves cannot produce a typed frame.",
        "Pass an exact catalog Ref[metric] or build a closed expression with mv.runtime_metric.* from exact Ref[measure]/Ref[metric] operands.",
        help_target="observe",
    ),
    ConstraintId.WINDOW_ABSOLUTE_PARSEABLE: _constraint(
        ConstraintId.WINDOW_ABSOLUTE_PARSEABLE,
        "WindowInvalid",
        "runtime",
        ("observe", "forecast", "transform", "TimeScope", "AbsoluteWindow"),
        "Windows and time scopes must be explicit, parseable absolute ranges.",
        "Analysis persistence records concrete bucket ranges and cannot infer an ambiguous natural-language window.",
        'Pass time_scope={"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} or an AbsoluteWindow.',
        help_target="observe",
    ),
    ConstraintId.FRAME_KIND_COMPATIBLE: _constraint(
        ConstraintId.FRAME_KIND_COMPATIBLE,
        "SemanticKindMismatch",
        "runtime",
        (
            "compare",
            "attribute",
            "decompose",
            "discover",
            "select",
            "correlate",
            "hypothesis_test",
            "MetricFrame",
            "DeltaFrame",
            "CandidateSet",
            "SemanticKindMismatchError",
        ),
        "Intent inputs must match the required frame family and semantic shape.",
        "Each intent consumes a bounded frame contract; accepting the wrong family silently would corrupt follow-up lineage.",
        "Check frame.meta.kind, frame.semantic_shape, or CandidateSet.meta.shape before narrowing or dispatching.",
        help_target="compare",
    ),
    ConstraintId.DISCOVER_MINIMUM_EVIDENCE: _constraint(
        ConstraintId.DISCOVER_MINIMUM_EVIDENCE,
        "DiscoverInsufficientData",
        "runtime",
        ("discover", "CandidateSet"),
        "Discovery objectives need enough rows or buckets to rank candidates.",
        "Candidate scoring needs a minimum evidence set; too few observations make rankings misleading.",
        "Use a wider time_scope or choose a discovery objective compatible with the source shape.",
        help_target="discover",
    ),
    ConstraintId.ALIGNMENT_POLICY_SHAPE: _constraint(
        ConstraintId.ALIGNMENT_POLICY_SHAPE,
        "AlignmentPolicyValidation",
        "runtime",
        ("compare", "hypothesis_test", "AlignmentPolicy", "alignment", "calendar"),
        "AlignmentPolicy arguments must match the selected alignment kind.",
        "Calendar-backed variants require a calendar, while window_bucket uses request-window buckets without one.",
        "Use kind='window_bucket' without calendar, or pass calendar=mv.CalendarRef(...) for calendar-backed kinds.",
        help_target="alignment",
    ),
    ConstraintId.CORRELATE_LAG_SEMANTICS: _constraint(
        ConstraintId.CORRELATE_LAG_SEMANTICS,
        "AlignmentFailed",
        "runtime",
        ("correlate", "AssociationResult"),
        "Signed lag k pairs a[t] with b[t+k]: positive means a leads b, negative means b leads a, and lag 0 is the default. Non-zero lags require time_series or panel frames; panel lag shifts stay within each dimension series, and null pairs are dropped after shifting.",
        "Per-series shifting preserves panel boundaries and missing bucket positions.",
        "Pass a signed lag_range whose offsets leave at least two overlapping, non-constant pairs, such as range(-3, 4).",
        help_target="correlate",
    ),
    ConstraintId.TRANSFORM_ARGUMENTS: _constraint(
        ConstraintId.TRANSFORM_ARGUMENTS,
        "TransformArg",
        "runtime",
        ("transform", "MetricFrame", "DeltaFrame"),
        "Transform operators require their documented keyword arguments.",
        "Each transform op has a specific parameter contract; missing or contradictory kwargs produce ambiguous frames.",
        "Inspect mv.help('transform') and pass the required args for the selected op.",
        help_target="transform",
    ),
    ConstraintId.TRANSFORM_FRAME_SHAPE: _constraint(
        ConstraintId.TRANSFORM_FRAME_SHAPE,
        "TransformShape",
        "runtime",
        ("transform", "MetricFrame", "DeltaFrame"),
        "Transform operators require compatible axes and value columns.",
        "Shape-changing transforms can only preserve lineage when the requested axes exist on the frame.",
        "Use frame.columns, frame.meta.axes, or mv.help('transform') before topk, rollup, slice, or rank.",
        help_target="transform",
    ),
    ConstraintId.TRANSFORM_OPERATOR_SUPPORTED: _constraint(
        ConstraintId.TRANSFORM_OPERATOR_SUPPORTED,
        "TransformOp",
        "runtime",
        ("transform",),
        "Transform op names are limited to the supported v1 operator set.",
        "The runtime records transform lineage by stable op id; unknown ops cannot be replayed.",
        "Use filter, slice, rollup, topk, bottomk, rank, normalize, or window.",
        help_target="transform",
    ),
    ConstraintId.FORECAST_INPUT_SHAPE: _constraint(
        ConstraintId.FORECAST_INPUT_SHAPE,
        "ForecastShapeUnsupported",
        "runtime",
        ("forecast", "ForecastFrame", "MetricFrame"),
        "Forecast accepts MetricFrame time_series or panel inputs.",
        "Forecast models need ordered history buckets and cannot operate on scalar or segmented-only frames.",
        "Observe the metric with a grain and enough history before calling session.forecast(...).",
        help_target="forecast",
    ),
    ConstraintId.QUALITY_TARGET_SHAPE: _constraint(
        ConstraintId.QUALITY_TARGET_SHAPE,
        "QualityShapeUnsupported",
        "runtime",
        ("assess_quality", "QualityReport", "MetricFrame"),
        "Quality assessment v1 accepts MetricFrame targets.",
        "Quality checks are currently defined against observed metric frames and their metric metadata.",
        "Call session.assess_quality(metric_frame) on an observe result.",
        help_target="assess_quality",
    ),
    ConstraintId.FRAME_IMMUTABLE: _constraint(
        ConstraintId.FRAME_IMMUTABLE,
        "FrameMutation",
        "runtime",
        ("BaseFrame", "MetricFrame", "DeltaFrame", "AttributionFrame", "CandidateSet"),
        "Persisted frames are immutable through the analysis wrapper.",
        "Lineage and persisted metadata assume frame contents do not change after materialization.",
        "Call frame.to_pandas() and mutate the copy when ad hoc analysis needs local changes.",
        help_target="boundary.to_pandas",
    ),
    ConstraintId.FRAME_READ_BOUNDS: _constraint(
        ConstraintId.FRAME_READ_BOUNDS,
        "FrameRead",
        "runtime",
        ("BaseFrame", "MetricFrame", "DeltaFrame", "CandidateSet"),
        "Frame read helpers enforce bounded inspection arguments.",
        "Help and show APIs should stay small enough for agents and terminals.",
        "Use frame.show() for bounded inspection, or frame.to_pandas() for terminal custom analysis.",
        help_target="artifacts",
    ),
    ConstraintId.BACKEND_FACTORY_CONFIGURED: _constraint(
        ConstraintId.BACKEND_FACTORY_CONFIGURED,
        "NoBackendFactory",
        "runtime",
        ("session", "observe", "datasources"),
        "Materializing analysis intents need a configured ibis backend factory.",
        "Observe and related intents need a live backend to compile and execute semantic metrics.",
        "Register a datasource and use mv.session.get_or_create(name=...), or pass an explicit backend override.",
        help_target="datasources",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_CONFIGURED: _constraint(
        ConstraintId.DATASOURCE_CONFIGURED,
        "DatasourceMissing",
        "runtime",
        ("datasources", "session", "observe"),
        "Named datasources must exist before analysis runtime lookup.",
        "Datasource-backed sessions resolve semantic source refs through persisted datasource metadata.",
        "Register the datasource with md.register(...) before creating or attaching the session.",
        help_target="datasources",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_ENV_AVAILABLE: _constraint(
        ConstraintId.DATASOURCE_ENV_AVAILABLE,
        "DatasourceEnvVarMissing",
        "runtime",
        ("datasources", "session"),
        "Datasource secret environment variables must be available at runtime.",
        "The datasource contract stores secret references, not plaintext credentials.",
        "Export the referenced environment variable or validate and remember it with md.test(...).",
        help_target="datasources",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_BACKEND_SUPPORTED: _constraint(
        ConstraintId.DATASOURCE_BACKEND_SUPPORTED,
        "DatasourceBackendTypeUnsupported",
        "runtime",
        ("datasources", "session"),
        "Datasource backend_type must have a registered backend adapter.",
        "The analysis runtime can only create ibis connections for supported datasource backend types.",
        "Use a supported backend_type or add an adapter before relying on datasource auto-loading.",
        help_target="datasources",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.COMPONENT_FRAME_AVAILABLE: _constraint(
        ConstraintId.COMPONENT_FRAME_AVAILABLE,
        "ComponentFrameUnavailable",
        "runtime",
        ("MetricFrame", "DeltaFrame", "ComponentFrame", "components"),
        "Observed MetricFrames require a persisted recursive component graph.",
        "Every graph root retains evaluator, child-role, quality, coverage, presentation, and governed-leaf lineage state.",
        "Re-run session.observe(...) when component_graph_ref is absent or its ComponentFrame cannot be loaded.",
        help_target="artifacts",
    ),
    ConstraintId.ATTRIBUTION_ADDITIVITY_COMPATIBLE: _constraint(
        ConstraintId.ATTRIBUTION_ADDITIVITY_COMPATIBLE,
        "AttributionAdditivity",
        "runtime",
        (
            "attribute",
            "decompose",
            "DeltaFrame",
            "AttributionAdditivityError",
        ),
        "Axis attribution requires compatible persisted additivity: additive, semi-additive "
        "off the status time axis, component-aware ratio/weighted-mean, or a Tier-1 mean "
        "lowered to sum/count_non_null components. If a DeltaFrame reports "
        "attribution_shape=weighted_mix lowered_from=mean, call attribute directly: its "
        "Tier-1 mean is already lowered to sum/count_non_null components, so do not manually "
        "split numerator and denominator. For other unsupported non-additive metrics, "
        "re-observe or attribute additive numerator and denominator separately.",
        "Axis-sum attribution is valid for additive metrics, semi-additive metrics away "
        "from their status time axis, component-aware ratio or weighted-mean deltas, and "
        "Tier-1 means with persisted sum/count_non_null components.",
        "Re-observe and compare old artifacts; model non-additive metrics as ratio or "
        "weighted_mean components, or attribute additive numerator and denominator separately.",
        help_target="attribute",
    ),
    ConstraintId.ATTRIBUTION_RECONCILIATION: _constraint(
        ConstraintId.ATTRIBUTION_RECONCILIATION,
        "AttributionReconciliation",
        "runtime",
        ("attribute", "decompose", "AttributionFrame", "ComponentDecompositionError"),
        "Attribution emits share_of_total_delta plus positive- and negative-pool shares; "
        "new and churned component rows keep exact one-sided contributions, and every "
        "deepest partition must reconcile to its independently computed total delta.",
        "The AttributionFrame show card reports total, contribution, one-sided, "
        "unattributed, and residual reconciliation facts instead of hiding missing rows "
        "behind a normalized percentage.",
        "Inspect the reconciliation card and component rows; repair invalid component "
        "weights or inputs before retrying if attribution fails closed.",
        help_target="attribute",
    ),
    ConstraintId.ATTRIBUTION_AXIS_COLUMN_COMPATIBLE: _constraint(
        ConstraintId.ATTRIBUTION_AXIS_COLUMN_COMPATIBLE,
        "SemanticKindMismatch",
        "runtime",
        ("attribute", "decompose", "AttributionFrame"),
        "Single-axis attribution preserves the resolved dimension name only when it does "
        "not collide with attribution result, value, or panel bucket columns.",
        "A flat pandas result cannot represent a dimension and an attribution measure under "
        "the same column name; evidence fields use an explicit metadata mapping instead.",
        "Rename the semantic dimension to a non-reserved name, reload the catalog, then "
        "re-observe and compare before retrying attribution.",
        help_target="attribute",
    ),
    ConstraintId.CUMULATIVE_COMPARE_COMPATIBLE: _constraint(
        ConstraintId.CUMULATIVE_COMPARE_COMPATIBLE,
        "CumulativeFrameUnsupported",
        "runtime",
        ("compare", "MetricFrame", "DeltaFrame"),
        "Cumulative compare requires matching trailing or grain_to_date anchors; derived "
        "frames require every outer component to share that anchor; all_history is rejected.",
        "A derived cumulative frame is comparable only when every outer component is "
        "cumulative and all components share the same trailing or grain_to_date anchor; "
        "all_history and mixed or unresolved component anchors are rejected.",
        "Re-observe both sides from the same metric contract, or compare compatible "
        "underlying flow or cumulative component metrics separately.",
        help_target="compare",
    ),
    ConstraintId.CUMULATIVE_ATTRIBUTION_UNSUPPORTED: _constraint(
        ConstraintId.CUMULATIVE_ATTRIBUTION_UNSUPPORTED,
        "CumulativeFrameUnsupported",
        "runtime",
        ("attribute", "decompose", "DeltaFrame"),
        "Cumulative deltas cannot be attributed or decomposed; use underlying flow metrics.",
        "The hard gate applies to direct cumulative metrics and derived cumulative "
        "wrappers even when compare was allowed.",
        "Attribute or decompose the underlying flow metrics separately.",
        help_target="attribute",
    ),
    ConstraintId.RUNTIME_METRIC_CLOSED_ALGEBRA: _constraint(
        ConstraintId.RUNTIME_METRIC_CLOSED_ALGEBRA,
        "SemanticKindMismatch",
        "runtime",
        (
            "runtime_metric.aggregate",
            "runtime_metric.slice",
            "runtime_metric.weighted_mean",
            "runtime_metric.ratio",
            "RuntimeMetricExpression",
        ),
        "Runtime metrics use a closed recursive algebra over governed refs.",
        "Closed descriptors preserve typed planning, replay, lineage, units, and quality facts without creating catalog authority.",
        "Use exact Ref[measure], Ref[metric], Ref[dimension], or Ref[time_dimension] values and materialize the descriptor only through session.observe(...).",
        help_target="runtime_metric",
    ),
    ConstraintId.RUNTIME_WEIGHTED_MEAN_VALID: _constraint(
        ConstraintId.RUNTIME_WEIGHTED_MEAN_VALID,
        "MetricShapeUnsupported",
        "runtime",
        ("runtime_metric.weighted_mean", "RuntimeWeightedMeanExpr"),
        "Runtime weighted means require same-entity measures and an additive weight.",
        "The value and weight must be evaluated on the same physical rows so null pairing and row-level multiplication remain exact.",
        "Pass exact loaded Ref[measure] values from the same entity and choose an additive measure for weight.",
        help_target="runtime_metric",
    ),
}

_DEFAULT_BY_ERROR_KIND: dict[str, str] = {}
for _constraint_obj in CONSTRAINTS.values():
    _DEFAULT_BY_ERROR_KIND.setdefault(_constraint_obj.error_kind, _constraint_obj.id)


def get_constraint(id: ConstraintId | str) -> Constraint | None:
    """Return a constraint by id."""

    try:
        constraint_id = id if isinstance(id, ConstraintId) else ConstraintId(id)
    except ValueError:
        return None
    return CONSTRAINTS.get(constraint_id)


def iter_constraints() -> tuple[Constraint, ...]:
    """Return all constraints in declaration order."""

    return tuple(CONSTRAINTS.values())


def constraints_for_symbol(symbol: str) -> tuple[Constraint, ...]:
    """Return constraints whose applies_to includes *symbol*."""

    return tuple(c for c in CONSTRAINTS.values() if symbol in c.applies_to)


def constraints_for_error_kind(error_kind: str) -> tuple[Constraint, ...]:
    """Return constraints that map to an analysis error kind."""

    return tuple(c for c in CONSTRAINTS.values() if c.error_kind == error_kind)


def default_constraint_for_error_kind(error_kind: str) -> Constraint | None:
    """Return the default constraint for an analysis error kind."""

    constraint_id = _DEFAULT_BY_ERROR_KIND.get(error_kind)
    if constraint_id is None:
        return None
    return get_constraint(constraint_id)


def default_hint_for_error_kind(error_kind: str) -> str | None:
    """Return the catalog-backed default hint for an analysis error kind."""

    constraint = default_constraint_for_error_kind(error_kind)
    return constraint.hint if constraint is not None else None
