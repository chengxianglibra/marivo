"""Typed delta analysis frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import pandas as pd
from pydantic import ConfigDict, Field, model_validator

from marivo._temporal import ComparisonTemporalContractV1
from marivo.analysis._cumulative import (
    BASELINE_EVALUATION_END_COLUMN,
    CURRENT_EVALUATION_END_COLUMN,
    AllHistoryLevelChangeV1,
    AllHistoryPairAlignmentV1,
    AuthoredGrainToDateAnchorV1,
    AuthoredSemanticGrainToDateAnchorV1,
    AuthoredTrailingAnchorV1,
    CumulativeAlignmentV1,
    SemanticGrainToDateAnchorSemanticsV1,
    authored_comparable_period_anchor,
    cumulative_compare_anchor,
)
from marivo.analysis._semantic_persistence import AxisBindingV1, SlicePredicateV1
from marivo.analysis.attribution_contract import (
    AttributeAdmissionV1,
    AttributeModeAdmissionV1,
    AttributionBasisV1,
    AttributionShape,
    BlockedAttributeAdmissionV1,
    CumulativeAttributionRouteAdmissionV1,
    SupportedAttributeAdmissionV1,
    attribute_method_is_installed,
)
from marivo.analysis.cumulative_attribution import (
    CumulativeAttributionContractV1,
    cumulative_attribution_capability,
    cumulative_attribution_method,
    cumulative_over_ref,
)
from marivo.analysis.errors import AnalysisRepair, SemanticKindMismatchError
from marivo.analysis.event import (
    CompletenessDeclaration,
    EventMatchingPolicy,
    EventPattern,
    FirstPerSubject,
)
from marivo.analysis.frames.base import (
    ArtifactPrecondition,
    BaseFrame,
    BaseFrameMeta,
    _capability_public_entrypoint,
    assert_semantic_shape,
)
from marivo.analysis.frames.event import CoverageBasis, SubjectAxisBinding
from marivo.analysis.windows.spec import TimeScope
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1
from marivo.render import Card
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    CumulativeEquivalentComparisonSemanticsV1,
    DeltaComparisonIdentity,
    ExactComparisonSemanticsV1,
    MetricIdentity,
    SemanticDependencyDigestV1,
)

if TYPE_CHECKING:
    from marivo.analysis.frames.base import ArtifactContract
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.frames.metric import MetricFrameMeta
    from marivo.analysis.frames.transforms import DeltaFrameTransforms


Additivity = Literal["additive", "semi_additive", "non_additive"]


def _compatible_metric_semantics(
    current: MetricFrameMeta | None,
    baseline: MetricFrameMeta | None,
) -> tuple[Additivity | None, str | None, str | None]:
    """Return shared metric semantics, or unknown when either source disagrees."""
    if current is None or baseline is None or current.additivity is None:
        return None, None, None
    current_values = (
        current.additivity,
        current.aggregation,
        current.status_time_dimension,
    )
    baseline_values = (
        baseline.additivity,
        baseline.aggregation,
        baseline.status_time_dimension,
    )
    if current_values != baseline_values:
        return None, None, None
    return current_values


def _supports_component_attribution(meta: DeltaFrameMeta) -> bool:
    if meta.component_ref is None or not isinstance(meta.composition, dict):
        return False
    return meta.composition.get("kind") in {"ratio", "weighted_mean"}


def _component_attribution_shape(meta: DeltaFrameMeta) -> Literal["ratio_mix", "weighted_mix"]:
    kind = meta.composition.get("kind") if isinstance(meta.composition, dict) else None
    return "ratio_mix" if kind == "ratio" else "weighted_mix"


def _attribute_repair(
    action: str, *, kind: Literal["inspect", "semantic_authoring"] = "inspect"
) -> AnalysisRepair:
    return AnalysisRepair(
        kind=kind,
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
    )


def _cumulative_canonical_anchor(meta: CumulativeDeltaFrameMetaV1) -> object:
    """Return the canonical comparable-period anchor when one is persisted."""
    alignment = meta.comparable_period_alignment()
    if alignment is None:
        return None
    return alignment.canonical_anchor


def _attribute_admission(meta: DeltaFrameMeta) -> AttributeAdmissionV1:
    """Project the single effective installed-runtime attribution admission."""
    rollup_modes = AttributeModeAdmissionV1(
        multiple_axes=("joint", "hierarchy"),
        multiple_axes_default="joint",
    )
    nonadditive_modes = AttributeModeAdmissionV1(
        multiple_axes=("joint", "hierarchy"),
        multiple_axes_default="joint",
    )
    basis = meta.attribution_basis
    if isinstance(meta, CumulativeDeltaFrameMetaV1):
        method = cumulative_attribution_method(meta.cumulative_attribution.structure)
        capability = cumulative_attribution_capability(meta.cumulative_attribution)
        business = capability.business_axes
        if business.status == "blocked":
            return BlockedAttributeAdmissionV1(
                attribution_shape=method,
                blocker=business.blocker,
                repair=business.repair,
            )
        if isinstance(_cumulative_canonical_anchor(meta), SemanticGrainToDateAnchorSemanticsV1):
            return BlockedAttributeAdmissionV1(
                attribution_shape="unavailable",
                blocker="semantic_grain_decomposition_unsupported",
                repair=_attribute_repair(
                    "Semantic-calendar cumulative deltas cannot be decomposed. "
                    "Attribute the underlying base flow metric directly, or re-observe "
                    "the metric with a builtin calendar reset grain.",
                ),
            )
        return SupportedAttributeAdmissionV1(
            attribution_shape=method,
            mode=rollup_modes,
        )
    if meta.cumulative is not None:
        raise ValueError("cumulative delta metadata requires cumulative-delta/v1")
    if basis is not None:
        shape: AttributionShape = (
            "distinct_membership" if basis.kind == "count_distinct" else "quantile_replacement"
        )
        reproduction = basis.reproduction
        if reproduction.status == "blocked":
            blocker = reproduction.blocker
            action = {
                "unsupported_key_type": (
                    "Author a scalar distinct key type and re-run observe and compare."
                ),
                "non_mergeable_sample": (
                    "Use an exact or matching mergeable-sketch datasource and re-run observe."
                ),
                "point_estimate_only": (
                    "Use a datasource that exposes matching distribution evidence."
                ),
                "missing_method_metadata": (
                    "Re-observe with a datasource profile that declares its quantile method."
                ),
                "matching_evaluator_unavailable": (
                    "Use a datasource with an installed matching quantile evaluator."
                ),
            }[blocker]
            return BlockedAttributeAdmissionV1(
                attribution_shape=shape,
                blocker=blocker,
                repair=_attribute_repair(action),
            )
        if not attribute_method_is_installed(basis):
            return BlockedAttributeAdmissionV1(
                attribution_shape=shape,
                blocker="operator_method_not_installed",
                repair=_attribute_repair(
                    "Use an installed Marivo runtime that activates this persisted "
                    "attribution method."
                ),
            )
        return SupportedAttributeAdmissionV1(
            attribution_shape=shape,
            mode=nonadditive_modes,
        )
    if meta.additivity == "non_additive" and (
        meta.aggregation == "count_distinct"
        or meta.aggregation == "median"
        or (meta.aggregation or "").startswith("percentile(")
    ):
        return BlockedAttributeAdmissionV1(
            attribution_shape=(
                "distinct_membership"
                if meta.aggregation == "count_distinct"
                else "quantile_replacement"
            ),
            blocker="legacy_missing_basis",
            repair=_attribute_repair(
                "Re-run observe and compare to persist graph-owned attribution evidence."
            ),
        )
    if _supports_component_attribution(meta):
        return SupportedAttributeAdmissionV1(
            attribution_shape=_component_attribution_shape(meta),
            mode=rollup_modes,
        )
    if meta.additivity == "semi_additive" and meta.status_time_dimension is None:
        return BlockedAttributeAdmissionV1(
            attribution_shape="sum",
            blocker="missing_additivity_metadata",
            repair=_attribute_repair(
                "Author the semi-additive status-time dimension and re-run observe and compare.",
                kind="semantic_authoring",
            ),
        )
    if meta.additivity in {"additive", "semi_additive"}:
        return SupportedAttributeAdmissionV1(
            attribution_shape="sum",
            mode=rollup_modes,
        )
    return BlockedAttributeAdmissionV1(
        attribution_shape="unavailable",
        blocker="unsupported_aggregate",
        repair=_attribute_repair(
            "Inspect the aggregate contract and author an approved attribution basis.",
            kind="semantic_authoring",
        ),
    )


def _attribute_admission_text(meta: DeltaFrameMeta, admission: AttributeAdmissionV1) -> str:
    """Render the effective admission with bounded persisted source diagnostics."""
    status = (
        f"supported; attribution_shape={admission.attribution_shape}"
        if admission.status == "supported"
        else f"blocked; reason={admission.blocker}"
    )
    if _supports_component_attribution(meta):
        lowered_from = meta.composition.get("lowered_from") if meta.composition else None
        if isinstance(lowered_from, str):
            status += f"; lowered_from={lowered_from}"
    basis = meta.attribution_basis
    if basis is None:
        return status
    if basis.kind == "count_distinct":
        distinct_reproduction = basis.reproduction
        detail = (
            "source=exact_distinct_membership"
            if distinct_reproduction.status == "reproducible"
            else f"source_dtype={distinct_reproduction.source_dtype}"
        )
    else:
        quantile_reproduction = basis.reproduction
        source_method = quantile_reproduction.source_method or "unknown"
        detail = (
            f"q={basis.effective_q:.12g} source={quantile_reproduction.source_mode}/{source_method}"
        )
    return f"{status} {detail}"


def _cumulative_route_text(route: CumulativeAttributionRouteAdmissionV1) -> str:
    if route.status == "supported":
        return f"supported path={route.path}"
    return f"blocked blocker={route.blocker}"


def _attribution_contract_precondition(meta: DeltaFrameMeta) -> ArtifactPrecondition | None:
    """Describe the persisted additivity gate without loading sidecars."""
    if isinstance(meta, CumulativeDeltaFrameMetaV1):
        capability = cumulative_attribution_capability(meta.cumulative_attribution)
        supported = tuple(
            route
            for route in (capability.business_axes, capability.accumulation_time)
            if route.status == "supported"
        )
        if supported:
            return ArtifactPrecondition(
                check="cumulative_attribution_available",
                status="pass",
                reason=("cumulative attribution route is selected from exact requested axis refs"),
            )
        blocker = capability.business_axes
        assert blocker.status == "blocked"
        return ArtifactPrecondition(
            check="cumulative_attribution_available",
            status="fail",
            reason=f"cumulative attribution is blocked: {blocker.blocker}",
            repair=blocker.repair,
        )
    if meta.cumulative is not None:
        raise ValueError("cumulative delta metadata requires cumulative-delta/v1")
    if meta.attribution_basis is not None:
        # The typed admission is the only mechanical support state for a
        # graph-owned non-additive basis; do not add a contradictory legacy
        # additivity precondition to the same affordance.
        return None
    if _supports_component_attribution(meta):
        shape = _component_attribution_shape(meta)
        lowered_from = meta.composition.get("lowered_from") if meta.composition else None
        source = f" lowered_from={lowered_from}" if isinstance(lowered_from, str) else ""
        return ArtifactPrecondition(
            check="component_attribution_available",
            status="pass",
            reason=f"direct attribute is supported with attribution_shape={shape}{source}",
        )
    if meta.additivity == "additive":
        return None
    help_target = LiveHelpTarget(surface="analysis", canonical_id="attribute")
    if meta.additivity == "semi_additive" and meta.status_time_dimension is not None:
        status_time_dimension = meta.status_time_dimension
        return ArtifactPrecondition(
            check="attribution_status_time_axis_excluded",
            status="fail",
            reason=(
                "semi-additive attribution requires axes that exclude status time dimension "
                f"{status_time_dimension!r}"
            ),
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect the available attribution axes and exclude the status "
                    f"time dimension {status_time_dimension!r} explicitly."
                ),
                help_target=help_target,
            ),
        )
    if meta.additivity is None or meta.additivity == "semi_additive":
        reason = "delta lacks complete persisted additivity metadata required by attribute"
        action = "Inspect the current metric additivity and rebuild the source frames if needed."
        repair_kind: Literal["inspect", "semantic_authoring"] = "inspect"
    else:
        reason = "non-additive metric delta requires component-aware attribution math"
        action = (
            "Author an approved ratio or weighted-mean composition before requesting "
            "typed attribution."
        )
        repair_kind = "semantic_authoring"
    return ArtifactPrecondition(
        check="attribution_additivity_compatible",
        status="fail",
        reason=reason,
        repair=AnalysisRepair(
            kind=repair_kind,
            action=action,
            help_target=help_target,
        ),
    )


class DeltaFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["delta_frame"] = "delta_frame"
    catalog_definition_fingerprint: str
    source_dependency_digests: tuple[SemanticDependencyDigestV1, ...]
    axis_bindings: tuple[AxisBindingV1, ...] = ()
    slice_predicates: tuple[SlicePredicateV1, ...] = ()
    status_time_dimension_ref: RefPayloadV1 | None = None
    metric_id: str = Field(default="", exclude=True)
    metric_identity: MetricIdentity | None = None
    baseline_metric_identity: MetricIdentity | None = None
    comparison_identity: DeltaComparisonIdentity
    unit: str | None = None
    source_current_ref: str
    source_baseline_ref: str
    alignment: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str = Field(default="", exclude=True)
    normalization: dict[str, Any] | None = None
    component_ref: str | None = None
    composition: dict[str, Any] | None = None
    fold: dict[str, Any] | None = None
    component_folds: list[dict[str, Any]] = Field(default_factory=list)
    additivity: Additivity | None = None
    aggregation: str | None = None
    status_time_dimension: str | None = Field(default=None, exclude=True)
    cumulative: dict[str, Any] | None = None
    cumulative_change: AllHistoryLevelChangeV1 | None = None
    cumulative_alignment: CumulativeAlignmentV1 | None = None
    rollup_fold: Literal["last"] | None = None
    attribution_basis: AttributionBasisV1 | None = None
    temporal_contract: ComparisonTemporalContractV1 | None = None

    @model_validator(mode="after")
    def _derive_semantic_displays(self) -> DeltaFrameMeta:
        current = self.comparison_identity.current
        baseline = self.comparison_identity.baseline
        if self.metric_identity is not None and self.metric_identity != current:
            raise ValueError("delta metric_identity does not match comparison current")
        if self.baseline_metric_identity is not None and self.baseline_metric_identity != baseline:
            raise ValueError("delta baseline identity does not match comparison baseline")
        self.metric_identity = current
        self.baseline_metric_identity = baseline

        derived_metric_id = (
            current.metric_ref.path
            if isinstance(current, CatalogMetricIdentity)
            else f"runtime:{current.expression_fingerprint}"
        )
        if self.metric_id and self.metric_id != derived_metric_id:
            raise ValueError("delta metric_id display does not match comparison identity")
        self.metric_id = derived_metric_id

        catalog_paths = [
            identity.metric_ref.path
            for identity in (current, baseline)
            if isinstance(identity, CatalogMetricIdentity)
        ]
        domains = {path.split(".", 1)[0] for path in catalog_paths if "." in path}
        derived_model = next(iter(domains)) if len(domains) == 1 else ""
        if self.semantic_model and derived_model and self.semantic_model != derived_model:
            raise ValueError("delta semantic_model display does not match comparison identity")
        self.semantic_model = derived_model

        derived_status = (
            self.status_time_dimension_ref.path
            if self.status_time_dimension_ref is not None
            else None
        )
        if self.status_time_dimension is not None and self.status_time_dimension != derived_status:
            raise ValueError("delta status time display does not match structured ref")
        self.status_time_dimension = derived_status

        pair_payload = self.alignment.get("cumulative_pairs")
        if self.cumulative_change is None:
            if pair_payload is not None:
                raise ValueError(
                    "delta cumulative_pairs require an all-history cumulative_change marker"
                )
        elif pair_payload is None:
            raise ValueError(
                "all-history cumulative_change requires cumulative_pairs alignment evidence"
            )
        else:
            AllHistoryPairAlignmentV1.model_validate(pair_payload)

        anchor = cumulative_compare_anchor(self.cumulative)
        comparable_period = isinstance(anchor, tuple) and anchor[0] in {"trailing", "grain_to_date"}
        semantics = self.comparison_identity.semantics
        if comparable_period:
            assert isinstance(anchor, tuple)
            if self.cumulative_alignment is None:
                raise ValueError(
                    "trailing/grain-to-date delta requires typed cumulative alignment evidence"
                )
            if not isinstance(semantics, CumulativeEquivalentComparisonSemanticsV1):
                raise ValueError(
                    "trailing/grain-to-date delta requires cumulative-equivalent identity semantics"
                )
            if self.cumulative_alignment.current_authored_anchor != (
                authored_comparable_period_anchor(anchor)
            ):
                raise ValueError(
                    "delta cumulative alignment current anchor does not match cumulative marker"
                )
            if self.cumulative_alignment.pairs.matched_rows != self.row_count:
                raise ValueError("delta row_count does not match cumulative alignment matched_rows")
        else:
            if self.cumulative_alignment is not None:
                raise ValueError(
                    "typed cumulative alignment evidence requires trailing/grain-to-date delta"
                )
            if not isinstance(semantics, ExactComparisonSemanticsV1):
                raise ValueError(
                    "ordinary/all-history delta requires exact comparison identity semantics"
                )
        return self

    def all_history_pair_alignment(self) -> AllHistoryPairAlignmentV1 | None:
        """Return validated all-history pair evidence when the marker is present."""

        if self.cumulative_change is None:
            return None
        return AllHistoryPairAlignmentV1.model_validate(self.alignment["cumulative_pairs"])

    def comparable_period_alignment(self) -> CumulativeAlignmentV1 | None:
        """Return typed trailing/grain-to-date alignment evidence when present."""

        return self.cumulative_alignment


class CumulativeDeltaFrameMetaV1(DeltaFrameMeta):
    """Current clean-break metadata contract for every cumulative delta."""

    artifact_schema: Literal["cumulative-delta/v1"] = "cumulative-delta/v1"
    cumulative: dict[str, Any]
    cumulative_attribution: CumulativeAttributionContractV1

    @model_validator(mode="after")
    def _validate_cumulative_attribution(self) -> CumulativeDeltaFrameMetaV1:
        if cumulative_over_ref(self.cumulative) != self.cumulative_attribution.over_ref:
            raise ValueError(
                "cumulative attribution over_ref does not match the cumulative metric contract"
            )
        return self


FUNNEL_DELTA_COLUMNS = (
    "step_key",
    "current_cohort_count",
    "baseline_cohort_count",
    "current_resolved_cohort_count",
    "baseline_resolved_cohort_count",
    "current_entry_count",
    "baseline_entry_count",
    "current_resolved_entry_count",
    "baseline_resolved_entry_count",
    "current_reached_count",
    "baseline_reached_count",
    "current_lost_count",
    "baseline_lost_count",
    "current_coverage_censored_count",
    "baseline_coverage_censored_count",
    "current_loss_rate_from_previous",
    "baseline_loss_rate_from_previous",
    "loss_rate_from_previous_delta",
)


class FunnelDeltaFrameMeta(BaseFrameMeta):
    """Metadata for one exact comparison of two compatible Event funnels."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["delta_frame"] = "delta_frame"
    semantic_kind: Literal["funnel"] = "funnel"
    row_contract_version: Literal["funnel-delta-rows/v1"] = "funnel-delta-rows/v1"
    operator_version: Literal["compare.funnel/v1"] = "compare.funnel/v1"
    alignment_kind: Literal["step_key_and_axis_tuple"] = "step_key_and_axis_tuple"

    catalog_definition_fingerprint: str
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    pattern: EventPattern
    matching: EventMatchingPolicy
    completion_through: str
    axes: tuple[SubjectAxisBinding, ...] = ()
    aligned_step_keys: tuple[str, ...]
    zero_filled_tuple_count: int = Field(ge=0)

    source_current_ref: str
    source_baseline_ref: str
    source_current_fingerprint: str
    source_baseline_fingerprint: str
    source_current_journey_ref: str
    source_baseline_journey_ref: str

    current_cohort_window: TimeScope
    baseline_cohort_window: TimeScope
    current_coverage_basis: CoverageBasis
    baseline_coverage_basis: CoverageBasis
    current_completeness: tuple[CompletenessDeclaration, ...] = ()
    baseline_completeness: tuple[CompletenessDeclaration, ...] = ()

    @model_validator(mode="after")
    def _validate_funnel_delta_contract(self) -> FunnelDeltaFrameMeta:
        if type(self.matching) is not FirstPerSubject:
            raise ValueError("DeltaFrame[funnel] requires first_per_subject matching")
        retained = tuple(step.key for step in self.pattern.steps)
        if self.aligned_step_keys != retained:
            raise ValueError("aligned_step_keys must equal the retained EventPattern step order")
        for label, value in (
            ("source_current_ref", self.source_current_ref),
            ("source_baseline_ref", self.source_baseline_ref),
            ("source_current_journey_ref", self.source_current_journey_ref),
            ("source_baseline_journey_ref", self.source_baseline_journey_ref),
        ):
            if not value.strip():
                raise ValueError(f"DeltaFrame[funnel] {label} must be non-empty")
        if self.source_current_ref == self.source_baseline_ref:
            raise ValueError("DeltaFrame[funnel] requires two distinct source funnels")
        columns = tuple(item.output_column for item in self.axes)
        if len(set(columns)) != len(columns):
            raise ValueError("DeltaFrame[funnel] axes must have unique output columns")
        return self

    # Funnel continuation admission is shape-gated: the funnel shape exposes
    # only its own fields and never projects Metric Delta facets. Consumers
    # must dispatch on the DeltaFrameMeta | FunnelDeltaFrameMeta union.
    @property
    def alignment(self) -> dict[str, Any]:
        return {
            "kind": self.alignment_kind,
            "axes": [axis.output_column for axis in self.axes],
        }


DeltaFrameMetaVariant = Annotated[
    DeltaFrameMeta | FunnelDeltaFrameMeta,
    Field(discriminator="semantic_kind"),
]


@dataclass(repr=False)
class DeltaFrame(BaseFrame):
    """Call marivo.help(DeltaFrame) for its public consumption contract."""

    meta: DeltaFrameMetaVariant

    _NEXT_INTENTS = ("attribute", "discover", "transform")

    def _repr_identity(self) -> str:
        if isinstance(self.meta, FunnelDeltaFrameMeta):
            return (
                f"DeltaFrame ref={self.meta.ref} shape=funnel "
                f"steps={len(self.meta.aligned_step_keys)} "
                f"axes={len(self.meta.axes)} rows={self.meta.row_count}"
            )
        unit_part = f" unit={self.meta.unit}" if self.meta.unit else ""
        return (
            f"DeltaFrame ref={self.meta.ref} metric={self.meta.metric_id}"
            f"{unit_part} rows={self.meta.row_count}"
        )

    @property
    def semantic_shape(self) -> Literal["scalar", "time_series", "segmented", "panel", "funnel"]:
        """The frame's semantic shape (distinct from .shape, the dataframe dims)."""
        return self.meta.semantic_kind

    def as_scalar(self) -> DeltaFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="scalar", frame_kind=self.meta.kind
        )
        return self

    def as_time_series(self) -> DeltaFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="time_series", frame_kind=self.meta.kind
        )
        return self

    def as_segmented(self) -> DeltaFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="segmented", frame_kind=self.meta.kind
        )
        return self

    def as_panel(self) -> DeltaFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="panel", frame_kind=self.meta.kind
        )
        return self

    def _cumulative_endpoint_order(self) -> Literal["forward", "reverse", "same", "mixed"]:
        """Compute direction from the exact persisted endpoint coordinates."""

        current = pd.to_datetime(self._df[CURRENT_EVALUATION_END_COLUMN], utc=True, errors="raise")
        baseline = pd.to_datetime(
            self._df[BASELINE_EVALUATION_END_COLUMN], utc=True, errors="raise"
        )
        directions = {
            "forward" if left > right else "reverse" if left < right else "same"
            for left, right in zip(current, baseline, strict=True)
        }
        if len(directions) == 1:
            return cast(
                "Literal['forward', 'reverse', 'same', 'mixed']",
                next(iter(directions)),
            )
        return "mixed"

    def _card(self) -> Card:
        if self.meta.semantic_kind == "funnel":
            meta = self.meta
            card = self._header_card().field(
                "alignment",
                (
                    f"{meta.alignment_kind} axes={len(meta.axes)} "
                    f"zero_filled={meta.zero_filled_tuple_count}"
                ),
            )
            card.field("current_coverage", meta.current_coverage_basis)
            card.field("baseline_coverage", meta.baseline_coverage_basis)
            self._append_evidence_sections(card)
            return self._append_preview_table(card)
        card = self._header_card()
        temporal_contract = getattr(self.meta, "temporal_contract", None)
        if temporal_contract is not None:
            evidence = temporal_contract.alignment_evidence
            policy = temporal_contract.alignment_policy
            policy_kind = policy.kind if policy is not None else "none"
            dropped = (
                f" dropped_reason={evidence.dropped_reason}" if evidence.dropped_reason else ""
            )
            card.field(
                "temporal_alignment",
                (
                    f"policy={policy_kind} paired={evidence.paired_points} "
                    f"current_only={evidence.current_only_points} "
                    f"baseline_only={evidence.baseline_only_points} "
                    f"unmatched={evidence.unmatched_points} "
                    f"dropped={evidence.dropped_points} "
                    f"path={evidence.execution_path} "
                    f"backend_optimized={str(evidence.backend_optimized).lower()}"
                    f"{dropped}"
                ),
            )
        if self.meta.cumulative_change is not None:
            pair_info = self.meta.all_history_pair_alignment()
            assert pair_info is not None
            card.field(
                "cumulative_change",
                "all_history observed_level_difference "
                f"endpoint_order={self._cumulative_endpoint_order()}",
            )
            card.field(
                "caveat",
                "source revision unverified; interval-flow equivalence not asserted",
            )
            card.field(
                "endpoints",
                "columns=current_evaluation_end,baseline_evaluation_end",
            )
            card.field(
                "alignment",
                (
                    f"matched_rows={pair_info.matched_rows} "
                    f"matched_null_rows={pair_info.matched_null_rows} "
                    f"current_unpaired_rows={pair_info.current_unpaired_rows} "
                    f"baseline_unpaired_rows={pair_info.baseline_unpaired_rows} "
                    f"action={pair_info.unpaired_action}"
                ),
            )
        comparable_alignment = self.meta.comparable_period_alignment()
        if comparable_alignment is not None:
            current_anchor = comparable_alignment.current_authored_anchor
            baseline_anchor = comparable_alignment.baseline_authored_anchor
            canonical = comparable_alignment.canonical_anchor
            if isinstance(current_anchor, AuthoredTrailingAnchorV1):
                assert isinstance(baseline_anchor, AuthoredTrailingAnchorV1)
                assert canonical.kind == "trailing"
                card.field(
                    "cumulative_alignment",
                    (
                        f"trailing span={canonical.span_seconds}s "
                        f"authored=current({current_anchor.count} {current_anchor.unit}), "
                        f"baseline({baseline_anchor.count} {baseline_anchor.unit})"
                    ),
                )
                card.field(
                    "caveat",
                    "rolling values overlap and are autocorrelated",
                )
            elif isinstance(current_anchor, AuthoredSemanticGrainToDateAnchorV1):
                assert isinstance(baseline_anchor, AuthoredSemanticGrainToDateAnchorV1)
                card.field(
                    "cumulative_alignment",
                    (
                        f"semantic grain_to_date calendar={current_anchor.calendar_ref} "
                        f"level={current_anchor.level} policy={self.meta.alignment.get('kind')}"
                    ),
                )
            else:
                assert isinstance(current_anchor, AuthoredGrainToDateAnchorV1)
                card.field(
                    "cumulative_alignment",
                    (
                        f"grain_to_date reset={current_anchor.reset_grain} "
                        f"policy={self.meta.alignment.get('kind')}"
                    ),
                )
            pairs = comparable_alignment.pairs
            card.field(
                "pairing",
                (
                    f"matched={pairs.matched_rows} matched_null={pairs.matched_null_rows} "
                    f"current_unpaired={pairs.current_unpaired_rows} "
                    f"baseline_unpaired={pairs.baseline_unpaired_rows} "
                    f"fallback={pairs.fallback_rows} action={pairs.unpaired_action}"
                ),
            )
        admission = _attribute_admission(self.meta)
        precondition = _attribution_contract_precondition(self.meta)
        attribute_entrypoint = _capability_public_entrypoint("attribute")
        if isinstance(self.meta, CumulativeDeltaFrameMetaV1):
            capability = cumulative_attribution_capability(self.meta.cumulative_attribution)
            card.field(
                f"{attribute_entrypoint} [business_axes]",
                _cumulative_route_text(capability.business_axes),
            )
            card.field(
                f"{attribute_entrypoint} [accumulation_time]",
                _cumulative_route_text(capability.accumulation_time),
            )
            card.field(
                f"{attribute_entrypoint} [mixed_axes]",
                _cumulative_route_text(capability.mixed_axes),
            )
        if admission.status == "supported":
            card.field(
                attribute_entrypoint,
                _attribute_admission_text(self.meta, admission),
            )
        elif (
            precondition is not None
            and precondition.check == "attribution_status_time_axis_excluded"
        ):
            card.field(
                attribute_entrypoint,
                f"conditional: {precondition.reason}; inspect frame.contract().show()",
            )
        else:
            card.field(
                attribute_entrypoint,
                _attribute_admission_text(self.meta, admission)
                + "; inspect frame.contract().show()",
            )
        self._append_evidence_sections(card)
        return self._append_preview_table(card)

    def contract(self) -> ArtifactContract:
        """Return the mechanical contract with persisted attribution gates."""
        contract = super().contract()
        if self.meta.semantic_kind == "funnel":
            # Metric-only continuations are structurally impossible on the
            # closed funnel union; do not advertise them to the agent. Keep
            # the real affordances (attribute / quality_report / discover.*).
            metric_only = {"DeltaFrame.components"} | {
                f"transform.{name}"
                for name in ("bottomk", "filter", "rank", "rollup", "slice", "topk", "window")
            }
            contract = contract.model_copy(
                update={
                    "affordances": tuple(
                        affordance
                        for affordance in contract.affordances
                        if affordance.capability_id not in metric_only
                    ),
                }
            )
            return contract
        from marivo.analysis.ontology_contract import attach_ontology_discovery_preconditions

        contract = attach_ontology_discovery_preconditions(self, contract)
        affordances = []
        for affordance in contract.affordances:
            if affordance.capability_id == "attribute":
                precondition = _attribution_contract_precondition(self.meta)
                affordance = affordance.model_copy(
                    update={
                        "preconditions": (
                            (*affordance.preconditions, precondition)
                            if precondition is not None
                            else affordance.preconditions
                        ),
                    }
                )
            affordances.append(affordance)
        contract = contract.model_copy(
            update={
                "affordances": tuple(affordances),
                "attribute_admission": _attribute_admission(self.meta),
                "cumulative_attribution": (
                    cumulative_attribution_capability(self.meta.cumulative_attribution)
                    if isinstance(self.meta, CumulativeDeltaFrameMetaV1)
                    else None
                ),
            }
        )
        comparable_alignment = self.meta.comparable_period_alignment()
        if comparable_alignment is None:
            return contract
        pairs = comparable_alignment.pairs
        caveat = ArtifactPrecondition(
            check="cumulative_pairing",
            status="pass",
            reason=(
                f"alignment retained {pairs.matched_rows} paired rows and dropped "
                f"{pairs.current_unpaired_rows} current / "
                f"{pairs.baseline_unpaired_rows} baseline unpaired rows; "
                f"fallback_rows={pairs.fallback_rows}"
            ),
        )
        affordances = [
            affordance.model_copy(update={"preconditions": (*affordance.preconditions, caveat)})
            for affordance in contract.affordances
        ]
        return contract.model_copy(update={"affordances": tuple(affordances)})

    def predicted_attribution_shape(self) -> AttributionShape:
        """Predict the AttributionFrame shape decompose will produce for this delta.

        Reads this delta's component_ref + decomposition kind only (no component
        load); "sum" when not component-aware, else "ratio_mix"/"weighted_mix".
        """
        if self.meta.semantic_kind == "funnel":
            return "ratio_mix"

        from marivo.analysis.intents._shape import attribution_output_shape

        return attribution_output_shape(self.meta)

    @property
    def transform(self) -> DeltaFrameTransforms:
        """Return typed transforms for this DeltaFrame."""
        from marivo.analysis.frames.transforms import DeltaFrameTransforms

        return DeltaFrameTransforms(self)

    def components(self) -> ComponentFrame:
        """Load the linked ComponentFrame for component-aware deltas."""
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.frames._component import _load_component_frame

        validate_capability_inputs("DeltaFrame.components", receiver=self)
        if not isinstance(self.meta, DeltaFrameMeta):
            raise SemanticKindMismatchError(
                message=(
                    "DeltaFrame[funnel] has no component frame; the funnel shape "
                    "does not project a Metric Delta component graph"
                ),
                context={
                    "semantic_kind": self.meta.semantic_kind,
                    "frame_ref": self.ref,
                },
            )
        return _load_component_frame(
            parent_ref=self.ref,
            parent_kind=self.meta.kind,
            session_id=self.meta.session_id,
            project_root=self.meta.project_root,
            component_ref=self.meta.component_ref,
            composition=self.meta.composition,
            advice="re-run compare() to regenerate it",
        )
