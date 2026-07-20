"""Runtime family gate driven by the registry ``accepted_inputs``.

This module owns the single runtime classification and acceptance check for
all family-bearing public entrypoints.  Capability-specific validators
(shape, arity, alignment, cumulative, semantic identity, policy) run AFTER
this gate and must not duplicate family-acceptance logic.

All names are private to ``marivo.analysis``.  Nothing is added to
``marivo/analysis/__init__.py``.
"""

from __future__ import annotations

from marivo.analysis._capabilities.model import (
    BoundaryCapability,
    OperatorCapability,
)
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.telemetry import staged

# ---------------------------------------------------------------------------
# Type classification
# ---------------------------------------------------------------------------

# Lazy imports to avoid circular dependencies at module load time.


def _classify_frame(value: object) -> str | None:
    """Classify a frame instance by its type name.

    Returns the family string (e.g. ``"MetricFrame"``) or ``None`` if the
    value is not a recognized frame type.
    """

    from marivo.analysis.frames.association import AssociationResult
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.candidate import CandidateSet
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.frames.coverage import CoverageFrame
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport

    if isinstance(value, MetricFrame):
        return "MetricFrame"
    if isinstance(value, DeltaFrame):
        return "DeltaFrame"
    if isinstance(value, AttributionFrame):
        return "AttributionFrame"
    if isinstance(value, ForecastFrame):
        return "ForecastFrame"
    if isinstance(value, QualityReport):
        return "QualityReport"
    if isinstance(value, CandidateSet):
        return "CandidateSet"
    if isinstance(value, AssociationResult):
        return "AssociationResult"
    if isinstance(value, HypothesisTestResult):
        return "HypothesisTestResult"
    if isinstance(value, ComponentFrame):
        return "ComponentFrame"
    if isinstance(value, CoverageFrame):
        return "CoverageFrame"
    return None


def _classify_semantic_ref(value: object) -> str | None:
    """Classify one exact Ref by its semantic kind.

    Returns the family string (e.g. ``"MetricSemantic"``) or ``None`` if the
    value is not a recognized semantic object.
    """

    from marivo.refs import Ref, SemanticKind

    if type(value) is not Ref:
        return None
    kind = value.kind
    if kind == SemanticKind.METRIC:
        return "MetricSemantic"
    if kind == SemanticKind.DIMENSION:
        return "DimensionSemantic"
    if kind == SemanticKind.TIME_DIMENSION:
        return "TimeDimensionSemantic"
    return None


def _classify_runtime_metric(value: object) -> str | None:
    from marivo.analysis.runtime_metric import (
        RuntimeAggregateExpr,
        RuntimeRatioExpr,
        RuntimeSliceExpr,
    )

    if isinstance(value, RuntimeAggregateExpr | RuntimeSliceExpr | RuntimeRatioExpr):
        return "RuntimeMetricExpression"
    return None


def _classify_policy_or_spec(value: object) -> str | None:
    """Classify policy, sampling, time-scope, query-spec, and column-binding values."""

    from marivo.analysis.policies import AlignmentPolicy, SamplingPolicy
    from marivo.analysis.windows.spec import AbsoluteWindow, TimeScope

    if isinstance(value, AlignmentPolicy):
        return "AlignmentPolicy"
    if isinstance(value, SamplingPolicy):
        return "SamplingPolicy"
    if isinstance(value, (TimeScope, AbsoluteWindow)):
        return "TimeScopeInput"
    # A plain dict is acceptable as a TimeScopeInput (normalized later by
    # the capability-specific validator, which may reject relative windows).
    if isinstance(value, dict):
        return "TimeScopeInput"
    return None


def classify_input_family(value: object) -> str:
    """Classify a runtime value into a registry input family string.

    Parameters
    ----------
    value:
        The runtime value to classify (frame, semantic ref, policy, etc.).

    Returns
    -------
    str
        One normalized family string matching a member of ``InputFamily``.

    Raises
    ------
    AnalysisError
        When the value's type does not map to any registered input family.
    """

    # Frames first (most common path).
    frame_family = _classify_frame(value)
    if frame_family is not None:
        return frame_family

    # Semantic refs and catalog objects.
    semantic_family = _classify_semantic_ref(value)
    if semantic_family is not None:
        return semantic_family

    runtime_metric_family = _classify_runtime_metric(value)
    if runtime_metric_family is not None:
        return runtime_metric_family

    # Policies, time scopes, query specs, column bindings.
    policy_family = _classify_policy_or_spec(value)
    if policy_family is not None:
        return policy_family

    # Lists of semantic refs (e.g. axes, search_space) — classify by the
    # first element if non-empty.
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        elem_family = _classify_semantic_ref(first)
        if elem_family is not None:
            return elem_family

    raise AnalysisError(
        message=(
            f"Input value of type {type(value).__name__} does not map to any "
            "registered analysis input family."
        ),
        expected="a registered frame, semantic ref, policy, or scope value",
        received=type(value).__name__,
        location="validate_capability_inputs",
        repair=AnalysisRepair(
            kind="inspect",
            action=(
                "Pass a typed Marivo artifact, semantic catalog object/ref, "
                "alignment policy, sampling policy, or time scope."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="help"),
        ),
    )


# ---------------------------------------------------------------------------
# Shared gate
# ---------------------------------------------------------------------------


@staged("validate")
def validate_capability_inputs(capability_id: str, **kwargs: object) -> None:
    """Validate that each family-bearing argument matches the registry.

    Looks up the descriptor by ``capability_id`` from :data:`REGISTRY`,
    classifies each input value, and raises :class:`AnalysisError` when the
    classified family is not in the descriptor's ``accepted_inputs`` set for
    that parameter.

    Parameters
    ----------
    capability_id:
        Canonical capability id (e.g. ``"compare"``, ``"transform.filter"``).
    **kwargs:
        Family-bearing arguments keyed by the registry's parameter name
        (e.g. ``a=``, ``b=``, ``alignment=``).  Parameters not declared in
        ``accepted_inputs`` are silently ignored — the gate binds only
        registered public input-bearing parameters.

    Raises
    ------
    AnalysisError
        When a family-bearing argument's classified family is not in the
        accepted set.  The error carries ``location`` as
        ``"{capability_id}.{parameter}"`` and ``repair.help_target`` matching
        the descriptor's ``help_target``.
    """

    descriptor = REGISTRY.by_id(capability_id)

    # Only OperatorCapability and BoundaryCapability have accepted_inputs.
    if not isinstance(descriptor, (OperatorCapability, BoundaryCapability)):
        return

    accepted_inputs = descriptor.accepted_inputs

    for param_name, accepted_families in accepted_inputs.items():
        if param_name not in kwargs:
            continue
        value = kwargs[param_name]
        if value is None:
            continue
        # Skip empty lists/tuples — arity checks are capability-specific
        # validators that run AFTER this gate.
        if isinstance(value, (list, tuple)) and not value:
            continue

        values = value if isinstance(value, (list, tuple)) else (value,)
        actual_families: list[str] = []
        for item in values:
            try:
                actual_families.append(classify_input_family(item))
            except AnalysisError:
                actual_families.append(type(item).__name__)

        rejected_family = next(
            (family for family in actual_families if family not in accepted_families),
            None,
        )
        if rejected_family is not None:
            accepted_str = " | ".join(sorted(accepted_families))
            raise AnalysisError(
                message=(
                    f"{capability_id} parameter {param_name!r} expected "
                    f"{accepted_str}, received {rejected_family}."
                ),
                expected=accepted_str,
                received=rejected_family,
                location=f"{capability_id}.{param_name}",
                repair=AnalysisRepair(
                    kind="retry",
                    action=(f"Pass a value whose family is one of: {accepted_str}."),
                    help_target=LiveHelpTarget(
                        surface="analysis", canonical_id=descriptor.help_target
                    ),
                ),
            )
