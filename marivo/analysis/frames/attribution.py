"""Typed attribution analysis frames."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis._cumulative import (
    GrainToDateAnchorSemanticsV1,
    TrailingAnchorSemanticsV1,
)
from marivo.analysis.attribution_contract import (
    AttributionAxisBindingV1,
    AttributionMode,
    AttributionShape,
)
from marivo.analysis.cumulative_attribution import CumulativeBridgeGrainV1
from marivo.analysis.errors import AttributionResolutionError
from marivo.analysis.event import EventMatchingPolicy
from marivo.analysis.frames.base import (
    BaseFrame,
    BaseFrameMeta,
    _ArtifactSemanticBinding,
    assert_attribution_shape,
)
from marivo.analysis.frames.event import CoverageBasis, SubjectAxisBinding
from marivo.analysis.funnel import FunnelLossRate
from marivo.refs import RefPayloadV1
from marivo.render import Card

if TYPE_CHECKING:
    from marivo.analysis.frames.base import ArtifactContract
    from marivo.refs import DimensionKind, TimeDimensionKind
    from marivo.semantic.catalog import _SemanticInput

from marivo.analysis.frames._attribution_columns import (
    ATTRIBUTION_AXIS_COLUMN,
    ATTRIBUTION_DRIVER_COLUMN,
    ATTRIBUTION_LEVEL_COLUMN,
    ATTRIBUTION_PATH_COLUMN,
)

JsonScalar: TypeAlias = str | int | float | bool | None


class AttributionBucketReconciliationV1(BaseModel):
    """Closed reconciliation facts for one additive attribution bucket."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bucket_key: tuple[tuple[str, JsonScalar], ...] = Field(min_length=1)
    row_count: int = Field(ge=0)
    total_delta: float
    contribution_sum: float
    residual: float
    tolerance: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_reconciliation(self) -> AttributionBucketReconciliationV1:
        if abs(self.residual) > self.tolerance:
            raise ValueError("attribution bucket residual exceeds tolerance")
        if not math.isclose(
            self.total_delta - self.contribution_sum,
            self.residual,
            rel_tol=0.0,
            abs_tol=self.tolerance,
        ):
            raise ValueError("attribution bucket arithmetic is inconsistent")
        return self


class AttributionReconciliation(BaseModel):
    """Closed reconciliation facts for an attribution result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["reconciled"] = "reconciled"
    partition_count: int
    total_delta: float | None = None
    contribution_sum: float | None = None
    one_sided_contribution_sum: float | None = None
    unattributed_contribution_sum: float | None = None
    residual: float | None = None
    max_abs_residual: float
    bucket_reconciliations: tuple[AttributionBucketReconciliationV1, ...] = ()


class QuantileResolutionExecutionV1(BaseModel):
    """Execution evidence for one quantile bucket and axis resolution."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )
    schema_: Literal["quantile-resolution-execution/v1"] = Field(
        default="quantile-resolution-execution/v1", alias="schema"
    )
    coalition: Literal["exact_shapley", "permutation_shapley"]
    permutation_count: int = Field(ge=0)
    deterministic_seed_fingerprint: str | None = None

    @model_validator(mode="after")
    def _validate_execution(self) -> QuantileResolutionExecutionV1:
        if self.coalition == "exact_shapley":
            if self.permutation_count != 0 or self.deterministic_seed_fingerprint is not None:
                raise ValueError("exact quantile execution cannot carry permutation evidence")
        elif self.permutation_count <= 0 or self.deterministic_seed_fingerprint is None:
            raise ValueError("permutation quantile execution requires count and seed evidence")
        return self


class AttributionResolutionReconciliationV1(BaseModel):
    """Reconciliation for one bucket and one independent axis resolution."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )
    schema_: Literal["attribution-resolution-reconciliation/v1"] = Field(
        default="attribution-resolution-reconciliation/v1", alias="schema"
    )
    status: Literal["reconciled"] = "reconciled"
    axis_refs: tuple[RefPayloadV1, ...]
    bucket_key: tuple[tuple[str, JsonScalar], ...] = ()
    partition_count: int = Field(ge=0)
    total_delta: float
    contribution_sum: float
    residual: float
    max_abs_residual: float = Field(ge=0)
    quantile_execution: QuantileResolutionExecutionV1 | None = None


class CompleteMultiresolutionScopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["complete"] = "complete"


class SelectedMultiresolutionScopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["selected"] = "selected"
    axis_refs: tuple[RefPayloadV1, ...] = Field(min_length=1)


MultiresolutionScopeV1: TypeAlias = Annotated[
    CompleteMultiresolutionScopeV1 | SelectedMultiresolutionScopeV1,
    Field(discriminator="kind"),
]


class IndependentMultiresolutionEvidenceV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )
    schema_: Literal["independent-multiresolution/v1"] = Field(
        default="independent-multiresolution/v1", alias="schema"
    )
    rollup_safe: Literal[False] = False
    scope: MultiresolutionScopeV1
    resolution_reconciliations: tuple[AttributionResolutionReconciliationV1, ...]


class DistinctMembershipEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["distinct_membership"] = "distinct_membership"
    allocation: Literal["equal_membership_shapley"] = "equal_membership_shapley"
    source_basis_fingerprint: str
    overlap_key_count: int = Field(ge=0)
    identities_persisted: Literal[False] = False
    multiresolution: IndependentMultiresolutionEvidenceV1 | None = None


class QuantileReplacementEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["quantile_replacement"] = "quantile_replacement"
    q: float = Field(ge=0.0, le=1.0)
    source_mode: Literal["exact", "approximate"]
    source_method: str
    distribution_representation: Literal["exact_value_frequency", "mergeable_sketch"]
    coalition: Literal["exact_shapley", "permutation_shapley", "mixed"]
    evaluated_partition_count: int = Field(ge=0)
    permutation_count: int = Field(ge=0)
    deterministic_seed_fingerprint: str | None = None
    source_error_bound: float | None = None
    operator_version: Literal["quantile-replacement/v1"] = "quantile-replacement/v1"
    scope_reconciliations: tuple[AttributionResolutionReconciliationV1, ...] = Field(min_length=1)
    multiresolution: IndependentMultiresolutionEvidenceV1 | None = None


def summarize_quantile_resolution_executions(
    reconciliations: Sequence[AttributionResolutionReconciliationV1],
) -> dict[str, Any]:
    """Project bounded top-level evidence from exact per-scope executions."""
    executions = [item.quantile_execution for item in reconciliations]
    if not executions or any(item is None for item in executions):
        raise ValueError("quantile resolution reconciliation is missing execution evidence")
    typed = [item for item in executions if item is not None]
    coalitions = {item.coalition for item in typed}
    coalition = next(iter(coalitions)) if len(coalitions) == 1 else "mixed"
    seeds = [
        item.deterministic_seed_fingerprint
        for item in typed
        if item.deterministic_seed_fingerprint is not None
    ]
    seed_fingerprint = (
        f"sha256:{hashlib.sha256('|'.join(seeds).encode()).hexdigest()}" if seeds else None
    )
    return {
        "coalition": coalition,
        "evaluated_partition_count": max(item.partition_count for item in reconciliations),
        "permutation_count": max(item.permutation_count for item in typed),
        "deterministic_seed_fingerprint": seed_fingerprint,
    }


class CumulativeAttributionPartitionV1(BaseModel):
    """Closed reconciliation for one parent cumulative comparison row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    comparison_key: tuple[tuple[str, JsonScalar], ...]
    target_delta: float
    contribution_sum: float
    row_count: int = Field(ge=0)
    residual: float
    tolerance: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_reconciliation(self) -> CumulativeAttributionPartitionV1:
        if abs(self.residual) > self.tolerance:
            raise ValueError("cumulative attribution partition residual exceeds tolerance")
        if not math.isclose(
            self.target_delta - self.contribution_sum,
            self.residual,
            rel_tol=0.0,
            abs_tol=self.tolerance,
        ):
            raise ValueError("cumulative attribution partition arithmetic is inconsistent")
        return self


class CumulativeAllHistoryPartitionV1(CumulativeAttributionPartitionV1):
    """One all-history bridge partition with exact observed cutoffs."""

    current_evaluation_end: datetime
    baseline_evaluation_end: datetime

    @model_validator(mode="after")
    def _validate_cutoffs(self) -> CumulativeAllHistoryPartitionV1:
        if (
            self.current_evaluation_end.tzinfo is None
            or self.baseline_evaluation_end.tzinfo is None
        ):
            raise ValueError("all-history cumulative attribution cutoffs must be timezone-aware")
        return self


class CumulativeComparablePeriodPartitionV1(CumulativeAttributionPartitionV1):
    """One paired cumulative scope on each independently replayed side."""

    current_scope_start: datetime
    current_scope_end: datetime
    baseline_scope_start: datetime
    baseline_scope_end: datetime

    @model_validator(mode="after")
    def _validate_scopes(self) -> CumulativeComparablePeriodPartitionV1:
        values = (
            self.current_scope_start,
            self.current_scope_end,
            self.baseline_scope_start,
            self.baseline_scope_end,
        )
        if any(value.tzinfo is None for value in values):
            raise ValueError("cumulative attribution scopes must be timezone-aware")
        if self.current_scope_start >= self.current_scope_end:
            raise ValueError("current cumulative attribution scope must be non-empty")
        if self.baseline_scope_start >= self.baseline_scope_end:
            raise ValueError("baseline cumulative attribution scope must be non-empty")
        return self


class AllHistoryAnchorSemanticsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["all_history"] = "all_history"


CumulativeAttributionAnchorV1: TypeAlias = Annotated[
    AllHistoryAnchorSemanticsV1 | GrainToDateAnchorSemanticsV1 | TrailingAnchorSemanticsV1,
    Field(discriminator="kind"),
]


class CumulativeBusinessAxisEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cumulative_business_axes"] = "cumulative_business_axes"
    route: Literal["business_axes"] = "business_axes"
    anchor: CumulativeAttributionAnchorV1
    over_ref: RefPayloadV1
    partitions: tuple[CumulativeAttributionPartitionV1, ...]


class CumulativeAllHistoryFlowEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cumulative_all_history_flow"] = "cumulative_all_history_flow"
    route: Literal["accumulation_time"] = "accumulation_time"
    anchor: AllHistoryAnchorSemanticsV1
    over_ref: RefPayloadV1
    bridge_grain: CumulativeBridgeGrainV1
    effect_kinds: tuple[Literal["between_cutoffs"], ...] = ("between_cutoffs",)
    partitions: tuple[CumulativeAllHistoryPartitionV1, ...]


class CumulativeGrainToDateFlowEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cumulative_grain_to_date_flow"] = "cumulative_grain_to_date_flow"
    route: Literal["accumulation_time"] = "accumulation_time"
    anchor: GrainToDateAnchorSemanticsV1
    over_ref: RefPayloadV1
    bridge_grain: CumulativeBridgeGrainV1
    effect_kinds: tuple[Literal["current_scope", "baseline_scope", "shared_scope_change"], ...] = (
        "current_scope",
        "baseline_scope",
        "shared_scope_change",
    )
    partitions: tuple[CumulativeComparablePeriodPartitionV1, ...]


class CumulativeTrailingFlowEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cumulative_trailing_flow"] = "cumulative_trailing_flow"
    route: Literal["accumulation_time"] = "accumulation_time"
    anchor: TrailingAnchorSemanticsV1
    over_ref: RefPayloadV1
    bridge_grain: CumulativeBridgeGrainV1
    effect_kinds: tuple[Literal["entering", "leaving", "retained_change"], ...] = (
        "entering",
        "leaving",
        "retained_change",
    )
    partitions: tuple[CumulativeComparablePeriodPartitionV1, ...]


CumulativeAttributionEvidenceV1: TypeAlias = Annotated[
    CumulativeBusinessAxisEvidenceV1
    | CumulativeAllHistoryFlowEvidenceV1
    | CumulativeGrainToDateFlowEvidenceV1
    | CumulativeTrailingFlowEvidenceV1,
    Field(discriminator="kind"),
]


AttributionMethodEvidenceV1: TypeAlias = Annotated[
    DistinctMembershipEvidenceV1
    | QuantileReplacementEvidenceV1
    | CumulativeBusinessAxisEvidenceV1
    | CumulativeAllHistoryFlowEvidenceV1
    | CumulativeGrainToDateFlowEvidenceV1
    | CumulativeTrailingFlowEvidenceV1,
    Field(discriminator="kind"),
]


def _multiresolution_evidence(
    evidence: AttributionMethodEvidenceV1 | None,
) -> IndependentMultiresolutionEvidenceV1 | None:
    if isinstance(evidence, DistinctMembershipEvidenceV1 | QuantileReplacementEvidenceV1):
        return evidence.multiresolution
    return None


class AttributionFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["attribution_frame"] = "attribution_frame"
    metric_ids: list[str]
    source_refs: list[str]
    scope_delta_ref: str | None = None
    attribution_kind: Literal["decomposition", "correlation", "anomaly"]
    driver_field: str | None
    value_column: str | None
    contribution_column: str | None
    method: str
    params: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str
    reconciliation: AttributionReconciliation | None = None
    row_contract_version: Literal[
        "generic-attribution-rows/v2", "cumulative-flow-attribution-rows/v1"
    ]
    causal_claim: Literal["none"] = "none"
    axis_bindings: tuple[AttributionAxisBindingV1, ...] = ()
    attribution_mode: AttributionMode | None = None
    bucket_column: str | None = None
    method_evidence: AttributionMethodEvidenceV1 | None = None
    source_attribution_ref: str | None = None

    @model_validator(mode="after")
    def _validate_generic_v2(self) -> AttributionFrameMeta:
        cumulative_evidence = (
            self.method_evidence
            if isinstance(
                self.method_evidence,
                CumulativeBusinessAxisEvidenceV1
                | CumulativeAllHistoryFlowEvidenceV1
                | CumulativeGrainToDateFlowEvidenceV1
                | CumulativeTrailingFlowEvidenceV1,
            )
            else None
        )
        if self.row_contract_version == "cumulative-flow-attribution-rows/v1":
            if not isinstance(
                cumulative_evidence,
                CumulativeAllHistoryFlowEvidenceV1
                | CumulativeGrainToDateFlowEvidenceV1
                | CumulativeTrailingFlowEvidenceV1,
            ):
                raise ValueError("cumulative flow rows require typed flow evidence")
            if self.method != "sum" or self.attribution_mode is not None:
                raise ValueError("cumulative flow rows require single-axis sum attribution")
            if (
                len(self.axis_bindings) != 1
                or self.axis_bindings[0].ref != cumulative_evidence.over_ref
            ):
                raise ValueError("cumulative flow axis must be the evidence over_ref")
            if self.reconciliation is None:
                raise ValueError("cumulative flow rows require typed reconciliation")
            return self
        if isinstance(cumulative_evidence, CumulativeBusinessAxisEvidenceV1):
            if any(binding.ref == cumulative_evidence.over_ref for binding in self.axis_bindings):
                raise ValueError("cumulative business-axis rows cannot include over_ref")
        elif cumulative_evidence is not None:
            raise ValueError("generic attribution rows cannot carry cumulative flow evidence")
        if self.method in {"distinct_membership", "quantile_replacement"} and (
            self.method_evidence is None
        ):
            raise ValueError("non-additive attribution rows v2 require typed method evidence")
        if (
            self.method_evidence is not None
            and cumulative_evidence is None
            and self.method_evidence.kind != self.method
        ):
            raise ValueError("generic attribution method and typed method evidence must agree")
        if self.method not in {
            "sum",
            "ratio_mix",
            "weighted_mix",
            "distinct_membership",
            "quantile_replacement",
        }:
            raise ValueError("generic attribution rows v2 require a canonical method shape")
        if len(self.axis_bindings) == 1 and self.attribution_mode is not None:
            raise ValueError("single-axis generic attribution omits mode")
        if not self.axis_bindings:
            raise ValueError("generic attribution rows v2 require at least one typed axis")
        if len({(item.ref.kind, item.ref.path) for item in self.axis_bindings}) != len(
            self.axis_bindings
        ):
            raise ValueError("generic attribution axis refs must be unique")
        if len({item.output_column for item in self.axis_bindings}) != len(self.axis_bindings):
            raise ValueError("generic attribution axis output columns must be unique")
        if len(self.axis_bindings) > 1 and self.attribution_mode is None:
            raise ValueError("multi-axis generic attribution requires a typed mode")
        if self.method in {"distinct_membership", "quantile_replacement"}:
            if self.attribution_mode == "hierarchy":
                raise ValueError("non-additive attribution forbids hierarchy mode")
        elif self.attribution_mode == "multiresolution":
            raise ValueError("rollup-safe attribution forbids multiresolution mode")
        multiresolution = (
            self.method_evidence.multiresolution
            if isinstance(
                self.method_evidence,
                DistinctMembershipEvidenceV1 | QuantileReplacementEvidenceV1,
            )
            else None
        )
        if (self.attribution_mode == "multiresolution") != (multiresolution is not None):
            raise ValueError("multiresolution mode and method evidence must agree")
        if multiresolution is not None and self.method_evidence is not None:
            reconciliations = multiresolution.resolution_reconciliations
            if self.method_evidence.kind == "quantile_replacement":
                if reconciliations != self.method_evidence.scope_reconciliations:
                    raise ValueError(
                        "quantile multiresolution and scope reconciliations must agree"
                    )
            elif any(item.quantile_execution is not None for item in reconciliations):
                raise ValueError("distinct reconciliation cannot carry quantile execution evidence")
        if self.method_evidence is not None and self.method_evidence.kind == "quantile_replacement":
            summary = summarize_quantile_resolution_executions(
                self.method_evidence.scope_reconciliations
            )
            observed = {
                "coalition": self.method_evidence.coalition,
                "evaluated_partition_count": self.method_evidence.evaluated_partition_count,
                "permutation_count": self.method_evidence.permutation_count,
                "deterministic_seed_fingerprint": (
                    self.method_evidence.deterministic_seed_fingerprint
                ),
            }
            if observed != summary:
                raise ValueError("quantile method evidence does not summarize its scope executions")
        if self.reconciliation is None:
            raise ValueError("generic attribution rows v2 require typed reconciliation")
        return self


FUNNEL_ATTRIBUTION_COLUMNS = (
    "contribution_kind",
    "contribution",
    "share_of_total_delta",
    "share_of_positive_pool",
    "share_of_negative_pool",
)
FUNNEL_ATTRIBUTION_TOLERANCE = 1e-9


class FunnelAttributionReconciliation(BaseModel):
    """Closed reconciliation facts for one funnel loss-rate attribution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["reconciled"] = "reconciled"
    target_loss_rate_delta: float | None
    contribution_sum: float | None
    positive_pool: float = Field(
        description="Positive contribution pool at the deepest joint axis partition."
    )
    negative_pool: float = Field(
        description="Negative contribution pool at the deepest joint axis partition."
    )
    residual: float
    max_abs_residual: float
    tolerance: float = FUNNEL_ATTRIBUTION_TOLERANCE

    @model_validator(mode="after")
    def _validate_residual(self) -> FunnelAttributionReconciliation:
        if abs(self.residual) > self.tolerance:
            raise ValueError(
                "funnel attribution residual exceeds tolerance; "
                f"residual={self.residual!r} tolerance={self.tolerance!r}"
            )
        return self


class FunnelAttributionFrameMeta(BaseFrameMeta):
    """Metadata for additive ratio-mix attribution of one funnel loss rate."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["attribution_frame"] = "attribution_frame"
    semantic_kind: Literal["funnel_loss_rate"] = "funnel_loss_rate"
    row_contract_version: Literal["funnel-attribution-rows/v1"] = "funnel-attribution-rows/v1"
    operator_version: Literal["attribute.funnel_loss_rate/v1"] = "attribute.funnel_loss_rate/v1"
    attribution_kind: Literal["decomposition"] = "decomposition"
    method: Literal["ratio_mix"] = "ratio_mix"
    causal_claim: Literal["none"] = "none"

    catalog_definition_fingerprint: str
    source_delta_ref: str
    source_delta_fingerprint: str
    source_current_journey_ref: str
    source_baseline_journey_ref: str
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    source_pattern_fingerprint: str
    matching: EventMatchingPolicy
    coverage_basis: CoverageBasis
    target: FunnelLossRate
    preceding_step_key: str
    axes: tuple[SubjectAxisBinding, ...]
    mode: Literal["joint", "hierarchy"] | None = None
    reconciliation: FunnelAttributionReconciliation

    @model_validator(mode="after")
    def _validate_attribution_contract(self) -> FunnelAttributionFrameMeta:
        if not self.axes:
            raise ValueError("AttributionFrame[funnel_loss_rate] requires at least one axis")
        columns = tuple(item.output_column for item in self.axes)
        if len(set(columns)) != len(columns):
            raise ValueError("attribution axes must have unique output columns")
        if len(self.axes) > 1 and self.mode is None:
            raise ValueError("multi-axis funnel attribution requires joint or hierarchy mode")
        if len(self.axes) == 1 and self.mode is not None:
            raise ValueError("single-axis funnel attribution omits mode")
        return self


AttributionFrameMetaVariant = Annotated[
    AttributionFrameMeta | FunnelAttributionFrameMeta,
    Field(discriminator="semantic_kind"),
]


def _reconciliation_bucket_scalar(value: object) -> JsonScalar:
    if pd.isna(cast("Any", value)):
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        value = isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        value = item()
    if value is not None and not isinstance(value, (str, int, float, bool)):
        value = str(value)
    return value


def _cumulative_partition_rows(
    dataframe: Any,
    partition: CumulativeAttributionPartitionV1,
    *,
    hierarchy: bool,
) -> Any:
    rows = dataframe
    for name, value in partition.comparison_key:
        rows = rows[rows[name].map(_reconciliation_bucket_scalar) == value]
    if hierarchy and not rows.empty:
        rows = rows[rows[ATTRIBUTION_LEVEL_COLUMN] == rows[ATTRIBUTION_LEVEL_COLUMN].max()]
    return rows


def reconcile_cumulative_business_evidence(
    evidence: CumulativeBusinessAxisEvidenceV1,
    dataframe: Any,
    *,
    hierarchy: bool,
) -> CumulativeBusinessAxisEvidenceV1:
    """Bind cumulative business evidence to the rows that will be persisted."""

    partitions = []
    for partition in evidence.partitions:
        rows = _cumulative_partition_rows(dataframe, partition, hierarchy=hierarchy)
        contribution_sum = float(pd.to_numeric(rows["contribution"], errors="raise").sum())
        partitions.append(
            CumulativeAttributionPartitionV1.model_validate(
                {
                    **partition.model_dump(),
                    "contribution_sum": contribution_sum,
                    "row_count": len(rows),
                    "residual": partition.target_delta - contribution_sum,
                }
            )
        )
    return evidence.model_copy(update={"partitions": tuple(partitions)})


def cumulative_reconciliation_from_partitions(
    partitions: Sequence[CumulativeAttributionPartitionV1],
    *,
    one_sided_contribution_sum: float | None = None,
) -> AttributionReconciliation:
    """Build the one canonical summary of typed cumulative partitions."""

    max_abs_residual = max((abs(item.residual) for item in partitions), default=0.0)
    single = partitions[0] if len(partitions) == 1 else None
    return AttributionReconciliation(
        partition_count=len(partitions),
        total_delta=single.target_delta if single is not None else None,
        contribution_sum=single.contribution_sum if single is not None else None,
        one_sided_contribution_sum=one_sided_contribution_sum,
        unattributed_contribution_sum=single.residual if single is not None else None,
        residual=single.residual if single is not None else None,
        max_abs_residual=max_abs_residual,
    )


def _validate_cumulative_reconciliation_summary(
    reconciliation: AttributionReconciliation,
    partitions: Sequence[CumulativeAttributionPartitionV1],
) -> None:
    if reconciliation.partition_count != len(partitions):
        raise ValueError("cumulative attribution reconciliation partition count mismatch")
    max_abs_residual = max((abs(item.residual) for item in partitions), default=0.0)
    if not math.isclose(
        reconciliation.max_abs_residual,
        max_abs_residual,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("cumulative attribution reconciliation residual bound mismatch")
    scalar_fields = (
        reconciliation.total_delta,
        reconciliation.contribution_sum,
        reconciliation.residual,
    )
    if len(partitions) == 1:
        partition = partitions[0]
        expected = (
            partition.target_delta,
            partition.contribution_sum,
            partition.residual,
        )
        if any(value is None for value in scalar_fields) or any(
            not math.isclose(cast("float", value), expected_value, rel_tol=0.0, abs_tol=1e-12)
            for value, expected_value in zip(scalar_fields, expected, strict=True)
        ):
            raise ValueError("cumulative attribution scalar reconciliation mismatch")
        if reconciliation.unattributed_contribution_sum is not None and not math.isclose(
            reconciliation.unattributed_contribution_sum,
            partition.residual,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("cumulative attribution unattributed sum mismatch")
    elif any(value is not None for value in scalar_fields) or (
        reconciliation.unattributed_contribution_sum is not None
    ):
        raise ValueError("multi-partition cumulative reconciliation must omit scalar totals")


def _validate_share(actual: object, expected: float | None, *, label: str) -> None:
    if expected is None:
        if not pd.isna(cast("Any", actual)):
            raise ValueError(f"cumulative attribution {label} must be null")
        return
    if pd.isna(cast("Any", actual)) or not math.isclose(
        float(cast("Any", actual)), expected, rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError(f"cumulative attribution {label} is inconsistent")


def _validate_cumulative_partition_shares(
    rows: Any,
    partition: CumulativeAttributionPartitionV1,
) -> None:
    contributions = pd.to_numeric(rows["contribution"], errors="raise")
    positive_pool = float(contributions[contributions > 0].sum())
    negative_pool = float(-contributions[contributions < 0].sum())
    for index, contribution in contributions.items():
        total_share = (
            float(contribution) / partition.target_delta if partition.target_delta != 0 else None
        )
        positive_share = (
            float(contribution) / positive_pool if contribution > 0 and positive_pool > 0 else None
        )
        negative_share = (
            -float(contribution) / negative_pool if contribution < 0 and negative_pool > 0 else None
        )
        _validate_share(
            rows.loc[index, "share_of_total_delta"],
            total_share,
            label="share_of_total_delta",
        )
        _validate_share(
            rows.loc[index, "share_of_positive_pool"],
            positive_share,
            label="share_of_positive_pool",
        )
        _validate_share(
            rows.loc[index, "share_of_negative_pool"],
            negative_share,
            label="share_of_negative_pool",
        )


def _validate_cumulative_partition_rows(
    meta: AttributionFrameMeta,
    dataframe: Any,
    partitions: Sequence[CumulativeAttributionPartitionV1],
) -> None:
    coordinate_names = tuple(name for name, _ in partitions[0].comparison_key) if partitions else ()
    if any(
        tuple(name for name, _ in partition.comparison_key) != coordinate_names
        for partition in partitions
    ):
        raise ValueError("cumulative attribution partition coordinate layouts differ")
    observed_keys = {
        tuple((name, _reconciliation_bucket_scalar(row[name])) for name in coordinate_names)
        for _, row in dataframe.iterrows()
    }
    expected_keys = {
        partition.comparison_key for partition in partitions if partition.row_count > 0
    }
    if observed_keys != expected_keys:
        raise ValueError("cumulative attribution rows and partition keys differ")
    hierarchy = meta.attribution_mode == "hierarchy"
    for partition in partitions:
        rows = _cumulative_partition_rows(dataframe, partition, hierarchy=hierarchy)
        if len(rows) != partition.row_count:
            raise ValueError("cumulative attribution partition row count mismatch")
        contribution_sum = float(pd.to_numeric(rows["contribution"], errors="raise").sum())
        if not math.isclose(
            contribution_sum,
            partition.contribution_sum,
            rel_tol=0.0,
            abs_tol=partition.tolerance,
        ):
            raise ValueError("cumulative attribution partition contribution sum mismatch")
        _validate_cumulative_partition_shares(rows, partition)
    reconciliation = meta.reconciliation
    assert reconciliation is not None
    _validate_cumulative_reconciliation_summary(reconciliation, partitions)


def _validate_row_ranks(meta: AttributionFrameMeta, dataframe: Any) -> None:
    group_columns = [] if meta.bucket_column is None else [meta.bucket_column]
    if meta.attribution_mode in {"hierarchy", "multiresolution"}:
        group_columns.append(ATTRIBUTION_LEVEL_COLUMN)
    groups = (
        ((None, dataframe),)
        if not group_columns
        else dataframe.groupby(group_columns, dropna=False, sort=False)
    )
    for _, rows in groups:
        try:
            ranks = sorted(int(value) for value in rows["rank"])
        except (TypeError, ValueError) as exc:
            raise ValueError("generic attribution ranks must be integers") from exc
        if ranks != list(range(1, len(rows) + 1)):
            raise ValueError("generic attribution ranks must restart within each row scope")


def _validate_scope_reconciliations(
    meta: AttributionFrameMeta,
    dataframe: Any,
    reconciliations: Sequence[AttributionResolutionReconciliationV1],
) -> None:
    axis_refs = tuple(binding.ref for binding in meta.axis_bindings)
    bucket_column = meta.bucket_column
    has_levels = meta.attribution_mode == "multiresolution"
    observed_keys: set[tuple[int, tuple[tuple[str, JsonScalar], ...]]] = set()
    for _, row in dataframe.iterrows():
        level = int(row[ATTRIBUTION_LEVEL_COLUMN]) if has_levels else len(axis_refs)
        bucket_key = (
            ()
            if bucket_column is None
            else ((bucket_column, _reconciliation_bucket_scalar(row[bucket_column])),)
        )
        observed_keys.add((level, bucket_key))

    expected_keys: set[tuple[int, tuple[tuple[str, JsonScalar], ...]]] = set()
    for reconciliation in reconciliations:
        level = len(reconciliation.axis_refs)
        if reconciliation.axis_refs != axis_refs[:level]:
            raise ValueError("scope reconciliation has a non-prefix axis identity")
        if not has_levels and level != len(axis_refs):
            raise ValueError("joint scope reconciliation must identify every attribution axis")
        if bucket_column is None:
            if reconciliation.bucket_key:
                raise ValueError("scalar scope reconciliation cannot carry a bucket")
        elif tuple(name for name, _ in reconciliation.bucket_key) != (bucket_column,):
            raise ValueError("scope reconciliation bucket identity is invalid")
        key = (level, reconciliation.bucket_key)
        if key in expected_keys:
            raise ValueError("attribution reconciliation scope is duplicated")
        expected_keys.add(key)
        rows = dataframe[dataframe[ATTRIBUTION_LEVEL_COLUMN] == level] if has_levels else dataframe
        if bucket_column is not None:
            expected_bucket = reconciliation.bucket_key[0][1]
            rows = rows[rows[bucket_column].map(_reconciliation_bucket_scalar) == expected_bucket]
        contribution_sum = float(rows["contribution"].sum())
        if len(rows) != reconciliation.partition_count:
            raise ValueError("attribution rows do not match typed scope partition count")
        if not math.isclose(
            contribution_sum,
            reconciliation.contribution_sum,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("attribution rows do not match typed scope contribution sum")
        if not math.isclose(
            reconciliation.total_delta - reconciliation.contribution_sum,
            reconciliation.residual,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("attribution scope reconciliation arithmetic is inconsistent")
        if not math.isclose(
            abs(reconciliation.residual),
            reconciliation.max_abs_residual,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("attribution scope reconciliation residual bound is inconsistent")
    if observed_keys != expected_keys:
        raise ValueError("attribution rows and typed reconciliation scopes differ")


def validate_generic_attribution_rows(meta: AttributionFrameMeta, dataframe: Any) -> None:
    """Fail closed when persisted generic v2 rows contradict typed metadata."""
    if meta.row_contract_version != "generic-attribution-rows/v2":
        return
    columns = {str(column) for column in dataframe.columns}
    axis_columns = tuple(binding.output_column for binding in meta.axis_bindings)
    required = {
        *axis_columns,
        "contribution",
        "share_of_total_delta",
        "share_of_positive_pool",
        "share_of_negative_pool",
        "rank",
    }
    if meta.bucket_column is not None:
        required.add(meta.bucket_column)
    prefix_columns = {
        ATTRIBUTION_LEVEL_COLUMN,
        ATTRIBUTION_AXIS_COLUMN,
        ATTRIBUTION_DRIVER_COLUMN,
        ATTRIBUTION_PATH_COLUMN,
    }
    if meta.attribution_mode in {"hierarchy", "multiresolution"}:
        required.update(prefix_columns)
    elif columns & prefix_columns:
        raise ValueError("single-axis and joint attribution rows forbid prefix coordinates")
    if meta.method == "distinct_membership":
        required.update(
            {
                "current_observed_distinct",
                "baseline_observed_distinct",
                "current_allocated_distinct",
                "baseline_allocated_distinct",
            }
        )
        forbidden = {"current_count", "baseline_count", "contribution_std_error"}
    elif meta.method == "quantile_replacement":
        required.update({"current_count", "baseline_count", "contribution_std_error"})
        forbidden = {
            "current_observed_distinct",
            "baseline_observed_distinct",
            "current_allocated_distinct",
            "baseline_allocated_distinct",
        }
    else:
        forbidden = {
            "current_observed_distinct",
            "baseline_observed_distinct",
            "current_allocated_distinct",
            "baseline_allocated_distinct",
            "current_count",
            "baseline_count",
            "contribution_std_error",
        }
    missing = sorted(required - columns)
    present_forbidden = sorted(forbidden & columns)
    if missing or present_forbidden:
        raise ValueError(
            "generic attribution row columns mismatch: "
            f"missing={missing!r} forbidden={present_forbidden!r}"
        )
    _validate_row_ranks(meta, dataframe)
    if meta.attribution_mode == "multiresolution":
        evidence = meta.method_evidence
        multiresolution = _multiresolution_evidence(evidence)
        assert multiresolution is not None
        valid_levels = set(range(1, len(axis_columns) + 1))
        observed_levels = {
            int(value) for value in dataframe[ATTRIBUTION_LEVEL_COLUMN].dropna().unique()
        }
        scope = multiresolution.scope
        expected_levels = valid_levels if scope.kind == "complete" else {len(scope.axis_refs)}
        if observed_levels != expected_levels:
            raise ValueError("multiresolution row levels do not match typed scope")
        for _, row in dataframe.iterrows():
            level = int(row[ATTRIBUTION_LEVEL_COLUMN])
            if row[ATTRIBUTION_AXIS_COLUMN] != axis_columns[level - 1]:
                raise ValueError("multiresolution row axis does not match its ordered prefix")
            for column in axis_columns[level:]:
                if not pd.isna(row[column]):
                    raise ValueError("multiresolution row has a value beyond its prefix")
        _validate_scope_reconciliations(
            meta,
            dataframe,
            multiresolution.resolution_reconciliations,
        )
    elif meta.method_evidence is not None and meta.method_evidence.kind == "quantile_replacement":
        _validate_scope_reconciliations(
            meta,
            dataframe,
            meta.method_evidence.scope_reconciliations,
        )
    evidence = meta.method_evidence
    if isinstance(evidence, CumulativeBusinessAxisEvidenceV1):
        _validate_cumulative_partition_rows(meta, dataframe, evidence.partitions)
        return
    reconciliation = meta.reconciliation
    assert reconciliation is not None
    reconciled_rows = dataframe
    if meta.attribution_mode in {"hierarchy", "multiresolution"}:
        reconciled_level = int(dataframe[ATTRIBUTION_LEVEL_COLUMN].max())
        reconciled_rows = dataframe[dataframe[ATTRIBUTION_LEVEL_COLUMN] == reconciled_level]
    nonadditive = meta.method in {"distinct_membership", "quantile_replacement"}
    observed_partition_count = (
        len(reconciled_rows)
        if nonadditive
        else 0
        if reconciled_rows.empty
        else 1
        if meta.bucket_column is None
        else reconciled_rows.groupby(meta.bucket_column, dropna=False, sort=False).ngroups
    )
    if reconciliation.partition_count != observed_partition_count:
        raise ValueError("generic attribution reconciliation partition count is inconsistent")
    bucket_reconciliations = reconciliation.bucket_reconciliations
    if nonadditive or meta.bucket_column is None:
        if bucket_reconciliations:
            raise ValueError("generic attribution unexpectedly carries bucket reconciliations")
    else:
        bucket_column = meta.bucket_column
        if len(bucket_reconciliations) != observed_partition_count:
            raise ValueError("generic attribution bucket reconciliation count is inconsistent")
        expected_keys = {item.bucket_key for item in bucket_reconciliations}
        observed_keys = {
            ((bucket_column, _reconciliation_bucket_scalar(value)),)
            for value in reconciled_rows[bucket_column].drop_duplicates()
        }
        if expected_keys != observed_keys:
            raise ValueError("generic attribution bucket reconciliation keys are inconsistent")
        for receipt in bucket_reconciliations:
            if len(receipt.bucket_key) != 1 or receipt.bucket_key[0][0] != bucket_column:
                raise ValueError("generic attribution bucket reconciliation key is invalid")
            bucket_value = receipt.bucket_key[0][1]
            rows = reconciled_rows[
                reconciled_rows[bucket_column].map(_reconciliation_bucket_scalar) == bucket_value
            ]
            if len(rows) != receipt.row_count:
                raise ValueError("generic attribution bucket reconciliation row count mismatch")
            bucket_sum = float(pd.to_numeric(rows["contribution"], errors="raise").sum())
            if not math.isclose(
                bucket_sum,
                receipt.contribution_sum,
                rel_tol=0.0,
                abs_tol=receipt.tolerance,
            ):
                raise ValueError("generic attribution bucket reconciliation sum mismatch")
        expected_max_residual = max(
            (abs(item.residual) for item in bucket_reconciliations),
            default=0.0,
        )
        if not math.isclose(
            reconciliation.max_abs_residual,
            expected_max_residual,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("generic attribution bucket reconciliation bound mismatch")
    contribution_sum = float(reconciled_rows["contribution"].sum())
    scalar_fields = (
        reconciliation.total_delta,
        reconciliation.contribution_sum,
        reconciliation.residual,
    )
    if nonadditive or observed_partition_count == 1:
        if any(value is None for value in scalar_fields) or not math.isclose(
            contribution_sum,
            cast("float", reconciliation.contribution_sum),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "generic attribution rows do not match typed contribution reconciliation"
            )
        if not math.isclose(
            cast("float", reconciliation.total_delta) - contribution_sum,
            cast("float", reconciliation.residual),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("generic attribution reconciliation arithmetic is inconsistent")
    elif any(value is not None for value in scalar_fields):
        raise ValueError("multi-partition generic attribution must omit scalar totals")
    if reconciliation.max_abs_residual > 1e-9:
        raise ValueError("generic attribution reconciliation exceeds tolerance")


def validate_cumulative_flow_attribution_rows(meta: AttributionFrameMeta, dataframe: Any) -> None:
    """Fail closed when persisted cumulative-flow rows contradict typed evidence."""

    if meta.row_contract_version != "cumulative-flow-attribution-rows/v1":
        return
    evidence = meta.method_evidence
    if not isinstance(
        evidence,
        CumulativeAllHistoryFlowEvidenceV1
        | CumulativeGrainToDateFlowEvidenceV1
        | CumulativeTrailingFlowEvidenceV1,
    ):
        raise ValueError("cumulative flow rows are missing typed flow evidence")
    over_column = meta.axis_bindings[0].output_column
    required = {
        over_column,
        "flow_interval_start",
        "flow_interval_end",
        "source_side",
        "effect_kind",
        "current_value",
        "baseline_value",
        "contribution",
        "rank",
        "share_of_total_delta",
        "share_of_positive_pool",
        "share_of_negative_pool",
    }
    comparison_names = (
        tuple(name for name, _ in evidence.partitions[0].comparison_key)
        if evidence.partitions
        else ()
    )
    if any(
        tuple(name for name, _ in partition.comparison_key) != comparison_names
        for partition in evidence.partitions
    ):
        raise ValueError("cumulative flow partition coordinate layouts differ")
    required.update(comparison_names)
    columns = {str(column) for column in dataframe.columns}
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"cumulative flow row columns mismatch: missing={missing!r}")

    allowed_pairs: set[tuple[str, str]]
    if isinstance(evidence, CumulativeAllHistoryFlowEvidenceV1):
        allowed_pairs = {("between_cutoffs", "current"), ("between_cutoffs", "baseline")}
    elif isinstance(evidence, CumulativeGrainToDateFlowEvidenceV1):
        allowed_pairs = {
            ("current_scope", "current"),
            ("baseline_scope", "baseline"),
            ("shared_scope_change", "both"),
        }
    else:
        allowed_pairs = {
            ("entering", "current"),
            ("leaving", "baseline"),
            ("retained_change", "both"),
        }
    partition_by_key = {item.comparison_key: item for item in evidence.partitions}
    for _, row in dataframe.iterrows():
        pair = (str(row["effect_kind"]), str(row["source_side"]))
        if pair not in allowed_pairs:
            raise ValueError("cumulative flow effect_kind/source_side pair is invalid")
        current_missing = pd.isna(row["current_value"])
        baseline_missing = pd.isna(row["baseline_value"])
        if pair[1] == "current" and (current_missing or not baseline_missing):
            raise ValueError("current-side flow row has invalid value nullability")
        if pair[1] == "baseline" and (baseline_missing or not current_missing):
            raise ValueError("baseline-side flow row has invalid value nullability")
        if pair[1] == "both" and (current_missing or baseline_missing):
            raise ValueError("both-side flow row requires two observed values")
        start = pd.Timestamp(row["flow_interval_start"])
        end = pd.Timestamp(row["flow_interval_end"])
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("cumulative flow row intervals must be timezone-aware")
        if start >= end:
            raise ValueError("cumulative flow row interval must be non-empty")
        partition_key = tuple(
            (name, _reconciliation_bucket_scalar(row[name])) for name in comparison_names
        )
        partition = partition_by_key.get(partition_key)
        if partition is None:
            raise ValueError("cumulative flow row has an undeclared comparison partition")
        if isinstance(evidence, CumulativeAllHistoryFlowEvidenceV1):
            assert isinstance(partition, CumulativeAllHistoryPartitionV1)
            current_end = pd.Timestamp(partition.current_evaluation_end)
            baseline_end = pd.Timestamp(partition.baseline_evaluation_end)
            if current_end == baseline_end:
                raise ValueError("equal all-history cutoffs cannot carry flow rows")
            expected_side = "current" if current_end > baseline_end else "baseline"
            if pair[1] != expected_side:
                raise ValueError("all-history flow source side contradicts cutoff direction")
            scope_start = min(current_end, baseline_end)
            scope_end = max(current_end, baseline_end)
            if start < scope_start or end > scope_end:
                raise ValueError("all-history flow interval is outside the cutoff scope")
        else:
            assert isinstance(partition, CumulativeComparablePeriodPartitionV1)
            current_scope = (
                pd.Timestamp(partition.current_scope_start),
                pd.Timestamp(partition.current_scope_end),
            )
            baseline_scope = (
                pd.Timestamp(partition.baseline_scope_start),
                pd.Timestamp(partition.baseline_scope_end),
            )
            in_current = start >= current_scope[0] and end <= current_scope[1]
            in_baseline = start >= baseline_scope[0] and end <= baseline_scope[1]
            if pair[1] == "current" and not in_current:
                raise ValueError("current-side flow interval is outside its typed scope")
            if pair[1] == "baseline" and not in_baseline:
                raise ValueError("baseline-side flow interval is outside its typed scope")
            if pair[1] == "both" and not (in_current and in_baseline):
                raise ValueError("both-side flow interval is outside a typed scope")
        expected_contribution = (
            float(row["current_value"])
            if pair[1] == "current"
            else -float(row["baseline_value"])
            if pair[1] == "baseline"
            else float(row["current_value"]) - float(row["baseline_value"])
        )
        if not math.isclose(
            float(row["contribution"]),
            expected_contribution,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("cumulative flow contribution contradicts observed side values")

    _validate_cumulative_partition_rows(meta, dataframe, evidence.partitions)
    for partition in evidence.partitions:
        rows = _cumulative_partition_rows(dataframe, partition, hierarchy=False)
        ranks = sorted(int(value) for value in rows["rank"])
        if ranks != list(range(1, len(rows) + 1)):
            raise ValueError("cumulative flow ranks must restart within each partition")


@dataclass(repr=False)
class AttributionFrame(BaseFrame):
    """Call marivo.help(AttributionFrame) for its public consumption contract."""

    meta: AttributionFrameMetaVariant

    def _semantic_input_bindings(self) -> tuple[_ArtifactSemanticBinding, ...]:
        if self.meta.semantic_kind == "funnel_loss_rate":
            return ()
        return tuple(
            _ArtifactSemanticBinding(
                role="attribution_axis",
                semantic_kind=binding.ref.kind,
                semantic_path=binding.ref.path,
                output_column=binding.output_column,
            )
            for binding in self.meta.axis_bindings
        )

    def _repr_identity(self) -> str:
        if self.meta.semantic_kind == "funnel_loss_rate":
            return (
                f"AttributionFrame ref={self.meta.ref} "
                f"shape=funnel_loss_rate method={self.meta.method} "
                f"axes={len(self.meta.axes)} rows={self.meta.row_count}"
            )
        mode_part = f" mode={self.attribution_mode}" if self.attribution_mode is not None else ""
        return (
            f"AttributionFrame ref={self.meta.ref} "
            f"attribution_kind={self.meta.attribution_kind} "
            f"method={self.attribution_shape}{mode_part} rows={self.meta.row_count}"
        )

    def _base_card(self) -> Card:
        card = super()._base_card()
        if self.meta.semantic_kind != "funnel_loss_rate" and self.meta.method_evidence is not None:
            evidence = self.meta.method_evidence
            if evidence.kind == "distinct_membership":
                card.field(
                    "method_evidence",
                    (
                        f"allocation={evidence.allocation} "
                        f"identities_persisted={str(evidence.identities_persisted).lower()}"
                    ),
                )
            elif isinstance(evidence, QuantileReplacementEvidenceV1):
                card.field(
                    "method_evidence",
                    (
                        f"q={evidence.q:.12g} source={evidence.source_mode}/"
                        f"{evidence.source_method} coalition={evidence.coalition}"
                    ),
                )
            else:
                card.field(
                    "method_evidence",
                    (
                        f"route={evidence.route} anchor={evidence.anchor.kind} "
                        f"partitions={len(evidence.partitions)}"
                    ),
                )
            multiresolution = _multiresolution_evidence(evidence)
            if multiresolution is not None:
                scope = multiresolution.scope
                selected = (
                    ""
                    if scope.kind == "complete"
                    else " selected=" + ",".join(ref.path for ref in scope.axis_refs)
                )
                card.field(
                    "multiresolution",
                    (
                        "rollup_safe=false "
                        f"resolutions={len(multiresolution.resolution_reconciliations)}"
                        f"{selected}"
                    ),
                )
                card.field(
                    "row_arithmetic",
                    (
                        "complete rows are not additive across resolutions; select one "
                        "exact prefix with at_resolution(axes=[...])"
                        if scope.kind == "complete"
                        else "selected rows may be summed once per comparison bucket"
                    ),
                )
        reconciliation = self.meta.reconciliation
        if reconciliation is None:
            return card
        if self.meta.semantic_kind == "funnel_loss_rate":
            assert isinstance(reconciliation, FunnelAttributionReconciliation)
            return card.field(
                "reconciliation",
                (
                    f"status={reconciliation.status} "
                    f"target_delta={reconciliation.target_loss_rate_delta!r} "
                    f"deepest_positive_pool={reconciliation.positive_pool:.12g} "
                    f"deepest_negative_pool={reconciliation.negative_pool:.12g} "
                    f"residual={reconciliation.residual:.12g}"
                ),
            )
        assert isinstance(reconciliation, AttributionReconciliation)
        values = [
            f"status={reconciliation.status}",
            f"partitions={reconciliation.partition_count}",
            f"max_abs_residual={reconciliation.max_abs_residual:.12g}",
        ]
        for name in (
            "total_delta",
            "contribution_sum",
            "one_sided_contribution_sum",
            "unattributed_contribution_sum",
            "residual",
        ):
            value = getattr(reconciliation, name)
            if value is not None:
                values.append(f"{name}={value:.12g}")
        card.field("reconciliation", " ".join(values))
        return card

    @property
    def attribution_shape(self) -> AttributionShape:
        """Return the canonical mathematical allocation shape."""
        return cast("AttributionShape", self.meta.method)

    @property
    def attribution_mode(self) -> AttributionMode | None:
        """The multi-axis row layout, distinct from attribution math ``method``."""
        if self.meta.semantic_kind == "funnel_loss_rate":
            return self.meta.mode
        return self.meta.attribution_mode

    def as_sum(self) -> AttributionFrame:
        assert_attribution_shape(
            got=self.attribution_shape, expected="sum", frame_kind=self.meta.kind
        )
        return self

    def as_ratio_mix(self) -> AttributionFrame:
        assert_attribution_shape(
            got=self.meta.method, expected="ratio_mix", frame_kind=self.meta.kind
        )
        return self

    def as_weighted_mix(self) -> AttributionFrame:
        assert_attribution_shape(
            got=self.meta.method, expected="weighted_mix", frame_kind=self.meta.kind
        )
        return self

    def at_resolution(
        self,
        *,
        axes: list[_SemanticInput[DimensionKind | TimeDimensionKind]],
    ) -> AttributionFrame:
        """Select one exact ordered semantic-ref prefix without executing a query."""
        if self.meta.semantic_kind == "funnel_loss_rate":
            raise AttributionResolutionError(
                message="at_resolution requires generic multiresolution attribution",
                expected="AttributionFrame attribution_mode='multiresolution'",
                received="funnel_loss_rate",
                location="AttributionFrame.at_resolution",
            )
        evidence = self.meta.method_evidence
        if self.meta.attribution_mode != "multiresolution" or evidence is None:
            raise AttributionResolutionError(
                message="at_resolution requires a complete multiresolution attribution frame",
                expected="attribution_mode='multiresolution'",
                received=repr(self.meta.attribution_mode),
                location="AttributionFrame.at_resolution",
            )
        multiresolution = _multiresolution_evidence(evidence)
        if multiresolution is None or multiresolution.scope.kind != "complete":
            raise AttributionResolutionError(
                message="this attribution frame already has a selected resolution",
                expected="multiresolution.scope.kind='complete'",
                received=(
                    "missing"
                    if multiresolution is None
                    else f"scope.kind={multiresolution.scope.kind!r}"
                ),
                location="AttributionFrame.at_resolution",
            )
        from marivo.analysis.semantic_inputs import normalize_dimension_input
        from marivo.analysis.session._runtime import require_current_session

        session = require_current_session()
        if session.id != self.meta.session_id:
            raise AttributionResolutionError(
                message="at_resolution frame belongs to a different analysis session",
                expected=self.meta.session_id,
                received=session.id,
                location="AttributionFrame.at_resolution",
            )
        requested_paths = tuple(
            normalize_dimension_input(
                session.catalog,
                axis,
                argument="AttributionFrame.at_resolution.axes",
            )
            for axis in axes
        )
        ordered_refs = tuple(binding.ref for binding in self.meta.axis_bindings)
        valid_prefixes = tuple(ordered_refs[:level] for level in range(1, len(ordered_refs) + 1))
        requested_refs = tuple(
            binding.ref
            for path in requested_paths
            for binding in self.meta.axis_bindings
            if binding.ref.path == path
        )
        if requested_refs not in valid_prefixes or len(requested_refs) != len(requested_paths):
            raise AttributionResolutionError(
                message="requested axes are not an exact ordered attribution prefix",
                expected="one of the persisted ordered axis-ref prefixes",
                received=repr(requested_paths),
                location="AttributionFrame.at_resolution.axes",
                context={
                    "valid_prefixes": [[ref.path for ref in prefix] for prefix in valid_prefixes]
                },
            )
        level = len(requested_refs)
        selected_df = self._dataframe_copy()
        if ATTRIBUTION_LEVEL_COLUMN not in selected_df.columns:
            raise AttributionResolutionError(
                message="multiresolution rows are missing the required level coordinate",
                expected="generic-attribution-rows/v2 with level",
                received=repr(list(selected_df.columns)),
                location="AttributionFrame.at_resolution",
            )
        selected_df = selected_df[selected_df[ATTRIBUTION_LEVEL_COLUMN] == level].reset_index(
            drop=True
        )
        selected_reconciliations = tuple(
            item
            for item in multiresolution.resolution_reconciliations
            if item.axis_refs == requested_refs
        )
        common = AttributionReconciliation(
            partition_count=sum(item.partition_count for item in selected_reconciliations),
            total_delta=sum(item.total_delta for item in selected_reconciliations),
            contribution_sum=sum(item.contribution_sum for item in selected_reconciliations),
            residual=sum(item.residual for item in selected_reconciliations),
            max_abs_residual=max(
                (item.max_abs_residual for item in selected_reconciliations),
                default=0.0,
            ),
        )
        selected_multiresolution = multiresolution.model_copy(
            update={
                "scope": SelectedMultiresolutionScopeV1(axis_refs=requested_refs),
                "resolution_reconciliations": selected_reconciliations,
            }
        )
        evidence_update: dict[str, object] = {"multiresolution": selected_multiresolution}
        if evidence.kind == "quantile_replacement":
            evidence_update["scope_reconciliations"] = selected_reconciliations
            evidence_update.update(
                summarize_quantile_resolution_executions(selected_reconciliations)
            )
        selected_evidence = evidence.model_copy(update=evidence_update)
        selected_digest = self.meta.evidence_digest
        if selected_digest is not None:
            from marivo.analysis.evidence.identity import make_digest_fingerprint

            selected_items = tuple(
                item
                for item in selected_digest.items
                if getattr(item, "resolution_axis_refs", ()) == requested_refs
            )
            removed = len(selected_digest.items) - len(selected_items)
            omissions = selected_digest.omissions.model_copy(
                update={
                    "retained_items": len(selected_items),
                    "omitted_items": selected_digest.omissions.omitted_items + removed,
                    "omitted_kinds": tuple(
                        dict.fromkeys(
                            (
                                *selected_digest.omissions.omitted_kinds,
                                *(("contribution",) if removed else ()),
                            )
                        )
                    ),
                    "bounded": bool(selected_digest.omissions.omitted_items + removed),
                }
            )
            selected_digest = selected_digest.model_copy(
                update={"items": selected_items, "omissions": omissions, "fingerprint": ""}
            )
            selected_digest = selected_digest.model_copy(
                update={"fingerprint": make_digest_fingerprint(selected_digest)}
            )
        selected_meta = self.meta.model_copy(
            update={
                "ref": f"{self.ref}:resolution:{level}",
                "artifact_id": None,
                "produced_by_job": None,
                "row_count": len(selected_df),
                "byte_size": int(selected_df.memory_usage(deep=True).sum()),
                "content_hash": None,
                "source_attribution_ref": self.meta.artifact_id or self.ref,
                "method_evidence": selected_evidence,
                "reconciliation": common,
                "evidence_digest": selected_digest,
            }
        )
        return AttributionFrame(_df=selected_df.copy(), meta=selected_meta)

    def contract(self) -> ArtifactContract:
        """Return the mechanical contract, marking selected views non-canonical."""
        contract = super().contract()
        multiresolution = (
            None
            if self.meta.semantic_kind == "funnel_loss_rate"
            else _multiresolution_evidence(self.meta.method_evidence)
        )
        if (
            self.meta.semantic_kind != "funnel_loss_rate"
            and multiresolution is not None
            and multiresolution.scope.kind == "complete"
        ):
            from marivo.analysis.frames.base import ArtifactAffordance, ArtifactCallOption

            prefixes = tuple(
                tuple(binding.ref.path for binding in self.meta.axis_bindings[:level])
                for level in range(1, len(self.meta.axis_bindings) + 1)
            )
            options = tuple(
                ArtifactCallOption(
                    label=" > ".join(prefix),
                    semantic_refs=prefix,
                    snippet=(
                        "frame.at_resolution(axes=["
                        + ", ".join(f'session.catalog.dimensions.get("{path}")' for path in prefix)
                        + "])"
                    ),
                )
                for prefix in prefixes
            )
            contract = contract.model_copy(
                update={
                    "row_arithmetic": "not_additive_across_resolutions",
                    "affordances": (
                        *contract.affordances,
                        ArtifactAffordance(
                            capability_id="AttributionFrame.at_resolution",
                            public_entrypoint="frame.at_resolution(axes=[...])",
                            help_target="AttributionFrame.at_resolution",
                            expected_output_family="AttributionFrame",
                            call_options=options,
                        ),
                    ),
                }
            )
        if (
            self.meta.semantic_kind != "funnel_loss_rate"
            and multiresolution is not None
            and multiresolution.scope.kind == "selected"
        ):
            return contract.model_copy(
                update={
                    "is_canonical": False,
                    "row_arithmetic": "additive_once_per_comparison_bucket",
                }
            )
        return contract
