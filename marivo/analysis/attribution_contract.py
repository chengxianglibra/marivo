"""Typed admission and persisted source bases for generic attribution."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis.errors import AnalysisRepair
from marivo.datasource.engines.base import EngineProfile
from marivo.refs import RefPayloadV1
from marivo.semantic.metric_graph import AggregateNodeV1, MetricExpressionGraphV1
from marivo.semantic.metric_graph_canonical import fingerprint, node_fingerprint

AttributionShape: TypeAlias = Literal[
    "sum",
    "ratio_mix",
    "weighted_mix",
    "distinct_membership",
    "quantile_replacement",
]
AttributionMode: TypeAlias = Literal["joint", "hierarchy", "multiresolution"]


class AggregateAttributionAuthorityV1(BaseModel):
    """Canonical authority copied from one arity-one aggregate graph root."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )

    schema_: Literal["aggregate-attribution-authority/v1"] = Field(
        default="aggregate-attribution-authority/v1", alias="schema"
    )
    aggregate_node_id: str
    expression_graph_fingerprint: str
    aggregate_node: AggregateNodeV1
    aggregate_node_fingerprint: str

    @model_validator(mode="after")
    def _validate_node_fingerprint(self) -> AggregateAttributionAuthorityV1:
        expected = node_fingerprint(self.aggregate_node)
        if self.aggregate_node_fingerprint != expected:
            raise ValueError("aggregate attribution authority node fingerprint mismatch")
        if self.aggregate_node_id != expected:
            raise ValueError("aggregate attribution authority node id mismatch")
        return self

    def validate_graph(self, graph: MetricExpressionGraphV1) -> None:
        """Reject a source graph that differs from the persisted authority."""
        if fingerprint(graph) != self.expression_graph_fingerprint:
            raise ValueError("attribution source graph fingerprint mismatch")
        nodes = {record.node_id: record.node for record in graph.nodes}
        node = nodes.get(self.aggregate_node_id)
        if node != self.aggregate_node or node_fingerprint(node) != self.aggregate_node_fingerprint:
            raise ValueError("attribution source aggregate authority mismatch")


def aggregate_attribution_authority(
    graph: MetricExpressionGraphV1,
) -> AggregateAttributionAuthorityV1 | None:
    """Build authority only for an arity-one graph rooted at one aggregate."""
    if len(graph.roots) != 1:
        return None
    root_id = graph.roots[0]
    node = next((record.node for record in graph.nodes if record.node_id == root_id), None)
    if not isinstance(node, AggregateNodeV1):
        return None
    return AggregateAttributionAuthorityV1(
        aggregate_node_id=root_id,
        expression_graph_fingerprint=fingerprint(graph),
        aggregate_node=node,
        aggregate_node_fingerprint=node_fingerprint(node),
    )


class ReproducibleDistinctAttributionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["reproducible"] = "reproducible"
    source_method: Literal["exact_distinct_membership"] = "exact_distinct_membership"


class BlockedDistinctAttributionReproductionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["blocked"] = "blocked"
    source_dtype: str
    blocker: Literal["unsupported_key_type"] = "unsupported_key_type"


DistinctAttributionReproductionV1: TypeAlias = Annotated[
    ReproducibleDistinctAttributionV1 | BlockedDistinctAttributionReproductionV1,
    Field(discriminator="status"),
]


class DistinctAttributionBasisV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )
    schema_: Literal["distinct-attribution-basis/v1"] = Field(
        default="distinct-attribution-basis/v1", alias="schema"
    )
    kind: Literal["count_distinct"] = "count_distinct"
    authority: AggregateAttributionAuthorityV1
    null_policy: Literal["exclude"] = "exclude"
    reproduction: DistinctAttributionReproductionV1

    @model_validator(mode="after")
    def _validate_aggregate(self) -> DistinctAttributionBasisV1:
        if self.authority.aggregate_node.agg != "count_distinct":
            raise ValueError("distinct attribution basis requires count_distinct authority")
        return self


class ReproducibleQuantileAttributionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["reproducible"] = "reproducible"
    source_mode: Literal["exact", "approximate"]
    source_method: str
    source_dtype: str
    distribution_representation: Literal["exact_value_frequency", "mergeable_sketch"]


class BlockedQuantileAttributionReproductionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["blocked"] = "blocked"
    source_mode: Literal["exact", "approximate", "unknown"]
    source_method: str | None
    blocker: Literal[
        "point_estimate_only",
        "non_mergeable_sample",
        "missing_method_metadata",
        "matching_evaluator_unavailable",
    ]


QuantileAttributionReproductionV1: TypeAlias = Annotated[
    ReproducibleQuantileAttributionV1 | BlockedQuantileAttributionReproductionV1,
    Field(discriminator="status"),
]


class QuantileAttributionBasisV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )
    schema_: Literal["quantile-attribution-basis/v1"] = Field(
        default="quantile-attribution-basis/v1", alias="schema"
    )
    kind: Literal["quantile"] = "quantile"
    authority: AggregateAttributionAuthorityV1
    null_policy: Literal["exclude"] = "exclude"
    reproduction: QuantileAttributionReproductionV1

    @model_validator(mode="after")
    def _validate_aggregate(self) -> QuantileAttributionBasisV1:
        agg = self.authority.aggregate_node.agg
        if agg != "median" and not (
            isinstance(agg, tuple) and len(agg) == 2 and agg[0] == "percentile"
        ):
            raise ValueError("quantile attribution basis requires median or percentile authority")
        return self

    @property
    def effective_q(self) -> float:
        agg = self.authority.aggregate_node.agg
        return 0.5 if agg == "median" else float(agg[1])


AttributionBasisV1: TypeAlias = Annotated[
    DistinctAttributionBasisV1 | QuantileAttributionBasisV1,
    Field(discriminator="kind"),
]


ATTRIBUTE_METHOD_REGISTRY_VERSION = "attribute-method-registry/v1"
INSTALLED_ATTRIBUTE_METHODS = frozenset(
    {
        "distinct_membership/v1",
        "quantile_exact_value_frequency/v1",
        "quantile_trino_qdigest/v1",
    }
)


def required_attribute_method(basis: AttributionBasisV1) -> str:
    """Return the deterministic runtime method required by one source basis."""
    if basis.kind == "count_distinct":
        return "distinct_membership/v1"
    reproduction = basis.reproduction
    if (
        reproduction.status == "reproducible"
        and reproduction.distribution_representation == "mergeable_sketch"
    ):
        return "quantile_trino_qdigest/v1"
    return "quantile_exact_value_frequency/v1"


def attribute_method_is_installed(basis: AttributionBasisV1) -> bool:
    """Project versioned code-state installation without catalog or datasource reads."""
    return required_attribute_method(basis) in INSTALLED_ATTRIBUTE_METHODS


class AttributeModeAdmissionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    single_axis: Literal["omit"] = "omit"
    multiple_axes: tuple[AttributionMode, ...] = Field(min_length=1)


class SupportedAttributeAdmissionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["supported"] = "supported"
    attribution_shape: AttributionShape
    mode: AttributeModeAdmissionV1


class BlockedAttributeAdmissionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["blocked"] = "blocked"
    attribution_shape: AttributionShape | Literal["unavailable"]
    blocker: Literal[
        "unsupported_key_type",
        "point_estimate_only",
        "non_mergeable_sample",
        "missing_method_metadata",
        "matching_evaluator_unavailable",
        "operator_method_not_installed",
        "legacy_missing_basis",
        "cumulative_delta",
        "base_non_additive",
        "bridge_grain_mismatch",
        "component_time_bridge_unsupported",
        "over_plus_business_axis_unsupported",
        "missing_additivity_metadata",
        "unsupported_aggregate",
        "semantic_grain_decomposition_unsupported",
    ]
    repair: AnalysisRepair


AttributeAdmissionV1: TypeAlias = Annotated[
    SupportedAttributeAdmissionV1 | BlockedAttributeAdmissionV1,
    Field(discriminator="status"),
]


class SupportedCumulativeAttributionRouteV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["supported"] = "supported"
    path: Literal["cumulative_level_decomposition", "accumulation_time_bridge"]


class BlockedCumulativeAttributionRouteV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["blocked"] = "blocked"
    blocker: Literal[
        "base_non_additive",
        "bridge_grain_mismatch",
        "component_time_bridge_unsupported",
        "over_plus_business_axis_unsupported",
    ]
    repair: AnalysisRepair


CumulativeAttributionRouteAdmissionV1: TypeAlias = Annotated[
    SupportedCumulativeAttributionRouteV1 | BlockedCumulativeAttributionRouteV1,
    Field(discriminator="status"),
]


class CumulativeAttributionCapabilityV1(BaseModel):
    """Query-free route map derived from one compact cumulative contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    business_axes: CumulativeAttributionRouteAdmissionV1
    accumulation_time: CumulativeAttributionRouteAdmissionV1
    mixed_axes: BlockedCumulativeAttributionRouteV1


class AttributionAxisBindingV1(BaseModel):
    """One ordered semantic attribution axis and its persisted output column."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: RefPayloadV1
    output_column: str


def basis_fingerprint(basis: AttributionBasisV1 | None) -> str | None:
    """Return the canonical fingerprint of a persisted attribution basis."""
    return fingerprint(basis.model_dump(mode="json")) if basis is not None else None


def build_attribution_basis(
    graph: MetricExpressionGraphV1,
    *,
    source_dtype: str,
    engine_profile: EngineProfile,
) -> AttributionBasisV1 | None:
    """Project reproducible non-additive evidence from graph and engine facts."""
    authority = aggregate_attribution_authority(graph)
    if authority is None:
        return None
    agg = authority.aggregate_node.agg
    if agg == "count_distinct":
        normalized_dtype = source_dtype.lower().replace(" ", "")
        unsupported = normalized_dtype.startswith(("array", "map", "struct", "unknown"))
        reproduction: DistinctAttributionReproductionV1
        if unsupported:
            reproduction = BlockedDistinctAttributionReproductionV1(source_dtype=source_dtype)
        else:
            reproduction = ReproducibleDistinctAttributionV1()
        return DistinctAttributionBasisV1(
            authority=authority,
            reproduction=reproduction,
        )
    if agg != "median" and not (
        isinstance(agg, tuple) and len(agg) == 2 and agg[0] == "percentile"
    ):
        return None
    capability = engine_profile.quantile
    if capability is None:
        quantile_reproduction: QuantileAttributionReproductionV1 = (
            BlockedQuantileAttributionReproductionV1(
                source_mode="unknown",
                source_method=None,
                blocker="missing_method_metadata",
            )
        )
    elif capability.mode == "exact" and capability.method == "linear_interpolation":
        quantile_reproduction = ReproducibleQuantileAttributionV1(
            source_mode="exact",
            source_method=capability.method,
            source_dtype=source_dtype,
            distribution_representation="exact_value_frequency",
        )
    elif capability.mode == "approximate" and capability.method == "qdigest":
        normalized_dtype = source_dtype.lower().replace(" ", "")
        if normalized_dtype.startswith(("int", "float")):
            quantile_reproduction = ReproducibleQuantileAttributionV1(
                source_mode="approximate",
                source_method=capability.method,
                source_dtype=source_dtype,
                distribution_representation="mergeable_sketch",
            )
        else:
            quantile_reproduction = BlockedQuantileAttributionReproductionV1(
                source_mode="approximate",
                source_method=capability.method,
                blocker="matching_evaluator_unavailable",
            )
    elif capability.mode == "approximate" and capability.method == "reservoir_sampling":
        quantile_reproduction = BlockedQuantileAttributionReproductionV1(
            source_mode="approximate",
            source_method=capability.method,
            blocker="non_mergeable_sample",
        )
    else:
        quantile_reproduction = BlockedQuantileAttributionReproductionV1(
            source_mode=capability.mode,
            source_method=capability.method,
            blocker="matching_evaluator_unavailable",
        )
    return QuantileAttributionBasisV1(
        authority=authority,
        reproduction=quantile_reproduction,
    )


__all__ = [
    "AggregateAttributionAuthorityV1",
    "AttributeAdmissionV1",
    "AttributeModeAdmissionV1",
    "AttributionAxisBindingV1",
    "AttributionBasisV1",
    "AttributionMode",
    "AttributionShape",
    "BlockedAttributeAdmissionV1",
    "BlockedCumulativeAttributionRouteV1",
    "BlockedDistinctAttributionReproductionV1",
    "BlockedQuantileAttributionReproductionV1",
    "CumulativeAttributionCapabilityV1",
    "CumulativeAttributionRouteAdmissionV1",
    "DistinctAttributionBasisV1",
    "QuantileAttributionBasisV1",
    "ReproducibleDistinctAttributionV1",
    "ReproducibleQuantileAttributionV1",
    "SupportedAttributeAdmissionV1",
    "SupportedCumulativeAttributionRouteV1",
    "aggregate_attribution_authority",
    "basis_fingerprint",
    "build_attribution_basis",
]
