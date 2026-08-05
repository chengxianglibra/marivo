"""Closed contracts and admission for cumulative metric attribution.

This module owns the compact, persisted projection used by cumulative deltas.
It deliberately does not own source graphs, replay payloads, comparison
identity, or authored-anchor provenance.  Those remain with observe/compare.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis._semantic_persistence import AxisBindingV1
from marivo.analysis.attribution_contract import (
    BlockedCumulativeAttributionRouteV1,
    CumulativeAttributionCapabilityV1,
    CumulativeAttributionRouteAdmissionV1,
    SupportedCumulativeAttributionRouteV1,
)
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.windows.grain import Grain, normalize_grain
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.refs import ref as ref_factory
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeNodeV1,
    LinearNodeV1,
    MetricExpressionGraphV1,
    MetricGraphNodeV1,
    RatioNodeV1,
    SliceNodeV1,
    WeightedMeanAggregateNodeV1,
)
from marivo.semantic.metric_graph_canonical import fingerprint, node_fingerprint, validate_graph

type CumulativeBaseAggregation = Literal["sum", "count", "count_distinct"]
type CumulativeAttributionMethod = Literal["sum", "ratio_mix", "weighted_mix"]
type CumulativeAttributionRoute = Literal["business_axes", "accumulation_time", "mixed_axes"]


class CumulativeBridgeGrainV1(BaseModel):
    """One exact bridge grain and report-timezone interpretation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    grain: Grain
    report_timezone: str = Field(min_length=1)
    origin: Literal[
        "observation_query_grain",
        "over_declared_granularity",
        "executor_day_default",
    ]


class AvailableCumulativeBridgeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["available"] = "available"
    value: CumulativeBridgeGrainV1


class BlockedCumulativeBridgeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["blocked"] = "blocked"
    blocker: Literal["bridge_grain_mismatch"] = "bridge_grain_mismatch"
    current_grain: Grain
    baseline_grain: Grain
    current_report_timezone: str = Field(min_length=1)
    baseline_report_timezone: str = Field(min_length=1)


type CumulativeBridgeV1 = Annotated[
    AvailableCumulativeBridgeV1 | BlockedCumulativeBridgeV1,
    Field(discriminator="status"),
]


class CumulativeBaseComponentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_expression_fingerprint: str = Field(min_length=1)
    aggregation: CumulativeBaseAggregation


class DirectCumulativeAttributionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["direct"] = "direct"
    base: CumulativeBaseComponentV1


class RatioCumulativeAttributionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["ratio"] = "ratio"
    numerator: CumulativeBaseComponentV1
    denominator: CumulativeBaseComponentV1


class WeightedCumulativeAttributionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["weighted_mean"] = "weighted_mean"
    numerator: CumulativeBaseComponentV1
    weight: CumulativeBaseComponentV1


class LinearCumulativeTermV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    coefficient: float
    component: CumulativeBaseComponentV1


class LinearCumulativeAttributionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["linear"] = "linear"
    terms: tuple[LinearCumulativeTermV1, ...] = Field(min_length=1)


type CumulativeAttributionStructureV1 = Annotated[
    DirectCumulativeAttributionV1
    | RatioCumulativeAttributionV1
    | WeightedCumulativeAttributionV1
    | LinearCumulativeAttributionV1,
    Field(discriminator="kind"),
]


class CumulativeAttributionContractV1(BaseModel):
    """Compact authority persisted by one current cumulative delta."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )

    schema_: Literal["cumulative-attribution/v1"] = Field(
        default="cumulative-attribution/v1", alias="schema"
    )
    over_ref: RefPayloadV1
    bridge: CumulativeBridgeV1
    structure: CumulativeAttributionStructureV1

    @model_validator(mode="after")
    def _validate_over_ref(self) -> CumulativeAttributionContractV1:
        if self.over_ref.kind is not SemanticKind.TIME_DIMENSION:
            raise ValueError("cumulative attribution over_ref must be a time dimension")
        return self


def _repair(action: str) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
    )


def _blocked(
    blocker: Literal[
        "base_non_additive",
        "bridge_grain_mismatch",
        "component_time_bridge_unsupported",
        "over_plus_business_axis_unsupported",
    ],
) -> BlockedCumulativeAttributionRouteV1:
    actions = {
        "base_non_additive": (
            "Inspect the cumulative base aggregation; cumulative count-distinct attribution "
            "is not installed."
        ),
        "bridge_grain_mismatch": (
            "Re-observe and compare both cumulative inputs with one query grain and report "
            "timezone."
        ),
        "component_time_bridge_unsupported": (
            "Attribute this derived cumulative delta over business dimensions; component "
            "time bridges are not installed."
        ),
        "over_plus_business_axis_unsupported": (
            "Request either business axes or exactly the cumulative over time axis."
        ),
    }
    return BlockedCumulativeAttributionRouteV1(
        blocker=blocker,
        repair=_repair(actions[blocker]),
    )


def _base_components(
    structure: CumulativeAttributionStructureV1,
) -> tuple[CumulativeBaseComponentV1, ...]:
    if isinstance(structure, DirectCumulativeAttributionV1):
        return (structure.base,)
    if isinstance(structure, RatioCumulativeAttributionV1):
        return (structure.numerator, structure.denominator)
    if isinstance(structure, WeightedCumulativeAttributionV1):
        return (structure.numerator, structure.weight)
    return tuple(term.component for term in structure.terms)


def cumulative_attribution_method(
    structure: CumulativeAttributionStructureV1,
) -> CumulativeAttributionMethod:
    if isinstance(structure, RatioCumulativeAttributionV1):
        return "ratio_mix"
    if isinstance(structure, WeightedCumulativeAttributionV1):
        return "weighted_mix"
    return "sum"


def cumulative_attribution_capability(
    contract: CumulativeAttributionContractV1,
) -> CumulativeAttributionCapabilityV1:
    """Derive the complete route map without catalog or datasource reads."""

    components = _base_components(contract.structure)
    base_blocked = any(item.aggregation == "count_distinct" for item in components)
    business: CumulativeAttributionRouteAdmissionV1
    accumulation: CumulativeAttributionRouteAdmissionV1
    if base_blocked:
        business = _blocked("base_non_additive")
        accumulation = _blocked("base_non_additive")
    else:
        business = SupportedCumulativeAttributionRouteV1(path="cumulative_level_decomposition")
        if not isinstance(contract.structure, DirectCumulativeAttributionV1):
            accumulation = _blocked("component_time_bridge_unsupported")
        elif isinstance(contract.bridge, BlockedCumulativeBridgeV1):
            accumulation = _blocked("bridge_grain_mismatch")
        else:
            accumulation = SupportedCumulativeAttributionRouteV1(path="accumulation_time_bridge")
    return CumulativeAttributionCapabilityV1(
        business_axes=business,
        accumulation_time=accumulation,
        mixed_axes=_blocked("over_plus_business_axis_unsupported"),
    )


def classify_cumulative_attribution_route(
    contract: CumulativeAttributionContractV1,
    axis_refs: tuple[RefPayloadV1, ...],
) -> CumulativeAttributionRoute:
    """Classify exact semantic axes before selecting the derived admission."""

    contains_over = any(item == contract.over_ref for item in axis_refs)
    if not contains_over:
        return "business_axes"
    if len(axis_refs) == 1:
        return "accumulation_time"
    return "mixed_axes"


def select_cumulative_attribution_route(
    contract: CumulativeAttributionContractV1,
    axis_refs: tuple[RefPayloadV1, ...],
) -> tuple[CumulativeAttributionRoute, CumulativeAttributionRouteAdmissionV1]:
    route = classify_cumulative_attribution_route(contract, axis_refs)
    capability = cumulative_attribution_capability(contract)
    return route, cast("CumulativeAttributionRouteAdmissionV1", getattr(capability, route))


def _bridge_grain_for_source(
    *,
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    axis_bindings: tuple[AxisBindingV1, ...],
    over_ref: RefPayloadV1,
    declared_over_grain: str | None,
    report_timezone: str,
) -> CumulativeBridgeGrainV1:
    if semantic_kind in {"time_series", "panel"}:
        binding = next((item for item in axis_bindings if item.ref == over_ref), None)
        if binding is None or binding.grain is None:
            raise ValueError(
                "time-series and panel cumulative bridges require the observed over-axis grain"
            )
        grain = normalize_grain(binding.grain)
        if grain is None:
            raise ValueError("cumulative observation query grain is missing")
        return CumulativeBridgeGrainV1(
            grain=grain,
            report_timezone=report_timezone,
            origin="observation_query_grain",
        )
    if declared_over_grain is not None:
        grain = normalize_grain(declared_over_grain)
        if grain is None:
            raise ValueError("cumulative over-axis granularity is missing")
        return CumulativeBridgeGrainV1(
            grain=grain,
            report_timezone=report_timezone,
            origin="over_declared_granularity",
        )
    return CumulativeBridgeGrainV1(
        grain=Grain(unit="day"),
        report_timezone=report_timezone,
        origin="executor_day_default",
    )


def derive_cumulative_bridge(
    *,
    current_semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    baseline_semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    current_axis_bindings: tuple[AxisBindingV1, ...],
    baseline_axis_bindings: tuple[AxisBindingV1, ...],
    over_ref: RefPayloadV1,
    current_declared_over_grain: str | None,
    baseline_declared_over_grain: str | None,
    current_report_timezone: str,
    baseline_report_timezone: str,
) -> CumulativeBridgeV1:
    """Derive one persisted bridge or retain the exact temporal mismatch."""

    current = _bridge_grain_for_source(
        semantic_kind=current_semantic_kind,
        axis_bindings=current_axis_bindings,
        over_ref=over_ref,
        declared_over_grain=current_declared_over_grain,
        report_timezone=current_report_timezone,
    )
    baseline = _bridge_grain_for_source(
        semantic_kind=baseline_semantic_kind,
        axis_bindings=baseline_axis_bindings,
        over_ref=over_ref,
        declared_over_grain=baseline_declared_over_grain,
        report_timezone=baseline_report_timezone,
    )
    if current.grain != baseline.grain or current.report_timezone != baseline.report_timezone:
        return BlockedCumulativeBridgeV1(
            current_grain=current.grain,
            baseline_grain=baseline.grain,
            current_report_timezone=current.report_timezone,
            baseline_report_timezone=baseline.report_timezone,
        )
    origin = (
        current.origin
        if current.origin == baseline.origin
        else "observation_query_grain"
        if "observation_query_grain" in {current.origin, baseline.origin}
        else "over_declared_granularity"
    )
    return AvailableCumulativeBridgeV1(value=current.model_copy(update={"origin": origin}))


def cumulative_over_ref(cumulative: Mapping[str, object]) -> RefPayloadV1:
    """Return the one exact time dimension owned by a cumulative marker."""

    if cumulative.get("kind") == "cumulative":
        over = cumulative.get("over")
        if not isinstance(over, str) or not over:
            raise ValueError("direct cumulative marker requires over")
        return RefPayloadV1.from_ref(ref_factory.time_dimension(over))
    components = cumulative.get("components")
    if not isinstance(components, Mapping) or not components:
        raise ValueError("derived cumulative marker requires components")
    payloads = tuple(components.values())
    if any(not isinstance(payload, Mapping) for payload in payloads):
        raise ValueError("derived cumulative components require mapping payloads")
    refs = {cumulative_over_ref(cast("Mapping[str, object]", payload)) for payload in payloads}
    if len(refs) != 1:
        raise ValueError("derived cumulative components require one exact over ref")
    return next(iter(refs))


def _nodes(graph: MetricExpressionGraphV1) -> dict[str, MetricGraphNodeV1]:
    validate_graph(graph)
    if len(graph.roots) != 1:
        raise ValueError("cumulative attribution requires one expression root")
    return {record.node_id: record.node for record in graph.nodes}


def _graph_over_refs(graph: MetricExpressionGraphV1) -> set[RefPayloadV1]:
    nodes = _nodes(graph)
    return {
        node.time_dimension_ref
        for node in nodes.values()
        if isinstance(node, CumulativeNodeV1) and node.time_dimension_ref is not None
    }


def _validate_marker_structure(
    cumulative: Mapping[str, object],
    structure: CumulativeAttributionStructureV1,
) -> None:
    marker_kind = cumulative.get("kind")
    if marker_kind == "cumulative":
        if not isinstance(
            structure,
            DirectCumulativeAttributionV1 | WeightedCumulativeAttributionV1,
        ):
            raise ValueError("direct cumulative marker does not match graph structure")
        return
    if marker_kind != "derived_contains_cumulative":
        raise ValueError("cumulative attribution marker kind is unsupported")
    components = cumulative.get("components")
    if not isinstance(components, Mapping):
        raise ValueError("derived cumulative marker requires components")
    roles = tuple(components)
    if roles == ("numerator", "denominator"):
        if not isinstance(structure, RatioCumulativeAttributionV1):
            raise ValueError("ratio cumulative marker does not match graph structure")
        return
    if roles and roles == tuple(f"term{index}" for index in range(len(roles))):
        if not isinstance(structure, LinearCumulativeAttributionV1):
            raise ValueError("linear cumulative marker does not match graph structure")
        return
    raise ValueError("derived cumulative marker has unsupported component roles")


def _cumulative_base(
    nodes: Mapping[str, MetricGraphNodeV1], node_id: str
) -> tuple[str, CumulativeNodeV1]:
    """Return the base child and cumulative node through filter-only wrappers."""

    visited: set[str] = set()
    current_id = node_id
    while current_id not in visited:
        visited.add(current_id)
        node = nodes[current_id]
        if isinstance(node, CumulativeNodeV1):
            return node.child_id, node
        if isinstance(node, SliceNodeV1):
            current_id = node.child_id
            continue
        break
    raise ValueError("cumulative attribution branch is not one cumulative component")


def _base_component(
    nodes: Mapping[str, MetricGraphNodeV1], base_id: str
) -> CumulativeBaseComponentV1:
    node = nodes[base_id]
    if isinstance(node, SliceNodeV1):
        nested = _base_component(nodes, node.child_id)
        return nested.model_copy(
            update={
                "canonical_expression_fingerprint": fingerprint(
                    {
                        "schema": "cumulative-base-component/v1",
                        "root": base_id,
                    }
                )
            }
        )
    if not isinstance(node, AggregateNodeV1):
        raise ValueError("cumulative base must be sum, count, or count_distinct")
    if node.agg == "sum":
        aggregation: CumulativeBaseAggregation = "sum"
    elif node.agg == "count":
        aggregation = "count"
    elif node.agg == "count_distinct":
        aggregation = "count_distinct"
    else:
        raise ValueError("cumulative base must be sum, count, or count_distinct")
    return CumulativeBaseComponentV1(
        canonical_expression_fingerprint=fingerprint(
            {"schema": "cumulative-base-component/v1", "root": base_id}
        ),
        aggregation=aggregation,
    )


def _weighted_structure(
    node: WeightedMeanAggregateNodeV1 | AggregateNodeV1,
) -> WeightedCumulativeAttributionV1:
    if isinstance(node, WeightedMeanAggregateNodeV1):
        common = {
            "schema": "cumulative-weighted-component/v1",
            "node_fingerprint": node_fingerprint(node),
        }
        numerator_payload = {**common, "role": "weighted_numerator"}
        weight_payload = {**common, "role": "weight_sum"}
    elif node.agg == "mean":
        common = {
            "schema": "cumulative-mean-component/v1",
            "node_fingerprint": node_fingerprint(node),
        }
        numerator_payload = {**common, "role": "sum"}
        weight_payload = {**common, "role": "count_non_null"}
    else:
        raise ValueError("weighted cumulative structure requires weighted mean or mean")
    return WeightedCumulativeAttributionV1(
        numerator=CumulativeBaseComponentV1(
            canonical_expression_fingerprint=fingerprint(numerator_payload),
            aggregation="sum",
        ),
        weight=CumulativeBaseComponentV1(
            canonical_expression_fingerprint=fingerprint(weight_payload),
            aggregation="count" if isinstance(node, AggregateNodeV1) else "sum",
        ),
    )


def project_cumulative_attribution_structure(
    graph: MetricExpressionGraphV1,
) -> CumulativeAttributionStructureV1:
    """Project a validated graph to the only cumulative arithmetic needed later."""

    nodes = _nodes(graph)
    root = nodes[graph.roots[0]]
    if isinstance(root, RatioNodeV1):
        numerator_id, _ = _cumulative_base(nodes, root.numerator_id)
        denominator_id, _ = _cumulative_base(nodes, root.denominator_id)
        return RatioCumulativeAttributionV1(
            numerator=_base_component(nodes, numerator_id),
            denominator=_base_component(nodes, denominator_id),
        )
    if isinstance(root, LinearNodeV1):
        return LinearCumulativeAttributionV1(
            terms=tuple(
                LinearCumulativeTermV1(
                    coefficient=term.coefficient,
                    component=_base_component(nodes, _cumulative_base(nodes, term.child_id)[0]),
                )
                for term in root.terms
            )
        )
    base_id, _ = _cumulative_base(nodes, graph.roots[0])
    base_node = nodes[base_id]
    if isinstance(base_node, WeightedMeanAggregateNodeV1) or (
        isinstance(base_node, AggregateNodeV1) and base_node.agg == "mean"
    ):
        return _weighted_structure(base_node)
    return DirectCumulativeAttributionV1(base=_base_component(nodes, base_id))


def build_cumulative_attribution_contract(
    *,
    current_graph: MetricExpressionGraphV1,
    baseline_graph: MetricExpressionGraphV1,
    current_cumulative: Mapping[str, object],
    baseline_cumulative: Mapping[str, object],
    bridge: CumulativeBridgeV1,
) -> CumulativeAttributionContractV1:
    """Require both sources to project to one compact cumulative authority."""

    current_over = cumulative_over_ref(current_cumulative)
    baseline_over = cumulative_over_ref(baseline_cumulative)
    if current_over != baseline_over:
        raise ValueError("cumulative attribution sources have different over refs")
    for label, graph in (("current", current_graph), ("baseline", baseline_graph)):
        graph_over_refs = _graph_over_refs(graph)
        if graph_over_refs != {current_over}:
            raise ValueError(
                f"{label} cumulative graph over refs do not match its persisted marker"
            )
    current_structure = project_cumulative_attribution_structure(current_graph)
    baseline_structure = project_cumulative_attribution_structure(baseline_graph)
    _validate_marker_structure(current_cumulative, current_structure)
    _validate_marker_structure(baseline_cumulative, baseline_structure)
    if current_structure != baseline_structure:
        raise ValueError("cumulative attribution source structure projections differ")
    return CumulativeAttributionContractV1(
        over_ref=current_over,
        bridge=bridge,
        structure=current_structure,
    )
