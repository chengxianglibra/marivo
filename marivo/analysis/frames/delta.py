"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from marivo.analysis._semantic_persistence import AxisBindingV1, SlicePredicateV1
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.frames.base import (
    ArtifactPrecondition,
    BaseFrame,
    BaseFrameMeta,
    _display_column_names,
    assert_semantic_shape,
)
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
                kind="retry",
                action=(
                    "Attribute the underlying flow metrics separately; cumulative wrapper "
                    "attribution is not supported."
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
                kind="retry",
                action=(
                    "Choose attribution axes that exclude "
                    f"{status_time_dimension!r}, then retry attribute."
                ),
                help_target=help_target,
            ),
        )
    if meta.additivity is None or meta.additivity == "semi_additive":
        reason = "delta lacks complete persisted additivity metadata required by attribute"
        action = "Re-run observe and compare with the current semantic model, then retry attribute."
    else:
        reason = "non-additive metric delta requires component-aware attribution math"
        action = (
            "Model the metric as a ratio or weighted average, or attribute its additive "
            "numerator and denominator separately."
        )
    return ArtifactPrecondition(
        check="attribution_additivity_compatible",
        status="fail",
        reason=reason,
        repair=AnalysisRepair(
            kind="retry",
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


@dataclass(repr=False)
class DeltaFrame(BaseFrame):
    """Call mv.help(DeltaFrame) for its public consumption contract."""

    meta: DeltaFrameMeta

    _NEXT_INTENTS = ("attribute", "discover", "transform")

    def _repr_identity(self) -> str:
        unit_part = f" unit={self.meta.unit}" if self.meta.unit else ""
        return (
            f"DeltaFrame ref={self.meta.ref} metric={self.meta.metric_id}"
            f"{unit_part} rows={self.meta.row_count}"
        )

    @property
    def semantic_shape(self) -> Literal["scalar", "time_series", "segmented", "panel"]:
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
        to_date = self.meta.alignment.get("to_date") if self.meta.alignment else None
        if not isinstance(to_date, dict):
            return None
        tail = to_date.get("baseline_tail_buckets")
        if not isinstance(tail, int) or tail <= 0:
            return None
        return to_date

    def _card(self) -> Card:
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
