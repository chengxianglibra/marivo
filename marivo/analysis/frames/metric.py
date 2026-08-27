"""Typed metric analysis frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo._temporal import (
    BuiltinPeriodBindingV1,
    FrameTemporalContractV1,
    SemanticPeriodBindingV1,
)
from marivo.analysis._cumulative import (
    SemanticGrainToDateAnchorSemanticsV1,
    canonical_comparable_period_anchor,
    cumulative_compare_anchor,
    cumulative_compare_blocker,
)
from marivo.analysis._semantic_persistence import (
    AxisBindingV1,
    MeasureBindingV1,
    SlicePredicateV1,
)
from marivo.analysis.attribution_contract import AttributionBasisV1
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.frames.base import (
    ArtifactAffordance,
    ArtifactContract,
    ArtifactPrecondition,
    BaseFrame,
    BaseFrameMeta,
    _ArtifactSemanticBinding,
    _capability_public_entrypoint,
    assert_semantic_shape,
)
from marivo.analysis.frames.subject import SubjectCohortBinding
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1, SemanticKind
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
from marivo.semantic.metric_graph_canonical import canonical_value
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
            kind="inspect",
            action=(
                "Inspect the cumulative component anchors and component metric frames; "
                "this wrapper has no mechanically valid compare retry."
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
            kind="semantic_authoring",
            action=(
                "Align every outer component to one approved cumulative anchor before "
                "building a new typed wrapper."
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
            "Inspect both frames and confirm identical trailing anchor payloads "
            "before correlation or hypothesis testing."
        )
    elif isinstance(anchor, tuple) and anchor and anchor[0] == "grain_to_date":
        reason = (
            "grain_to_date values reset at period boundaries; non-stationary within "
            "and across periods, which can pollute correlation and hypothesis-test interpretation"
        )
        repair_action = (
            "Inspect both frames and confirm single-period, boundary-anchored "
            "windows before correlation or hypothesis testing."
        )
    else:
        reason = (
            "cumulative values are running totals anchored to all history; "
            "shared monotonic trend can pollute correlation and "
            "hypothesis-test interpretation"
        )
        repair_action = (
            "Inspect the non-cumulative source frames and the shared monotonic trend "
            "before correlation or hypothesis testing."
        )
    return ArtifactPrecondition(
        check="running_total_caveat",
        status="fail",
        reason=reason,
        repair=AnalysisRepair(
            kind="inspect",
            action=repair_action,
            help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
        ),
    )


def _cumulative_compare_pair_contract(anchor: object) -> ArtifactPrecondition | None:
    if not isinstance(anchor, tuple) or anchor[0] not in {"trailing", "grain_to_date"}:
        return None
    if anchor[0] == "grain_to_date" and getattr(anchor[1], "kind", None) == "semantic":
        return ArtifactPrecondition(
            check="cumulative_comparable_period_pair",
            status="fail",
            reason=(
                "semantic grain-to-date comparison requires the same certified calendar "
                "snapshot and is not inferred from a display label"
            ),
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Use the persisted temporal authority contract to verify both frames "
                    "share one certified semantic calendar snapshot before comparing."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
            ),
        )
    canonical = canonical_comparable_period_anchor(anchor)
    if canonical.kind == "trailing":
        reason = (
            "baseline must have the same canonical trailing span "
            f"({canonical.span_seconds} seconds); equivalent fixed units are accepted, and "
            "only paired day-of-week/progress/correspondence positions enter the delta"
        )
    elif isinstance(canonical, SemanticGrainToDateAnchorSemanticsV1):
        reason = (
            f"baseline must use the same {canonical.calendar_ref} / {canonical.level} "
            "semantic reset and query grain; temporal alignment is allowed only at that "
            "reset period, and only paired day-of-week/progress/correspondence positions "
            "enter the delta"
        )
    else:
        reason = (
            f"baseline must use the same {canonical.reset_grain} reset and query grain; "
            "temporal alignment is allowed only at that reset period, and only paired "
            "day-of-week/progress/correspondence positions enter the delta"
        )
    return ArtifactPrecondition(
        check="cumulative_comparable_period_pair",
        status="pass",
        reason=reason,
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


def _clamp_reaggregatable(additivity: str | None, reaggregatable: bool) -> bool:
    """Conservative plain-sum rollup gate for ``reaggregatable`` (issue #110).

    ``reaggregatable`` means "a plain ``.sum()`` rollup is safe for the value
    column".  Only ``additive`` values are closed under cross-grain summation;
    ``semi_additive`` folds via ``rollup_fold`` and ``non_additive``/unknown
    values have no plain-sum rollup, so any persisted ``reaggregatable=True``
    must be downgraded to ``False`` unless the additivity is ``additive``.

    This is idempotent for the values the observe path already writes
    (additive keeps ``True``, everything else is already ``False``), so it is
    lossless for current payloads and only converges legacy artifacts
    (pre-issue-110 fold/cumulative-only rule) to the blocked state.
    """
    return bool(reaggregatable) and additivity == "additive"


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
    replay_graph_ref: str | None = None
    comparable_value_semantics_ref: str | None = None
    comparable_value_semantics: ComparableValueSemanticsV1 | None = None
    execution_stats: MetricExecutionStatsV1 | None = None
    axis_bindings: tuple[AxisBindingV1, ...] = ()
    slice_predicates: tuple[SlicePredicateV1, ...] = ()
    status_time_dimension_ref: RefPayloadV1 | None = None
    unit: str | None = None
    unit_state: MetricUnitStateV2 | None = None
    measure_bindings: tuple[MeasureBindingV1, ...] = ()
    axes: dict[str, Any] = Field(default_factory=dict, exclude=True)
    measure: dict[str, Any]
    measures: list[dict[str, Any]] | None = None
    window: dict[str, Any] | None
    report_tz: str | None = None
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
    #: Whether the materialized frame has a known safe *plain-sum* rollup.
    #: In the v1 contract this is only True for ``additive`` metrics with no
    #: fold/cumulative contract; ``semi_additive`` folds via ``rollup_fold`` and
    #: ``non_additive``/unknown values must be conservatively blocked (issue #110).
    reaggregatable: bool = True
    #: How the result may be summed on a business axis
    #: (``additive``/``semi_additive``/``non_additive``); ``None`` means unknown.
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = None
    #: How the source rows were aggregated (``sum``/``count``/``percentile(q)``/...);
    #: ``None`` means unknown or not applicable (e.g. ratio/linear).
    aggregation: str | None = None
    status_time_dimension: str | None = Field(default=None, exclude=True)
    sample_set_digest: str | None = None
    quantile_mode: Literal["exact", "approximate"] | None = None
    quantile_method: str | None = None
    attribution_basis: AttributionBasisV1 | None = None
    coverage_ref: str | None = None
    coverage_summary: dict[str, Any] | None = None
    cumulative: dict[str, Any] | None = None
    temporal_contract: FrameTemporalContractV1 | None = None
    rollup_fold: Literal["last"] | None = None
    cohort: SubjectCohortBinding | None = None

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
        if self.measure_bindings:
            if len(self.measure_bindings) != len(self.metric_identities):
                raise ValueError("measure_bindings count must match metric_identities count")
            for measure_binding, identity in zip(
                self.measure_bindings, self.metric_identities, strict=True
            ):
                if measure_binding.identity != identity:
                    raise ValueError(
                        "measure binding identity does not match metric_identities entry"
                    )
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


def _compact_metadata_value(value: object) -> str:
    """Render one persisted metadata value deterministically."""

    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{key}={_compact_metadata_value(value[key])}" for key in sorted(value))
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_compact_metadata_value(item) for item in value) + "]"
    if value is None:
        return "none"
    return str(value)


def _observation_scope(meta: MetricFrameMeta) -> str:
    """Return the exact persisted observation scope."""

    if meta.window is None:
        return "all available rows"
    start = _compact_metadata_value(meta.window.get("start"))
    end = _compact_metadata_value(meta.window.get("end"))
    parts = [f"[{start}, {end})"]
    for key in ("grain", "time_dimension"):
        value = meta.window.get(key)
        if value is not None:
            parts.append(f"{key}={_compact_metadata_value(value)}")
    return " ".join(parts)


def _axis_lines(meta: MetricFrameMeta) -> tuple[str, ...]:
    """Return deterministic persisted axis descriptions."""

    return tuple(
        " ".join(
            (
                f"{binding.role}={binding.ref.path}",
                f"column={binding.column}",
                *((f"grain={binding.grain}",) if binding.grain is not None else ()),
            )
        )
        for binding in meta.axis_bindings
    )


def _slice_lines(meta: MetricFrameMeta) -> tuple[str, ...]:
    """Return deterministic persisted slice descriptions."""

    return tuple(
        f"{predicate.dimension_ref.path}={_compact_metadata_value(predicate.value)}"
        for predicate in meta.slice_predicates
    )


def _fold_line(fold: dict[str, Any]) -> str:
    """Return one compact temporal-fold description."""

    keys = (
        "component_metric_id",
        "time_fold",
        "fold_kind",
        "fold_strategy",
        "status_time_dimension",
        "sample_interval",
        "identity_keys",
    )
    return " ".join(
        f"{key}={_compact_metadata_value(fold[key])}"
        for key in keys
        if key in fold and (fold[key] is not None or key == "sample_interval")
    )


def _temporal_authority_line(contract: FrameTemporalContractV1) -> str:
    """Return one stable, non-JSON summary of the persisted time authority."""

    parts: list[str] = []
    period = contract.observation_period
    if isinstance(period, BuiltinPeriodBindingV1):
        parts.extend(
            (
                f"kind={period.kind}",
                f"authority={period.authority_id}",
                f"level={period.level_name}",
                f"boundary_timezone={period.boundary_timezone}",
            )
        )
    elif isinstance(period, SemanticPeriodBindingV1):
        parts.extend(
            (
                f"kind={period.kind}",
                f"calendar={period.calendar_ref}",
                f"level={period.level_name}",
                f"snapshot={period.snapshot_digest}",
            )
        )
    else:
        parts.append("authority=none")
    parts.append(f"display_timezone={contract.display_timezone}")
    if contract.actual_start is not None and contract.actual_end is not None:
        parts.append(f"actual=[{contract.actual_start},{contract.actual_end})")
    if contract.data_extent_end is not None:
        parts.append(f"data_extent_end={contract.data_extent_end}")
    if contract.output_period_keys:
        parts.append(f"period_keys={len(contract.output_period_keys)}")
    elif contract.period_key_absence_reason is not None:
        parts.append(f"period_keys=none({contract.period_key_absence_reason})")
    return " ".join(parts)


def _append_metric_execution_semantics(card: Card, meta: MetricFrameMeta) -> None:
    """Append persisted decision-critical execution facts to a frame card."""

    card.field("observation_scope", _observation_scope(meta))
    card.field(
        "value_semantics",
        " ".join(
            (
                f"aggregation={meta.aggregation or 'unknown'}",
                f"additivity={meta.additivity or 'unknown'}",
                f"reaggregatable={'yes' if meta.reaggregatable else 'no'}",
            )
        ),
    )
    if meta.temporal_contract is not None:
        card.field(
            "temporal_authority",
            _temporal_authority_line(meta.temporal_contract),
        )
    axes = _axis_lines(meta)
    if axes:
        card.listing("axes", axes)
    slices = _slice_lines(meta)
    if slices:
        card.listing("slices", slices)

    fold = meta.fold
    if fold is None:
        return
    component_folds = fold.get("component_folds")
    if isinstance(component_folds, list):
        rendered_components = tuple(
            _fold_line(component) for component in component_folds if isinstance(component, dict)
        )
        if rendered_components:
            card.listing("component folds", rendered_components)
    else:
        rendered_fold = _fold_line(fold)
        if rendered_fold:
            card.field("time_fold", rendered_fold)

    if meta.coverage_summary is not None:
        summary = meta.coverage_summary
        card.field(
            "expected_sample_coverage",
            " ".join(
                (
                    f"min={_compact_metadata_value(summary.get('min'))}",
                    f"avg={_compact_metadata_value(summary.get('avg'))}",
                    f"partial_buckets={_compact_metadata_value(summary.get('partial_buckets'))}",
                    f"sidecar={meta.coverage_ref or 'none'}",
                )
            ),
        )
    elif fold.get("fold_strategy") == "snapshot_selection" or (
        isinstance(component_folds, list)
        and any(
            isinstance(component, dict) and component.get("fold_strategy") == "snapshot_selection"
            for component in component_folds
        )
    ):
        card.field(
            "expected_sample_coverage",
            "not_applicable (unsampled snapshot selection)",
        )


@dataclass(repr=False)
class MetricFrame(BaseFrame):
    """Metric artifact; call marivo.help(MetricFrame) for its public contract.

    Single-metric frames expose the metric name consistently through every
    public read path while typed analysis retains an internal canonical
    ``"value"`` column. Call ``marivo.help(MetricFrame)`` for the full consumption
    contract.
    """

    meta: MetricFrameMeta

    #: Canonical column name for the metric value in the wrapped DataFrame.
    VALUE_COLUMN: str = "value"

    _NEXT_INTENTS = (
        "compare",
        "discover",
        "correlate",
        "transform",
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
        """Ordered per-metric measure records; derived from typed bindings.

        Issue #54: typed ``measure_bindings`` are the authority; the compact
        ``measures``/``measure`` dicts are a render-only projection and are only
        consulted for the display name.
        """
        bindings = self.meta.measure_bindings
        if bindings:
            return [
                {
                    "metric_id": (
                        binding.identity.metric_ref.path
                        if isinstance(binding.identity, CatalogMetricIdentity)
                        else f"runtime:{binding.identity.expression_fingerprint}"
                    ),
                    "name": binding.display_name,
                    "column": binding.value_column,
                    "unit": binding.unit,
                    "unit_state": (
                        canonical_value(binding.unit_state)
                        if binding.unit_state is not None
                        else None
                    ),
                    "additivity": binding.additivity,
                    "aggregation": binding.aggregation,
                    "status_time_dimension": (
                        binding.status_time_dimension_ref.path
                        if binding.status_time_dimension_ref is not None
                        else None
                    ),
                    "reaggregatable": binding.reaggregatable,
                    "cumulative": binding.cumulative,
                }
                for binding in bindings
            ]
        if self.meta.measures:
            return [
                {
                    **dict(entry),
                    # The compact legacy records predate the cumulative marker;
                    # normalize the display key set to match the typed branch.
                    "cumulative": self.meta.cumulative,
                }
                for entry in self.meta.measures
            ]
        measure = self.meta.measure if isinstance(self.meta.measure, dict) else {}
        return [
            {
                "metric_id": self.meta.metric_id,
                "name": measure.get("name"),
                "column": self.VALUE_COLUMN,
                "unit": self.meta.unit,
                "unit_state": (
                    canonical_value(self.meta.unit_state)
                    if self.meta.unit_state is not None
                    else None
                ),
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
        """Public value column name(s), in metric order.

        Arity-1 frames expose the metric short name across ``show()``,
        ``columns``, ``contract()``, indexing, and ``to_pandas()``. Multi-metric
        frames use one column per metric. Exposed so callers can merge or rename
        frames without guessing the naming from arity or lineage.
        """
        if self.arity <= 1:
            return (self._arity1_exported_column_name(),)
        return tuple(str(entry["column"]) for entry in self.measures_meta())

    @property
    def time_dimension_columns(self) -> dict[str, str]:
        """Map each public time-axis column to its time_dimension semantic path.

        A time-series/panel frame buckets one physical time column (``bucket_start``
        by default). This mapping lets terminal callers resolve the selected time
        axis behind that column when joining or aligning multiple frames observed
        over different time dimensions, without memorizing which axis each
        ``bucket_start`` came from.

        Example:
            >>> frame.time_dimension_columns
            {'bucket_start': 'sales.orders.create_time'}
        """
        mapping: dict[str, str] = {}
        for binding in self._semantic_input_bindings():
            if binding.role == "time_axis" and binding.output_column is not None:
                mapping[binding.output_column] = binding.semantic_path
        return mapping

    def _arity1_exported_column_name(self) -> str:
        """The public column name used for the single metric value."""
        if self.VALUE_COLUMN not in self._df.columns:
            return self.VALUE_COLUMN
        value_index = list(self._df.columns).index(self.VALUE_COLUMN)
        occupied = {
            column
            for index, column in enumerate(BaseFrame._public_column_names(self))
            if index != value_index
        }
        measure = self.meta.measure if isinstance(self.meta.measure, dict) else {}
        name = measure.get("name")
        if not isinstance(name, str) or not name:
            metric_id = self.meta.metric_id
            name = metric_id.rsplit(".", 1)[-1] if metric_id else self.VALUE_COLUMN
        if name not in occupied:
            return name

        metric_id = self.meta.metric_id
        qualified_name = metric_id.replace(".", "__") if metric_id else name
        candidate = qualified_name
        suffix = 2
        while candidate in occupied:
            candidate = f"{qualified_name}#{suffix}"
            suffix += 1
        return candidate

    @property
    def arity(self) -> int:
        """Number of metrics carried by this frame."""
        return len(self.measures_meta())

    def _public_column_names(self) -> list[str]:
        """Project the internal canonical value column to the metric name."""
        columns = super()._public_column_names()
        if self.arity != 1 or self.VALUE_COLUMN not in self._df.columns:
            return columns
        value_index = list(self._df.columns).index(self.VALUE_COLUMN)
        columns[value_index] = self._arity1_exported_column_name()
        return columns

    def _semantic_input_bindings(self) -> tuple[_ArtifactSemanticBinding, ...]:
        """Expose exact metric, axis, slice, and runtime-leaf acquisition paths."""
        bindings: list[_ArtifactSemanticBinding] = []
        value_columns = self.value_columns
        for index, identity in enumerate(self.meta.metric_identities):
            if not isinstance(identity, CatalogMetricIdentity):
                continue
            bindings.append(
                _ArtifactSemanticBinding(
                    role="metric" if self.arity == 1 else f"metric[{index}]",
                    semantic_kind=SemanticKind.METRIC,
                    semantic_path=identity.metric_ref.path,
                    output_column=value_columns[index],
                )
            )

        public_columns = self.columns
        internal_columns = list(self._df.columns)
        for axis in self.meta.axis_bindings:
            output_column = (
                public_columns[internal_columns.index(axis.column)]
                if axis.column in internal_columns
                else axis.column
            )
            bindings.append(
                _ArtifactSemanticBinding(
                    role="time_axis" if axis.role == "time_dimension" else "dimension_axis",
                    semantic_kind=axis.ref.kind,
                    semantic_path=axis.ref.path,
                    output_column=output_column,
                )
            )
        for predicate in self.meta.slice_predicates:
            bindings.append(
                _ArtifactSemanticBinding(
                    role="slice",
                    semantic_kind=predicate.dimension_ref.kind,
                    semantic_path=predicate.dimension_ref.path,
                )
            )
        if self.meta.status_time_dimension_ref is not None:
            bindings.append(
                _ArtifactSemanticBinding(
                    role="status_time",
                    semantic_kind=self.meta.status_time_dimension_ref.kind,
                    semantic_path=self.meta.status_time_dimension_ref.path,
                )
            )

        if any(
            isinstance(identity, RuntimeExpressionIdentity)
            for identity in self.meta.metric_identities
        ):
            for dependency in self.meta.semantic_dependency_digest.entries:
                if dependency.ref.kind not in {SemanticKind.METRIC, SemanticKind.MEASURE}:
                    continue
                bindings.append(
                    _ArtifactSemanticBinding(
                        role=f"{dependency.ref.kind.value}_dependency",
                        semantic_kind=dependency.ref.kind,
                        semantic_path=dependency.ref.path,
                    )
                )
        return tuple(bindings)

    # These capability-id prefixes identify analytical continuations that
    # require one projected metric.
    _GATED_CAPABILITY_PREFIXES: tuple[str, ...] = (
        "compare",
        "discover",
        "correlate",
        "transform",
        "hypothesis_test",
        "forecast",
    )

    def _card(self) -> Card:
        card = self._header_card()
        _append_metric_execution_semantics(card, self.meta)
        anchor = _cumulative_anchor(self.meta.cumulative)
        blocker = cumulative_compare_blocker(self.meta.cumulative)
        if self.meta.cumulative is not None:
            card.field("cumulative", _cumulative_status_line(anchor, blocker=blocker))
        if anchor == "all_history" and blocker is None:
            card.field(
                _capability_public_entrypoint("compare"),
                "available; pair compatibility is validated at call time",
            )
            card.field(
                "caveat",
                "the result is not asserted to be interval flow; source history may be restated",
            )
        card.listing(
            label="measures",
            items=[
                f"{entry['metric_id']} column={self.value_columns[index]}"
                + (f" unit={entry['unit']}" if entry.get("unit") else "")
                for index, entry in enumerate(self.measures_meta())
            ],
        )
        self._append_evidence_sections(card)
        return self._append_preview_table(card)

    def contract(self) -> ArtifactContract:
        """Return the mechanical consumption contract, gating multi-metric frames.

        At arity > 1, gated affordances (compare, correlate, transform,
        hypothesis_test, forecast, discover) carry a
        ``single_metric`` precondition teaching the agent to project to one
        metric first. Construction quality evaluates the full multi-metric
        frame automatically. Cumulative pair-dependent checks are evaluated
        only by ``session.compare(...)`` once both frames and the selected
        alignment are available. Other statistical continuations retain their
        local running-total caveat. Derived wrappers surface either their common
        anchor or their exact local compare blocker. A rollup transform
        affordance appears iff ``meta.rollup_fold`` is set.
        """
        contract = super().contract()
        from marivo.analysis.ontology_contract import attach_ontology_discovery_preconditions

        contract = attach_ontology_discovery_preconditions(self, contract)
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
            anchored_affordances: list[ArtifactAffordance] = []
            for affordance in contract.affordances:
                if affordance.capability_id == "compare":
                    # Pair-dependent anchor, grain, timezone, dimensions, and
                    # aligned-row checks do not belong to a single-frame contract.
                    preconditions = list(affordance.preconditions)
                    pair_contract = _cumulative_compare_pair_contract(anchor)
                    if pair_contract is not None:
                        preconditions.append(pair_contract)
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
        projection_options = tuple(
            AnalysisRepair(
                kind="retry",
                action=f'Project the current frame to metric "{metric_id}".',
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id="MetricFrame.metric",
                ),
                snippet=f'frame.metric("{metric_id}")',
            )
            for metric_id in self.metrics
        )
        precondition = ArtifactPrecondition(
            check="single_metric",
            status="fail",
            reason=f"capability requires arity=1; frame carries arity={self.arity}",
            repair_options=projection_options,
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
            component_ref=self.meta.component_ref,
            composition=self.meta.composition
            or ({"kind": "metric_graph"} if self.meta.component_ref is not None else None),
            advice="re-run observe() to regenerate it",
        )

    def coverage(self) -> CoverageFrame | None:
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

        Returns:
            The linked :class:`CoverageFrame`, or ``None`` when the parent
            frame has no ``coverage_ref`` (e.g. all_history and grain_to_date
            cumulatives, or any observe result that did not emit a coverage
            sidecar). ``None`` is the ordinary "no coverage" state; construction
            quality coverage checks are available through ``quality_report()``. A set
            ``coverage_ref`` whose sidecar is missing or corrupt on disk still
            raises a fail-closed ``FrameReadError``.
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
