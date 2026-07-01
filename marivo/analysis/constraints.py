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

    METRIC_REF_REGISTERED = "metric_ref_registered"
    WINDOW_ABSOLUTE_PARSEABLE = "window_absolute_parseable"
    FRAME_KIND_COMPATIBLE = "frame_kind_compatible"
    DISCOVER_MINIMUM_EVIDENCE = "discover_minimum_evidence"
    ALIGNMENT_POLICY_SHAPE = "alignment_policy_shape"
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
    )


_EXAMPLE_BASE = "marivo/skills/marivo-analysis/references/examples"
_PITFALLS = "marivo/skills/marivo-analysis/references/pitfalls.md"
_CHEATSHEET = "marivo/skills/marivo-analysis/references/cheatsheet.md"
_DATASOURCE_DOC = "marivo/skills/marivo-semantic/references/datasource.md"

CONSTRAINTS: dict[ConstraintId, Constraint] = {
    ConstraintId.METRIC_REF_REGISTERED: _constraint(
        ConstraintId.METRIC_REF_REGISTERED,
        "MetricNotFound",
        "runtime",
        ("observe", "SemanticObject"),
        "Observed metrics must resolve to a registered semantic metric.",
        "Analysis frames are materialized from semantic metric ids; unresolved ids cannot produce SQL.",
        "Use session.catalog.get('metric.<model.metric>') and confirm the id with session.catalog.list(...).",
        example=f"{_EXAMPLE_BASE}/01_observe_single_window.py",
        docs_ref=_PITFALLS,
    ),
    ConstraintId.WINDOW_ABSOLUTE_PARSEABLE: _constraint(
        ConstraintId.WINDOW_ABSOLUTE_PARSEABLE,
        "WindowInvalid",
        "runtime",
        ("observe", "forecast", "transform", "TimeScope", "AbsoluteWindow"),
        "Windows and time scopes must be explicit, parseable absolute ranges.",
        "Analysis persistence records concrete bucket ranges and cannot infer an ambiguous natural-language window.",
        'Pass time_scope={"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} or an AbsoluteWindow.',
        example=f"{_EXAMPLE_BASE}/01_observe_single_window.py",
        docs_ref=_PITFALLS,
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
        example=f"{_EXAMPLE_BASE}/99_pitfall_pass_delta_to_compare.py",
        docs_ref=_PITFALLS,
    ),
    ConstraintId.DISCOVER_MINIMUM_EVIDENCE: _constraint(
        ConstraintId.DISCOVER_MINIMUM_EVIDENCE,
        "DiscoverInsufficientData",
        "runtime",
        ("discover", "CandidateSet"),
        "Discovery objectives need enough rows or buckets to rank candidates.",
        "Candidate scoring needs a minimum evidence set; too few observations make rankings misleading.",
        "Use a wider time_scope or choose a discovery objective compatible with the source shape.",
        example=f"{_EXAMPLE_BASE}/04_discover_point_anomaly.py",
        docs_ref=_PITFALLS,
    ),
    ConstraintId.ALIGNMENT_POLICY_SHAPE: _constraint(
        ConstraintId.ALIGNMENT_POLICY_SHAPE,
        "AlignmentPolicyValidation",
        "runtime",
        ("compare", "hypothesis_test", "AlignmentPolicy", "alignment", "calendar"),
        "AlignmentPolicy arguments must match the selected alignment kind.",
        "Calendar-backed variants require a calendar, while window_bucket uses request-window buckets without one.",
        "Use kind='window_bucket' without calendar, or pass calendar=mv.CalendarRef(...) for calendar-backed kinds.",
        example=f"{_EXAMPLE_BASE}/02_compare_yoy.py",
        docs_ref=_PITFALLS,
    ),
    ConstraintId.TRANSFORM_ARGUMENTS: _constraint(
        ConstraintId.TRANSFORM_ARGUMENTS,
        "TransformArg",
        "runtime",
        ("transform", "MetricFrame", "DeltaFrame"),
        "Transform operators require their documented keyword arguments.",
        "Each transform op has a specific parameter contract; missing or contradictory kwargs produce ambiguous frames.",
        "Inspect mv.help('transform') and pass the required args for the selected op.",
        docs_ref=_CHEATSHEET,
    ),
    ConstraintId.TRANSFORM_FRAME_SHAPE: _constraint(
        ConstraintId.TRANSFORM_FRAME_SHAPE,
        "TransformShape",
        "runtime",
        ("transform", "MetricFrame", "DeltaFrame"),
        "Transform operators require compatible axes and value columns.",
        "Shape-changing transforms can only preserve lineage when the requested axes exist on the frame.",
        "Use frame.columns, frame.meta.axes, or mv.help('transform') before topk, rollup, slice, or rank.",
        docs_ref=_CHEATSHEET,
    ),
    ConstraintId.TRANSFORM_OPERATOR_SUPPORTED: _constraint(
        ConstraintId.TRANSFORM_OPERATOR_SUPPORTED,
        "TransformOp",
        "runtime",
        ("transform",),
        "Transform op names are limited to the supported v1 operator set.",
        "The runtime records transform lineage by stable op id; unknown ops cannot be replayed.",
        "Use filter, slice, rollup, topk, bottomk, rank, normalize, or window.",
        docs_ref=_CHEATSHEET,
    ),
    ConstraintId.FORECAST_INPUT_SHAPE: _constraint(
        ConstraintId.FORECAST_INPUT_SHAPE,
        "ForecastShapeUnsupported",
        "runtime",
        ("forecast", "ForecastFrame", "MetricFrame"),
        "Forecast accepts MetricFrame time_series or panel inputs.",
        "Forecast models need ordered history buckets and cannot operate on scalar or segmented-only frames.",
        "Observe the metric with a grain and enough history before calling session.forecast(...).",
        docs_ref=_PITFALLS,
    ),
    ConstraintId.QUALITY_TARGET_SHAPE: _constraint(
        ConstraintId.QUALITY_TARGET_SHAPE,
        "QualityShapeUnsupported",
        "runtime",
        ("assess_quality", "QualityReport", "MetricFrame"),
        "Quality assessment v1 accepts MetricFrame targets.",
        "Quality checks are currently defined against observed metric frames and their metric metadata.",
        "Call session.assess_quality(metric_frame) on an observe result.",
        docs_ref=_PITFALLS,
    ),
    ConstraintId.FRAME_IMMUTABLE: _constraint(
        ConstraintId.FRAME_IMMUTABLE,
        "FrameMutation",
        "runtime",
        ("BaseFrame", "MetricFrame", "DeltaFrame", "AttributionFrame", "CandidateSet"),
        "Persisted frames are immutable through the analysis wrapper.",
        "Lineage and persisted metadata assume frame contents do not change after materialization.",
        "Call frame.to_pandas() and mutate the copy when ad hoc analysis needs local changes.",
        docs_ref=_CHEATSHEET,
    ),
    ConstraintId.FRAME_READ_BOUNDS: _constraint(
        ConstraintId.FRAME_READ_BOUNDS,
        "FrameRead",
        "runtime",
        ("BaseFrame", "MetricFrame", "DeltaFrame", "CandidateSet"),
        "Frame read helpers enforce bounded inspection arguments.",
        "Help and show APIs should stay small enough for agents and terminals.",
        "Use frame.show() for bounded inspection, or frame.to_pandas() for terminal custom analysis.",
        docs_ref=_CHEATSHEET,
    ),
    ConstraintId.BACKEND_FACTORY_CONFIGURED: _constraint(
        ConstraintId.BACKEND_FACTORY_CONFIGURED,
        "NoBackendFactory",
        "runtime",
        ("session", "observe", "datasources"),
        "Materializing analysis intents need a configured ibis backend factory.",
        "Observe and related intents need a live backend to compile and execute semantic metrics.",
        "Register a datasource and use mv.session.get_or_create(name=...), or pass an explicit backend override.",
        example=f"{_EXAMPLE_BASE}/00_real_project_template.py",
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
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.COMPONENT_FRAME_AVAILABLE: _constraint(
        ConstraintId.COMPONENT_FRAME_AVAILABLE,
        "ComponentFrameUnavailable",
        "runtime",
        ("MetricFrame", "DeltaFrame", "ComponentFrame", "components"),
        "Component frames exist only for component-aware derived metric results.",
        "Base sum metrics and non-derived frames have no linked component_ref to load.",
        "Call frame.components() only when frame.meta.component_ref is present.",
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
