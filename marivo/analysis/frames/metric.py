"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis._cumulative import (
    cumulative_compare_anchor,
    cumulative_compare_blocker,
)
from marivo.analysis._semantic_persistence import AxisBindingV1, SlicePredicateV1
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.frames.base import (
    ArtifactAffordance,
    ArtifactContract,
    ArtifactPrecondition,
    BaseFrame,
    BaseFrameMeta,
    assert_semantic_shape,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1
from marivo.render import Card
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    ComparableValueSemanticsV1,
    DatasourceCompatibilityDomainV1,
    ExpressionPresentationV1,
    MetricArtifactIdentityV1,
    MetricExpressionGraphV1,
    MetricIdentity,
    MetricKeySchemaV1,
    RuntimeExpressionIdentity,
    SemanticDependencyDigestV1,
)
from marivo.semantic.unit_algebra import MetricUnitStateV2

if TYPE_CHECKING:
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.frames.coverage import CoverageFrame
    from marivo.analysis.frames.transforms import MetricFrameTransforms


class MetricExecutionStatsV1(BaseModel):
    """Bounded structural execution facts retained for operation telemetry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stats_schema: Literal["metric-execution-stats/v1"] = "metric-execution-stats/v1"
    root_origins: tuple[Literal["catalog", "runtime"], ...]
    physical_execution_count: int = Field(ge=0)
    cse_reused_occurrences: int = Field(ge=0)
    cache_hit: bool = False
    artifact_deduplicated: bool = False
    replay_used: bool = False
    downstream_blockers: tuple[str, ...] = ()


def _cumulative_anchor(meta_cumulative: dict[str, Any] | None) -> object | None:
    """Return the anchor payload from a cumulative marker, or None."""
    return cumulative_compare_anchor(meta_cumulative)


def _cumulative_blocked_precondition(blocker: str) -> ArtifactPrecondition:
    """Return the hard compare gate for an incompatible derived wrapper."""
    return ArtifactPrecondition(
        check="cumulative_compare_compatible",
        status="fail",
        reason=f"derived cumulative compare is blocked: {blocker}",
        repair=AnalysisRepair(
            kind="retry",
            action=(
                "Use cumulative components that all share one trailing or grain_to_date "
                "anchor, or compare the component metrics separately."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
        ),
    )


def _derived_cumulative_caveat(blocker: str) -> ArtifactPrecondition:
    """Return a generic caveat without inventing an anchor for blocked wrappers."""
    return ArtifactPrecondition(
        check="derived_cumulative_caveat",
        status="fail",
        reason=(
            f"derived metric contains cumulative components but has no valid common anchor: "
            f"{blocker}"
        ),
        repair=AnalysisRepair(
            kind="retry",
            action=(
                "Use the underlying flow components separately, or re-author every outer "
                "component with one common cumulative anchor."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
        ),
    )


def _cumulative_caveat(anchor: object) -> ArtifactPrecondition:
    """Anchor-dispatched running_total_caveat precondition.

    all_history frames keep the v1 monotonic-trend caveat; trailing frames
    surface rolling-window autocorrelation; grain_to_date frames surface the
    non-stationary period-reset caveat.
    """
    if isinstance(anchor, tuple) and anchor and anchor[0] == "trailing":
        reason = (
            "trailing values are a rolling window; rolling-series autocorrelation "
            "can pollute correlation and hypothesis-test interpretation"
        )
        repair_action = (
            "Use trailing cumulative frames only with identical anchor payloads "
            "for correlation and hypothesis tests."
        )
    elif isinstance(anchor, tuple) and anchor and anchor[0] == "grain_to_date":
        reason = (
            "grain_to_date values reset at period boundaries; non-stationary within "
            "and across periods, which can pollute correlation and hypothesis-test interpretation"
        )
        repair_action = (
            "Use grain_to_date cumulative frames only with single-period, "
            "boundary-anchored windows for correlation and hypothesis tests."
        )
    else:
        reason = (
            "cumulative values are running totals anchored to all history; "
            "shared monotonic trend can pollute correlation and "
            "hypothesis-test interpretation"
        )
        repair_action = (
            "Prefer non-cumulative frames for correlation and hypothesis tests; "
            "or interpret results with awareness of the shared monotonic trend."
        )
    return ArtifactPrecondition(
        check="running_total_caveat",
        status="fail",
        reason=reason,
        repair=AnalysisRepair(
            kind="retry",
            action=repair_action,
            help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
        ),
    )


def _cumulative_status_line(anchor: object, *, blocker: str | None = None) -> str:
    """Anchor-dispatched one-line cumulative status for the show() card."""
    if blocker is not None:
        return f"derived cumulative compare blocked: {blocker}"
    if isinstance(anchor, tuple) and anchor and anchor[0] == "trailing":
        return (
            f"cumulative=trailing({anchor[1]}, {anchor[2]}) rolling-window; "
            "rolling-series autocorrelation "
            "can pollute correlation and hypothesis-test interpretation"
        )
    if isinstance(anchor, tuple) and anchor and anchor[0] == "grain_to_date":
        return (
            f"cumulative=grain_to_date({anchor[1]}); values reset at period boundaries "
            "(non-stationary within and across periods)"
        )
    return (
        "cumulative=all_history running total; shared monotonic trend can "
        "pollute correlation and hypothesis-test interpretation"
    )


def _compare_conditional_preconditions(anchor: object) -> list[ArtifactPrecondition]:
    """Conditional compare preconditions for trailing/grain_to_date frames.

    For trailing the precondition states the identical-anchor requirement; for
    grain_to_date it states the single-period boundary-anchored requirement.
    The running_total_caveat is still attached so the agent sees the statistical
    hazard alongside the mechanical precondition.
    """
    caveat = _cumulative_caveat(anchor)
    if isinstance(anchor, tuple) and anchor and anchor[0] == "trailing":
        conditional = ArtifactPrecondition(
            check="compare_anchor_match",
            status="fail",
            reason=(
                "trailing cumulative compare requires an identical anchor payload "
                "(same count and unit) on both frames"
            ),
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Ensure both cumulative frames use the same trailing anchor "
                    "payload (same count and unit) before calling compare()."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            ),
        )
    elif isinstance(anchor, tuple) and anchor and anchor[0] == "grain_to_date":
        conditional = ArtifactPrecondition(
            check="compare_single_period_boundary",
            status="fail",
            reason=(
                "grain_to_date cumulative compare requires single-period, "
                "boundary-anchored windows on both frames (window starts on a "
                "reset boundary and spans exactly one reset period)"
            ),
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Ensure both grain_to_date cumulative frames use "
                    "single-period, boundary-anchored windows before calling compare()."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            ),
        )
    else:
        return [caveat]
    return [caveat, conditional]


def _attach_rollup_affordance(contract: ArtifactContract) -> ArtifactContract:
    """Expose the persisted rollup capability as a visible precondition fact."""
    affordances: list[ArtifactAffordance] = []
    for affordance in contract.affordances:
        if affordance.capability_id.startswith("transform."):
            affordances.append(
                affordance.model_copy(
                    update={
                        "preconditions": (
                            *affordance.preconditions,
                            ArtifactPrecondition(
                                check="rollup_fold",
                                status="pass",
                                reason="this cumulative frame supports a last-value rollup fold",
                            ),
                        )
                    }
                )
            )
        else:
            affordances.append(affordance)
    return contract.model_copy(update={"affordances": tuple(affordances)})


class MetricFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["metric_frame"] = "metric_frame"
    catalog_definition_fingerprint: str
    metric_id: str | None = Field(default=None, exclude=True)
    metric_identity: MetricIdentity | None = None
    metric_identities: tuple[MetricIdentity, ...] = ()
    expression_graph_ref: str | None = None
    expression_graph: MetricExpressionGraphV1 | None = None
    expression_fingerprint: str | None = None
    semantic_dependency_digest: SemanticDependencyDigestV1
    presentation_ref: str | None = None
    presentation: ExpressionPresentationV1 | None = None
    presentation_fingerprint: str | None = None
    artifact_identity: MetricArtifactIdentityV1 | None = None
    key_schema: MetricKeySchemaV1 | None = None
    source_compatibility_domain: DatasourceCompatibilityDomainV1 | None = None
    component_graph_ref: str | None = None
    quality_ref: str | None = None
    replay_graph_ref: str | None = None
    comparable_value_semantics_ref: str | None = None
    comparable_value_semantics: ComparableValueSemanticsV1 | None = None
    execution_stats: MetricExecutionStatsV1 | None = None
    axis_bindings: tuple[AxisBindingV1, ...] = ()
    slice_predicates: tuple[SlicePredicateV1, ...] = ()
    status_time_dimension_ref: RefPayloadV1 | None = None
    unit: str | None = None
    unit_state: MetricUnitStateV2 | None = None
    axes: dict[str, Any] = Field(default_factory=dict, exclude=True)
    measure: dict[str, Any]
    measures: list[dict[str, Any]] | None = None
    window: dict[str, Any] | None
    where: dict[str, Any] = Field(default_factory=dict, exclude=True)
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str = Field(default="", exclude=True)
    normalization: dict[str, Any] | None = None
    component_ref: str | None = None
    composition: dict[str, Any] | None = None
    #: Rows whose present division denominator was zero (null result); None
    #: for metrics whose composition does not divide.
    zero_denominator_rows: int | None = None
    fold: dict[str, Any] | None = None
    reaggregatable: bool = True
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = None
    aggregation: str | None = None
    status_time_dimension: str | None = Field(default=None, exclude=True)
    sample_set_digest: str | None = None
    quantile_mode: Literal["exact", "approximate"] | None = None
    quantile_method: str | None = None
    coverage_ref: str | None = None
    coverage_summary: dict[str, Any] | None = None
    cumulative: dict[str, Any] | None = None
    rollup_fold: Literal["last"] | None = None

    @model_validator(mode="after")
    def _validate_metric_identities(self) -> MetricFrameMeta:
        if not self.catalog_definition_fingerprint:
            raise ValueError("MetricFrameMeta requires catalog_definition_fingerprint")
        if not self.metric_identities:
            raise ValueError("MetricFrameMeta requires at least one metric identity")
        if self.metric_identity is None:
            if len(self.metric_identities) == 1:
                raise ValueError(
                    "arity-one MetricFrameMeta requires metric_identity to match metric_identities"
                )
        elif self.metric_identities != (self.metric_identity,):
            raise ValueError("metric_identity requires metric_identities=(metric_identity,)")
        if self.measures is not None:
            expected_measure_ids = tuple(
                identity.metric_ref.path
                if isinstance(identity, CatalogMetricIdentity)
                else f"runtime:{identity.expression_fingerprint}"
                for identity in self.metric_identities
            )
            actual_measure_ids = tuple(entry.get("metric_id") for entry in self.measures)
            if actual_measure_ids != expected_measure_ids:
                raise ValueError("measures metric_id displays do not match metric_identities")
        catalog_paths = tuple(
            identity.metric_ref.path
            for identity in self.metric_identities
            if isinstance(identity, CatalogMetricIdentity)
        )
        runtime_fingerprints = tuple(
            identity.expression_fingerprint
            for identity in self.metric_identities
            if isinstance(identity, RuntimeExpressionIdentity)
        )
        derived_metric_id = (
            catalog_paths[0]
            if len(catalog_paths) == 1
            else (f"runtime:{runtime_fingerprints[0]}" if len(runtime_fingerprints) == 1 else None)
        )
        if self.metric_id is not None and derived_metric_id is not None:
            if self.metric_id != derived_metric_id:
                raise ValueError("metric_id display value does not match metric_identity")
        elif self.metric_id is None:
            self.metric_id = derived_metric_id

        derived_models = {path.split(".", 1)[0] for path in catalog_paths}
        if not derived_models and self.semantic_dependency_digest is not None:
            derived_models = {
                entry.ref.path.split(".", 1)[0]
                for entry in self.semantic_dependency_digest.entries
                if "." in entry.ref.path
            }
        derived_model = next(iter(derived_models)) if len(derived_models) == 1 else ""
        if (
            catalog_paths
            and self.semantic_model
            and derived_model
            and self.semantic_model != derived_model
        ):
            raise ValueError("semantic_model display value does not match structured refs")
        if not self.semantic_model or not catalog_paths:
            self.semantic_model = derived_model

        derived_axes: dict[str, Any] = {}
        for binding in self.axis_bindings:
            key = (
                "time" if binding.role == "time_dimension" else binding.ref.path.rsplit(".", 1)[-1]
            )
            axis: dict[str, Any] = {
                "role": "time" if binding.role == "time_dimension" else "dimension",
                "column": binding.column,
                "ref": binding.ref.path,
            }
            if binding.grain is not None:
                axis["grain"] = binding.grain
            if binding.role == "time_dimension":
                axis["time_dimension"] = binding.ref.path.rsplit(".", 1)[-1]
            derived_axes[key] = axis
        if not self.axes:
            self.axes = derived_axes

        derived_where = {
            predicate.dimension_ref.path: predicate.value for predicate in self.slice_predicates
        }
        if not self.where:
            self.where = derived_where

        derived_status = (
            self.status_time_dimension_ref.path
            if self.status_time_dimension_ref is not None
            else None
        )
        if self.status_time_dimension is not None and derived_status is not None:
            if self.status_time_dimension != derived_status:
                raise ValueError(
                    "status_time_dimension display value does not match structured ref"
                )
        elif self.status_time_dimension is None:
            self.status_time_dimension = derived_status
        return self


@dataclass(repr=False)
class MetricFrame(BaseFrame):
    """Call mv.help(MetricFrame) for its public consumption contract."""

    meta: MetricFrameMeta

    #: Canonical column name for the metric value in the wrapped DataFrame.
    VALUE_COLUMN: str = "value"

    _NEXT_INTENTS = (
        "compare",
        "discover",
        "correlate",
        "transform",
        "assess_quality",
        "hypothesis_test",
        "forecast",
    )

    def _repr_identity(self) -> str:
        if self.arity > 1:
            return (
                f"MetricFrame ref={self.meta.ref} metrics={self.arity} "
                f"shape={self.meta.semantic_kind} rows={self.meta.row_count}"
            )
        unit_part = f" unit={self.meta.unit}" if self.meta.unit else ""
        return (
            f"MetricFrame ref={self.meta.ref} metric={self.meta.metric_id} "
            f"shape={self.meta.semantic_kind}{unit_part} rows={self.meta.row_count}"
        )

    @property
    def semantic_shape(self) -> Literal["scalar", "time_series", "segmented", "panel"]:
        """The frame's semantic shape (distinct from .shape, the dataframe dims)."""
        return self.meta.semantic_kind

    def measures_meta(self) -> list[dict[str, Any]]:
        """Ordered per-metric measure records; derived from scalar fields at arity-1."""
        if self.meta.measures:
            return [dict(entry) for entry in self.meta.measures]
        measure = self.meta.measure if isinstance(self.meta.measure, dict) else {}
        return [
            {
                "metric_id": self.meta.metric_id,
                "name": measure.get("name"),
                "column": self.VALUE_COLUMN,
                "unit": self.meta.unit,
                "additivity": self.meta.additivity,
                "aggregation": self.meta.aggregation,
                "status_time_dimension": self.meta.status_time_dimension,
                "reaggregatable": self.meta.reaggregatable,
                "cumulative": self.meta.cumulative,
            }
        ]

    @property
    def metrics(self) -> tuple[str, ...]:
        """Ordered metric ids carried by this frame."""
        return tuple(entry["metric_id"] for entry in self.measures_meta())

    @property
    def value_columns(self) -> tuple[str, ...]:
        """Value column name(s) in ``to_pandas()`` output, in metric order.

        Arity-1 observe frames export the metric short name (matching
        multi-metric output); other arity-1 frames use ``"value"``. Multi-metric
        frames use one column per metric. Exposed so callers can merge/rename
        without guessing the naming from arity. See issue #33.
        """
        if self.arity <= 1:
            return (self._arity1_exported_column_name(),)
        return tuple(str(entry["column"]) for entry in self.measures_meta())

    def _arity1_exported_column_name(self) -> str:
        """The column name ``to_pandas()`` uses for the single value column."""
        last_intent = self.lineage.steps[-1].intent if self.lineage.steps else None
        if last_intent != "observe" or self.VALUE_COLUMN not in self._df.columns:
            return self.VALUE_COLUMN
        measure = self.meta.measure if isinstance(self.meta.measure, dict) else {}
        name = measure.get("name")
        if not isinstance(name, str) or not name:
            metric_id = self.meta.metric_id
            name = metric_id.rsplit(".", 1)[-1] if metric_id else self.VALUE_COLUMN
        if name == self.VALUE_COLUMN:
            return self.VALUE_COLUMN
        if name in self._df.columns and name != self.VALUE_COLUMN:
            metric_id = self.meta.metric_id
            name = metric_id.replace(".", "__") if metric_id else name
        return name

    @property
    def arity(self) -> int:
        """Number of metrics carried by this frame."""
        return len(self.measures_meta())

    def _export_dataframe(self) -> pd.DataFrame:
        """Return a copy whose value columns are named by metric.

        Arity-1 frames returned by ``observe`` keep ``"value"`` as their
        internal canonical column but export the metric short name, matching
        multi-metric observe output. If that name collides with an axis column,
        the qualified metric id is used.
        """
        df = self._dataframe_copy()
        last_intent = self.lineage.steps[-1].intent if self.lineage.steps else None
        if last_intent != "observe" or self.arity != 1 or self.VALUE_COLUMN not in df.columns:
            return df

        measure = self.meta.measure if isinstance(self.meta.measure, dict) else {}
        measure_name = measure.get("name")
        if not isinstance(measure_name, str) or not measure_name:
            metric_id = self.meta.metric_id
            measure_name = metric_id.rsplit(".", 1)[-1] if metric_id else self.VALUE_COLUMN
        if measure_name == self.VALUE_COLUMN:
            return df
        if measure_name in df.columns:
            metric_id = self.meta.metric_id
            measure_name = metric_id.replace(".", "__") if metric_id else measure_name
        return df.rename(columns={self.VALUE_COLUMN: measure_name})

    # Every next-intent is gated at arity > 1; derive from _NEXT_INTENTS so
    # the two cannot drift.  These are capability-id prefixes: any
    # capability whose id starts with one of these prefixes is gated.
    _GATED_CAPABILITY_PREFIXES: tuple[str, ...] = _NEXT_INTENTS

    def _card(self) -> Card:
        card = super()._card()
        anchor = _cumulative_anchor(self.meta.cumulative)
        blocker = cumulative_compare_blocker(self.meta.cumulative)
        if self.meta.cumulative is not None:
            card.field("cumulative", _cumulative_status_line(anchor, blocker=blocker))
        if self.arity > 1:
            card.listing(
                label="measures",
                items=[
                    f"{entry['metric_id']} column={entry['column']}"
                    + (f" unit={entry['unit']}" if entry.get("unit") else "")
                    for entry in self.measures_meta()
                ],
            )
        return card

    def contract(self) -> ArtifactContract:
        """Return the mechanical consumption contract, gating multi-metric frames.

        At arity > 1, gated affordances (compare, correlate, transform,
        assess_quality, hypothesis_test, forecast, discover) carry a
        ``single_metric`` precondition teaching the agent to project to one
        metric first. When ``meta.cumulative`` is set, affordances carry an
        anchor-dispatched ``running_total_caveat`` precondition: all_history
        keeps the v1 monotonic-trend caveat (hard fail on compare); trailing
        surfaces rolling-window autocorrelation and grain_to_date surfaces
        the non-stationary period-reset caveat, with compare downgraded to a
        conditional affordance stating the mechanical preconditions. Derived
        wrappers surface either their common anchor or their exact compare
        blocker. A rollup transform affordance appears iff
        ``meta.rollup_fold`` is set.
        """
        contract = super().contract()
        anchor = _cumulative_anchor(self.meta.cumulative)
        blocker = cumulative_compare_blocker(self.meta.cumulative)
        if self.meta.cumulative is not None and blocker is not None:
            caveat = _derived_cumulative_caveat(blocker)
            blocked = _cumulative_blocked_precondition(blocker)
            blocked_affordances = []
            for affordance in contract.affordances:
                preconditions = [*affordance.preconditions, caveat]
                if affordance.capability_id == "compare":
                    preconditions.append(blocked)
                blocked_affordances.append(
                    affordance.model_copy(update={"preconditions": tuple(preconditions)})
                )
            contract = contract.model_copy(update={"affordances": tuple(blocked_affordances)})
        elif anchor is not None:
            caveat = _cumulative_caveat(anchor)
            compare_preconditions = _compare_conditional_preconditions(anchor)
            anchored_affordances: list[ArtifactAffordance] = []
            for affordance in contract.affordances:
                if affordance.capability_id == "compare":
                    # all_history: hard caveat only. trailing/grain_to_date:
                    # conditional affordance (caveat + mechanical preconditions).
                    preconditions = (
                        [caveat]
                        if not isinstance(anchor, tuple)
                        else [*affordance.preconditions, *compare_preconditions]
                    )
                else:
                    preconditions = [*affordance.preconditions, caveat]
                anchored_affordances.append(
                    affordance.model_copy(update={"preconditions": tuple(preconditions)})
                )
            contract = contract.model_copy(update={"affordances": tuple(anchored_affordances)})
        # Rollup affordance iff meta.rollup_fold is set; replaces the plain
        # transform re-observe hint with a rollup-tagged transform affordance.
        if self.meta.rollup_fold is not None:
            contract = _attach_rollup_affordance(contract)
        if self.arity <= 1:
            return contract
        first_metric = self.metrics[0]
        precondition = ArtifactPrecondition(
            check="single_metric",
            status="fail",
            reason=(f'frame carries {self.arity} metrics; call .metric("{first_metric}") first'),
            repair=AnalysisRepair(
                kind="retry",
                action=f'Call .metric("{first_metric}") to project to a single metric first.',
                help_target=LiveHelpTarget(surface="analysis", canonical_id="MetricFrame.metric"),
                snippet=f'frame.metric("{first_metric}")',
            ),
        )
        gated_prefixes = set(self._GATED_CAPABILITY_PREFIXES)

        def _is_gated(capability_id: str) -> bool:
            return any(
                capability_id == prefix or capability_id.startswith(prefix + ".")
                for prefix in gated_prefixes
            )

        affordances = [
            affordance.model_copy(
                update={"preconditions": (*affordance.preconditions, precondition)}
            )
            if _is_gated(affordance.capability_id)
            else affordance
            for affordance in contract.affordances
        ]
        return contract.model_copy(update={"affordances": tuple(affordances)})

    def as_scalar(self) -> MetricFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="scalar", frame_kind=self.meta.kind
        )
        return self

    def as_time_series(self) -> MetricFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="time_series", frame_kind=self.meta.kind
        )
        return self

    def as_segmented(self) -> MetricFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="segmented", frame_kind=self.meta.kind
        )
        return self

    def as_panel(self) -> MetricFrame:
        assert_semantic_shape(
            got=self.meta.semantic_kind, expected="panel", frame_kind=self.meta.kind
        )
        return self

    def components(self) -> ComponentFrame:
        """Load the recursive ComponentFrame persisted for this metric graph."""
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.frames._component import _load_component_frame

        validate_capability_inputs("MetricFrame.components", receiver=self)
        return _load_component_frame(
            parent_ref=self.ref,
            parent_kind=self.meta.kind,
            session_id=self.meta.session_id,
            project_root=self.meta.project_root,
            artifact_id=self.meta.artifact_id,
            component_ref=self.meta.component_ref or self.meta.component_graph_ref,
            composition=self.meta.composition
            or ({"kind": "metric_graph"} if self.meta.component_graph_ref is not None else None),
            advice="re-run observe() to regenerate it",
        )

    def coverage(self) -> CoverageFrame:
        """Load the linked CoverageFrame for this metric frame.

        The sidecar's ``coverage_kind`` is kind-dispatched and the two kinds
        never share one summary payload:

        - ``time_slot``: sampled semi-additive (time_fold) coverage. Rows carry
          ``(bucket_start, actual_samples, expected_samples, coverage_ratio,
          coverage_status)``; ``meta.sample_interval`` is the fold's sample
          interval (e.g. ``"5minute"``).
        - ``window_coverage``: trailing (rolling N) cumulative coverage. Rows
          carry ``(bucket_start, expected_span, covered_span, coverage_ratio,
          coverage_status)`` where ``expected_span`` is the window span in
          seconds and ``covered_span`` is clipped by the data start;
          ``meta.sample_interval`` is ``None``.

        Returns ``None`` coverage (no sidecar) when the parent frame has no
        ``coverage_ref`` (e.g. all_history and grain_to_date cumulatives).
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.frames._coverage import _load_coverage_frame

        validate_capability_inputs("MetricFrame.coverage", receiver=self)
        return _load_coverage_frame(
            parent_ref=self.ref,
            session_id=self.meta.session_id,
            project_root=self.meta.project_root,
            artifact_id=self.meta.artifact_id,
            coverage_ref=self.meta.coverage_ref,
        )

    @property
    def transform(self) -> MetricFrameTransforms:
        """Return typed transforms for this MetricFrame."""
        from marivo.analysis.frames.transforms import MetricFrameTransforms

        return MetricFrameTransforms(self)

    def metric(self, metric_id: str) -> MetricFrame:
        """Project one metric out of this frame as an arity-1 MetricFrame.

        Args:
            metric_id: Full metric id carried by this frame (see ``.metrics``).

        Returns:
            An arity-1 MetricFrame with the shared axes and that metric's
            values in the canonical ``value`` column. On an arity-1 frame,
            returns ``self`` when the id matches.

        Example:
            >>> revenue = frame.metric("sales.revenue")

        Constraints:
            Requires the frame's owning session to be current; commits a
            ``select_metric`` step (no backend query).
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.frames._metric_projection import project_metric

        validate_capability_inputs("MetricFrame.metric", receiver=self)
        return project_metric(self, metric_id)
