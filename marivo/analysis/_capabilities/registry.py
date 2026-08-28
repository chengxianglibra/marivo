"""Immutable capability registry for ``marivo.analysis``.

This module owns the complete immutable capability table, callable/type
indexes, reverse edges, grouping-topic expansion, public type/member
allowlists, and generated type-algebra rows.

All names are private to ``marivo.analysis``.  Nothing is added to
``marivo/analysis/__init__.py``.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, get_args

from pydantic import BaseModel

from marivo.analysis._capabilities.model import (
    ANALYSIS_HELP_RENDER_BUDGETS,
    ARTIFACT_FAMILIES,
    AnalysisArtifactFamilyContract,
    AnalysisHelpDescriptor,
    AnalysisHelpRenderBudget,
    AnalysisHelpRenderClass,
    AnalysisMethodFamily,
    AnalysisNavigationTopic,
    ArtifactAdmissionRule,
    ArtifactConsumerEdge,
    ArtifactFamily,
    ArtifactOutputContract,
    ArtifactProducerEdge,
    AuthorityPolicy,
    BoundaryCapability,
    CapabilityDescriptor,
    ConstructorCapability,
    EpistemicKind,
    HelpExample,
    InputFamily,
    OperatorCapability,
    ParameterHelpContract,
    ReadCapability,
    RecoveryCapability,
    SameAsInputFamily,
)
from marivo.analysis._contract_budget import ARTIFACT_CONTRACT_RENDER_BUDGET
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.reflect import callable_identity, import_registered_callable
from marivo.refs import SemanticKind
from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS


def _analysis_target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="analysis", canonical_id=canonical_id)


def _semantic_target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="semantic", canonical_id=canonical_id)


_ARTIFACT_EVIDENCE_TARGETS: tuple[LiveHelpTarget, ...] = tuple(
    _analysis_target(target)
    for target in (
        "session.evidence.digest",
        "session.evidence.findings",
        "session.evidence.trace",
    )
)


_ROOT_HELP_MEMBERS: tuple[LiveHelpTarget, ...] = (
    _analysis_target("entry"),
    _analysis_target("methods"),
    _analysis_target("inputs"),
    _analysis_target("artifacts"),
    _analysis_target("evidence"),
    _analysis_target("runtime"),
    _analysis_target("boundary.to_pandas"),
)


def _root_navigation_topics() -> tuple[AnalysisNavigationTopic, ...]:
    """Build the final progressive analysis-root topology."""

    return (
        AnalysisNavigationTopic(
            canonical_id="entry",
            summary="Route governed semantic inputs into their typed entry producers.",
            render_class="decision_hub",
            members=(
                _analysis_target("observe"),
                _analysis_target("events.match"),
                _analysis_target("lifecycle.replay"),
                _analysis_target("catalog"),
                _analysis_target("catalog.readiness"),
                _semantic_target("authoring"),
                _analysis_target("entry.event_observations"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="methods",
            summary="Route by the deterministic computation performed.",
            render_class="decision_hub",
            members=(
                _analysis_target("observe"),
                _analysis_target("methods.change"),
                _analysis_target("discover"),
                _analysis_target("methods.relationship_testing"),
                _analysis_target("forecast"),
                _analysis_target("BaseFrame.quality_report"),
                _analysis_target("events"),
                _analysis_target("lifecycle"),
                _analysis_target("select_subjects"),
                _analysis_target("transform"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="inputs",
            summary="Route by the policy, scope, selection, or option value required.",
            render_class="decision_hub",
            members=(
                _analysis_target("catalog"),
                _analysis_target("inputs.scope"),
                _analysis_target("alignment"),
                _analysis_target("SamplingPolicy"),
                _analysis_target("runtime_metric"),
                _analysis_target("inputs.events"),
                _analysis_target("inputs.subject_selection"),
                _analysis_target("inputs.operator_options"),
                _analysis_target("inputs.transform_options"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="artifacts",
            summary="Route to static Artifact family contracts and the common read protocol.",
            render_class="decision_hub",
            members=(
                _analysis_target("artifacts.metric_change"),
                _analysis_target("artifacts.event_lifecycle"),
                _analysis_target("artifacts.discovery_inference"),
                _analysis_target("artifacts.quality_projection"),
                _analysis_target("artifacts.reading"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="evidence",
            summary=(
                "Route by the Evidence identity or proof boundary being checked; "
                "quality, compatibility, revalidation, and source freshness remain distinct."
            ),
            render_class="decision_hub",
            members=(
                _analysis_target("BaseFrame.show"),
                _analysis_target("evidence.browse"),
                _analysis_target("evidence.exact"),
                _analysis_target("session.evidence.compatibility"),
                _analysis_target("session.revalidate"),
                _analysis_target("BaseFrame.quality_report"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="runtime",
            summary=(
                "Route persisted identities by Session name or id, Artifact ref, job id, "
                "and exact Evidence id."
            ),
            render_class="decision_hub",
            members=(
                _analysis_target("runtime.sessions"),
                _analysis_target("runtime.artifacts"),
                _analysis_target("runtime.jobs"),
                _analysis_target("Session"),
                _analysis_target("evidence"),
            ),
        ),
    )


def _slice2_navigation_topics() -> tuple[AnalysisNavigationTopic, ...]:
    """Build explicit multi-member entry, input, and Artifact navigation."""

    return (
        AnalysisNavigationTopic(
            canonical_id="entry.event_observations",
            summary="Read bounded observed Event occurrence facts without matching journeys.",
            render_class="navigation",
            members=(
                _analysis_target("events.watermark"),
                _analysis_target("events.occurrence_bounds"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="catalog",
            summary="Browse or require exact current reusable semantic inputs and readiness.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in (
                    "catalog.domains",
                    "catalog.datasources",
                    "catalog.entities",
                    "catalog.dimensions",
                    "catalog.time_dimensions",
                    "catalog.measures",
                    "catalog.metrics",
                    "catalog.relationships",
                    "catalog.events",
                    "catalog.state_models",
                    "catalog.period_calendars",
                    "catalog.temporal_sets",
                    "catalog.work_schedules",
                    "catalog.require",
                    "catalog.readiness",
                    "catalog.temporal",
                )
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="catalog.temporal",
            summary="Browse bounded certified calendar periods or named temporal occurrences.",
            render_class="navigation",
            members=(
                _analysis_target("calendar.periods"),
                _analysis_target("temporal_set.occurrences"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="inputs.scope",
            summary="Construct time, grain, governed-period, and execution scope inputs.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in (
                    "grain",
                    "time_scope",
                    "AbsoluteWindow",
                    "calendar.grain",
                    "calendar.period",
                    "calendar.period_on",
                    "temporal_set.occurrence",
                    "Session.source_bindings",
                )
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="alignment",
            summary="Construct an explicit temporal alignment policy for compatible Artifacts.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in (
                    "window_bucket",
                    "day_of_week",
                    "period_progress",
                    "period_correspondence",
                    "occurrence_progress",
                    "working_day_progress",
                )
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="runtime_metric",
            summary="Compose one closed question-scoped runtime metric expression.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in (
                    "runtime_metric.aggregate",
                    "runtime_metric.slice",
                    "runtime_metric.weighted_mean",
                    "runtime_metric.ratio",
                    "runtime_metric.linear",
                )
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="inputs.events",
            summary="Construct Event patterns, matching, completeness, and funnel target inputs.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in (
                    "step",
                    "sequence",
                    "first_per_subject",
                    "every_start",
                    "declared_complete_through",
                    "funnel_loss_rate",
                )
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="inputs.subject_selection",
            summary="Select subjects or construct an explicit Lifecycle replay seed.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in ("dropped_before", "in_state", "from_inception")
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="inputs.operator_options",
            summary="Choose one closed analysis-method option value.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in ("AttributionMode", "SemanticShape", "PointAnomalyStrategy")
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="inputs.transform_options",
            summary="Choose one closed transform option value.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in ("RankMethod", "NormalizeKind", "NormalizeBaseline")
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="artifacts.metric_change",
            summary="Inspect governed metric observation, change, and attribution families.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in ("MetricFrame", "DeltaFrame", "AttributionFrame")
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="artifacts.event_lifecycle",
            summary="Inspect Event, Lifecycle, and exact subject-cohort Artifact families.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in ("EventFrame", "LifecycleFrame", "SubjectSet")
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="artifacts.discovery_inference",
            summary="Inspect discovery, association, test, and forecast result families.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in (
                    "CandidateSet",
                    "AssociationResult",
                    "HypothesisTestResult",
                    "ForecastFrame",
                )
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="artifacts.quality_projection",
            summary="Inspect quality and bounded component or coverage projections.",
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in ("QualityReport", "ComponentFrame", "CoverageFrame")
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="artifacts.reading",
            summary=(
                "Read progressively: repr -> show/render -> contract -> exact Evidence "
                "or rows -> terminal exit."
            ),
            render_class="navigation",
            members=(
                _analysis_target("BaseFrame.show"),
                _analysis_target("BaseFrame.contract"),
            ),
        ),
    )


def _slice3_navigation_topics() -> tuple[AnalysisNavigationTopic, ...]:
    """Build explicit multi-member Evidence and runtime navigation."""

    return (
        AnalysisNavigationTopic(
            canonical_id="evidence.browse",
            summary=(
                "Browse bounded persisted digest or Finding pages; a healthy empty page "
                "is distinct from an unavailable Evidence store."
            ),
            render_class="navigation",
            members=(
                _analysis_target("session.evidence.digests"),
                _analysis_target("session.evidence.findings"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="evidence.exact",
            summary="Read one exact persisted digest, Finding, or derivation trace by identity.",
            render_class="navigation",
            members=(
                _analysis_target("session.evidence.digest"),
                _analysis_target("session.evidence.finding"),
                _analysis_target("session.evidence.trace"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="runtime.sessions",
            summary=(
                "Create or locate Sessions by stable name and inspect or resume them by "
                "immutable session id."
            ),
            render_class="navigation",
            members=tuple(
                _analysis_target(target)
                for target in (
                    "session.get_or_create",
                    "session.current",
                    "session.recent",
                    "session.inspect",
                    "session.resume",
                    "session.delete",
                )
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="runtime.artifacts",
            summary="Find persisted Artifact summaries and recover one exact Artifact by ref.",
            render_class="navigation",
            members=(
                _analysis_target("session.frame_summaries"),
                _analysis_target("session.get_frame"),
            ),
        ),
        AnalysisNavigationTopic(
            canonical_id="runtime.jobs",
            summary="Inspect bounded job summaries or one exact persisted job by job id.",
            render_class="navigation",
            members=(
                _analysis_target("session.jobs"),
                _analysis_target("session.recent_jobs"),
                _analysis_target("session.job"),
            ),
        ),
    )


def _slice2_method_families() -> tuple[AnalysisMethodFamily, ...]:
    """Build explicit multi-member deterministic computation families."""

    return (
        AnalysisMethodFamily(
            canonical_id="methods.change",
            summary="Align compatible Artifacts into change facts and reconcile contributions.",
            epistemic_kinds=("algebraic",),
            members=(_analysis_target("compare"), _analysis_target("attribute")),
            input_routes=(
                _analysis_target("artifacts.metric_change"),
                _analysis_target("artifacts.event_lifecycle"),
                _analysis_target("alignment"),
            ),
            output_routes=(_analysis_target("artifacts.metric_change"),),
        ),
        AnalysisMethodFamily(
            canonical_id="discover",
            summary="Produce bounded candidates for one closed discovery objective.",
            epistemic_kinds=("candidate",),
            members=tuple(
                _analysis_target(target)
                for target in (
                    "discover.point_anomalies",
                    "discover.period_shifts",
                    "discover.driver_axes",
                    "discover.interesting_slices",
                    "discover.interesting_windows",
                    "discover.cross_sectional_outliers",
                    "discover.semantic_hypotheses",
                )
            ),
            input_routes=(_analysis_target("artifacts.metric_change"),),
            output_routes=(_analysis_target("CandidateSet"),),
        ),
        AnalysisMethodFamily(
            canonical_id="methods.relationship_testing",
            summary="Measure association or evaluate one explicit paired hypothesis.",
            epistemic_kinds=("association", "statistical_decision"),
            members=(_analysis_target("correlate"), _analysis_target("hypothesis_test")),
            input_routes=(
                _analysis_target("MetricFrame"),
                _analysis_target("alignment"),
                _analysis_target("SamplingPolicy"),
            ),
            output_routes=(
                _analysis_target("AssociationResult"),
                _analysis_target("HypothesisTestResult"),
            ),
        ),
        AnalysisMethodFamily(
            canonical_id="events",
            summary="Match and reduce governed Event journeys.",
            epistemic_kinds=("observed", "algebraic"),
            members=tuple(
                _analysis_target(target)
                for target in ("events.match", "events.funnel", "events.time_to_event")
            ),
            input_routes=(
                _analysis_target("inputs.events"),
                _analysis_target("inputs.scope"),
            ),
            output_routes=(_analysis_target("EventFrame"),),
        ),
        AnalysisMethodFamily(
            canonical_id="lifecycle",
            summary="Replay and reduce governed StateModel history.",
            epistemic_kinds=("observed", "algebraic"),
            members=tuple(
                _analysis_target(target)
                for target in (
                    "lifecycle.replay",
                    "lifecycle.distribution",
                    "lifecycle.transitions",
                    "lifecycle.dwell",
                    "lifecycle.violations",
                )
            ),
            input_routes=(
                _analysis_target("inputs.scope"),
                _analysis_target("inputs.subject_selection"),
            ),
            output_routes=(_analysis_target("LifecycleFrame"),),
        ),
        AnalysisMethodFamily(
            canonical_id="transform",
            summary="Reshape a MetricFrame or DeltaFrame without changing its family.",
            epistemic_kinds=("algebraic",),
            members=tuple(
                _analysis_target(target)
                for target in (
                    "transform.filter",
                    "transform.slice",
                    "transform.rollup",
                    "transform.topk",
                    "transform.bottomk",
                    "transform.rank",
                    "transform.window",
                    "transform.normalize",
                )
            ),
            input_routes=(
                _analysis_target("artifacts.metric_change"),
                _analysis_target("inputs.scope"),
                _analysis_target("inputs.transform_options"),
            ),
            output_routes=(_analysis_target("artifacts.metric_change"),),
        ),
    )


def _artifact_contract(
    family: ArtifactFamily,
    summary: str,
    epistemic_kinds: tuple[EpistemicKind, ...],
    *,
    shapes: tuple[str, ...] = (),
    specialized_members: tuple[str, ...] = (),
) -> AnalysisArtifactFamilyContract:
    return AnalysisArtifactFamilyContract(
        canonical_id=family,
        artifact_family=family,
        summary=summary,
        epistemic_kinds=epistemic_kinds,
        semantic_shapes=shapes,
        type_name=family,
        specialized_member_targets=tuple(
            _analysis_target(target) for target in specialized_members
        ),
    )


def _slice2_artifact_contracts() -> tuple[AnalysisArtifactFamilyContract, ...]:
    """Build one native contract for every closed public Artifact family."""

    metric_shapes = ("scalar", "time_series", "segmented", "panel")
    return (
        _artifact_contract(
            "MetricFrame",
            "Governed observed metric values with one closed semantic shape.",
            ("observed",),
            shapes=metric_shapes,
            specialized_members=tuple(
                f"MetricFrame.{member}"
                for member in (
                    "metric",
                    "components",
                    "coverage",
                    "as_scalar",
                    "as_time_series",
                    "as_segmented",
                    "as_panel",
                )
            ),
        ),
        _artifact_contract(
            "EventFrame",
            "Persisted Event journey, funnel, or time-to-event facts.",
            ("observed", "algebraic"),
            shapes=("journey", "funnel", "time_to_event"),
        ),
        _artifact_contract(
            "LifecycleFrame",
            "Persisted StateModel history or one closed reducer shape.",
            ("observed", "algebraic"),
            shapes=("history", "distribution", "transitions", "dwell", "violations"),
        ),
        _artifact_contract(
            "SubjectSet",
            "Exact persisted subject identities for a typed cohort handoff.",
            ("selection",),
            shapes=("subjects",),
        ),
        _artifact_contract(
            "DeltaFrame",
            "Aligned metric or funnel change facts.",
            ("algebraic",),
            shapes=(*metric_shapes, "funnel"),
            specialized_members=tuple(
                f"DeltaFrame.{member}"
                for member in (
                    "components",
                    "predicted_attribution_shape",
                    "as_scalar",
                    "as_time_series",
                    "as_segmented",
                    "as_panel",
                )
            ),
        ),
        _artifact_contract(
            "AttributionFrame",
            "Reconciled arithmetic contribution facts.",
            ("algebraic",),
            shapes=(
                "scalar",
                "time_series",
                "segmented",
                "panel",
                "funnel_loss_rate",
                "sum",
                "ratio_mix",
                "weighted_mix",
                "distinct_membership",
                "quantile_replacement",
            ),
            specialized_members=tuple(
                f"AttributionFrame.{member}"
                for member in ("as_sum", "as_ratio_mix", "as_weighted_mix", "at_resolution")
            ),
        ),
        _artifact_contract(
            "ForecastFrame",
            "Projected future metric buckets.",
            ("projection",),
            shapes=("time_series", "panel"),
        ),
        _artifact_contract(
            "QualityReport",
            "Fixed quality evaluations over one supported Artifact.",
            ("quality_evaluation",),
            shapes=PUBLIC_TYPE_VARIANTS["QualityReport"],
        ),
        _artifact_contract(
            "CandidateSet",
            "Bounded candidates for one closed discovery objective.",
            ("candidate",),
            shapes=(
                "point_anomaly",
                "period_shift",
                "driver_axis",
                "slice",
                "window",
                "cross_sectional_outlier",
                "semantic_hypothesis",
            ),
            specialized_members=tuple(
                f"CandidateSet.{member}"
                for member in (
                    "select",
                    "as_point_anomaly",
                    "as_period_shift",
                    "as_driver_axis",
                    "as_slice",
                    "as_window",
                    "as_cross_sectional_outlier",
                    "as_semantic_hypothesis",
                )
            ),
        ),
        _artifact_contract(
            "AssociationResult",
            "Estimated association facts between observed metrics.",
            ("association",),
        ),
        _artifact_contract(
            "ComponentFrame",
            "A bounded component projection from a supported parent.",
            ("projection",),
            shapes=metric_shapes,
        ),
        _artifact_contract(
            "CoverageFrame",
            "A bounded coverage projection from a supported parent.",
            ("projection",),
            shapes=("time_slot", "window_coverage"),
        ),
        _artifact_contract(
            "HypothesisTestResult",
            "A statistical decision under one declared paired test.",
            ("statistical_decision",),
            shapes=("single", "per_segment"),
        ),
    )


def _slice3_discovery_memberships(
    navigation_topics: tuple[AnalysisNavigationTopic, ...],
    method_families: tuple[AnalysisMethodFamily, ...],
    artifact_contracts: tuple[AnalysisArtifactFamilyContract, ...],
) -> Mapping[str, tuple[LiveHelpTarget, ...]]:
    """Build the single-owner discovery projection without naming inference."""

    navigation_by_id = {topic.canonical_id: topic for topic in navigation_topics}
    family_by_id = {family.canonical_id: family for family in method_families}
    contract_by_id = {contract.canonical_id: contract for contract in artifact_contracts}
    memberships: dict[str, tuple[LiveHelpTarget, ...]] = {
        "entry": (_analysis_target("entry.event_observations"),),
        "entry.event_observations": navigation_by_id["entry.event_observations"].members,
        "methods": tuple(
            _analysis_target(target)
            for target in (
                "observe",
                "methods.change",
                "discover",
                "methods.relationship_testing",
                "forecast",
                "BaseFrame.quality_report",
                "events",
                "lifecycle",
                "select_subjects",
                "transform",
            )
        ),
        "inputs": tuple(
            _analysis_target(target)
            for target in (
                "catalog",
                "inputs.scope",
                "alignment",
                "SamplingPolicy",
                "runtime_metric",
                "inputs.events",
                "inputs.subject_selection",
                "inputs.operator_options",
                "inputs.transform_options",
            )
        ),
        "artifacts": tuple(
            _analysis_target(target)
            for target in (
                "artifacts.metric_change",
                "artifacts.event_lifecycle",
                "artifacts.discovery_inference",
                "artifacts.quality_projection",
                "artifacts.reading",
            )
        ),
        "evidence": tuple(
            _analysis_target(target)
            for target in (
                "evidence.browse",
                "evidence.exact",
                "session.evidence.compatibility",
                "session.revalidate",
            )
        ),
        "runtime": tuple(
            _analysis_target(target)
            for target in (
                "runtime.sessions",
                "runtime.artifacts",
                "runtime.jobs",
            )
        ),
        "Session": (_analysis_target("Session.render"), _analysis_target("Session.show")),
        "analysis": _ROOT_HELP_MEMBERS,
    }
    for owner_id in (
        "catalog",
        "catalog.temporal",
        "inputs.scope",
        "alignment",
        "runtime_metric",
        "inputs.events",
        "inputs.subject_selection",
        "inputs.operator_options",
        "inputs.transform_options",
        "artifacts.metric_change",
        "artifacts.event_lifecycle",
        "artifacts.discovery_inference",
        "artifacts.quality_projection",
        "artifacts.reading",
        "evidence.browse",
        "evidence.exact",
        "runtime.sessions",
        "runtime.artifacts",
        "runtime.jobs",
    ):
        memberships[owner_id] = navigation_by_id[owner_id].members
    for owner_id, family in family_by_id.items():
        memberships[owner_id] = family.members
    for owner_id, contract in contract_by_id.items():
        if contract.specialized_member_targets:
            memberships[owner_id] = contract.specialized_member_targets
    return MappingProxyType(memberships)


def _slice3_cross_links() -> Mapping[str, tuple[LiveHelpTarget, ...]]:
    """Return explicit decision, proof-boundary, and recovery links."""

    return MappingProxyType(
        {
            "entry": (
                _analysis_target("observe"),
                _analysis_target("events.match"),
                _analysis_target("lifecycle.replay"),
                _analysis_target("catalog"),
                _analysis_target("catalog.readiness"),
                _semantic_target("authoring"),
            ),
            "evidence": (
                _analysis_target("BaseFrame.show"),
                _analysis_target("BaseFrame.quality_report"),
            ),
            "runtime": (
                _analysis_target("Session"),
                _analysis_target("evidence"),
            ),
            "runtime.artifacts": (_analysis_target("session.revalidate"),),
        }
    )


# ---------------------------------------------------------------------------
# Public type/member contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicObjectContract:
    """Stable intrinsic members for one public analysis object."""

    properties: tuple[str, ...] = ()
    intrinsic_methods: tuple[str, ...] = ("render", "show")


@dataclass(frozen=True)
class SemanticHandoffContract:
    """Analysis continuation owned by one semantic catalog kind."""

    semantic_kind: SemanticKind
    collection_property: str
    input_family: InputFamily | None = None
    handoff_targets: tuple[LiveHelpTarget, ...] = ()
    preparation_targets: tuple[LiveHelpTarget, ...] = ()


def _catalog_property(kind: SemanticKind) -> str:
    return next(member.property_name for member in CATALOG_MEMBER_CONTRACTS if member.kind is kind)


SEMANTIC_HANDOFF_CONTRACTS: Mapping[SemanticKind, SemanticHandoffContract] = MappingProxyType(
    {
        SemanticKind.METRIC: SemanticHandoffContract(
            SemanticKind.METRIC,
            _catalog_property(SemanticKind.METRIC),
            "MetricSemantic",
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="observe"),),
        ),
        SemanticKind.DIMENSION: SemanticHandoffContract(
            SemanticKind.DIMENSION,
            _catalog_property(SemanticKind.DIMENSION),
            "DimensionSemantic",
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="observe"),),
        ),
        SemanticKind.TIME_DIMENSION: SemanticHandoffContract(
            SemanticKind.TIME_DIMENSION,
            _catalog_property(SemanticKind.TIME_DIMENSION),
            "TimeDimensionSemantic",
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="observe"),),
        ),
        SemanticKind.EVENT: SemanticHandoffContract(
            SemanticKind.EVENT,
            _catalog_property(SemanticKind.EVENT),
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="events.match"),),
            preparation_targets=(
                LiveHelpTarget(surface="semantic", canonical_id="participant_role"),
                LiveHelpTarget(surface="analysis", canonical_id="step"),
                LiveHelpTarget(surface="analysis", canonical_id="sequence"),
            ),
        ),
        SemanticKind.STATE_MODEL: SemanticHandoffContract(
            SemanticKind.STATE_MODEL,
            _catalog_property(SemanticKind.STATE_MODEL),
            "StateModelSemantic",
            handoff_targets=(LiveHelpTarget(surface="analysis", canonical_id="lifecycle.replay"),),
        ),
        SemanticKind.PERIOD_CALENDAR: SemanticHandoffContract(
            SemanticKind.PERIOD_CALENDAR,
            _catalog_property(SemanticKind.PERIOD_CALENDAR),
            handoff_targets=(
                LiveHelpTarget(surface="analysis", canonical_id="period_progress"),
                LiveHelpTarget(surface="analysis", canonical_id="period_correspondence"),
            ),
        ),
        SemanticKind.TEMPORAL_SET: SemanticHandoffContract(
            SemanticKind.TEMPORAL_SET,
            _catalog_property(SemanticKind.TEMPORAL_SET),
            handoff_targets=(
                LiveHelpTarget(surface="analysis", canonical_id="occurrence_progress"),
            ),
        ),
        SemanticKind.WORK_SCHEDULE: SemanticHandoffContract(
            SemanticKind.WORK_SCHEDULE,
            _catalog_property(SemanticKind.WORK_SCHEDULE),
            handoff_targets=(
                LiveHelpTarget(surface="analysis", canonical_id="working_day_progress"),
            ),
        ),
    }
)

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
        "AttributionFrame": (
            "as_sum",
            "as_ratio_mix",
            "as_weighted_mix",
            "at_resolution",
        ),
        "EventFrame": (),
        "LifecycleFrame": (),
        "SubjectSet": (),
        "CandidateSet": (
            "select",
            "as_point_anomaly",
            "as_period_shift",
            "as_driver_axis",
            "as_slice",
            "as_window",
            "as_cross_sectional_outlier",
            "as_semantic_hypothesis",
        ),
    }
)

PUBLIC_FRAME_PROPERTIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "BaseFrame": (
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
        "MetricFrame": (
            "semantic_shape",
            "metrics",
            "arity",
            "value_columns",
            "time_dimension_columns",
            "transform",
        ),
        "DeltaFrame": ("semantic_shape", "transform"),
        "AttributionFrame": ("attribution_shape", "attribution_mode"),
        "LifecycleFrame": ("semantic_shape",),
        "QualityReport": (
            "overall_status",
            "blocking_issue_count",
            "warning_count",
        ),
    }
)

PUBLIC_TYPE_VARIANTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "ArtifactIssue": (
            "DataQualityIssue",
            "ComparabilityIssue",
            "EvidenceAvailabilityIssue",
            "CandidateResolutionIssue",
        ),
        "CandidateSelection": (
            "PointAnomalySelection",
            "PeriodShiftSelection",
            "DriverAxisSelection",
            "SliceSelection",
            "WindowSelection",
            "CrossSectionalOutlierSelection",
            "OntologyMetricCandidate",
        ),
        "EventFrame": (
            "journey",
            "funnel",
            "time_to_event",
        ),
        "LifecycleFrame": (
            "history",
            "distribution",
            "transitions",
            "dwell",
            "violations",
        ),
        "QualityReport": (
            "metric",
            "delta",
            "event_journey",
            "event_funnel",
            "event_time_to_event",
            "lifecycle_history",
            "lifecycle_distribution",
            "lifecycle_transitions",
            "lifecycle_dwell",
            "lifecycle_violations",
            "funnel_delta",
            "attribution",
            "funnel_attribution",
        ),
    }
)


def _installed_artifact_types() -> Mapping[ArtifactFamily, type]:
    """Return the installed public class for every closed Artifact family."""

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

    return MappingProxyType(
        {
            "MetricFrame": MetricFrame,
            "EventFrame": EventFrame,
            "LifecycleFrame": LifecycleFrame,
            "SubjectSet": SubjectSet,
            "DeltaFrame": DeltaFrame,
            "AttributionFrame": AttributionFrame,
            "ForecastFrame": ForecastFrame,
            "QualityReport": QualityReport,
            "CandidateSet": CandidateSet,
            "AssociationResult": AssociationResult,
            "ComponentFrame": ComponentFrame,
            "CoverageFrame": CoverageFrame,
            "HypothesisTestResult": HypothesisTestResult,
        }
    )


PUBLIC_OBJECT_CONTRACTS: Mapping[str, PublicObjectContract] = MappingProxyType(
    {
        "Session": PublicObjectContract(
            properties=(
                "id",
                "name",
                "question",
                "catalog",
                "created_at",
                "updated_at",
                "report_tz_name",
                "is_read_only",
                "evidence",
                "discover",
                "events",
                "lifecycle",
            ),
            intrinsic_methods=("source_bindings",),
        ),
        "SessionEvents": PublicObjectContract(),
        "SessionLifecycle": PublicObjectContract(),
        "FrameSummaryEntry": PublicObjectContract(properties=("ref",)),
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
    output_contract:
        Typed artifact output or an external terminal type string.
    is_terminal:
        ``True`` for the single aggregate ``boundary.to_pandas`` row.
    """

    help_target: str
    source_families: frozenset[str]
    output_contract: ArtifactOutputContract | str
    is_terminal: bool = False

    @property
    def output_family(self) -> str:
        """Return the family-only view retained by algebra callers."""

        if isinstance(self.output_contract, ArtifactOutputContract):
            family = self.output_contract.family
            if isinstance(family, SameAsInputFamily):
                return f"same as {family.parameter}"
            return family
        return self.output_contract

    def render(self) -> str:
        """Render the row as a single-line type-algebra edge string."""
        sources_text = (
            "all registered artifact families"
            if self.is_terminal
            else ", ".join(sorted(self.source_families))
        )
        suffix = " (terminal)" if self.is_terminal else ""
        output = (
            self.output_contract.render()
            if isinstance(self.output_contract, ArtifactOutputContract)
            else self.output_contract
        )
        return f"{sources_text} -> {self.help_target} -> {output}{suffix}"


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRegistry:
    """Immutable registry of all analysis capabilities.

    Provides lookup by id, help_target, and callable identity, plus
    reverse-edge indexes and generated type-algebra rows.
    """

    _help_descriptors: tuple[AnalysisHelpDescriptor, ...]
    _descriptors: tuple[CapabilityDescriptor, ...]
    _by_id: Mapping[str, AnalysisHelpDescriptor] = field(default_factory=dict)
    _by_help_target: Mapping[str, AnalysisHelpDescriptor] = field(default_factory=dict)
    _by_callable: Mapping[str, CapabilityDescriptor] = field(default_factory=dict)
    _navigation_topics: Mapping[str, AnalysisNavigationTopic] = field(default_factory=dict)
    _method_families: Mapping[str, AnalysisMethodFamily] = field(default_factory=dict)
    _artifact_contracts: Mapping[str, AnalysisArtifactFamilyContract] = field(default_factory=dict)
    _root_members: tuple[LiveHelpTarget, ...] = field(default_factory=tuple)
    _render_budgets: Mapping[AnalysisHelpRenderClass, AnalysisHelpRenderBudget] = field(
        default_factory=dict
    )
    _constructor_consumers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _discovery_owners: Mapping[str, LiveHelpTarget] = field(default_factory=dict)
    _discovery_members: Mapping[str, tuple[LiveHelpTarget, ...]] = field(default_factory=dict)
    _cross_links: Mapping[str, tuple[LiveHelpTarget, ...]] = field(default_factory=dict)
    _artifact_producers: Mapping[ArtifactFamily, tuple[LiveHelpTarget, ...]] = field(
        default_factory=dict
    )
    _artifact_consumers: Mapping[ArtifactFamily, tuple[LiveHelpTarget, ...]] = field(
        default_factory=dict
    )
    _artifact_producer_edges: Mapping[ArtifactFamily, tuple[ArtifactProducerEdge, ...]] = field(
        default_factory=dict
    )
    _artifact_consumer_edges: Mapping[ArtifactFamily, tuple[ArtifactConsumerEdge, ...]] = field(
        default_factory=dict
    )
    _algebra_rows: tuple[TypeAlgebraRow, ...] = field(default_factory=tuple)

    @property
    def surface(self) -> Literal["analysis"]:
        """Return the owning help surface for the neutral registry protocol."""
        return "analysis"

    # -- Properties --------------------------------------------------------

    @property
    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        """Return exact runtime capabilities only."""

        return self._descriptors

    @property
    def help_descriptors(self) -> tuple[AnalysisHelpDescriptor, ...]:
        """Return the currently resolvable static Help descriptors."""

        return self._help_descriptors

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(d.id for d in self._descriptors)

    @property
    def help_targets(self) -> tuple[str, ...]:
        return tuple(d.help_target for d in self._help_descriptors)

    @property
    def navigation_topics(self) -> tuple[AnalysisNavigationTopic, ...]:
        """Return the immutable native navigation topology in registration order."""

        return tuple(self._navigation_topics.values())

    @property
    def method_families(self) -> tuple[AnalysisMethodFamily, ...]:
        """Return registered native method families in registration order."""

        return tuple(self._method_families.values())

    @property
    def artifact_contracts(self) -> tuple[AnalysisArtifactFamilyContract, ...]:
        """Return registered Artifact-family contracts in closed-family order."""

        return tuple(self._artifact_contracts[family] for family in ARTIFACT_FAMILIES)

    @property
    def root_members(self) -> tuple[LiveHelpTarget, ...]:
        """Return the final progressive analysis-root edges."""

        return self._root_members

    @property
    def render_budgets(
        self,
    ) -> Mapping[AnalysisHelpRenderClass, AnalysisHelpRenderBudget]:
        """Return analysis-owned immutable static Help budgets."""

        return self._render_budgets

    def render_budget(
        self,
        render_class: AnalysisHelpRenderClass,
    ) -> AnalysisHelpRenderBudget:
        """Return the registered budget for one static Help render class."""

        return self._render_budgets[render_class]

    def navigation_topic(self, canonical_id: str) -> AnalysisNavigationTopic:
        """Return one native navigation topic."""

        return self._navigation_topics[canonical_id]

    def artifact_contract(self, family: str) -> AnalysisArtifactFamilyContract:
        """Return one registered Artifact-family contract by family or type name."""

        return self._artifact_contracts[family]

    def discovery_owner(self, canonical_id: str) -> LiveHelpTarget | None:
        """Return the single explicit discovery owner for one canonical target."""

        return self._discovery_owners.get(canonical_id)

    def discovery_members(self, owner_id: str) -> tuple[LiveHelpTarget, ...]:
        """Return explicitly owned members without prefix inference."""

        return self._discovery_members.get(owner_id, ())

    @property
    def discovery_memberships(self) -> Mapping[str, tuple[LiveHelpTarget, ...]]:
        """Return the immutable owner-to-members discovery projection."""

        return self._discovery_members

    def cross_links(self, owner_id: str) -> tuple[LiveHelpTarget, ...]:
        """Return immutable typed cross-links owned by one static Help target."""

        return self._cross_links.get(owner_id, ())

    @property
    def cross_link_index(self) -> Mapping[str, tuple[LiveHelpTarget, ...]]:
        """Return the complete immutable static cross-link projection."""

        return self._cross_links

    def artifact_producers(self, family: ArtifactFamily) -> tuple[LiveHelpTarget, ...]:
        """Return exact producer targets derived from output contracts."""

        return self._artifact_producers[family]

    def artifact_consumers(self, family: ArtifactFamily) -> tuple[LiveHelpTarget, ...]:
        """Return exact consumer targets derived from admitted inputs."""

        return self._artifact_consumers[family]

    def artifact_producer_edges(
        self,
        family: ArtifactFamily,
    ) -> tuple[ArtifactProducerEdge, ...]:
        """Return output-qualified producer edges for one Artifact family."""

        return self._artifact_producer_edges[family]

    def artifact_consumer_edges(
        self,
        family: ArtifactFamily,
    ) -> tuple[ArtifactConsumerEdge, ...]:
        """Return parameter- and admission-qualified consumer edges."""

        return self._artifact_consumer_edges[family]

    def continuation_group(self, target: LiveHelpTarget) -> LiveHelpTarget:
        """Return the registry-owned discovery group for one exact continuation."""

        if target.surface != "analysis" or target.canonical_id is None:
            raise KeyError(target.display)
        descriptor = self.by_help_target(target.canonical_id)
        if not isinstance(descriptor, (OperatorCapability, ReadCapability)):
            raise KeyError(target.display)
        owner = self.discovery_owner(target.canonical_id)
        if owner is None:
            raise KeyError(target.display)
        return owner

    def focused_summary(self, descriptor: AnalysisHelpDescriptor) -> str:
        """Return the descriptor-owned focused summary."""

        return descriptor.summary

    def discovery_ids(self) -> tuple[str, ...]:
        """Return the bounded public analysis-root discovery targets."""

        return tuple(
            target.canonical_id for target in self._root_members if target.canonical_id is not None
        )

    @property
    def constructor_consumers(self) -> Mapping[str, tuple[str, ...]]:
        return self._constructor_consumers

    def public_member_names(self, receiver_family: str) -> tuple[str, ...]:
        """Return registered public methods owned by one receiver type."""

        names: list[str] = []
        for descriptor in self._descriptors:
            receiver: str | None = None
            if isinstance(descriptor, OperatorCapability):
                receiver = descriptor.receiver
            elif isinstance(descriptor, ReadCapability):
                receiver = descriptor.receiver_family
            elif (
                isinstance(descriptor, RecoveryCapability)
                and descriptor.callable_path is not None
                and f".{receiver_family}." in descriptor.callable_path
            ):
                receiver = receiver_family
            if receiver != receiver_family:
                continue
            callable_name = descriptor.public_entrypoint.partition("(")[0]
            member_name = callable_name.rsplit(".", 1)[-1]
            if member_name not in names:
                names.append(member_name)
        return tuple(names)

    def public_member_calls(self, receiver_family: str) -> tuple[str, ...]:
        """Return receiver-relative registered call skeletons."""

        calls: list[str] = []
        member_names = set(self.public_member_names(receiver_family))
        for descriptor in self._descriptors:
            callable_text = descriptor.public_entrypoint
            callable_path, separator, call_suffix = callable_text.partition("(")
            callable_name = callable_path.rsplit(".", 1)[-1]
            if callable_name not in member_names:
                continue
            if isinstance(descriptor, OperatorCapability):
                receiver = descriptor.receiver
            elif isinstance(descriptor, ReadCapability):
                receiver = descriptor.receiver_family
            elif (
                isinstance(descriptor, RecoveryCapability)
                and descriptor.callable_path is not None
                and f".{receiver_family}." in descriptor.callable_path
            ):
                receiver = receiver_family
            else:
                receiver = None
            if receiver != receiver_family:
                continue
            call = f".{callable_name}"
            if separator:
                call += f"({call_suffix}"
            if call not in calls:
                calls.append(call)
        return tuple(calls)

    def public_object_members(
        self,
        type_name: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return registry-backed properties and methods for one public object."""

        contract = PUBLIC_OBJECT_CONTRACTS.get(type_name)
        if contract is None:
            return (), ()
        methods = tuple(
            dict.fromkeys((*contract.intrinsic_methods, *self.public_member_names(type_name)))
        )
        return contract.properties, methods

    def producer_targets(self, input_family: InputFamily) -> tuple[LiveHelpTarget, ...]:
        """Return registered producers and acquisition paths for one input family."""

        targets: list[LiveHelpTarget] = []
        for descriptor in self._descriptors:
            if isinstance(descriptor, OperatorCapability):
                family = descriptor.output_contract.family
                if not isinstance(family, SameAsInputFamily) and family == input_family:
                    targets.append(
                        LiveHelpTarget(
                            surface="analysis",
                            canonical_id=descriptor.help_target,
                        )
                    )
            elif (
                isinstance(descriptor, ConstructorCapability)
                and descriptor.produced_input_family == input_family
            ) or (
                isinstance(descriptor, ReadCapability)
                and descriptor.produced_input_family == input_family
            ):
                targets.append(
                    LiveHelpTarget(
                        surface="analysis",
                        canonical_id=descriptor.help_target,
                    )
                )
        for handoff in SEMANTIC_HANDOFF_CONTRACTS.values():
            if handoff.input_family == input_family:
                targets.append(
                    LiveHelpTarget(
                        surface="analysis",
                        canonical_id=f"catalog.{handoff.collection_property}",
                    )
                )
        if input_family == "EventPattern":
            targets = [
                target
                for target in targets
                if not (target.surface == "analysis" and target.canonical_id == "sequence")
            ]
            targets.extend(SEMANTIC_HANDOFF_CONTRACTS[SemanticKind.EVENT].preparation_targets)
        return tuple(dict.fromkeys(targets))

    def semantic_handoff(self, semantic_kind: str) -> SemanticHandoffContract | None:
        """Return the typed analysis handoff for one semantic kind value."""

        try:
            kind = SemanticKind(semantic_kind)
        except ValueError:
            return None
        return SEMANTIC_HANDOFF_CONTRACTS.get(kind)

    def semantic_handoffs_for_input_family(
        self,
        input_family: InputFamily,
    ) -> tuple[SemanticHandoffContract, ...]:
        """Return semantic acquisition contracts satisfying one input family."""

        return tuple(
            handoff
            for handoff in SEMANTIC_HANDOFF_CONTRACTS.values()
            if handoff.input_family == input_family
        )

    # -- Lookup -----------------------------------------------------------

    def by_id(self, capability_id: str) -> CapabilityDescriptor:
        """Return the descriptor with the given canonical id."""
        descriptor = self._by_id[capability_id]
        if isinstance(
            descriptor,
            (AnalysisNavigationTopic, AnalysisMethodFamily, AnalysisArtifactFamilyContract),
        ):
            raise KeyError(capability_id)
        return descriptor

    def canonical_ids(self) -> tuple[str, ...]:
        """Return canonical help targets in native registry order."""
        return self.help_targets

    def by_canonical_id(self, canonical_id: str) -> AnalysisHelpDescriptor:
        """Resolve canonical help grammar, then the native capability id."""
        try:
            return self.by_help_target(canonical_id)
        except KeyError:
            return self.by_id(canonical_id)

    def by_help_target(self, help_target: str) -> AnalysisHelpDescriptor:
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

    def compatible_consumers(
        self,
        output: ArtifactOutputContract,
    ) -> tuple[str, ...]:
        """Return consumers compatible with all statically known output facts."""

        family = output.family
        if isinstance(family, SameAsInputFamily):
            return ()
        consumers: set[str] = set()
        for desc in self._descriptors:
            if not isinstance(desc, (OperatorCapability, BoundaryCapability)):
                continue
            for parameter, accepted in desc.accepted_inputs.items():
                if family not in accepted:
                    continue
                if isinstance(desc, OperatorCapability):
                    admission = desc.artifact_admission.get(parameter)
                    if admission is not None:
                        shapes = admission.semantic_shapes.get(family)
                        if (
                            shapes
                            and output.semantic_shapes
                            and shapes.isdisjoint(output.semantic_shapes)
                        ):
                            continue
                        matching = admission.matching_kinds.get(family)
                        if (
                            matching
                            and output.matching_kinds
                            and matching.isdisjoint(output.matching_kinds)
                        ):
                            continue
                consumers.add(desc.id)
        return tuple(sorted(consumers))


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------

_MF: frozenset[InputFamily] = frozenset({"MetricFrame"})
_EF: frozenset[InputFamily] = frozenset({"EventFrame"})
_LF: frozenset[InputFamily] = frozenset({"LifecycleFrame"})
_SS: frozenset[InputFamily] = frozenset({"SubjectSet"})
_DF: frozenset[InputFamily] = frozenset({"DeltaFrame"})
_MF_OR_DF: frozenset[InputFamily] = frozenset({"MetricFrame", "DeltaFrame"})
_CS: frozenset[InputFamily] = frozenset({"CandidateSet"})
_AF: frozenset[InputFamily] = frozenset({"AttributionFrame"})
_FIELD_SEMANTIC: frozenset[InputFamily] = frozenset({"DimensionSemantic", "TimeDimensionSemantic"})
_METRIC_ARTIFACT_SHAPES = frozenset({"scalar", "time_series", "segmented", "panel"})


def _output(
    family: Any,
    *,
    shapes: tuple[str, ...] = (),
    matching: tuple[str, ...] = (),
    nullable: bool = False,
) -> ArtifactOutputContract:
    """Build one immutable artifact output contract."""

    return ArtifactOutputContract(
        family=family,
        semantic_shapes=frozenset(shapes),
        matching_kinds=frozenset(matching),
        nullable=nullable,
    )


def _parameter_help(
    acquisition: str,
    *qualified_targets: str,
    required: bool = False,
    derivable_from_current_artifact: bool = False,
) -> ParameterHelpContract:
    """Build one parameter-level acquisition contract from qualified targets."""

    targets: list[LiveHelpTarget] = []
    for qualified_target in qualified_targets:
        surface, separator, canonical_id = qualified_target.partition(".")
        if not separator or not canonical_id:
            raise ValueError(f"invalid parameter help target: {qualified_target!r}")
        if surface == "analysis":
            owning_surface: Literal["analysis", "semantic"] = "analysis"
        elif surface == "semantic":
            owning_surface = "semantic"
        else:
            raise ValueError(f"invalid parameter help target: {qualified_target!r}")
        targets.append(LiveHelpTarget(surface=owning_surface, canonical_id=canonical_id))
    return ParameterHelpContract(
        acquisition=acquisition,
        help_targets=tuple(targets),
        required=required,
        derivable_from_current_artifact=derivable_from_current_artifact,
    )


def _build_registry() -> CapabilityRegistry:
    """Build the complete immutable capability registry."""

    # Late imports to avoid circular dependencies at module load time.
    from marivo.analysis import grain, time_scope
    from marivo.analysis.event import (
        declared_complete_through,
        every_start,
        first_per_subject,
        sequence,
        step,
    )
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.candidate import CandidateSet
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.funnel import funnel_loss_rate
    from marivo.analysis.lifecycle import from_inception, in_state
    from marivo.analysis.policies import (
        SamplingPolicy,
        day_of_week,
        occurrence_progress,
        period_correspondence,
        period_progress,
        window_bucket,
        working_day_progress,
    )
    from marivo.analysis.runtime_metric import aggregate, linear, ratio, slice, weighted_mean
    from marivo.analysis.subject import dropped_before
    from marivo.analysis.windows.spec import AbsoluteWindow

    all_artifact_families: frozenset[InputFamily] = frozenset(ARTIFACT_FAMILIES)

    descriptors: list[AnalysisHelpDescriptor] = []
    root_navigation_topics = _root_navigation_topics()
    slice2_navigation_topics = _slice2_navigation_topics()
    slice3_navigation_topics = _slice3_navigation_topics()
    slice2_method_families = _slice2_method_families()
    slice2_artifact_contracts = _slice2_artifact_contracts()

    # -- Session operators ------------------------------------------------

    descriptors.append(
        OperatorCapability(
            id="observe",
            public_entrypoint="session.observe(...)",
            help_target="observe",
            summary=(
                "Materialize exact current-catalog entries/refs or closed runtime "
                "metric expressions through one bounded graph into a typed MetricFrame; "
                "grain accepts builtin mv.grain(...) values or certified semantic "
                "ms.calendar_grain(...) values."
            ),
            constraint_ids=(
                "metric_expression_resolvable",
                "metric_readiness_verified",
                "window_absolute_parseable",
                "observe_time_grain_compatible",
            ),
            callable_path="marivo.analysis.session.core.Session.observe",
            authority_policy="semantic_current",
            receiver="Session",
            accepted_inputs={
                "metrics": frozenset(
                    {"MetricSemantic", "RuntimeMetricExpression", "OntologyMetricCandidate"}
                ),
                "time_scope": frozenset({"TimeScopeInput"}),
                "dimensions": _FIELD_SEMANTIC,
                "slice_by": _FIELD_SEMANTIC,
                "time_dimension": frozenset({"TimeDimensionSemantic"}),
                "cohort": _SS,
            },
            parameter_help={
                "grain": _parameter_help(
                    "build a builtin or certified semantic Grain",
                    "analysis.grain",
                    "semantic.calendar_grain",
                ),
                "expect_shape": _parameter_help(
                    "choose one closed expected MetricFrame shape",
                    "analysis.SemanticShape",
                ),
            },
            artifact_admission={
                "cohort": ArtifactAdmissionRule(
                    coverage_statuses={"SubjectSet": frozenset({"ready"})},
                ),
            },
            output_contract=_output("MetricFrame"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="events.match",
            public_entrypoint="session.events.match(...)",
            help_target="events.match",
            summary=(
                "Match a typed EventPattern into dense subject journeys with "
                "explicit follow-up completeness."
            ),
            constraint_ids=(
                "event_pattern_valid",
                "event_window_valid",
                "event_completeness_valid",
            ),
            callable_path="marivo.analysis.session.core.SessionEvents.match",
            authority_policy="semantic_current",
            receiver="SessionEvents",
            accepted_inputs={
                "pattern": frozenset({"EventPattern"}),
                "cohort_window": frozenset({"TimeScopeInput"}),
                "matching": frozenset({"EventMatchingPolicy"}),
                "completeness": frozenset({"CompletenessDeclaration"}),
                "cohort": _SS,
            },
            artifact_admission={
                "cohort": ArtifactAdmissionRule(
                    coverage_statuses={"SubjectSet": frozenset({"ready"})},
                ),
            },
            output_contract=_output("EventFrame", shapes=("journey",)),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="events.funnel",
            public_entrypoint="session.events.funnel(...)",
            help_target="events.funnel",
            summary=(
                "Reduce first-per-subject journeys into censoring-aware funnel "
                "counts with optional governed subject axes."
            ),
            constraint_ids=("event_reducer_source_valid", "event_subject_axis_valid"),
            callable_path="marivo.analysis.session.core.SessionEvents.funnel",
            authority_policy="semantic_current",
            receiver="SessionEvents",
            accepted_inputs={
                "journeys": _EF,
                "axes": frozenset({"DimensionSemantic"}),
            },
            artifact_admission={
                "journeys": ArtifactAdmissionRule(
                    semantic_shapes={"EventFrame": frozenset({"journey"})},
                    matching_kinds={"EventFrame": frozenset({"first_per_subject"})},
                ),
            },
            output_contract=_output(
                "EventFrame",
                shapes=("funnel",),
                matching=("first_per_subject",),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="lifecycle.replay",
            public_entrypoint="session.lifecycle.replay(...)",
            help_target="lifecycle.replay",
            summary=(
                "Replay one exact StateModel from its explicit inception seed into "
                "canonical clipped state history."
            ),
            constraint_ids=(
                "lifecycle_model_valid",
                "lifecycle_window_valid",
                "lifecycle_seed_valid",
                "event_completeness_valid",
            ),
            callable_path="marivo.analysis.session.core.SessionLifecycle.replay",
            authority_policy="semantic_current",
            receiver="SessionLifecycle",
            accepted_inputs={
                "model": frozenset({"StateModelSemantic"}),
                "window": frozenset({"TimeScopeInput"}),
                "seed": frozenset({"LifecycleSeed"}),
                "completeness": frozenset({"CompletenessDeclaration"}),
                "cohort": _SS,
            },
            artifact_admission={
                "cohort": ArtifactAdmissionRule(
                    coverage_statuses={"SubjectSet": frozenset({"ready"})},
                ),
            },
            output_contract=_output("LifecycleFrame", shapes=("history",)),
        )
    )

    lifecycle_reducer_specs: tuple[tuple[str, str, str, AuthorityPolicy], ...] = (
        (
            "lifecycle.distribution",
            "Reduce replay history into dense point-in-time state distributions.",
            "marivo.analysis.session.core.SessionLifecycle.distribution",
            "semantic_current",
        ),
        (
            "lifecycle.transitions",
            "Count dense modeled state pairs from committed replay history.",
            "marivo.analysis.session.core.SessionLifecycle.transitions",
            "materialized_only",
        ),
        (
            "lifecycle.dwell",
            "Summarize completed and censored state intervals from committed history.",
            "marivo.analysis.session.core.SessionLifecycle.dwell",
            "materialized_only",
        ),
        (
            "lifecycle.violations",
            "Expose the fixed-contract illegal modeled-Event trace from replay.",
            "marivo.analysis.session.core.SessionLifecycle.violations",
            "materialized_only",
        ),
    )
    for capability_id, summary, callable_path, authority_policy in lifecycle_reducer_specs:
        descriptors.append(
            OperatorCapability(
                id=capability_id,
                public_entrypoint=f"session.{capability_id}(...)",
                help_target=capability_id,
                summary=summary,
                constraint_ids=("lifecycle_reducer_source_valid",),
                callable_path=callable_path,
                authority_policy=authority_policy,
                receiver="SessionLifecycle",
                accepted_inputs={
                    "history": _LF,
                    **(
                        {"axes": frozenset({"DimensionSemantic"})}
                        if capability_id == "lifecycle.distribution"
                        else {}
                    ),
                },
                artifact_admission={
                    "history": ArtifactAdmissionRule(
                        semantic_shapes={"LifecycleFrame": frozenset({"history"})},
                    ),
                },
                output_contract=_output(
                    "LifecycleFrame",
                    shapes=(capability_id.rsplit(".", 1)[-1],),
                ),
            )
        )

    descriptors.append(
        OperatorCapability(
            id="events.time_to_event",
            public_entrypoint="session.events.time_to_event(...)",
            help_target="events.time_to_event",
            summary=(
                "Project exact persisted journey assignments into time-to-event rows "
                "without querying or rematching Events, with optional governed "
                "subject axes to group elapsed durations."
            ),
            constraint_ids=(
                "event_reducer_source_valid",
                "event_step_pair_valid",
                "event_subject_axis_valid",
            ),
            callable_path="marivo.analysis.session.core.SessionEvents.time_to_event",
            authority_policy="semantic_current",
            receiver="SessionEvents",
            accepted_inputs={
                "journeys": _EF,
                "axes": frozenset({"DimensionSemantic"}),
            },
            parameter_help={
                "start_step": _parameter_help(
                    "select one exact earlier step from journeys.meta.pattern.steps",
                    "analysis.PatternStep",
                    "analysis.step",
                    required=True,
                    derivable_from_current_artifact=True,
                ),
                "end_step": _parameter_help(
                    "select one exact later step from journeys.meta.pattern.steps",
                    "analysis.PatternStep",
                    "analysis.step",
                    required=True,
                    derivable_from_current_artifact=True,
                ),
            },
            artifact_admission={
                "journeys": ArtifactAdmissionRule(
                    semantic_shapes={"EventFrame": frozenset({"journey"})},
                ),
            },
            output_contract=_output("EventFrame", shapes=("time_to_event",)),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="select_subjects",
            public_entrypoint="session.select_subjects(...)",
            help_target="select_subjects",
            summary=(
                "Materialize a closed typed SubjectSet from resolved Event loss "
                "or replayed Lifecycle state."
            ),
            constraint_ids=("event_reducer_source_valid", "subject_selection_valid"),
            callable_path="marivo.analysis.session.core.Session.select_subjects",
            authority_policy="semantic_current",
            receiver="Session",
            accepted_inputs={
                "artifact": _EF | _LF,
                "selection": frozenset({"SubjectSelection"}),
            },
            artifact_admission={
                "artifact": ArtifactAdmissionRule(
                    semantic_shapes={
                        "EventFrame": frozenset({"journey"}),
                        "LifecycleFrame": frozenset({"history"}),
                    },
                    matching_kinds={"EventFrame": frozenset({"first_per_subject"})},
                ),
            },
            output_contract=_output("SubjectSet"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="compare",
            public_entrypoint="session.compare(...)",
            help_target="compare",
            summary=(
                "Compute a typed Metric delta, including canonical cumulative-period "
                "pairing, or exactly align two compatible EventFrame[funnel] artifacts. "
                "MetricFrame inputs must each carry a single metric (arity=1); a "
                'multi-metric frame is projected with frame.metric("<metric_id>") '
                "before comparing."
            ),
            constraint_ids=(
                "frame_kind_compatible",
                "single_metric_input",
                "alignment_policy_shape",
                "cumulative_compare_compatible",
                "funnel_comparison_compatible",
            ),
            callable_path="marivo.analysis.session.core.Session.compare",
            authority_policy="materialized_only",
            receiver="Session",
            accepted_inputs={
                "current": _MF | _EF,
                "baseline": _MF | _EF,
                "alignment": frozenset({"AlignmentPolicy"}),
            },
            artifact_admission={
                "current": ArtifactAdmissionRule(
                    semantic_shapes={"EventFrame": frozenset({"funnel"})},
                    matching_kinds={"EventFrame": frozenset({"first_per_subject"})},
                ),
                "baseline": ArtifactAdmissionRule(
                    semantic_shapes={"EventFrame": frozenset({"funnel"})},
                    matching_kinds={"EventFrame": frozenset({"first_per_subject"})},
                ),
            },
            output_contract=_output(
                "DeltaFrame",
                shapes=("scalar", "time_series", "segmented", "panel", "funnel"),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="attribute",
            public_entrypoint="session.attribute(...)",
            help_target="attribute",
            summary=(
                "Attribute a DeltaFrame's movement over explicit axes with "
                "reconciled contributions and explicit share denominators. The installed "
                "automatic methods are additive/component allocation, exact distinct "
                "membership, exact value-frequency quantiles, Trino native approx_percentile "
                "replay, and native Top-K grouping into a governed Other player, plus "
                "typed cumulative endpoint/base-flow routes; non-mergeable reservoir quantiles "
                "and unsupported cumulative route combinations remain blocked by the delta "
                "contract."
            ),
            constraint_ids=(
                "frame_kind_compatible",
                "attribution_additivity_compatible",
                "attribution_reconciliation",
                "cumulative_attribution_route_compatible",
                "funnel_attribution_target_valid",
                "funnel_attribution_reconciliation",
            ),
            callable_path="marivo.analysis.session.core.Session.attribute",
            authority_policy="semantic_current",
            receiver="Session",
            accepted_inputs={
                "frame": _DF,
                "axes": _FIELD_SEMANTIC,
                "target": frozenset({"FunnelLossRate"}),
            },
            parameter_help={
                "mode": _parameter_help(
                    "choose the closed multi-axis row layout or omit when allowed",
                    "analysis.AttributionMode",
                ),
            },
            artifact_admission={
                "frame": ArtifactAdmissionRule(
                    semantic_shapes={
                        "DeltaFrame": frozenset(
                            {"scalar", "time_series", "segmented", "panel", "funnel"}
                        )
                    },
                ),
            },
            output_contract=_output("AttributionFrame"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="correlate",
            public_entrypoint="session.correlate(...)",
            help_target="correlate",
            summary="Measure the association between two MetricFrames.",
            constraint_ids=(
                "frame_kind_compatible",
                "alignment_policy_shape",
                "correlate_lag_semantics",
                "single_metric_input",
            ),
            callable_path="marivo.analysis.session.core.Session.correlate",
            authority_policy="materialized_only",
            receiver="Session",
            accepted_inputs={
                "a": _MF,
                "b": _MF,
                "alignment": frozenset({"AlignmentPolicy"}),
            },
            output_contract=_output("AssociationResult"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="hypothesis_test",
            public_entrypoint="session.hypothesis_test(...)",
            help_target="hypothesis_test",
            summary="Run a paired hypothesis test over two MetricFrames.",
            constraint_ids=(
                "frame_kind_compatible",
                "alignment_policy_shape",
                "single_metric_input",
            ),
            callable_path="marivo.analysis.session.core.Session.hypothesis_test",
            authority_policy="materialized_only",
            receiver="Session",
            accepted_inputs={
                "a": _MF,
                "b": _MF,
                "alignment": frozenset({"AlignmentPolicy"}),
                "sampling": frozenset({"SamplingPolicy"}),
            },
            output_contract=_output("HypothesisTestResult"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="forecast",
            public_entrypoint="session.forecast(...)",
            help_target="forecast",
            summary=(
                "Project a time_series or panel MetricFrame forward; certified semantic "
                "periods use their exact ordinal binding and future coverage."
            ),
            constraint_ids=("forecast_input_shape", "single_metric_input"),
            callable_path="marivo.analysis.session.core.Session.forecast",
            authority_policy="materialized_only",
            receiver="Session",
            accepted_inputs={
                "history": _MF,
            },
            artifact_admission={
                "history": ArtifactAdmissionRule(
                    semantic_shapes={
                        "MetricFrame": frozenset({"time_series", "panel"}),
                    }
                )
            },
            output_contract=_output("ForecastFrame"),
        )
    )

    # -- Discover operators -----------------------------------------------

    _discover_specs: tuple[
        tuple[
            str,
            str,
            frozenset[InputFamily],
            Mapping[str, frozenset[InputFamily]],
            Mapping[ArtifactFamily, frozenset[str]],
        ],
        ...,
    ] = (
        (
            "discover.point_anomalies",
            "Find time-series points with unusual values.",
            _MF,
            {},
            {"MetricFrame": frozenset({"time_series", "panel"})},
        ),
        (
            "discover.period_shifts",
            "Find period-shift candidates from a DeltaFrame.",
            _DF,
            {},
            {"DeltaFrame": frozenset({"time_series", "panel"})},
        ),
        (
            "discover.driver_axes",
            "Find dimensions that explain a delta.",
            _DF,
            {"search_space": _FIELD_SEMANTIC},
            {"DeltaFrame": _METRIC_ARTIFACT_SHAPES},
        ),
        (
            "discover.interesting_slices",
            "Find dimension slices with notable values.",
            _MF_OR_DF,
            {"search_space": _FIELD_SEMANTIC},
            {
                "MetricFrame": frozenset({"time_series", "segmented", "panel"}),
                "DeltaFrame": frozenset({"time_series", "segmented", "panel"}),
            },
        ),
        (
            "discover.interesting_windows",
            "Find time windows with notable behavior.",
            _MF_OR_DF,
            {},
            {
                "MetricFrame": frozenset({"time_series", "panel"}),
                "DeltaFrame": frozenset({"time_series", "panel"}),
            },
        ),
        (
            "discover.cross_sectional_outliers",
            "Find segments that are outliers compared to their peers.",
            _MF,
            {"peer_scope": _FIELD_SEMANTIC},
            {"MetricFrame": frozenset({"segmented", "panel"})},
        ),
    )

    for obj_id, summary, source_families, extra_inputs, source_shapes in _discover_specs:
        objective = obj_id.split(".", 1)[1]
        # Objectives that accept a MetricFrame source gate on single-metric
        # arity via require_single_metric; declare the precondition in help.
        discover_constraints: tuple[str, ...] = (
            "discover_minimum_evidence",
            "frame_kind_compatible",
        )
        if "MetricFrame" in source_families:
            discover_constraints = (*discover_constraints, "single_metric_input")
        descriptors.append(
            OperatorCapability(
                id=obj_id,
                public_entrypoint=f"session.discover.{objective}(...)",
                help_target=obj_id,
                summary=summary,
                constraint_ids=discover_constraints,
                callable_path=f"marivo.analysis.session.core.SessionDiscoverNamespace.{objective}",
                authority_policy="materialized_only",
                receiver="SessionDiscoverNamespace",
                accepted_inputs={
                    "source": source_families,
                    **extra_inputs,
                },
                parameter_help=(
                    {
                        "strategy": _parameter_help(
                            "choose the point-anomaly scoring strategy or omit for zscore",
                            "analysis.PointAnomalyStrategy",
                        )
                    }
                    if obj_id == "discover.point_anomalies"
                    else {}
                ),
                artifact_admission={
                    "source": ArtifactAdmissionRule(semantic_shapes=source_shapes),
                },
                output_contract=_output("CandidateSet"),
            )
        )

    descriptors.append(
        OperatorCapability(
            id="discover.semantic_hypotheses",
            public_entrypoint="session.discover.semantic_hypotheses(source, limit=50)",
            help_target="discover.semantic_hypotheses",
            summary=(
                "Resolve bounded unscored Metric hypotheses through one explicit "
                "ontology edge while preserving the exact source scope."
            ),
            constraint_ids=("frame_kind_compatible",),
            callable_path=(
                "marivo.analysis.session.core.SessionDiscoverNamespace.semantic_hypotheses"
            ),
            authority_policy="semantic_current",
            receiver="SessionDiscoverNamespace",
            accepted_inputs={"source": _MF_OR_DF},
            artifact_admission={
                "source": ArtifactAdmissionRule(
                    semantic_shapes={
                        "MetricFrame": frozenset({"scalar", "time_series", "segmented", "panel"}),
                        "DeltaFrame": frozenset({"scalar", "time_series", "segmented", "panel"}),
                    }
                )
            },
            output_contract=_output("CandidateSet", shapes=("semantic_hypothesis",)),
        )
    )

    # -- Transform operators ----------------------------------------------

    shared_transform_ops: tuple[
        tuple[str, str, frozenset[InputFamily], Mapping[str, frozenset[InputFamily]]], ...
    ] = (
        (
            "filter",
            "Filter rows using a boolean predicate over public frame columns.",
            _MF_OR_DF,
            {},
        ),
        (
            "slice",
            "Filter rows by catalog-backed axis values.",
            _MF_OR_DF,
            {"slice_by": _FIELD_SEMANTIC},
        ),
        (
            "rollup",
            "Aggregate by dropping axes or re-bucketing time; certified semantic "
            "Grain roll-ups require containment and complete source periods.",
            _MF_OR_DF,
            {"drop_axes": _FIELD_SEMANTIC},
        ),
        ("topk", "Keep the largest rows ordered by a public frame column.", _MF_OR_DF, {}),
        (
            "bottomk",
            "Keep the smallest rows ordered by a public frame column.",
            _MF_OR_DF,
            {},
        ),
        (
            "rank",
            "Add a rank column ordered by a public frame column; value is reserved "
            "for canonical MetricFrame storage and cannot be used as rank_column.",
            _MF_OR_DF,
            {},
        ),
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
                constraint_ids=(
                    "transform_arguments",
                    "transform_frame_shape",
                    "transform_operator_supported",
                    "single_metric_input",
                ),
                callable_path=f"marivo.analysis.frames.transforms._FrameTransforms.{op_name}",
                authority_policy="materialized_only",
                receiver="MetricFrameTransforms|DeltaFrameTransforms",
                accepted_inputs={
                    "receiver": families,
                    **extra_inputs,
                },
                artifact_admission={
                    "receiver": ArtifactAdmissionRule(
                        semantic_shapes={
                            "MetricFrame": _METRIC_ARTIFACT_SHAPES,
                            "DeltaFrame": _METRIC_ARTIFACT_SHAPES,
                        }
                    )
                },
                parameter_help=(
                    {
                        "grain": _parameter_help(
                            "build a coarser builtin or certified semantic Grain",
                            "analysis.grain",
                            "semantic.calendar_grain",
                        )
                    }
                    if op_name == "rollup"
                    else {
                        "method": _parameter_help(
                            "choose one closed tie-handling method",
                            "analysis.RankMethod",
                        )
                    }
                    if op_name == "rank"
                    else {}
                ),
                output_contract=_output(SameAsInputFamily(parameter="receiver")),
            )
        )

    # normalize is MetricFrame-only
    descriptors.append(
        OperatorCapability(
            id="transform.normalize",
            public_entrypoint="frame.transform.normalize(...)",
            help_target="transform.normalize",
            summary="Normalize MetricFrame values.",
            constraint_ids=(
                "transform_arguments",
                "transform_frame_shape",
                "transform_operator_supported",
                "single_metric_input",
            ),
            callable_path="marivo.analysis.frames.transforms.MetricFrameTransforms.normalize",
            authority_policy="materialized_only",
            receiver="MetricFrameTransforms",
            accepted_inputs={
                "receiver": _MF,
            },
            parameter_help={
                "mode": _parameter_help(
                    "choose one closed normalization mode",
                    "analysis.NormalizeKind",
                    required=True,
                ),
                "baseline": _parameter_help(
                    "omit when allowed or build an exact numeric/row-selector mapping",
                    "analysis.NormalizeBaseline",
                ),
            },
            output_contract=_output("MetricFrame"),
        )
    )

    # -- Frame methods (operators / reads) --------------------------------

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.metric",
            public_entrypoint="frame.metric(...)",
            help_target="MetricFrame.metric",
            summary="Project one metric out of a multi-metric frame.",
            constraint_ids=("frame_kind_compatible",),
            callable_path="marivo.analysis.frames.metric.MetricFrame.metric",
            authority_policy="materialized_only",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_contract=_output("MetricFrame"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.components",
            public_entrypoint="frame.components()",
            help_target="MetricFrame.components",
            summary="Load the recursive component graph persisted for a MetricFrame.",
            constraint_ids=("component_frame_available",),
            callable_path="marivo.analysis.frames.metric.MetricFrame.components",
            authority_policy="materialized_only",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_contract=_output("ComponentFrame"),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="MetricFrame.coverage",
            public_entrypoint="frame.coverage()",
            help_target="MetricFrame.coverage",
            summary="Load the linked CoverageFrame for this metric frame.",
            constraint_ids=(),
            callable_path="marivo.analysis.frames.metric.MetricFrame.coverage",
            authority_policy="materialized_only",
            receiver="MetricFrame",
            accepted_inputs={"receiver": _MF},
            output_contract=_output("CoverageFrame", nullable=True),
            additional_examples=(
                HelpExample(
                    label="Guard nullable coverage",
                    code="coverage = frame.coverage()",
                    requires=("frame",),
                ),
            ),
        )
    )

    descriptors.append(
        OperatorCapability(
            id="DeltaFrame.components",
            public_entrypoint="frame.components()",
            help_target="DeltaFrame.components",
            summary="Load the linked ComponentFrame for component-aware deltas.",
            constraint_ids=("component_frame_available",),
            callable_path="marivo.analysis.frames.delta.DeltaFrame.components",
            authority_policy="materialized_only",
            receiver="DeltaFrame",
            accepted_inputs={"receiver": _DF},
            artifact_admission={
                "receiver": ArtifactAdmissionRule(
                    semantic_shapes={"DeltaFrame": _METRIC_ARTIFACT_SHAPES}
                )
            },
            output_contract=_output("ComponentFrame"),
        )
    )

    descriptors.append(
        ReadCapability(
            id="CandidateSet.select",
            public_entrypoint="candidates.select(item_id=...)",
            help_target="CandidateSet.select",
            summary="Return one closed shape-specific selection by its stable item_id.",
            constraint_ids=("frame_kind_compatible",),
            callable_path="marivo.analysis.frames.candidate.CandidateSet.select",
            receiver_family="CandidateSet",
            result_kind="defensive_copy",
            read_bound="bounded",
            produced_input_family="OntologyMetricCandidate",
            artifact_output_by_shape={
                "point_anomaly": "PointAnomalySelection",
                "period_shift": "PeriodShiftSelection",
                "driver_axis": "DriverAxisSelection",
                "slice": "SliceSelection",
                "window": "WindowSelection",
                "cross_sectional_outlier": "CrossSectionalOutlierSelection",
                "semantic_hypothesis": "OntologyMetricCandidate",
            },
            exposes_artifact_affordance=True,
        )
    )
    descriptors.append(
        ReadCapability(
            id="AttributionFrame.at_resolution",
            public_entrypoint="frame.at_resolution(axes=[...])",
            help_target="AttributionFrame.at_resolution",
            summary="Select one exact ordered semantic-ref resolution without a query.",
            constraint_ids=("frame_kind_compatible",),
            callable_path=("marivo.analysis.frames.attribution.AttributionFrame.at_resolution"),
            receiver_family="AttributionFrame",
            result_kind="defensive_copy",
            read_bound="bounded",
            output_type="AttributionFrame",
            exposes_artifact_affordance=True,
            additional_examples=(
                HelpExample(
                    label="Select one exact ordered semantic-ref prefix",
                    code="regional = frame.at_resolution(axes=[region])",
                    requires=("frame", "region"),
                ),
            ),
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
                "as_semantic_hypothesis",
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
            constraint_ids=(),
            callable_path="marivo.analysis.frames.delta.DeltaFrame.predicted_attribution_shape",
            receiver_family="DeltaFrame",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    # -- BaseFrame reads --------------------------------------------------

    descriptors.append(
        OperatorCapability(
            id="BaseFrame.quality_report",
            public_entrypoint="frame.quality_report()",
            help_target="BaseFrame.quality_report",
            summary="Load the construction-time quality report linked to this Artifact.",
            constraint_ids=(),
            callable_path="marivo.analysis.frames.base.BaseFrame.quality_report",
            authority_policy="materialized_only",
            receiver="BaseFrame",
            accepted_inputs={"receiver": _MF | _EF | _LF | _DF | _AF},
            output_contract=_output("QualityReport", nullable=True),
        )
    )

    descriptors.append(
        ReadCapability(
            id="BaseFrame.show",
            public_entrypoint="frame.show()",
            help_target="BaseFrame.show",
            summary="Bounded inspection of the artifact.",
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
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.Session.{method_name}",
                receiver_family="Session",
                result_kind="terminal_text",
                read_bound="bounded",
            )
        )

    descriptors.append(
        ReadCapability(
            id="events.watermark",
            public_entrypoint="session.events.watermark(...)",
            help_target="events.watermark",
            summary="Return the authoritative observed completeness watermark for one Event, or None.",
            constraint_ids=(),
            callable_path="marivo.analysis.session.core.SessionEvents.watermark",
            receiver_family="SessionEvents",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    descriptors.append(
        ReadCapability(
            id="events.occurrence_bounds",
            public_entrypoint="session.events.occurrence_bounds(...)",
            help_target="events.occurrence_bounds",
            summary=(
                "Return EventOccurrenceBounds for one exact Event or "
                "StateModel's observed occurrences."
            ),
            constraint_ids=(),
            callable_path=("marivo.analysis.session.core.SessionEvents.occurrence_bounds"),
            receiver_family="SessionEvents",
            result_kind="immutable_metadata",
            read_bound="bounded",
        )
    )

    descriptors.append(
        ReadCapability(
            id="BaseFrame.contract",
            public_entrypoint="frame.contract()",
            help_target="BaseFrame.contract",
            summary="Return the mechanical consumption contract for the artifact.",
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

    constructor_specs: tuple[
        tuple[str, str, str, str, object, str, InputFamily | None],
        ...,
    ] = (
        (
            "grain",
            "mv.grain(...)",
            "grain",
            "Construct one builtin aggregation Grain.",
            grain,
            "Grain",
            None,
        ),
        (
            "funnel_loss_rate",
            "mv.funnel_loss_rate(...)",
            "funnel_loss_rate",
            "Target one exact non-initial funnel PatternStep loss rate.",
            funnel_loss_rate,
            "FunnelLossRate",
            "FunnelLossRate",
        ),
        (
            "step",
            "mv.step(...)",
            "step",
            "Construct one typed Event Journey step from a participant role.",
            step,
            "PatternStep",
            None,
        ),
        (
            "sequence",
            "mv.sequence(...)",
            "sequence",
            "Construct an ordered EventPattern from typed steps.",
            sequence,
            "EventPattern",
            "EventPattern",
        ),
        (
            "first_per_subject",
            "mv.first_per_subject()",
            "first_per_subject",
            "Choose the earliest first-step occurrence per subject.",
            first_per_subject,
            "FirstPerSubject",
            "EventMatchingPolicy",
        ),
        (
            "every_start",
            "mv.every_start(...)",
            "every_start",
            "Create one attempt per first-step occurrence.",
            every_start,
            "EveryStart",
            "EventMatchingPolicy",
        ),
        (
            "declared_complete_through",
            "mv.declared_complete_through(...)",
            "declared_complete_through",
            "Declare exact Event inputs complete through a follow-up bound.",
            declared_complete_through,
            "CompletenessDeclaration",
            "CompletenessDeclaration",
        ),
        (
            "dropped_before",
            "mv.dropped_before(...)",
            "dropped_before",
            "Select resolved subjects lost before one exact EventPattern step.",
            dropped_before,
            "DroppedBefore",
            "SubjectSelection",
        ),
        (
            "from_inception",
            "mv.from_inception()",
            "from_inception",
            "Construct the required first-inception Lifecycle replay seed.",
            from_inception,
            "FromInception",
            "LifecycleSeed",
        ),
        (
            "in_state",
            "mv.in_state(...)",
            "in_state",
            "Select subjects in one exact modeled state at an explicit instant.",
            in_state,
            "InState",
            "SubjectSelection",
        ),
        (
            "window_bucket",
            "mv.window_bucket()",
            "window_bucket",
            "Construct a window-bucket alignment policy.",
            window_bucket,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "day_of_week",
            "mv.day_of_week(...)",
            "day_of_week",
            "Construct a day-of-week containing-period alignment policy.",
            day_of_week,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "period_progress",
            "mv.period_progress(...)",
            "period_progress",
            "Construct same-progress alignment inside one certified target period.",
            period_progress,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "period_correspondence",
            "mv.period_correspondence(...)",
            "period_correspondence",
            "Construct named certified period correspondence alignment.",
            period_correspondence,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "occurrence_progress",
            "mv.occurrence_progress(...)",
            "occurrence_progress",
            "Construct same-local-day progress alignment inside two exact temporal occurrences.",
            occurrence_progress,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "working_day_progress",
            "mv.working_day_progress(...)",
            "working_day_progress",
            "Construct same-working-day ordinal alignment under one certified work schedule.",
            working_day_progress,
            "AlignmentPolicy",
            "AlignmentPolicy",
        ),
        (
            "time_scope",
            "mv.time_scope(...)",
            "time_scope",
            "Half-open time interval [start, end) for observe time_scope; "
            'start is inclusive and end is exclusive (for example, end="2026-08-01" '
            "includes all of July and excludes August 1).",
            time_scope,
            "TimeScope",
            "TimeScopeInput",
        ),
        (
            "AbsoluteWindow",
            "mv.time_scope(...)",
            "AbsoluteWindow",
            "Half-open time interval [start, end) with optional grain.",
            AbsoluteWindow,
            "AbsoluteWindow",
            "TimeScopeInput",
        ),
        (
            "SamplingPolicy",
            "mv.SamplingPolicy(...)",
            "SamplingPolicy",
            "Sampling policy for hypothesis_test.",
            SamplingPolicy,
            "SamplingPolicy",
            "SamplingPolicy",
        ),
    )

    for (
        cap_id,
        entrypoint,
        target,
        summary,
        callable_obj,
        output_type,
        produced_input_family,
    ) in constructor_specs:
        descriptors.append(
            ConstructorCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                constraint_ids=(
                    ("window_absolute_parseable",)
                    if cap_id in {"time_scope", "AbsoluteWindow"}
                    else ("alignment_policy_shape",)
                    if cap_id == "working_day_progress"
                    else ()
                ),
                callable_path=_module_path_for(callable_obj),
                output_type=output_type,
                produced_input_family=produced_input_family,
            )
        )

    descriptors.append(
        ConstructorCapability(
            id="Session.source_bindings",
            public_entrypoint="session.source_bindings({...})",
            help_target="Session.source_bindings",
            summary=(
                "Bind declared non-secret JSON request parameters to one Session runtime "
                "for a nested analysis execution scope."
            ),
            constraint_ids=("source_bindings_exact",),
            callable_path="marivo.analysis.session.core.Session.source_bindings",
            output_type="AbstractContextManager[None]",
            produced_input_family=None,
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
        (
            "runtime_metric.linear",
            "mv.runtime_metric.linear(...) ",
            linear,
            "RuntimeLinearExpr",
        ),
    )
    for cap_id, entrypoint, callable_obj, output_type in runtime_metric_specs:
        descriptors.append(
            ConstructorCapability(
                id=cap_id,
                public_entrypoint=entrypoint.rstrip(),
                help_target=cap_id,
                summary="Build one frozen node in the closed runtime metric expression algebra.",
                constraint_ids=(
                    "runtime_metric_closed_algebra",
                    *(
                        ("runtime_metric_fold_requires_semi_additive",)
                        if cap_id == "runtime_metric.aggregate"
                        else (
                            ("runtime_weighted_mean_valid",)
                            if cap_id == "runtime_metric.weighted_mean"
                            else (
                                ("runtime_linear_units_commensurable",)
                                if cap_id == "runtime_metric.linear"
                                else ()
                            )
                        )
                    ),
                ),
                callable_path=_module_path_for(callable_obj),
                output_type=output_type,
                produced_input_family="RuntimeMetricExpression",
            )
        )

    descriptors.append(
        ConstructorCapability(
            id="AttributionMode",
            public_entrypoint=('mode="joint" | mode="hierarchy"'),
            help_target="AttributionMode",
            summary=(
                "Multi-axis row layout: joint emits one additive row per complete axis "
                "combination; hierarchy emits ordered prefix rows. Typed hierarchy evidence "
                "marks additive/component results as rollup-safe and distinct/quantile "
                "results as independently reconciled at every prefix. "
                "Metric session.attribute calls default to joint for multiple axes. "
                "Funnel attribution and decompose still require an explicit multi-axis mode. "
                "Omit mode for one axis, where a supplied value has no effect. Mode is "
                "distinct from attribution method. DeltaFrame.contract().attribute_admission "
                "lists the exact legal pair and multiple-axes default."
            ),
            constraint_ids=(),
            callable_path=None,
            output_type='Literal["joint", "hierarchy"]',
        )
    )

    value_contract_specs: tuple[tuple[str, str, str, str], ...] = (
        (
            "SemanticShape",
            'expect_shape="scalar" | "time_series" | "segmented" | "panel"',
            (
                "Optional observe output-shape assertion. Omit it to accept the shape "
                "derived from grain and dimensions."
            ),
            'Literal["scalar", "time_series", "segmented", "panel"]',
        ),
        (
            "PointAnomalyStrategy",
            'strategy="zscore" | "seasonal_robust_zscore"',
            (
                "Point-anomaly scoring kernel. Omit strategy for zscore; use "
                "seasonal_robust_zscore for a day-of-week median/MAD baseline."
            ),
            'Literal["zscore", "seasonal_robust_zscore"]',
        ),
        (
            "RankMethod",
            'method="ordinal" | "dense" | "min" | "max"',
            "Rank tie handling. Omit method for ordinal ranking.",
            'Literal["ordinal", "dense", "min", "max"]',
        ),
        (
            "NormalizeKind",
            'mode="index" | "share" | "pct_change" | "per_unit" | "z_score"',
            (
                "Required metric normalization mode; there is no default. Only index and "
                "per_unit accept baseline; per_unit requires it."
            ),
            'Literal["index", "share", "pct_change", "per_unit", "z_score"]',
        ),
        (
            "NormalizeBaseline",
            'baseline={"value": <number>} | {<axis_column>: <value>, ...}',
            (
                "Normalize index/per_unit denominator. Omit it for index to use the first "
                "series-ordered row overall, or the first row per dimension group after time "
                "ordering; per_unit requires it. Share, pct_change, and z_score reject it. "
                'When supplied, pass {"value": <finite non-zero number>} or a non-empty '
                "selector over persisted frame columns; grouped selectors choose a row within "
                "each series and cannot include group axes."
            ),
            "dict[str, str | int | float | bool | None]",
        ),
    )
    for target, entrypoint, summary, output_type in value_contract_specs:
        descriptors.append(
            ConstructorCapability(
                id=target,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
                callable_path=None,
                output_type=output_type,
                produced_input_family=None,
            )
        )

    # -- Recovery / reads: session lifecycle ------------------------------

    recovery_specs: tuple[tuple[str, str, str, str, str, str, str], ...] = (
        (
            "session.get_or_create",
            "mv.session.get_or_create(...)",
            "session.get_or_create",
            "Create or reuse a named session and apply an explicit current question.",
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
            "session.resume",
            "mv.session.resume(session_id)",
            "session.resume",
            "Explicitly resume an existing project session by its immutable id.",
            "recovery",
            "Session",
            "session_id",
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
            "None",
            "session_name",
        ),
    )

    for cap_id, entrypoint, target, summary, _group, restored, identity in recovery_specs:
        descriptors.append(
            RecoveryCapability(
                id=cap_id,
                public_entrypoint=entrypoint,
                help_target=target,
                summary=summary,
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
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.Session.{method_name}",
                identity_input="session_id_or_frame_ref",
                restored_family=restored,
                query_behavior="none",
            )
        )

    descriptors.append(
        ReadCapability(
            id="session.revalidate",
            public_entrypoint="session.revalidate(frame)",
            help_target="session.revalidate",
            summary=(
                "Revalidate one committed Artifact against current semantic authority "
                "and persisted evidence integrity."
            ),
            constraint_ids=(),
            callable_path="marivo.analysis.session.core.Session.revalidate",
            receiver_family="Session",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="ArtifactRevalidation",
        )
    )

    # -- Evidence namespace reads -----------------------------------------

    evidence_specs: tuple[tuple[str, str, str, str], ...] = (
        (
            "session.evidence.compatibility",
            "session.evidence.compatibility(finding_ids=[...])",
            "session.evidence.compatibility",
            "Check one canonical Finding selection for mechanical compatibility.",
        ),
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
                constraint_ids=(),
                callable_path=f"marivo.analysis.session.core.EvidenceNamespace.{method_name}",
                receiver_family="EvidenceNamespace",
                result_kind="immutable_metadata",
                read_bound="bounded",
                output_type=(
                    "EvidenceCompatibility" if cap_id == "session.evidence.compatibility" else ""
                ),
            )
        )

    # -- Semantic catalog reads -------------------------------------------

    catalog_specs: tuple[tuple[str, str, str, str], ...] = (
        *(
            (
                f"catalog.{member.property_name}",
                f"catalog.{member.property_name}",
                f"catalog.{member.property_name}",
                f"Browse catalog {member.property_name}.",
            )
            for member in CATALOG_MEMBER_CONTRACTS
        ),
        (
            "catalog.require",
            "catalog.require(ref)",
            "catalog.require",
            "Require one exact ref in the compiled catalog.",
        ),
        (
            "catalog.readiness",
            "catalog.readiness(refs=...)",
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
                constraint_ids=(),
                callable_path=f"marivo.semantic.catalog.SemanticCatalog.{cap_id.split('.', 1)[1]}",
                receiver_family="SemanticCatalog",
                result_kind="immutable_metadata",
                read_bound="bounded",
            )
        )

    temporal_catalog_reads = (
        ReadCapability(
            id="catalog.period_calendars.grain",
            public_entrypoint="calendar.grain(level)",
            help_target="calendar.grain",
            summary="Return the governed Grain for one declared calendar level.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.grain",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="Grain",
        ),
        ReadCapability(
            id="catalog.period_calendars.period",
            public_entrypoint="calendar.period(level, key)",
            help_target="calendar.period",
            summary="Return one exact certified TimeScope for a named calendar period.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.period",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            produced_input_family="TimeScopeInput",
            output_type="TimeScope",
        ),
        ReadCapability(
            id="catalog.period_calendars.period_on",
            public_entrypoint="calendar.period_on(level, value)",
            help_target="calendar.period_on",
            summary="Return the exact certified TimeScope containing one civil date.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.period_on",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            produced_input_family="TimeScopeInput",
            output_type="TimeScope",
        ),
        ReadCapability(
            id="catalog.period_calendars.periods",
            public_entrypoint="calendar.periods(level, limit=20, cursor=None)",
            help_target="calendar.periods",
            summary="Browse one bounded page of certified periods for a calendar level.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.PeriodCalendarEntry.periods",
            receiver_family="PeriodCalendarEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="CalendarPeriodPage",
        ),
        ReadCapability(
            id="catalog.temporal_sets.occurrence",
            public_entrypoint="temporal_set.occurrence(key)",
            help_target="temporal_set.occurrence",
            summary="Return one exact certified TimeScope for a named temporal occurrence.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.TemporalSetEntry.occurrence",
            receiver_family="TemporalSetEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            produced_input_family="TimeScopeInput",
            output_type="TimeScope",
        ),
        ReadCapability(
            id="catalog.temporal_sets.occurrences",
            public_entrypoint="temporal_set.occurrences(limit=20, cursor=None)",
            help_target="temporal_set.occurrences",
            summary="Browse one bounded filtered page of certified temporal occurrences.",
            constraint_ids=(),
            callable_path="marivo.semantic.catalog.TemporalSetEntry.occurrences",
            receiver_family="TemporalSetEntry",
            result_kind="immutable_metadata",
            read_bound="bounded",
            output_type="TemporalOccurrencePage",
        ),
    )
    descriptors.extend(temporal_catalog_reads)

    descriptors.extend(root_navigation_topics)
    descriptors.extend(slice2_navigation_topics)
    descriptors.extend(slice3_navigation_topics)
    descriptors.extend(slice2_method_families)
    descriptors.extend(slice2_artifact_contracts)

    # -- Finalize: build indexes ------------------------------------------

    return _finalize_registry(
        tuple(descriptors),
        navigation_topics=(
            *root_navigation_topics,
            *slice2_navigation_topics,
            *slice3_navigation_topics,
        ),
        method_families=slice2_method_families,
        artifact_contracts=slice2_artifact_contracts,
        discovery_memberships=_slice3_discovery_memberships(
            (
                *root_navigation_topics,
                *slice2_navigation_topics,
                *slice3_navigation_topics,
            ),
            slice2_method_families,
            slice2_artifact_contracts,
        ),
        explicit_cross_links=_slice3_cross_links(),
        root_members=_ROOT_HELP_MEMBERS,
    )


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
    help_descriptors: tuple[AnalysisHelpDescriptor, ...],
    *,
    navigation_topics: tuple[AnalysisNavigationTopic, ...] = (),
    method_families: tuple[AnalysisMethodFamily, ...] = (),
    artifact_contracts: tuple[AnalysisArtifactFamilyContract, ...] = (),
    discovery_memberships: Mapping[str, tuple[LiveHelpTarget, ...]] = MappingProxyType({}),
    explicit_cross_links: Mapping[str, tuple[LiveHelpTarget, ...]] = MappingProxyType({}),
    root_members: tuple[LiveHelpTarget, ...] = (),
    render_budgets: Mapping[
        AnalysisHelpRenderClass,
        AnalysisHelpRenderBudget,
    ] = ANALYSIS_HELP_RENDER_BUDGETS,
) -> CapabilityRegistry:
    """Build indexes, validate uniqueness, and generate type algebra rows."""

    descriptors = tuple(
        descriptor
        for descriptor in help_descriptors
        if not isinstance(
            descriptor,
            (AnalysisNavigationTopic, AnalysisMethodFamily, AnalysisArtifactFamilyContract),
        )
    )

    _validate_public_type_variants()
    _validate_authority_policies(descriptors)
    _validate_help_topology(
        descriptors=descriptors,
        navigation_topics=navigation_topics,
        method_families=method_families,
        artifact_contracts=artifact_contracts,
        discovery_memberships=discovery_memberships,
        root_members=root_members,
        render_budgets=render_budgets,
    )

    # Validate no duplicate ids
    by_id: dict[str, AnalysisHelpDescriptor] = {}
    for desc in help_descriptors:
        if desc.id in by_id:
            raise ValueError(f"duplicate capability id: {desc.id}")
        if isinstance(
            desc,
            (AnalysisNavigationTopic, AnalysisMethodFamily, AnalysisArtifactFamilyContract),
        ):
            pass
        else:
            _validate_additional_examples(desc)
        by_id[desc.id] = desc

    # Validate no duplicate help_targets
    by_help_target: dict[str, AnalysisHelpDescriptor] = {}
    for desc in help_descriptors:
        if desc.help_target in by_help_target:
            raise ValueError(f"duplicate help_target: {desc.help_target}")
        by_help_target[desc.help_target] = desc
    _validate_parameter_help(descriptors)
    _validate_artifact_affordance_reads(descriptors)

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
    capability_by_id = {descriptor.id: descriptor for descriptor in descriptors}
    algebra_rows = _generate_algebra_rows(descriptors, capability_by_id)
    discovery_owners = _invert_discovery_memberships(discovery_memberships)
    (
        artifact_producers,
        artifact_consumers,
        artifact_producer_edges,
        artifact_consumer_edges,
    ) = _derive_artifact_algebra(descriptors)
    _validate_artifact_continuation_budget(artifact_consumer_edges)
    cross_links = _derive_cross_links(
        help_descriptors=help_descriptors,
        method_families=method_families,
        explicit_cross_links=explicit_cross_links,
        discovery_owners=discovery_owners,
        producer_edges=artifact_producer_edges,
        consumer_edges=artifact_consumer_edges,
    )
    _validate_cross_links(
        help_descriptors=help_descriptors,
        cross_links=cross_links,
        render_budgets=render_budgets,
    )

    registry = CapabilityRegistry(
        _help_descriptors=help_descriptors,
        _descriptors=descriptors,
        _by_id=MappingProxyType(by_id),
        _by_help_target=MappingProxyType(by_help_target),
        _by_callable=MappingProxyType(by_callable),
        _navigation_topics=MappingProxyType(
            {topic.canonical_id: topic for topic in navigation_topics}
        ),
        _method_families=MappingProxyType(
            {family.canonical_id: family for family in method_families}
        ),
        _artifact_contracts=MappingProxyType(
            {contract.artifact_family: contract for contract in artifact_contracts}
        ),
        _root_members=root_members,
        _render_budgets=MappingProxyType(dict(render_budgets)),
        _constructor_consumers=MappingProxyType(constructor_consumers_frozen),
        _discovery_owners=MappingProxyType(discovery_owners),
        _discovery_members=MappingProxyType(dict(discovery_memberships)),
        _cross_links=MappingProxyType(cross_links),
        _artifact_producers=MappingProxyType(artifact_producers),
        _artifact_consumers=MappingProxyType(artifact_consumers),
        _artifact_producer_edges=MappingProxyType(artifact_producer_edges),
        _artifact_consumer_edges=MappingProxyType(artifact_consumer_edges),
        _algebra_rows=algebra_rows,
    )
    _validate_input_producers(registry)
    return registry


def _target_key(target: LiveHelpTarget) -> tuple[str, str | None]:
    return target.surface, target.canonical_id


def _invert_discovery_memberships(
    memberships: Mapping[str, tuple[LiveHelpTarget, ...]],
) -> dict[str, LiveHelpTarget]:
    owners: dict[str, LiveHelpTarget] = {}
    for owner_id, members in memberships.items():
        owner = _analysis_target(owner_id)
        for member in members:
            if member.surface != "analysis" or member.canonical_id is None:
                continue
            previous = owners.get(member.canonical_id)
            if previous is not None:
                raise ValueError(
                    "duplicate analysis discovery owner: "
                    f"{member.canonical_id} ({previous.display}, {owner.display})"
                )
            owners[member.canonical_id] = owner
    return owners


def _derive_artifact_algebra(
    descriptors: tuple[CapabilityDescriptor, ...],
) -> tuple[
    dict[ArtifactFamily, tuple[LiveHelpTarget, ...]],
    dict[ArtifactFamily, tuple[LiveHelpTarget, ...]],
    dict[ArtifactFamily, tuple[ArtifactProducerEdge, ...]],
    dict[ArtifactFamily, tuple[ArtifactConsumerEdge, ...]],
]:
    """Derive complete qualified Artifact algebra from exact runtime contracts."""

    artifact_families = frozenset(ARTIFACT_FAMILIES)
    producer_edges: dict[ArtifactFamily, list[ArtifactProducerEdge]] = {
        family: [] for family in ARTIFACT_FAMILIES
    }
    consumer_edges: dict[ArtifactFamily, list[ArtifactConsumerEdge]] = {
        family: [] for family in ARTIFACT_FAMILIES
    }
    for descriptor in descriptors:
        target = _analysis_target(descriptor.help_target)
        if isinstance(descriptor, OperatorCapability):
            output = descriptor.output_contract
            output_family = output.family
            if isinstance(output_family, SameAsInputFamily):
                for family in descriptor.accepted_inputs.get(output_family.parameter, frozenset()):
                    if family in artifact_families:
                        producer_edges[family].append(
                            ArtifactProducerEdge(
                                target=target,
                                semantic_shapes=output.semantic_shapes,
                                matching_kinds=output.matching_kinds,
                                nullable=output.nullable,
                                same_as_parameter=output_family.parameter,
                            )
                        )
            else:
                producer_edges[output_family].append(
                    ArtifactProducerEdge(
                        target=target,
                        semantic_shapes=output.semantic_shapes,
                        matching_kinds=output.matching_kinds,
                        nullable=output.nullable,
                    )
                )
        elif (
            isinstance(descriptor, BoundaryCapability)
            and descriptor.direction == "governed_entry"
            and descriptor.output_family in artifact_families
        ):
            producer_edges[descriptor.output_family].append(ArtifactProducerEdge(target=target))

        if isinstance(descriptor, (OperatorCapability, BoundaryCapability)):
            for parameter, families in descriptor.accepted_inputs.items():
                admission = (
                    descriptor.artifact_admission.get(parameter)
                    if isinstance(descriptor, OperatorCapability)
                    else None
                )
                for family in families:
                    if family not in artifact_families:
                        continue
                    consumer_edges[family].append(
                        ArtifactConsumerEdge(
                            target=target,
                            parameter=parameter,
                            semantic_shapes=(
                                admission.semantic_shapes.get(family, frozenset())
                                if admission is not None
                                else frozenset()
                            ),
                            matching_kinds=(
                                admission.matching_kinds.get(family, frozenset())
                                if admission is not None
                                else frozenset()
                            ),
                            coverage_statuses=(
                                admission.coverage_statuses.get(family, frozenset())
                                if admission is not None
                                else frozenset()
                            ),
                        )
                    )
        elif (
            isinstance(descriptor, ReadCapability)
            and descriptor.exposes_artifact_affordance
            and descriptor.receiver_family in artifact_families
        ):
            consumer_edges[descriptor.receiver_family].append(
                ArtifactConsumerEdge(
                    target=target,
                    parameter="receiver",
                )
            )

    frozen_producer_edges = {
        family: tuple(dict.fromkeys(producer_edges[family])) for family in ARTIFACT_FAMILIES
    }
    frozen_consumer_edges = {
        family: tuple(dict.fromkeys(consumer_edges[family])) for family in ARTIFACT_FAMILIES
    }

    def targets(
        values: Mapping[ArtifactFamily, tuple[ArtifactProducerEdge | ArtifactConsumerEdge, ...]],
    ) -> dict[ArtifactFamily, tuple[LiveHelpTarget, ...]]:
        return {
            family: tuple(dict.fromkeys(edge.target for edge in values[family]))
            for family in ARTIFACT_FAMILIES
        }

    return (
        targets(frozen_producer_edges),
        targets(frozen_consumer_edges),
        frozen_producer_edges,
        frozen_consumer_edges,
    )


def _validate_artifact_affordance_reads(
    descriptors: tuple[CapabilityDescriptor, ...],
) -> None:
    """Reject receiver reads that cannot be exact Artifact continuations."""

    artifact_families = frozenset(ARTIFACT_FAMILIES)
    invalid: list[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, ReadCapability) or not descriptor.exposes_artifact_affordance:
            continue
        if descriptor.receiver_family not in artifact_families:
            invalid.append(f"{descriptor.id}: receiver is not an Artifact family")
        if descriptor.callable_path is None:
            invalid.append(f"{descriptor.id}: Artifact affordance lacks a callable")
        if descriptor.read_bound != "bounded":
            invalid.append(f"{descriptor.id}: Artifact affordance must be bounded")
        if not descriptor.output_type and not descriptor.artifact_output_by_shape:
            invalid.append(f"{descriptor.id}: Artifact affordance lacks an output family")
    if invalid:
        raise ValueError("invalid Artifact read affordances: " + ", ".join(sorted(invalid)))


def _validate_artifact_continuation_budget(
    consumer_edges: Mapping[ArtifactFamily, tuple[ArtifactConsumerEdge, ...]],
) -> None:
    """Reject a static typed-continuation surface that cannot render completely."""

    for family, edges in consumer_edges.items():
        typed_targets = {
            edge.target
            for edge in edges
            if edge.target != LiveHelpTarget(surface="analysis", canonical_id="boundary.to_pandas")
        }
        if len(typed_targets) > ARTIFACT_CONTRACT_RENDER_BUDGET.max_affordances:
            raise ValueError(
                f"{family} exposes {len(typed_targets)} typed continuations > "
                f"{ARTIFACT_CONTRACT_RENDER_BUDGET.max_affordances}"
            )


def _artifact_algebra_route(
    *,
    artifact_family: ArtifactFamily,
    target: LiveHelpTarget,
    discovery_owners: Mapping[str, LiveHelpTarget],
) -> LiveHelpTarget | None:
    """Compress one exact algebra fact to its bounded discovery route."""

    if target.canonical_id is None:
        return target
    if target.canonical_id == "boundary.to_pandas":
        return target
    owner = discovery_owners.get(target.canonical_id)
    if owner is None or owner.canonical_id in {None, "analysis", "methods", "inputs"}:
        return target
    if owner.canonical_id == artifact_family:
        return None
    return owner


def _derive_cross_links(
    *,
    help_descriptors: tuple[AnalysisHelpDescriptor, ...],
    method_families: tuple[AnalysisMethodFamily, ...],
    explicit_cross_links: Mapping[str, tuple[LiveHelpTarget, ...]],
    discovery_owners: Mapping[str, LiveHelpTarget],
    producer_edges: Mapping[ArtifactFamily, tuple[ArtifactProducerEdge, ...]],
    consumer_edges: Mapping[ArtifactFamily, tuple[ArtifactConsumerEdge, ...]],
) -> dict[str, tuple[LiveHelpTarget, ...]]:
    """Build immutable typed cross-links without renderer-owned route discovery."""

    links: dict[str, list[LiveHelpTarget]] = {}

    def add(owner_id: str, targets: tuple[LiveHelpTarget, ...]) -> None:
        if targets:
            links.setdefault(owner_id, []).extend(targets)

    for descriptor in help_descriptors:
        if isinstance(descriptor, OperatorCapability):
            add(
                descriptor.help_target,
                tuple(
                    target
                    for contract in descriptor.parameter_help.values()
                    for target in contract.help_targets
                ),
            )
    for family in method_families:
        add(family.canonical_id, (*family.input_routes, *family.output_routes))
    for owner_id, targets in explicit_cross_links.items():
        add(owner_id, targets)

    add(
        "artifacts.reading",
        (*_ARTIFACT_EVIDENCE_TARGETS, _analysis_target("boundary.to_pandas")),
    )
    for artifact_family in ARTIFACT_FAMILIES:
        routed_algebra_values: list[LiveHelpTarget] = []
        for producer_edge in producer_edges[artifact_family]:
            route = _artifact_algebra_route(
                artifact_family=artifact_family,
                target=producer_edge.target,
                discovery_owners=discovery_owners,
            )
            if route is not None:
                routed_algebra_values.append(route)
        for consumer_edge in consumer_edges[artifact_family]:
            route = _artifact_algebra_route(
                artifact_family=artifact_family,
                target=consumer_edge.target,
                discovery_owners=discovery_owners,
            )
            if route is not None:
                routed_algebra_values.append(route)
        independently_recoverable = artifact_family != "QualityReport"
        add(
            artifact_family,
            (
                *((_analysis_target("artifacts.reading"),) if independently_recoverable else ()),
                *routed_algebra_values,
                *((_analysis_target("session.get_frame"),) if independently_recoverable else ()),
            ),
        )

    return {owner_id: tuple(dict.fromkeys(targets)) for owner_id, targets in links.items()}


def _render_class_for_descriptor(
    descriptor: AnalysisHelpDescriptor,
) -> AnalysisHelpRenderClass:
    if isinstance(descriptor, AnalysisNavigationTopic):
        return descriptor.render_class
    if isinstance(descriptor, AnalysisMethodFamily):
        return "navigation"
    if isinstance(descriptor, AnalysisArtifactFamilyContract):
        return "public_type"
    return "exact_callable"


def _validate_cross_links(
    *,
    help_descriptors: tuple[AnalysisHelpDescriptor, ...],
    cross_links: Mapping[str, tuple[LiveHelpTarget, ...]],
    render_budgets: Mapping[AnalysisHelpRenderClass, AnalysisHelpRenderBudget],
) -> None:
    """Reject dead, duplicate, or over-budget registry-owned cross-links."""

    import marivo.analysis as mv

    by_target = {descriptor.help_target: descriptor for descriptor in help_descriptors}
    invalid: list[str] = []
    for owner_id, targets in cross_links.items():
        owner = by_target.get(owner_id)
        if owner is None:
            invalid.append(f"unknown cross-link owner {owner_id}")
            continue
        keys = tuple(_target_key(target) for target in targets)
        if len(keys) != len(set(keys)):
            invalid.append(f"duplicate cross-link under {owner_id}")
        for target in targets:
            if (
                target.surface == "analysis"
                and target.canonical_id not in by_target
                and not (
                    target.canonical_id is not None
                    and "." not in target.canonical_id
                    and hasattr(mv, target.canonical_id)
                )
            ):
                invalid.append(f"dead cross-link {owner_id} -> {target.display}")
        budget = render_budgets[_render_class_for_descriptor(owner)]
        owned_members = (
            owner.members
            if isinstance(owner, (AnalysisNavigationTopic, AnalysisMethodFamily))
            else ()
        )
        route_count = len({_target_key(target) for target in (*owned_members, *targets)})
        if route_count > budget.max_outgoing_routes:
            invalid.append(
                f"{owner_id} exposes {route_count} routes > {budget.max_outgoing_routes}"
            )
    if invalid:
        raise ValueError("invalid analysis cross-links: " + ", ".join(sorted(invalid)))


def _validate_help_topology(
    *,
    descriptors: tuple[CapabilityDescriptor, ...],
    navigation_topics: tuple[AnalysisNavigationTopic, ...],
    method_families: tuple[AnalysisMethodFamily, ...],
    artifact_contracts: tuple[AnalysisArtifactFamilyContract, ...],
    discovery_memberships: Mapping[str, tuple[LiveHelpTarget, ...]],
    root_members: tuple[LiveHelpTarget, ...],
    render_budgets: Mapping[AnalysisHelpRenderClass, AnalysisHelpRenderBudget],
) -> None:
    """Fail eagerly when the inactive progressive topology is malformed."""

    expected_render_classes = set(get_args(AnalysisHelpRenderClass))
    if set(render_budgets) != expected_render_classes:
        raise ValueError("analysis help render budgets must cover every render class exactly")
    for render_class, budget in render_budgets.items():
        if (
            budget.max_lines <= 0
            or budget.max_codepoints <= 0
            or budget.max_outgoing_routes <= 0
            or budget.max_examples_or_snippets < 0
        ):
            raise ValueError(f"invalid analysis help render budget: {render_class}")

    descriptor_ids = {
        identity
        for descriptor in descriptors
        for identity in (descriptor.canonical_id, descriptor.help_target)
    }
    topology_ids: set[str] = set()
    navigation_by_id: dict[str, AnalysisNavigationTopic] = {}

    for topic in navigation_topics:
        if topic.canonical_id in topology_ids or topic.canonical_id in descriptor_ids:
            raise ValueError(f"duplicate analysis help canonical id: {topic.canonical_id}")
        topology_ids.add(topic.canonical_id)
        navigation_by_id[topic.canonical_id] = topic
        if topic.public_entrypoint is not None or topic.callable_path is not None:
            raise ValueError(f"navigation topic must not be invokable: {topic.canonical_id}")
        if topic.render_class not in {"decision_hub", "navigation"}:
            raise ValueError(f"unknown analysis navigation render class: {topic.render_class}")
        _validate_topology_members(topic.canonical_id, topic.members)
        budget = render_budgets[topic.render_class]
        if len(topic.members) > budget.max_outgoing_routes:
            raise ValueError(f"navigation topic exceeds route budget: {topic.canonical_id}")

    allowed_epistemic_kinds = set(get_args(EpistemicKind))
    for family in method_families:
        if family.canonical_id in topology_ids or family.canonical_id in descriptor_ids:
            raise ValueError(f"duplicate analysis help canonical id: {family.canonical_id}")
        topology_ids.add(family.canonical_id)
        if family.public_entrypoint is not None or family.callable_path is not None:
            raise ValueError(f"method family must not be invokable: {family.canonical_id}")
        if not family.epistemic_kinds:
            raise ValueError(f"method family requires an epistemic kind: {family.canonical_id}")
        if (
            len(set(family.epistemic_kinds)) != len(family.epistemic_kinds)
            or not set(family.epistemic_kinds) <= allowed_epistemic_kinds
        ):
            raise ValueError(f"invalid method-family epistemic kinds: {family.canonical_id}")
        _validate_topology_members(family.canonical_id, family.members)
        if len(family.members) > render_budgets["navigation"].max_outgoing_routes:
            raise ValueError(f"method family exceeds route budget: {family.canonical_id}")

    artifact_by_family: dict[ArtifactFamily, AnalysisArtifactFamilyContract] = {}
    for contract in artifact_contracts:
        if contract.canonical_id in topology_ids or contract.canonical_id in descriptor_ids:
            raise ValueError(f"duplicate analysis help canonical id: {contract.canonical_id}")
        topology_ids.add(contract.canonical_id)
        if contract.public_entrypoint is not None or contract.callable_path is not None:
            raise ValueError(
                f"Artifact family contract must not be invokable: {contract.canonical_id}"
            )
        if (
            contract.canonical_id != contract.type_name
            or contract.type_name != contract.artifact_family
        ):
            raise ValueError(
                f"Artifact family contract must use its canonical public type: "
                f"{contract.canonical_id}"
            )
        if contract.artifact_family in artifact_by_family:
            raise ValueError(f"duplicate Artifact family contract: {contract.artifact_family}")
        if (
            not contract.epistemic_kinds
            or not set(contract.epistemic_kinds) <= allowed_epistemic_kinds
        ):
            raise ValueError(f"invalid Artifact epistemic kinds: {contract.canonical_id}")
        if len(set(contract.semantic_shapes)) != len(contract.semantic_shapes):
            raise ValueError(f"duplicate Artifact shapes: {contract.canonical_id}")
        specialized_keys = tuple(
            _target_key(target) for target in contract.specialized_member_targets
        )
        if len(set(specialized_keys)) != len(specialized_keys):
            raise ValueError(f"duplicate Artifact specialized members: {contract.canonical_id}")
        for target in contract.specialized_member_targets:
            if target.surface != "analysis" or target.canonical_id not in descriptor_ids:
                raise ValueError(f"unknown Artifact specialized member: {target.display}")
            receiver, separator, member = target.canonical_id.partition(".")
            if (
                not separator
                or receiver != contract.type_name
                or member not in PUBLIC_FRAME_METHODS.get(contract.type_name, ())
            ):
                raise ValueError(
                    f"Artifact specialized member is absent from the public allowlist: "
                    f"{target.display}"
                )
        artifact_by_family[contract.artifact_family] = contract
    if artifact_contracts and tuple(artifact_by_family) != ARTIFACT_FAMILIES:
        raise ValueError("Artifact family contracts must cover the closed family order exactly")

    known_analysis_targets = descriptor_ids | topology_ids
    for family in method_families:
        for route_kind, routes in (
            ("input", family.input_routes),
            ("output", family.output_routes),
        ):
            keys = tuple(_target_key(target) for target in routes)
            if len(set(keys)) != len(keys):
                raise ValueError(
                    f"duplicate method-family {route_kind} cross-link: {family.canonical_id}"
                )
            for target in routes:
                if (
                    target.surface == "analysis"
                    and target.canonical_id not in known_analysis_targets
                ):
                    raise ValueError(f"dead method-family cross-link: {target.display}")
    if discovery_memberships:
        _validate_discovery_memberships(
            descriptors=descriptors,
            artifact_contracts=artifact_contracts,
            memberships=discovery_memberships,
            known_analysis_targets=known_analysis_targets,
        )
    if artifact_contracts:
        _validate_artifact_contract_alignment(descriptors, artifact_by_family)
        _validate_artifact_public_members(artifact_by_family)

    if root_members:
        if root_members != _ROOT_HELP_MEMBERS:
            raise ValueError("analysis root edges must match the registered Slice 1 topology")
        if len({_target_key(target) for target in root_members}) != len(root_members):
            raise ValueError("analysis root edges must be distinct")
        if len(root_members) > render_budgets["root"].max_outgoing_routes:
            raise ValueError("analysis root exceeds its route budget")
        for target in root_members[:-1]:
            if target.surface != "analysis" or target.canonical_id not in navigation_by_id:
                raise ValueError(f"analysis root has an invalid hub edge: {target.display}")
        terminal = root_members[-1]
        if (
            terminal != _analysis_target("boundary.to_pandas")
            or "boundary.to_pandas" not in descriptor_ids
        ):
            raise ValueError("analysis root terminal edge must resolve to boundary.to_pandas")


def _validate_topology_members(
    canonical_id: str,
    members: tuple[LiveHelpTarget, ...],
) -> None:
    if len(members) < 2:
        raise ValueError(f"analysis navigation requires at least two members: {canonical_id}")
    keys = tuple(_target_key(target) for target in members)
    if len(set(keys)) != len(keys):
        raise ValueError(f"analysis navigation has duplicate members: {canonical_id}")
    if any(target.canonical_id is None for target in members):
        raise ValueError(f"analysis navigation member lacks a canonical id: {canonical_id}")


def _validate_discovery_memberships(
    *,
    descriptors: tuple[CapabilityDescriptor, ...],
    artifact_contracts: tuple[AnalysisArtifactFamilyContract, ...],
    memberships: Mapping[str, tuple[LiveHelpTarget, ...]],
    known_analysis_targets: set[str],
) -> None:
    """Require one explicit owner for every ordinary progressive-help target."""

    owners = _invert_discovery_memberships(memberships)
    valid_owners = known_analysis_targets | set(PUBLIC_OBJECT_CONTRACTS) | {"analysis"}
    for owner_id, members in memberships.items():
        if owner_id not in valid_owners:
            raise ValueError(f"unknown analysis discovery owner: {owner_id}")
        keys = tuple(_target_key(target) for target in members)
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate discovery member under owner: {owner_id}")
        for target in members:
            if target.surface == "analysis" and target.canonical_id not in known_analysis_targets:
                raise ValueError(f"dead analysis discovery edge: {target.display}")

    ordinary_targets = {
        descriptor.help_target
        for descriptor in descriptors
        if descriptor.callable_path is not None
        or (isinstance(descriptor, ConstructorCapability) and bool(descriptor.output_type))
    }
    ordinary_targets.update(contract.canonical_id for contract in artifact_contracts)
    missing = ordinary_targets - set(owners)
    if missing:
        raise ValueError("analysis targets lack a discovery owner: " + ", ".join(sorted(missing)))


def _validate_artifact_contract_alignment(
    descriptors: tuple[CapabilityDescriptor, ...],
    contracts: Mapping[ArtifactFamily, AnalysisArtifactFamilyContract],
) -> None:
    """Fail on output, admission, shape, or public-member drift."""

    artifact_families = frozenset(ARTIFACT_FAMILIES)
    invalid: list[str] = []
    for family, contract in contracts.items():
        expected_members = tuple(
            f"{family}.{member}" for member in PUBLIC_FRAME_METHODS.get(family, ())
        )
        actual_members = tuple(
            target.canonical_id for target in contract.specialized_member_targets
        )
        if actual_members != expected_members:
            invalid.append(f"{family}: specialized public-member allowlist drift")

    for descriptor in descriptors:
        if not isinstance(descriptor, OperatorCapability):
            continue
        output = descriptor.output_contract
        if isinstance(output.family, SameAsInputFamily):
            accepted = descriptor.accepted_inputs.get(output.family.parameter)
            if accepted is None or not set(accepted) & artifact_families:
                invalid.append(
                    f"{descriptor.id}: SameAsInputFamily names no Artifact input parameter"
                )
        else:
            family_contract = contracts.get(output.family)
            if family_contract is None:
                invalid.append(f"{descriptor.id}: unknown Artifact output family {output.family}")
            elif output.semantic_shapes and not output.semantic_shapes <= set(
                family_contract.semantic_shapes
            ):
                invalid.append(f"{descriptor.id}: output shapes drift from {output.family}")

        for parameter, admission in descriptor.artifact_admission.items():
            accepted = descriptor.accepted_inputs.get(parameter)
            if accepted is None:
                invalid.append(f"{descriptor.id}.{parameter}: admission lacks accepted input")
                continue
            admission_families = (
                set(admission.semantic_shapes)
                | set(admission.matching_kinds)
                | set(admission.coverage_statuses)
            )
            if not admission_families <= set(accepted):
                invalid.append(f"{descriptor.id}.{parameter}: admission family is not accepted")
            for family, shapes in admission.semantic_shapes.items():
                family_contract = contracts.get(family)
                if family_contract is None:
                    invalid.append(
                        f"{descriptor.id}.{parameter}: unknown admission family {family}"
                    )
                elif family_contract.semantic_shapes and not shapes <= set(
                    family_contract.semantic_shapes
                ):
                    invalid.append(
                        f"{descriptor.id}.{parameter}: admission shapes drift from {family}"
                    )
    if invalid:
        raise ValueError("invalid Artifact family contracts: " + ", ".join(sorted(invalid)))


def _validate_artifact_public_members(
    contracts: Mapping[ArtifactFamily, AnalysisArtifactFamilyContract],
) -> None:
    """Require every disclosed Artifact property and method to exist live."""

    from marivo.analysis.frames.base import BaseFrame

    installed = _installed_artifact_types()
    invalid: list[str] = []
    for family, contract in contracts.items():
        artifact_type = installed.get(family)
        if artifact_type is None or artifact_type.__name__ != contract.type_name:
            invalid.append(f"{family}: installed public type is absent or renamed")
            continue
        properties = (
            *PUBLIC_FRAME_PROPERTIES.get("BaseFrame", ()),
            *PUBLIC_FRAME_PROPERTIES.get(family, ()),
        )
        for property_name in properties:
            member = inspect.getattr_static(artifact_type, property_name, None)
            if not isinstance(member, property):
                invalid.append(f"{family}.{property_name}: public property is absent live")
        for method_name in PUBLIC_FRAME_METHODS.get(family, ()):
            member = inspect.getattr_static(artifact_type, method_name, None)
            if member is None or not callable(getattr(artifact_type, method_name, None)):
                invalid.append(f"{family}.{method_name}: public method is absent live")
        for method_name in ("show", "contract"):
            if not callable(getattr(artifact_type, method_name, None)):
                invalid.append(f"{family}.{method_name}: inherited read is absent live")
        if not issubclass(artifact_type, BaseFrame):
            invalid.append(f"{family}: public Artifact does not inherit BaseFrame")
    if invalid:
        raise ValueError("invalid Artifact public members: " + ", ".join(sorted(invalid)))


def _validate_input_producers(registry: CapabilityRegistry) -> None:
    """Require every closed operator input family to have a teaching path."""

    missing: list[str] = []
    for descriptor in registry.descriptors:
        if not isinstance(descriptor, (OperatorCapability, BoundaryCapability)):
            continue
        for parameter, families in descriptor.accepted_inputs.items():
            for family in families:
                if not registry.producer_targets(family):
                    missing.append(f"{descriptor.id}.{parameter}:{family}")
    if missing:
        raise ValueError(
            "analysis input families lack registered producers: " + ", ".join(sorted(missing))
        )


DerivationWitness = Callable[[OperatorCapability, str, ParameterHelpContract], bool]


def _event_pattern_step_witness(
    descriptor: OperatorCapability,
    parameter: str,
    contract: ParameterHelpContract,
) -> bool:
    """Prove one reducer step can be selected from its journey Artifact."""

    from marivo.analysis.event import EventPattern, PatternStep
    from marivo.analysis.frames.event import EventFrameMeta

    if "journeys.meta.pattern.steps" not in contract.acquisition:
        return False
    if descriptor.accepted_inputs.get("journeys") != frozenset({"EventFrame"}):
        return False
    admission = descriptor.artifact_admission.get("journeys")
    if admission is None or admission.semantic_shapes.get("EventFrame") != frozenset({"journey"}):
        return False
    callable_obj = import_registered_callable(descriptor.callable_path or "")
    if not callable(callable_obj):
        return False
    live_parameter = inspect.signature(callable_obj).parameters.get(parameter)
    if live_parameter is None or live_parameter.annotation not in {PatternStep, "PatternStep"}:
        return False
    pattern_field = EventFrameMeta.model_fields.get("pattern")
    steps_field = EventPattern.model_fields.get("steps")
    return bool(
        pattern_field is not None
        and pattern_field.annotation is EventPattern
        and steps_field is not None
        and PatternStep in get_args(steps_field.annotation)
    )


_DERIVATION_WITNESSES: Mapping[tuple[str, str], DerivationWitness] = MappingProxyType(
    {
        ("events.time_to_event", "start_step"): _event_pattern_step_witness,
        ("events.time_to_event", "end_step"): _event_pattern_step_witness,
    }
)


def _validate_parameter_help(
    descriptors: tuple[CapabilityDescriptor, ...],
) -> None:
    """Bind parameter guidance to installed signatures and resolvable targets."""

    import marivo.analysis as mv
    import marivo.semantic as ms

    invalid: list[str] = []
    descriptor_targets = {descriptor.help_target for descriptor in descriptors}
    derivable_claims: set[tuple[str, str]] = set()
    for descriptor in descriptors:
        if not isinstance(descriptor, OperatorCapability):
            continue
        if descriptor.callable_path is None:
            invalid.append(f"{descriptor.id}: parameter help requires an installed callable")
            continue
        callable_obj = import_registered_callable(descriptor.callable_path)
        if not callable(callable_obj):
            invalid.append(f"{descriptor.id}: registered parameter owner is not callable")
            continue
        signature = inspect.signature(callable_obj)
        for parameter, contract in descriptor.parameter_help.items():
            if not parameter or not contract.acquisition.strip() or not contract.help_targets:
                invalid.append(f"{descriptor.id}.{parameter}: incomplete parameter help")
            live_parameter = signature.parameters.get(parameter)
            if live_parameter is None:
                invalid.append(
                    f"{descriptor.id}.{parameter}: parameter is absent from live signature"
                )
            elif contract.required != (live_parameter.default is inspect.Parameter.empty):
                invalid.append(
                    f"{descriptor.id}.{parameter}: required state disagrees with live default"
                )
            for target in contract.help_targets:
                if target.canonical_id is None:
                    invalid.append(f"{descriptor.id}.{parameter}: target lacks canonical id")
                    continue
                if target.surface == "analysis":
                    resolved = target.canonical_id in descriptor_targets or hasattr(
                        mv, target.canonical_id
                    )
                elif target.surface == "semantic":
                    resolved = hasattr(ms, target.canonical_id)
                else:
                    resolved = False
                if not resolved:
                    invalid.append(
                        f"{descriptor.id}.{parameter}: unresolved target {target.display}"
                    )
            if contract.derivable_from_current_artifact:
                claim = (descriptor.id, parameter)
                derivable_claims.add(claim)
                witness = _DERIVATION_WITNESSES.get(claim)
                if witness is None or not witness(descriptor, parameter, contract):
                    invalid.append(
                        f"{descriptor.id}.{parameter}: behavioral derivation witness failed"
                    )
    descriptor_ids = {descriptor.id for descriptor in descriptors}
    applicable_witnesses = {
        witness for witness in _DERIVATION_WITNESSES if witness[0] in descriptor_ids
    }
    if derivable_claims != applicable_witnesses:
        invalid.append(
            "derivable parameter claims lack exact behavioral witnesses: "
            f"claims={sorted(derivable_claims)!r}, witnesses={sorted(applicable_witnesses)!r}"
        )
    if invalid:
        raise ValueError("invalid analysis parameter help: " + ", ".join(sorted(invalid)))


def _validate_authority_policies(
    descriptors: tuple[CapabilityDescriptor, ...],
) -> None:
    """Fail closed when an operator carries an unknown authority policy."""
    allowed = set(get_args(AuthorityPolicy))
    invalid = tuple(
        f"{descriptor.id}:{descriptor.authority_policy}"
        for descriptor in descriptors
        if isinstance(descriptor, OperatorCapability) and descriptor.authority_policy not in allowed
    )
    if invalid:
        raise ValueError(
            "analysis operators have unknown authority policies: " + ", ".join(invalid)
        )


def _validate_public_type_variants() -> None:
    """Keep help variants equal to the closed persisted shape contracts."""
    from marivo.analysis.frames.event import (
        EventFrameMeta,
        EventFunnelFrameMeta,
        EventTimeToEventFrameMeta,
    )
    from marivo.analysis.frames.lifecycle import (
        LifecycleDistributionFrameMeta,
        LifecycleDwellFrameMeta,
        LifecycleHistoryFrameMeta,
        LifecycleTransitionsFrameMeta,
        LifecycleViolationsFrameMeta,
    )
    from marivo.analysis.frames.quality import QualityReportMeta

    def one_literal(model: type[BaseModel], field_name: str) -> str:
        values = get_args(model.model_fields[field_name].annotation)
        if len(values) != 1 or not isinstance(values[0], str):
            raise ValueError(f"{model.__name__}.{field_name} must be one string Literal")
        return values[0]

    expected = {
        "EventFrame": tuple(
            one_literal(model, "semantic_kind")
            for model in (
                EventFrameMeta,
                EventFunnelFrameMeta,
                EventTimeToEventFrameMeta,
            )
        ),
        "LifecycleFrame": tuple(
            one_literal(model, "semantic_kind")
            for model in (
                LifecycleHistoryFrameMeta,
                LifecycleDistributionFrameMeta,
                LifecycleTransitionsFrameMeta,
                LifecycleDwellFrameMeta,
                LifecycleViolationsFrameMeta,
            )
        ),
        "QualityReport": get_args(QualityReportMeta.model_fields["report_shape"].annotation),
    }
    for type_name, expected_variants in expected.items():
        actual = PUBLIC_TYPE_VARIANTS.get(type_name)
        if actual != expected_variants:
            raise ValueError(
                f"{type_name} help variants must match persisted shapes: "
                f"expected={expected_variants!r}, received={actual!r}"
            )


def _validate_additional_examples(descriptor: CapabilityDescriptor) -> None:
    """Validate bounded examples and their ownership before indexing."""
    if not descriptor.additional_examples:
        return
    owned_call = descriptor.public_entrypoint.split("(", 1)[0].strip()
    for example in descriptor.additional_examples:
        if not example.label.strip():
            raise ValueError(f"{descriptor.id}: additional example label must not be empty")
        code = example.code.strip()
        if not code:
            raise ValueError(f"{descriptor.id}: additional example code must not be empty")
        if "..." in code or re.search(r"<[^>]+>", code):
            raise ValueError(f"{descriptor.id}: additional example contains a placeholder")
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise ValueError(f"{descriptor.id}: additional example is not parseable") from exc
        matching_calls = tuple(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _call_name(node.func) == owned_call
        )
        if owned_call and len(matching_calls) != 1:
            raise ValueError(
                f"{descriptor.id}: additional example must call {owned_call!r} exactly once"
            )
        discarded_contract = next(
            (
                statement
                for statement in tree.body
                if isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and statement.value.func.attr == "contract"
            ),
            None,
        )
        if discarded_contract is not None:
            raise ValueError(
                f"{descriptor.id}: additional example discards .contract() output; "
                "call .contract().show() or assign the contract to a name"
            )
        assigned_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        assigned_names.update(
            alias.asname or alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        )
        assigned_names.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and isinstance(node.name, str)
        )
        loaded_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        implicit_names = {"ms", "mv", "session", *dir(builtins)}
        external_names = loaded_names - assigned_names - implicit_names
        if external_names != set(example.requires):
            raise ValueError(
                f"{descriptor.id}: additional example requirements must match "
                f"external names: expected={sorted(external_names)!r}, "
                f"received={sorted(example.requires)!r}"
            )


def _call_name(node: ast.expr) -> str | None:
    """Return one static dotted call name without evaluating the expression."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


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

        rows.append(
            TypeAlgebraRow(
                help_target=desc.help_target,
                source_families=frozenset(source_families),
                output_contract=desc.output_contract,
                is_terminal=False,
            )
        )

    # Collapsed grouping rows
    if discover_source_families:
        rows.append(
            TypeAlgebraRow(
                help_target="discover",
                source_families=frozenset(discover_source_families),
                output_contract=_output("CandidateSet"),
                is_terminal=False,
            )
        )

    if transform_source_families:
        rows.append(
            TypeAlgebraRow(
                help_target="transform",
                source_families=frozenset(transform_source_families),
                output_contract="MetricFrame|DeltaFrame",
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
                output_contract=desc.output_family,
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
                output_contract="pandas.DataFrame",
                is_terminal=True,
            )
        )

    return tuple(rows)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

REGISTRY: CapabilityRegistry = _build_registry()
