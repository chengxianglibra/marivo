"""Typed scored and semantic-hypothesis candidate results."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis.candidate_lineage import (
    CandidateOrigin,
    CandidateResolutionIssue,
    InheritedObservationScope,
    SemanticEdgeContext,
    SemanticHypothesisResolutionSummary,
    merge_candidate_origins,
)
from marivo.analysis.evidence.types import ArtifactIssue, JsonScalar
from marivo.analysis.frames.base import (
    ArtifactAffordance,
    ArtifactContract,
    ArtifactInputRequirement,
    BaseFrame,
    BaseFrameMeta,
    _capability_public_entrypoint,
)
from marivo.analysis.windows import AbsoluteWindow
from marivo.ontology.types import SemanticEdgeRef
from marivo.refs import DimensionKind, Ref, RefPayloadV1, SemanticKind
from marivo.render import Card

ScoredCandidateShape = Literal[
    "point_anomaly",
    "period_shift",
    "driver_axis",
    "slice",
    "window",
    "cross_sectional_outlier",
]
CandidateShape = ScoredCandidateShape | Literal["semantic_hypothesis"]
ScoredCandidateObjective = Literal[
    "point_anomalies",
    "period_shifts",
    "driver_axes",
    "interesting_slices",
    "interesting_windows",
    "cross_sectional_outliers",
]
CandidateObjective = ScoredCandidateObjective | Literal["semantic_hypotheses"]
CandidateStrategy = Literal[
    "zscore",
    "delta_window_zscore",
    "concentration",
    "slice_zscore",
    "global_zscore_runs",
    "mad",
    "seasonal_robust_zscore",
]
PointAnomalyStrategy = Literal["zscore", "seasonal_robust_zscore"]
CandidateSourceKind = Literal["metric_frame", "delta_frame"]
CandidateSemanticKind = Literal["scalar", "time_series", "segmented", "panel"]

_ONTOLOGY_CANDIDATE_CONSTRUCTION: ContextVar[bool] = ContextVar(
    "_marivo_ontology_candidate_construction", default=False
)


class ScoredCandidateSetMeta(BaseFrameMeta):
    """Metadata for the existing score-bearing discovery shapes."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["candidate_set"] = "candidate_set"
    shape: ScoredCandidateShape
    objective: ScoredCandidateObjective
    strategy: CandidateStrategy
    source_ref: str
    source_kind: CandidateSourceKind
    metric_ids: list[str]
    semantic_kind: CandidateSemanticKind
    semantic_model: str
    source_refs: list[str]
    params: dict[str, Any]


class CandidateReadinessBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_ref: RefPayloadV1
    fingerprint: str


class SemanticHypothesisCandidateSetMeta(BaseFrameMeta):
    """Closed unscored metadata for ontology-guided Metric candidates."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["candidate_set"] = "candidate_set"
    shape: Literal["semantic_hypothesis"] = "semantic_hypothesis"
    objective: Literal["semantic_hypotheses"] = "semantic_hypotheses"
    source_ref: str
    source_kind: CandidateSourceKind
    source_metric_ref: RefPayloadV1
    source_entity_ref: RefPayloadV1 | None = None
    ontology_catalog_fingerprint: str
    semantic_catalog_fingerprint: str
    inherited_scope: InheritedObservationScope
    resolution_summary: SemanticHypothesisResolutionSummary
    edge_contexts: tuple[SemanticEdgeContext, ...]
    readiness_bindings: tuple[CandidateReadinessBinding, ...]
    upstream_origins: tuple[CandidateOrigin, ...] = ()

    @model_validator(mode="after")
    def _validate_closed_metadata(self) -> SemanticHypothesisCandidateSetMeta:
        if self.source_metric_ref.kind is not SemanticKind.METRIC:
            raise ValueError("source_metric_ref must identify a Metric")
        if (
            self.source_entity_ref is not None
            and self.source_entity_ref.kind is not SemanticKind.ENTITY
        ):
            raise ValueError("source_entity_ref must identify an Entity")
        context_refs = tuple(context.semantic_edge_ref for context in self.edge_contexts)
        if len(set(context_refs)) != len(context_refs):
            raise ValueError("edge_contexts must contain unique semantic edge refs")
        readiness_refs = tuple(binding.metric_ref for binding in self.readiness_bindings)
        if any(ref.kind is not SemanticKind.METRIC for ref in readiness_refs):
            raise ValueError("readiness_bindings must identify Metrics")
        if len(set(readiness_refs)) != len(readiness_refs):
            raise ValueError("readiness_bindings must contain unique Metric refs")
        normalized_upstream = merge_candidate_origins(self.upstream_origins)
        if normalized_upstream != self.upstream_origins:
            raise ValueError("upstream_origins must already be ordered and de-duplicated")
        if self.upstream_origins != self.candidate_origins:
            raise ValueError("upstream_origins must equal the CandidateSet candidate_origins")
        summary = self.resolution_summary
        if summary.emitted_candidates != self.row_count:
            raise ValueError("emitted candidate count must match CandidateSet row_count")
        if (
            summary.candidate_count_before_limit - summary.emitted_candidates
            != summary.candidates_omitted
        ):
            raise ValueError("candidate omission count does not match the resolution summary")
        if summary.emitted_candidates > summary.candidate_limit:
            raise ValueError("emitted candidate count exceeds candidate_limit")
        return self


# Existing internal imports keep this scored name; the public algebra is the sibling union below.
CandidateSetMeta = ScoredCandidateSetMeta
CandidateSetMetaValue = ScoredCandidateSetMeta | SemanticHypothesisCandidateSetMeta


class ScoredCandidateSelectionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    candidate_set_ref: str
    item_id: str
    source_artifact_ref: str
    score: float
    reason_codes: tuple[str, ...] = ()


class PointAnomalySelection(ScoredCandidateSelectionBase):
    kind: Literal["point_anomaly"] = "point_anomaly"
    window: AbsoluteWindow | None = None
    keys: dict[str | Ref[DimensionKind], JsonScalar] = Field(default_factory=dict)
    direction: str
    observed_value: float
    baseline_value: float
    delta: float


class PeriodShiftSelection(ScoredCandidateSelectionBase):
    kind: Literal["period_shift"] = "period_shift"
    window: AbsoluteWindow
    baseline_window: AbsoluteWindow
    keys: dict[str | Ref[DimensionKind], JsonScalar] = Field(default_factory=dict)
    direction: str


class DriverAxisSelection(ScoredCandidateSelectionBase):
    kind: Literal["driver_axis"] = "driver_axis"
    axis: str | Ref[DimensionKind]


class SliceSelection(ScoredCandidateSelectionBase):
    kind: Literal["slice"] = "slice"
    selector: dict[str | Ref[DimensionKind], JsonScalar]
    window: AbsoluteWindow | None = None


class WindowSelection(ScoredCandidateSelectionBase):
    kind: Literal["window"] = "window"
    window: AbsoluteWindow
    keys: dict[str | Ref[DimensionKind], JsonScalar] = Field(default_factory=dict)


class CrossSectionalOutlierSelection(ScoredCandidateSelectionBase):
    kind: Literal["cross_sectional_outlier"] = "cross_sectional_outlier"
    keys: dict[str | Ref[DimensionKind], JsonScalar]
    direction: str
    peer_scope: tuple[str, ...] = ()


class OntologyMetricCandidate(BaseModel):
    """One selected, unscored ontology candidate ready for explicit observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["semantic_hypothesis"] = "semantic_hypothesis"
    candidate_set_ref: str
    item_id: str
    source_artifact_ref: str
    metric_ref: RefPayloadV1
    semantic_edge_ref: SemanticEdgeRef
    edge_relation: Literal["influences", "related_to"]
    candidate_semantic_ref: RefPayloadV1
    edge_context: SemanticEdgeContext
    inherited_scope: InheritedObservationScope
    readiness_fingerprint: str
    ontology_catalog_fingerprint: str
    semantic_catalog_fingerprint: str
    source_metric_ref: RefPayloadV1
    upstream_origins: tuple[CandidateOrigin, ...] = ()

    def __init__(self, **data: Any) -> None:
        if not _ONTOLOGY_CANDIDATE_CONSTRUCTION.get():
            raise TypeError(
                "OntologyMetricCandidate has no public constructor; "
                "use candidates.select(item_id=...)"
            )
        super().__init__(**data)

    def __repr__(self) -> str:
        return (
            "OntologyMetricCandidate("
            f"item_id={self.item_id!r}, metric_ref={self.metric_ref.path!r})"
        )


def _make_ontology_metric_candidate(**data: Any) -> OntologyMetricCandidate:
    token = _ONTOLOGY_CANDIDATE_CONSTRUCTION.set(True)
    try:
        return OntologyMetricCandidate(**data)
    finally:
        _ONTOLOGY_CANDIDATE_CONSTRUCTION.reset(token)


CandidateSelection = Annotated[
    PointAnomalySelection
    | PeriodShiftSelection
    | DriverAxisSelection
    | SliceSelection
    | WindowSelection
    | CrossSectionalOutlierSelection
    | OntologyMetricCandidate,
    Field(discriminator="kind"),
]


_SELECTION_OUTPUT_BY_SHAPE: dict[CandidateShape, str] = {
    "point_anomaly": "PointAnomalySelection",
    "period_shift": "PeriodShiftSelection",
    "driver_axis": "DriverAxisSelection",
    "slice": "SliceSelection",
    "window": "WindowSelection",
    "cross_sectional_outlier": "CrossSectionalOutlierSelection",
    "semantic_hypothesis": "OntologyMetricCandidate",
}


@dataclass(repr=False)
class CandidateSet(BaseFrame):
    """Call marivo.help(CandidateSet) for its public consumption contract."""

    meta: CandidateSetMetaValue
    _NEXT_INTENTS = ("select",)

    def _repr_identity(self) -> str:
        if isinstance(self.meta, SemanticHypothesisCandidateSetMeta):
            return (
                f"CandidateSet ref={self.meta.ref} objective=semantic_hypotheses "
                f"unscored rows={self.meta.row_count}"
            )
        return (
            f"CandidateSet ref={self.meta.ref} objective={self.meta.objective} "
            f"strategy={self.meta.strategy} rows={self.meta.row_count}"
        )

    def _card(self) -> Card:
        if not isinstance(self.meta, SemanticHypothesisCandidateSetMeta):
            return super()._card()
        meta = self.meta
        card = (
            self._header_card()
            .field("source_metric", meta.source_metric_ref.path)
            .field(
                "source_entity", meta.source_entity_ref.path if meta.source_entity_ref else "none"
            )
            .field("candidate_limit", str(meta.resolution_summary.candidate_limit))
            .field(
                "resolution",
                (
                    f"examined_edges={meta.resolution_summary.examined_edges} "
                    f"candidates_before_limit={meta.resolution_summary.candidate_count_before_limit} "
                    f"emitted={meta.resolution_summary.emitted_candidates} "
                    f"candidates_omitted={meta.resolution_summary.candidates_omitted}"
                ),
            )
            .field(
                "excluded_counts",
                json.dumps(
                    meta.resolution_summary.excluded_counts.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
        self._append_evidence_sections(card)
        contexts = {context.semantic_edge_ref.path: context for context in meta.edge_contexts}

        def candidate_lines() -> tuple[str, ...]:
            lines: list[str] = []
            for row in self._df.itertuples(index=False):
                edge_payload = json.loads(str(row.semantic_edge_ref))
                candidate_payload = json.loads(str(row.candidate_semantic_ref))
                metric_payload = json.loads(str(row.metric_ref))
                edge_path = str(edge_payload["path"])
                candidate_path = str(candidate_payload["path"])
                metric_path = str(metric_payload["path"])
                context = contexts[edge_path]
                relation = str(row.edge_relation)
                role = (
                    f"{candidate_path} may influence source Metric {meta.source_metric_ref.path}"
                    if relation == "influences"
                    else f"{candidate_path} is related to source Metric {meta.source_metric_ref.path}"
                )
                guardrails = "; ".join(context.guardrails) or "none"
                lines.append(
                    f"{row.item_id}: {role}; resolved_metric={metric_path}; "
                    f"business_definition={context.business_definition}; "
                    f"guardrails={guardrails}; "
                    f"select=candidates.select(item_id={str(row.item_id)!r})"
                )
            return tuple(lines)

        card.listing("candidates", candidate_lines())
        card.listing(
            "exclusions",
            (
                f"{item.reason}: edge={item.semantic_edge_ref.key}; "
                f"candidate={item.candidate_semantic_ref.path if item.candidate_semantic_ref else 'none'}; "
                f"metric={item.metric_ref.path if item.metric_ref else 'none'}; "
                f"matched_anchors={','.join(ref.path for ref in item.matched_anchor_refs) or 'none'}"
                for item in meta.resolution_summary.exclusions
            ),
        )
        card.field("exclusions_omitted", str(meta.resolution_summary.exclusions_omitted))
        if meta.resolution_summary.candidates_omitted:
            if meta.resolution_summary.candidate_limit < 200:
                larger = min(
                    200,
                    meta.resolution_summary.candidate_count_before_limit,
                )
                card.field(
                    "rerun",
                    f"session.discover.semantic_hypotheses(source, limit={larger})",
                )
            else:
                card.field("rerun", "limit=200 is the hard maximum")
        return card

    def contract(self) -> ArtifactContract:
        """Return the artifact contract with live/historical exclusion repairs."""
        contract = super().contract()
        select_affordance = ArtifactAffordance(
            capability_id="CandidateSet.select",
            public_entrypoint=_capability_public_entrypoint("CandidateSet.select"),
            help_target="CandidateSet.select",
            input_requirements=(
                ArtifactInputRequirement(
                    parameter="receiver",
                    accepted_families=("CandidateSet",),
                    bindable_from_current_artifact=True,
                ),
            ),
            expected_output_family=_SELECTION_OUTPUT_BY_SHAPE[self.meta.shape],
        )
        contract = contract.model_copy(
            update={"affordances": (*contract.affordances, select_affordance)}
        )
        if not isinstance(self.meta, SemanticHypothesisCandidateSetMeta):
            return contract
        from marivo.analysis.errors import AnalysisRepair
        from marivo.analysis.session._runtime import get_process_current
        from marivo.introspection.live.model import LiveHelpTarget
        from marivo.refs import ref as ref_factory

        session = get_process_current()
        live = (
            session is not None
            and session.id == self.meta.session_id
            and session.catalog.definition_fingerprint == self.meta.semantic_catalog_fingerprint
            and session._ontology_state == "ready"
            and session._ontology_catalog is not None
            and session._ontology_catalog.definition_fingerprint
            == self.meta.ontology_catalog_fingerprint
        )
        live_edge_refs = (
            {edge.ref for edge in session._ontology_catalog._edges_for_discovery()}
            if live and session is not None and session._ontology_catalog is not None
            else set()
        )
        issues: list[ArtifactIssue] = []
        for issue in contract.issues:
            if not isinstance(issue, CandidateResolutionIssue):
                issues.append(issue)
                continue
            refs_valid = live and issue.semantic_edge_ref in live_edge_refs
            if refs_valid and session is not None:
                for payload in (issue.candidate_semantic_ref, issue.metric_ref):
                    if payload is None:
                        continue
                    try:
                        factory = {
                            "entity": ref_factory.entity,
                            "measure": ref_factory.measure,
                            "metric": ref_factory.metric,
                        }.get(payload.kind.value)
                        if factory is None:
                            refs_valid = False
                            break
                        session.catalog.require(factory(payload.path))
                    except Exception:
                        refs_valid = False
                        break
            if refs_valid:
                issues.append(issue)
                continue
            issues.append(
                issue.model_copy(
                    update={
                        "historical": True,
                        "repair": AnalysisRepair(
                            kind="environment",
                            action=(
                                "Inspect this historical exclusion and rerun ontology discovery "
                                "against the current catalogs."
                            ),
                            help_target=LiveHelpTarget(
                                surface="ontology", canonical_id="authoring"
                            ),
                        ),
                    }
                )
            )
        return contract.model_copy(update={"issues": tuple(issues)})

    def _assert_shape(self, expected: CandidateShape) -> CandidateSet:
        if self.meta.shape != expected:
            from marivo.analysis.errors import SemanticKindMismatchError

            raise SemanticKindMismatchError(
                message=f"CandidateSet shape mismatch: expected {expected!r}",
                context={"got_shape": self.meta.shape, "expected_shape": expected},
            )
        return self

    def as_point_anomaly(self) -> CandidateSet:
        return self._assert_shape("point_anomaly")

    def as_period_shift(self) -> CandidateSet:
        return self._assert_shape("period_shift")

    def as_driver_axis(self) -> CandidateSet:
        return self._assert_shape("driver_axis")

    def as_slice(self) -> CandidateSet:
        return self._assert_shape("slice")

    def as_window(self) -> CandidateSet:
        return self._assert_shape("window")

    def as_cross_sectional_outlier(self) -> CandidateSet:
        return self._assert_shape("cross_sectional_outlier")

    def as_semantic_hypothesis(self) -> CandidateSet:
        return self._assert_shape("semantic_hypothesis")

    def select(self, *, item_id: str) -> CandidateSelection:
        """Return one closed typed selection by its stable candidate identity.

        Args:
            item_id: Exact copyable item id rendered by ``show()``.

        Returns:
            The shape-specific immutable CandidateSelection.

        Example:
            selection = candidates.select(item_id="candidate_<full sha256>")

        Constraints:
            Numeric ranks are not candidate identity and are not accepted.
        """
        from marivo.analysis.intents.select import select

        return select(self, item_id=item_id)
