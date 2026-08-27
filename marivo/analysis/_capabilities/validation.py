"""Runtime family gate driven by the registry ``accepted_inputs``.

This module owns the single runtime classification and acceptance check for
all family-bearing public entrypoints.  Capability-specific validators
(shape, arity, alignment, cumulative, semantic identity, policy) run AFTER
this gate and must not duplicate family-acceptance logic.

All names are private to ``marivo.analysis``.  Nothing is added to
``marivo/analysis/__init__.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from marivo.analysis._capabilities.model import (
    ARTIFACT_FAMILIES,
    ArtifactAdmissionRule,
    BoundaryCapability,
    OperatorCapability,
)
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    ArtifactAuthorityUnknownError,
    ArtifactStaleError,
    EventCoverageUnknownError,
    FunnelAttributionUnsupportedError,
    InvalidEventMatchingPolicyError,
    InvalidSubjectAxisError,
    ModelStateMismatchError,
    PatternStepMismatchError,
    SubjectSetMismatchError,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.telemetry import staged

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

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
    from marivo.analysis.frames.event import EventFrame
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.lifecycle import LifecycleFrame
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.frames.subject import SubjectSet

    if isinstance(value, MetricFrame):
        return "MetricFrame"
    if isinstance(value, EventFrame):
        return "EventFrame"
    if isinstance(value, LifecycleFrame):
        return "LifecycleFrame"
    if isinstance(value, SubjectSet):
        return "SubjectSet"
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
    """Classify one exact Ref or loaded catalog entry by its semantic kind.

    Returns the family string (e.g. ``"MetricSemantic"``) or ``None`` if the
    value is not a recognized semantic object.
    """

    from marivo.refs import Ref, SemanticKind
    from marivo.semantic.catalog import CatalogEntry

    if type(value) is Ref or isinstance(value, CatalogEntry):
        kind = value.kind
    else:
        return None
    if kind == SemanticKind.METRIC:
        return "MetricSemantic"
    if kind == SemanticKind.DIMENSION:
        return "DimensionSemantic"
    if kind == SemanticKind.TIME_DIMENSION:
        return "TimeDimensionSemantic"
    if kind == SemanticKind.STATE_MODEL:
        return "StateModelSemantic"
    return None


def _classify_runtime_metric(value: object) -> str | None:
    from marivo.analysis.runtime_metric import (
        RuntimeAggregateExpr,
        RuntimeLinearExpr,
        RuntimeRatioExpr,
        RuntimeSliceExpr,
        RuntimeWeightedMeanExpr,
    )

    if isinstance(
        value,
        RuntimeAggregateExpr
        | RuntimeSliceExpr
        | RuntimeRatioExpr
        | RuntimeWeightedMeanExpr
        | RuntimeLinearExpr,
    ):
        return "RuntimeMetricExpression"
    return None


def _classify_semantic_candidate(value: object) -> str | None:
    from marivo.analysis.frames.candidate import OntologyMetricCandidate

    return "OntologyMetricCandidate" if type(value) is OntologyMetricCandidate else None


def _classify_policy_or_spec(value: object) -> str | None:
    """Classify policy, sampling, time-scope, query-spec, and column-binding values."""

    from marivo.analysis.event import (
        CompletenessDeclaration,
        EventPattern,
        EveryStart,
        FirstPerSubject,
    )
    from marivo.analysis.funnel import FunnelLossRate
    from marivo.analysis.lifecycle import FromInception, InState
    from marivo.analysis.policies import AlignmentPolicy, SamplingPolicy
    from marivo.analysis.subject import DroppedBefore
    from marivo.analysis.windows.spec import AbsoluteWindow, TimeScope

    if isinstance(value, AlignmentPolicy):
        return "AlignmentPolicy"
    if isinstance(value, SamplingPolicy):
        return "SamplingPolicy"
    if isinstance(value, (TimeScope, AbsoluteWindow)):
        return "TimeScopeInput"
    if isinstance(value, EventPattern):
        return "EventPattern"
    if isinstance(value, (FirstPerSubject, EveryStart)):
        return "EventMatchingPolicy"
    if isinstance(value, CompletenessDeclaration):
        return "CompletenessDeclaration"
    if isinstance(value, FromInception):
        return "LifecycleSeed"
    if isinstance(value, (DroppedBefore, InState)):
        return "SubjectSelection"
    if isinstance(value, FunnelLossRate):
        return "FunnelLossRate"
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

    candidate_family = _classify_semantic_candidate(value)
    if candidate_family is not None:
        return candidate_family

    if isinstance(value, Mapping) and value:
        key_families = {_classify_semantic_ref(key) for key in value}
        if None not in key_families and key_families <= {
            "DimensionSemantic",
            "TimeDimensionSemantic",
        }:
            return (
                "TimeDimensionSemantic"
                if key_families == {"TimeDimensionSemantic"}
                else "DimensionSemantic"
            )

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


@dataclass(frozen=True)
class ArtifactAdmissionResult:
    """One registry-owned artifact predicate evaluation."""

    allowed: bool
    expected: str | None = None
    received: str | None = None
    predicate: str | None = None


def _raise_typed_family_error(
    *,
    capability_id: str,
    param_name: str,
    received: str,
    help_target: str,
    artifact: object | None,
) -> None:
    """Preserve closed typed repair contracts at the shared family gate."""

    key = (capability_id, param_name)
    if key == ("attribute", "target"):
        raise FunnelAttributionUnsupportedError(
            message="attribute target must be an exact supported attribution target.",
            expected="mv.funnel_loss_rate(step=<non-initial PatternStep>)",
            received=received,
            location="session.attribute.target",
            repair=AnalysisRepair(
                kind="user_choice",
                action=(
                    "Choose a non-initial PatternStep from the compared funnel and "
                    "build mv.funnel_loss_rate(step=...)."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=help_target,
                ),
            ),
        )
    if key == ("events.funnel", "axes"):
        raise InvalidSubjectAxisError(
            message="events.funnel axes must be exact governed Dimension inputs.",
            expected="DimensionEntry | Ref[dimension]",
            received=received,
            location="session.events.funnel.axes",
            repair=AnalysisRepair(
                kind="user_choice",
                action="Choose an exact subject Dimension from the current catalog.",
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=help_target,
                ),
            ),
        )
    if key == ("select_subjects", "selection"):
        if _classify_frame(artifact) == "LifecycleFrame":
            raise ModelStateMismatchError(
                message="Lifecycle subject selection requires a typed state and instant.",
                expected=("mv.in_state(state=<ModelStateHandle>, as_of=<timezone-aware instant>)"),
                received=received,
                location="session.select_subjects.selection",
                repair=AnalysisRepair(
                    kind="user_choice",
                    action=(
                        "Choose an exact ModelStateHandle retained by the source history "
                        "and build mv.in_state(state=..., as_of=...)."
                    ),
                    help_target=LiveHelpTarget(
                        surface="analysis",
                        canonical_id=help_target,
                    ),
                ),
            )
        raise PatternStepMismatchError(
            message="select_subjects requires a closed typed subject selection.",
            expected="mv.dropped_before(step=<PatternStep>)",
            received=received,
            location="session.select_subjects.selection",
            repair=AnalysisRepair(
                kind="user_choice",
                action=(
                    "Choose an exact non-initial PatternStep from the source journey "
                    "and build mv.dropped_before(step=...)."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=help_target,
                ),
            ),
        )
    if key in {
        ("events.match", "cohort"),
        ("lifecycle.replay", "cohort"),
        ("observe", "cohort"),
    }:
        raise SubjectSetMismatchError(
            message=f"{capability_id} cohort must be an exact persisted SubjectSet.",
            expected="SubjectSet",
            received=received,
            location=f"session.{capability_id}.cohort",
            repair=AnalysisRepair(
                kind="inspect",
                action=("Pass the persisted SubjectSet returned by session.select_subjects(...)."),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=help_target,
                ),
            ),
        )
    if key in {
        ("events.funnel", "journeys"),
        ("events.time_to_event", "journeys"),
        ("select_subjects", "artifact"),
    }:
        argument = "journeys" if capability_id.startswith("events.") else "artifact"
        raise SubjectSetMismatchError(
            message=f"{capability_id} requires a canonical EventFrame[journey].",
            expected="EventFrame semantic_kind='journey'",
            received=received,
            location=f"session.{capability_id}.{argument}",
            repair=AnalysisRepair(
                kind="inspect",
                action=("Pass the persisted source journey returned by session.events.match(...)."),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=help_target,
                ),
            ),
        )


def _artifact_fact(value: object, attribute: str) -> str | None:
    meta = getattr(value, "meta", None)
    fact = getattr(meta, attribute, None)
    return fact if isinstance(fact, str) and fact else None


def _matching_kind(value: object) -> str | None:
    meta = getattr(value, "meta", None)
    matching = getattr(meta, "matching", None)
    kind = getattr(matching, "kind", None)
    return kind if isinstance(kind, str) and kind else None


def evaluate_artifact_admission(
    capability_id: str,
    parameter: str,
    value: object,
) -> ArtifactAdmissionResult:
    """Evaluate the descriptor's closed artifact predicates for one value."""
    descriptor = REGISTRY.by_id(capability_id)
    if not isinstance(descriptor, OperatorCapability):
        return ArtifactAdmissionResult(allowed=True)
    rule: ArtifactAdmissionRule | None = descriptor.artifact_admission.get(parameter)
    if rule is None:
        return ArtifactAdmissionResult(allowed=True)
    try:
        family = classify_input_family(value)
    except AnalysisError:
        return ArtifactAdmissionResult(allowed=True)
    if family not in ARTIFACT_FAMILIES:
        return ArtifactAdmissionResult(allowed=True)
    artifact_family = family

    shapes = rule.semantic_shapes.get(artifact_family)
    if shapes:
        received = _artifact_fact(value, "semantic_kind")
        if received not in shapes:
            return ArtifactAdmissionResult(
                allowed=False,
                expected=" | ".join(sorted(shapes)),
                received=received or "<missing>",
                predicate="semantic_shape",
            )

    matching_kinds = rule.matching_kinds.get(artifact_family)
    if matching_kinds:
        received = _matching_kind(value)
        if received not in matching_kinds:
            return ArtifactAdmissionResult(
                allowed=False,
                expected=" | ".join(sorted(matching_kinds)),
                received=received or "<missing>",
                predicate="matching",
            )

    coverage_statuses = rule.coverage_statuses.get(artifact_family)
    if coverage_statuses:
        received = _artifact_fact(value, "coverage_status")
        if received not in coverage_statuses:
            return ArtifactAdmissionResult(
                allowed=False,
                expected=" | ".join(sorted(coverage_statuses)),
                received=received or "<missing>",
                predicate="coverage_status",
            )
    return ArtifactAdmissionResult(allowed=True)


def _artifact_ref(value: object) -> str:
    meta = getattr(value, "meta", None)
    artifact_id = getattr(meta, "artifact_id", None)
    ref = getattr(meta, "ref", None)
    if isinstance(artifact_id, str) and artifact_id:
        return artifact_id
    return ref if isinstance(ref, str) and ref else "<unknown>"


def validate_artifact_authority(
    capability_id: str,
    parameter: str,
    value: object,
    *,
    session: Session | None,
    required_semantic_refs: frozenset[str] | None = None,
    required_source_refs: frozenset[str] | None = None,
) -> None:
    """Enforce one registry-owned current-authority policy for an Artifact."""
    descriptor = REGISTRY.by_id(capability_id)
    if (
        not isinstance(descriptor, OperatorCapability)
        or descriptor.authority_policy == "materialized_only"
    ):
        return

    from marivo.analysis._artifact_authority import (
        CatalogOnlyDependencyAuthority,
        ScopedDependencyAuthority,
        UnresolvedDependencyAuthority,
        authority_context,
        evaluate_semantic_authority,
    )
    from marivo.analysis.frames.base import BaseFrame

    if not isinstance(value, BaseFrame):
        return
    artifact_ref = _artifact_ref(value)
    location = f"session.{descriptor.help_target}.{parameter}"
    if session is None:
        raise ArtifactAuthorityUnknownError(
            message=(
                f"{descriptor.public_entrypoint} requires a Session-bound current-authority "
                "check for its Artifact input."
            ),
            expected="one current Session for semantic-current Artifact admission",
            received="session=None",
            location=location,
            repair=AnalysisRepair(
                kind="retry",
                action="Call the capability through its owning Session object.",
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=descriptor.help_target,
                ),
            ),
            context={
                "capability_id": capability_id,
                "parameter": parameter,
                "artifact_ref": artifact_ref,
            },
        )

    from marivo.analysis._artifact_integrity import load_canonical_frame_identity

    try:
        canonical_value, _ = load_canonical_frame_identity(session=session, frame=value)
    except AnalysisError:
        # Session ownership, persistence, and content-integrity failures remain
        # owned by the operator's existing input path. Semantic admission must
        # not replace those more specific errors or bless a caller-mutated copy.
        return

    context = authority_context(
        canonical_value,
        session=session,
        strict_source_identity=False,
    )
    if required_source_refs is not None:
        represented_source_refs = {
            dependency.source_artifact_ref for dependency in context.semantic_dependencies
        }
        missing_source_refs = required_source_refs - represented_source_refs
        if missing_source_refs:
            context = replace(
                context,
                semantic_dependencies=(
                    *context.semantic_dependencies,
                    *(
                        UnresolvedDependencyAuthority(
                            semantic_ref_if_known=None,
                            source_artifact_ref=source_ref,
                            missing_authority_fields=(
                                "canonical source Artifact semantic authority",
                            ),
                        )
                        for source_ref in sorted(missing_source_refs)
                    ),
                ),
            )
    if required_semantic_refs is not None:
        dependencies = tuple(
            dependency
            for dependency in context.semantic_dependencies
            if (
                dependency.source_artifact_ref in (required_source_refs or frozenset())
                or (
                    isinstance(dependency, UnresolvedDependencyAuthority)
                    and (
                        dependency.semantic_ref_if_known is None
                        or (
                            f"{dependency.semantic_ref_if_known.kind.value}:"
                            f"{dependency.semantic_ref_if_known.path}" in required_semantic_refs
                        )
                    )
                )
                or (
                    isinstance(
                        dependency,
                        ScopedDependencyAuthority | CatalogOnlyDependencyAuthority,
                    )
                    and (
                        f"{dependency.semantic_ref.kind.value}:{dependency.semantic_ref.path}"
                        in required_semantic_refs
                    )
                )
            )
        )
        context = replace(context, semantic_dependencies=dependencies)
    evaluation = evaluate_semantic_authority(context, session=session)
    error_context = {
        "capability_id": capability_id,
        "parameter": parameter,
        "artifact_ref": context.artifact_ref,
        "source_refs": context.source_refs,
        "recorded_catalog_fingerprint": evaluation.recorded_catalog_fingerprint,
        "current_catalog_fingerprint": evaluation.current_catalog_fingerprint,
        "authority_fingerprint": evaluation.authority_fingerprint,
    }
    if evaluation.status == "stale":
        raise ArtifactStaleError(
            message=(
                f"{descriptor.public_entrypoint} cannot consume Artifact "
                f"{context.artifact_ref!r} because its semantic authority is stale."
            ),
            expected=(
                f"semantic_status='current' under catalog {evaluation.current_catalog_fingerprint}"
            ),
            received=(
                "semantic_status='stale'; drifted_definition_refs="
                f"{evaluation.drifted_definition_refs!r}; recorded_catalog="
                f"{evaluation.recorded_catalog_fingerprint!r}"
            ),
            location=location,
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Re-run the Artifact's producing branch under the current semantic "
                    "catalog, then retry this capability."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=descriptor.help_target,
                ),
            ),
            context={
                **error_context,
                "definition_refs": evaluation.drifted_definition_refs,
            },
        )
    if evaluation.status == "indeterminate":
        raise ArtifactAuthorityUnknownError(
            message=(
                f"{descriptor.public_entrypoint} cannot establish current semantic authority "
                f"for Artifact {context.artifact_ref!r}."
            ),
            expected="canonical current scoped authority for every required dependency",
            received=(
                "semantic_status='indeterminate'; unresolved_definition_refs="
                f"{evaluation.indeterminate_definition_refs!r}"
            ),
            location=location,
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Restore the missing semantic definitions or regenerate the Artifact "
                    "under a complete current catalog before retrying."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=descriptor.help_target,
                ),
            ),
            context={
                **error_context,
                "definition_refs": evaluation.indeterminate_definition_refs,
            },
        )


@staged("validate")
def validate_capability_inputs(
    capability_id: str,
    *,
    session: Session | None = None,
    **kwargs: object,
) -> None:
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
    session:
        Owning Session required only when the registered operator policy is
        ``semantic_current`` and an Artifact input is present.

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
        if (
            isinstance(value, Mapping)
            and not value
            and accepted_families <= frozenset({"DimensionSemantic", "TimeDimensionSemantic"})
        ):
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
            _raise_typed_family_error(
                capability_id=capability_id,
                param_name=param_name,
                received=rejected_family,
                help_target=descriptor.help_target,
                artifact=kwargs.get("artifact"),
            )
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

        for item in values:
            admission = evaluate_artifact_admission(capability_id, param_name, item)
            if admission.allowed:
                continue
            predicate = admission.predicate or "artifact"
            if classify_input_family(item) == "SubjectSet" and predicate == "coverage_status":
                raise EventCoverageUnknownError(
                    message=(f"{capability_id} cannot consume a coverage-censored SubjectSet."),
                    expected=admission.expected,
                    received=admission.received,
                    location=f"session.{capability_id}.{param_name}",
                    repair=AnalysisRepair(
                        kind="inspect",
                        action=(
                            "Inspect the SubjectSet coverage and rebuild it from "
                            "authoritatively resolved journey loss."
                        ),
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id=descriptor.help_target,
                        ),
                    ),
                )
            if capability_id == "events.funnel" and predicate == "matching":
                raise InvalidEventMatchingPolicyError(
                    message="events.funnel requires first_per_subject matching.",
                    expected=admission.expected,
                    received=admission.received,
                    location="session.events.funnel.journeys.matching",
                    repair=AnalysisRepair(
                        kind="user_choice",
                        action=(
                            "Match the source EventPattern with "
                            "mv.first_per_subject() before funnel reduction."
                        ),
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id="events.funnel",
                        ),
                        candidates=("first_per_subject",),
                    ),
                )
            if predicate == "semantic_shape" and capability_id in {
                "events.funnel",
                "events.time_to_event",
                "select_subjects",
            }:
                public_location = (
                    f"session.{capability_id}.journeys"
                    if capability_id.startswith("events.")
                    else "session.select_subjects.artifact"
                )
                raise SubjectSetMismatchError(
                    message=f"{capability_id} requires a canonical EventFrame[journey].",
                    expected=admission.expected,
                    received=admission.received,
                    location=public_location,
                    repair=AnalysisRepair(
                        kind="inspect",
                        action=(
                            "Pass the persisted source journey returned by "
                            "session.events.match(...)."
                        ),
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id=descriptor.help_target,
                        ),
                    ),
                )
            if predicate == "semantic_shape" and capability_id in {
                "lifecycle.distribution",
                "lifecycle.transitions",
                "lifecycle.dwell",
                "lifecycle.violations",
            }:
                raise SubjectSetMismatchError(
                    message=f"{capability_id} requires canonical replay history.",
                    expected=admission.expected,
                    received=admission.received,
                    location=f"session.{capability_id}.history",
                    repair=AnalysisRepair(
                        kind="inspect",
                        action=(
                            "Pass the persisted source history returned by "
                            "session.lifecycle.replay(...)."
                        ),
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id=descriptor.help_target,
                        ),
                    ),
                )
            raise AnalysisError(
                message=(
                    f"{capability_id} parameter {param_name!r} failed the "
                    f"registered {predicate} admission predicate."
                ),
                expected=admission.expected,
                received=admission.received,
                location=f"{capability_id}.{param_name}.{predicate}",
                repair=AnalysisRepair(
                    kind="inspect",
                    action=(
                        "Inspect the artifact contract and pass an artifact whose "
                        f"{predicate} satisfies this capability."
                    ),
                    help_target=LiveHelpTarget(
                        surface="analysis", canonical_id=descriptor.help_target
                    ),
                ),
            )

        for item in values:
            validate_artifact_authority(
                capability_id,
                param_name,
                item,
                session=session,
            )
