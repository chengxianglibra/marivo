"""Typed attribution analysis frames."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis.attribution_contract import (
    AttributionAxisBindingV1,
    AttributionMode,
    AttributionShape,
)
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


type JsonScalar = str | int | float | bool | None


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


type MultiresolutionScopeV1 = Annotated[
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


type AttributionMethodEvidenceV1 = Annotated[
    DistinctMembershipEvidenceV1 | QuantileReplacementEvidenceV1,
    Field(discriminator="kind"),
]


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
    row_contract_version: Literal["generic-attribution-rows/v2"] | None = None
    causal_claim: Literal["none"] = "none"
    axis_bindings: tuple[AttributionAxisBindingV1, ...] = ()
    attribution_mode: AttributionMode | None = None
    bucket_column: str | None = None
    method_evidence: AttributionMethodEvidenceV1 | None = None
    source_attribution_ref: str | None = None

    @model_validator(mode="after")
    def _validate_generic_v2(self) -> AttributionFrameMeta:
        if self.row_contract_version is None:
            return self
        if self.method in {"distinct_membership", "quantile_replacement"} and (
            self.method_evidence is None
        ):
            raise ValueError("non-additive attribution rows v2 require typed method evidence")
        if self.method_evidence is not None and self.method_evidence.kind != self.method:
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
            self.method_evidence.multiresolution if self.method_evidence is not None else None
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


def _validate_row_ranks(meta: AttributionFrameMeta, dataframe: Any) -> None:
    group_columns = [] if meta.bucket_column is None else [meta.bucket_column]
    if meta.attribution_mode in {"hierarchy", "multiresolution"}:
        group_columns.append("level")
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
        level = int(row["level"]) if has_levels else len(axis_refs)
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
        rows = dataframe[dataframe["level"] == level] if has_levels else dataframe
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
    prefix_columns = {"level", "axis", "driver", "path"}
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
        assert evidence is not None and evidence.multiresolution is not None
        valid_levels = set(range(1, len(axis_columns) + 1))
        observed_levels = {int(value) for value in dataframe["level"].dropna().unique()}
        scope = evidence.multiresolution.scope
        expected_levels = valid_levels if scope.kind == "complete" else {len(scope.axis_refs)}
        if observed_levels != expected_levels:
            raise ValueError("multiresolution row levels do not match typed scope")
        for _, row in dataframe.iterrows():
            level = int(row["level"])
            if row["axis"] != axis_columns[level - 1]:
                raise ValueError("multiresolution row axis does not match its ordered prefix")
            for column in axis_columns[level:]:
                if not pd.isna(row[column]):
                    raise ValueError("multiresolution row has a value beyond its prefix")
        _validate_scope_reconciliations(
            meta,
            dataframe,
            evidence.multiresolution.resolution_reconciliations,
        )
    elif meta.method_evidence is not None and meta.method_evidence.kind == "quantile_replacement":
        _validate_scope_reconciliations(
            meta,
            dataframe,
            meta.method_evidence.scope_reconciliations,
        )
    reconciliation = meta.reconciliation
    assert reconciliation is not None
    reconciled_rows = dataframe
    if meta.attribution_mode in {"hierarchy", "multiresolution"}:
        reconciled_level = int(dataframe["level"].max())
        reconciled_rows = dataframe[dataframe["level"] == reconciled_level]
    contribution_sum = float(reconciled_rows["contribution"].sum())
    if reconciliation.contribution_sum is None or not math.isclose(
        contribution_sum,
        reconciliation.contribution_sum,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("generic attribution rows do not match typed contribution reconciliation")
    if (
        reconciliation.total_delta is not None
        and reconciliation.residual is not None
        and not math.isclose(
            reconciliation.total_delta - reconciliation.contribution_sum,
            reconciliation.residual,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        raise ValueError("generic attribution reconciliation arithmetic is inconsistent")
    if reconciliation.max_abs_residual > 1e-9:
        raise ValueError("generic attribution reconciliation exceeds tolerance")


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
        if (
            self.meta.semantic_kind != "funnel_loss_rate"
            and self.meta.method == "ordered_hierarchy_sum"
        ):
            card.field("legacy_method", "ordered_hierarchy_sum")
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
            else:
                card.field(
                    "method_evidence",
                    (
                        f"q={evidence.q:.12g} source={evidence.source_mode}/"
                        f"{evidence.source_method} coalition={evidence.coalition}"
                    ),
                )
            if evidence.multiresolution is not None:
                scope = evidence.multiresolution.scope
                selected = (
                    ""
                    if scope.kind == "complete"
                    else " selected=" + ",".join(ref.path for ref in scope.axis_refs)
                )
                card.field(
                    "multiresolution",
                    (
                        "rollup_safe=false "
                        f"resolutions={len(evidence.multiresolution.resolution_reconciliations)}"
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
        shape = "sum" if self.meta.method == "ordered_hierarchy_sum" else self.meta.method
        return cast("AttributionShape", shape)

    @property
    def attribution_mode(self) -> AttributionMode | None:
        """The multi-axis row layout, distinct from attribution math ``method``."""
        if self.meta.semantic_kind == "funnel_loss_rate":
            return self.meta.mode
        if self.meta.row_contract_version == "generic-attribution-rows/v2":
            return self.meta.attribution_mode
        mode = self.meta.params.get("mode")
        return mode if mode in {"joint", "hierarchy", "multiresolution"} else None

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
        multiresolution = evidence.multiresolution
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
        if "level" not in selected_df.columns:
            raise AttributionResolutionError(
                message="multiresolution rows are missing the required level coordinate",
                expected="generic-attribution-rows/v2 with level",
                received=repr(list(selected_df.columns)),
                location="AttributionFrame.at_resolution",
            )
        selected_df = selected_df[selected_df["level"] == level].reset_index(drop=True)
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
        if (
            self.meta.semantic_kind != "funnel_loss_rate"
            and self.meta.method_evidence is not None
            and self.meta.method_evidence.multiresolution is not None
            and self.meta.method_evidence.multiresolution.scope.kind == "complete"
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
            and self.meta.method_evidence is not None
            and self.meta.method_evidence.multiresolution is not None
            and self.meta.method_evidence.multiresolution.scope.kind == "selected"
        ):
            return contract.model_copy(
                update={
                    "is_canonical": False,
                    "row_arithmetic": "additive_once_per_comparison_bucket",
                }
            )
        return contract
