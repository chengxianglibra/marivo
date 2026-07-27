"""Typed delta analysis frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from marivo.analysis._semantic_persistence import AxisBindingV1, SlicePredicateV1
from marivo.analysis.errors import AnalysisRepair
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
    _display_column_names,
    assert_semantic_shape,
)
from marivo.analysis.frames.event import CoverageBasis, SubjectAxisBinding
from marivo.analysis.windows.spec import TimeScope
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1
from marivo.render import Card
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    DeltaComparisonIdentityV1,
    MetricIdentity,
    SemanticDependencyDigestV1,
)

if TYPE_CHECKING:
    from marivo.analysis.frames.base import ArtifactContract
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.frames.metric import MetricFrameMeta
    from marivo.analysis.frames.transforms import DeltaFrameTransforms
    from marivo.analysis.intents._shape import AttributionShape


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


def _attribution_contract_precondition(meta: DeltaFrameMeta) -> ArtifactPrecondition | None:
    """Describe the persisted additivity gate without loading sidecars."""
    if meta.cumulative is not None:
        return ArtifactPrecondition(
            check="cumulative_attribution_unsupported",
            status="fail",
            reason="attribute does not support cumulative deltas, including derived wrappers",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Inspect the underlying flow metric frames; this cumulative wrapper "
                    "has no mechanically valid attribution retry."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
            ),
        )
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
    comparison_identity: DeltaComparisonIdentityV1
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
    rollup_fold: Literal["last"] | None = None

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

    # Read-only family compatibility projections. These are intentionally
    # properties rather than persisted model fields: funnel continuation
    # admission is shape-gated, while generic DeltaFrame readers can still
    # inspect a closed, non-Metric value for legacy optional facets.
    @property
    def alignment(self) -> dict[str, Any]:
        return {
            "kind": self.alignment_kind,
            "axes": [axis.output_column for axis in self.axes],
        }

    @property
    def component_ref(self) -> None:
        return None

    @property
    def composition(self) -> None:
        return None

    @property
    def status_time_dimension(self) -> None:
        return None

    @property
    def fold(self) -> None:
        return None

    @property
    def additivity(self) -> None:
        return None

    @property
    def aggregation(self) -> None:
        return None

    @property
    def cumulative(self) -> None:
        return None

    @property
    def metric_id(self) -> str:
        return "funnel_loss_rate"

    @property
    def semantic_model(self) -> str:
        return ""


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

    def _to_date_tail(self) -> dict[str, Any] | None:
        """Return the to-date alignment dump when a non-empty baseline tail exists.

        Surfaced in ``show()`` / ``contract()`` so the agent knows the baseline
        window was longer than the current window: the extra tail buckets were
        dropped from the delta rows but remain available via ``to_pandas()``.
        """
        if self.meta.semantic_kind == "funnel":
            return None
        to_date = self.meta.alignment.get("to_date") if self.meta.alignment else None
        if not isinstance(to_date, dict):
            return None
        tail = to_date.get("baseline_tail_buckets")
        if not isinstance(tail, int) or tail <= 0:
            return None
        return to_date

    def _card(self) -> Card:
        if self.meta.semantic_kind == "funnel":
            meta = self.meta
            return (
                self._base_card()
                .field(
                    "alignment",
                    (
                        f"{meta.alignment_kind} axes={len(meta.axes)} "
                        f"zero_filled={meta.zero_filled_tuple_count}"
                    ),
                )
                .field("current_coverage", meta.current_coverage_basis)
                .field("baseline_coverage", meta.baseline_coverage_basis)
                .lazy_table(
                    columns=_display_column_names(self._df.columns),
                    rows_provider=self._preview_rows_provider,
                    row_count=len(self._df),
                )
            )
        card = self._base_card()
        precondition = _attribution_contract_precondition(self.meta)
        if precondition is None:
            card.field("attribute", "supported attribution_shape=sum")
        elif precondition.status == "pass":
            card.field("attribute", precondition.reason or "supported")
        elif precondition.check == "attribution_status_time_axis_excluded":
            card.field(
                "attribute",
                f"conditional: {precondition.reason}; inspect .contract() for repair",
            )
        else:
            card.field(
                "attribute",
                f"blocked: {precondition.reason}; inspect .contract() for repair",
            )
        to_date = self._to_date_tail()
        if to_date is not None:
            card.field(
                "to_date_alignment",
                (
                    f"matched_buckets={to_date.get('matched_buckets')} "
                    f"baseline_tail_buckets={to_date.get('baseline_tail_buckets')} "
                    f"reset_grain={to_date.get('reset_grain')}"
                ),
            )
        return card.lazy_table(
            columns=_display_column_names(self._df.columns),
            rows_provider=self._preview_rows_provider,
            row_count=len(self._df),
        )

    def contract(self) -> ArtifactContract:
        """Return the mechanical contract with persisted attribution gates."""
        contract = super().contract()
        if self.meta.semantic_kind == "funnel":
            return contract
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
        contract = contract.model_copy(update={"affordances": tuple(affordances)})
        to_date = self._to_date_tail()
        if to_date is None:
            return contract
        caveat = ArtifactPrecondition(
            check="to_date_baseline_tail",
            status="pass",
            reason=(
                f"ordinal alignment matched {to_date.get('matched_buckets')} buckets; "
                f"{to_date.get('baseline_tail_buckets')} baseline tail bucket(s) dropped "
                f"from delta rows (reset_grain={to_date.get('reset_grain')})"
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
        return _load_component_frame(
            parent_ref=self.ref,
            parent_kind=self.meta.kind,
            session_id=self.meta.session_id,
            project_root=self.meta.project_root,
            artifact_id=self.meta.artifact_id,
            component_ref=self.meta.component_ref,
            composition=self.meta.composition,
            advice="re-run compare() to regenerate it",
        )
