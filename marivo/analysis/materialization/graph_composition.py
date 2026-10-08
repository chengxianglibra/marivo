"""Ordered source endpoint composition with exact frozen node sharing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, replace
from functools import cache
from typing import Literal

import pyarrow as pa

from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
    MethodNode,
    Node,
    SourceLeaf,
    definition_fingerprints,
    method_node,
    topology,
)
from marivo.analysis.core.model import (
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    OriginalStatePart,
)
from marivo.analysis.core.rules import (
    AssociationScore,
    AttachCategory,
    BindProject,
    CellDerive,
    CompleteGroups,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrenceCombine,
    OriginalRatio,
    OriginalReduce,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
    RowState,
    TimeProduct,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.graph_members import MemberGraph
from marivo.analysis.materialization.graph_protocol import digest
from marivo.analysis.materialization.graph_snapshot import same_node_definition
from marivo.analysis.methods.comparison import output_type
from marivo.analysis.methods.native_numeric import linear_type
from marivo.analysis.methods.numeric_state import merge_original
from marivo.analysis.methods.physical import ScalarType, arrow_scalar_type
from marivo.semantic.ir import TargetRelationshipContract


def combine_linear_occurrences(
    occurrences: tuple[MemberGraph, ...],
    quantities: tuple[ObservedQuantity, ...],
    signs: tuple[int, ...],
    quantity: ObservedQuantity,
    *,
    union_targets: bool = False,
) -> MemberGraph:
    """Combine N independently reduced occurrences under ordered signed terms."""
    if len(occurrences) < 2 or not len(occurrences) == len(signs) == len(quantities):
        raise _invalid_composition("two or more occurrences with one sign each")
    current = occurrences[0]
    known = {node.identity: node for node in topology(current.root)}
    for following in occurrences[1:]:
        if following.runtime is not current.runtime or following.registry is not current.registry:
            raise _invalid_composition("different Session or Semantic binding")
        for node in topology(following.root):
            prior = known.get(node.identity)
            if prior is not None:
                if not same_node_definition(prior, node):
                    raise _invalid_composition("one node identity has different frozen definitions")
                continue
            if isinstance(node, MethodNode):
                sources = tuple(known[source.identity] for source in node.sources)
                if any(not isinstance(source, SourceLeaf) for source in sources):
                    raise _invalid_composition("non-source dependency in a source node")
                node = replace(
                    node,
                    inputs=tuple(
                        Edge(edge.role, known[edge.node.identity]) for edge in node.inputs
                    ),
                    sources=tuple(source for source in sources if isinstance(source, SourceLeaf)),
                )
            known[node.identity] = node
    roots = tuple(known[item.root.identity] for item in occurrences)
    methods: list[MethodNode] = []
    for item in roots:
        if not isinstance(item, MethodNode) or item.parameters.__class__ not in (
            ObserveMetric,
            ObserveWeightedMean,
            OriginalReduce,
            RowState,
            TimeProduct,
            ObserveCount,
        ):
            raise _invalid_composition("an occurrence is not an exact original observation")
        methods.append(item)
    first = methods[0]
    binding = first.signature.domain.binding
    for item in methods[1:]:
        if item.signature.domain.binding != binding:
            raise _invalid_composition("occurrences do not share one frozen member binding")
        if item.inputs[0].node.identity != first.inputs[0].node.identity:
            raise _invalid_composition("occurrences do not share the same frozen member node")
    domain = DomainSignature(
        binding,
        first.signature.domain.kind,
        first.signature.domain.instance_key,
        first.signature.domain.target_key,
        quantity.definition_id,
    )
    root = method_node(
        tuple(Edge("quantity", item) for item in methods),
        OccurrenceCombine(
            quantity,
            tuple(zip(quantities, signs, strict=True)),
            domain,
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
            union_targets,
        ),
        value_type=linear_type(tuple(item.value_type for item in methods)),
    )
    schemas = {
        leaf.identity: schema for occurrence in occurrences for schema, leaf in occurrence.sources
    }
    source_nodes = tuple(node for node in topology(root) if isinstance(node, SourceLeaf))
    bindings = tuple((schemas[node.identity], node) for node in source_nodes)
    return replace(current, root=root, sources=bindings)


def _invalid_composition(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="compatible ordered numeric definitions and exact target/correspondence bindings",
        received=received,
        repair="Use shared targets for time comparisons, common Group/Singleton coordinates for cohorts, and complete retained definitions for fixed inputs.",
        location="analysis.graph_composition",
    )


def period_mapping(
    current: MethodNode, baseline: MethodNode
) -> tuple[int, tuple[tuple[str, str], ...]]:
    """Bind the original complete bucket vectors before reading either endpoint."""
    left, right = current.signature.domain, baseline.signature.domain
    first, second = left.time_grid, right.time_grid
    if first is None or second is None or len(first.cells) != len(second.cells):
        raise _invalid_composition("PeriodChange requires complete equal-length frozen grids")
    indexes = tuple(i for i, key in enumerate(left.instance_key) if key.role == "anchor")
    if len(indexes) != 1 or indexes != tuple(
        i for i, key in enumerate(right.instance_key) if key.role == "anchor"
    ):
        raise _invalid_composition(
            "PeriodChange requires one corresponding complete time coordinate"
        )
    return indexes[0], tuple(
        (a.identity, b.identity) for a, b in zip(first.cells, second.cells, strict=True)
    )


def comparison_empty_rules(
    current: MethodNode, baseline: MethodNode
) -> tuple[Literal["null", "zero", "zero_denominator"], ...]:
    """Only a complete raw observation may contribute its Metric's empty finish."""
    result: list[Literal["null", "zero", "zero_denominator"]] = []
    for node in (current, baseline):
        params = node.parameters
        while isinstance(params, (OriginalReduce, AttachCategory)):
            node = comparison_endpoints(node)[0]
            params = node.parameters

        def complete_original(value: MethodNode) -> bool:
            if isinstance(value.parameters, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
                return True
            if isinstance(value.parameters, (OriginalRatio, OccurrenceCombine)):
                return all(complete_original(child) for child in comparison_endpoints(value))
            return False

        if not complete_original(node):
            raise _invalid_composition(
                "metric_empty requires complete raw observations, not filtered or derived rows"
            )
        state = next(
            (part for part in node.signature.parts if isinstance(part, OriginalStatePart)), None
        )
        if state is None:
            raise _invalid_composition("metric_empty requires the concrete original Metric state")
        method = state.method_version.split("@", 1)[0]
        if method not in (
            "sum",
            "sum_zero",
            "count",
            "min",
            "max",
            "mean",
            "weighted_mean",
            "ratio",
            "linear",
        ):
            raise _invalid_composition("Metric has no registered retained empty finish")
        physical = arrow_scalar_type(node.value_type)
        if pa.types.is_duration(physical):
            physical = pa.int64()
        schema = pa.schema(
            [
                pa.field(
                    "original_state__" + name, pa.int64() if name.endswith("count") else physical
                )
                for name in state.components
            ]
        )
        _, value, tag, reason = merge_original(
            (), schema, state.components, method, node.value_type, state.empty_rules
        )
        if tag == "defined" and value == 0:
            result.append("zero")
        elif tag == "null" and reason == "empty_contribution":
            result.append("null")
        elif tag == "undefined" and reason == "zero_denominator":
            result.append("zero_denominator")
        else:
            raise _invalid_composition("Metric empty finish is not an admitted empty contribution")
    return tuple(result)


def comparison_template(node: MethodNode, *, period: bool = False) -> tuple[object, ...]:
    """Inspect each captured template once, preserving ordered child roles."""

    @cache
    def visit(current: MethodNode) -> tuple[object, ...]:
        return _comparison_template(current, period=period, visit=visit)

    return visit(node)


def _comparison_template(
    node: MethodNode,
    *,
    period: bool,
    visit: Callable[[MethodNode], tuple[object, ...]],
) -> tuple[object, ...]:
    params = node.parameters
    if isinstance(params, PreparedObservation):
        params = params.observation
    if isinstance(params, PartsTransport) and params.keep_quantity:
        return visit(comparison_endpoints(node)[0])
    if isinstance(params, CompleteGroups):
        return (
            "complete_groups",
            tuple(
                replace(key, field="time") if period and key.role == "anchor" else key
                for key in params.output_domain.instance_key
            ),
            visit(comparison_endpoints(node)[0]),
        )
    if isinstance(params, ReferenceDerive):
        return (
            "reference",
            params.kind,
            params.reference_id,
            params.strata,
            params.statistical_unit,
            params.unit,
            tuple(
                visit(child)
                for child in comparison_endpoints(node)
                if child.signature.quantity is not None
            ),
        )
    if isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
        return (
            type(params).__name__,
            tuple(
                (item.name, getattr(params, item.name))
                for item in fields(params)
                if item.name not in ("target", "quantity", "start", "end")
            ),
            params.quantity.unit,
            params.quantity.value_policy,
            tuple(
                replace(key, field="time") if period and key.role == "anchor" else key
                for key in params.target.domain.instance_key
            ),
        )
    if isinstance(params, CellDerive):
        return (
            "comparison",
            params.method,
            params.design,
            params.pairing,
            params.relationship,
            params.empty_rules,
            params.value_policy,
            params.unit,
            tuple(visit(child) for child in comparison_endpoints(node)),
        )
    if isinstance(params, AttachCategory):
        return (
            "classification",
            params.coordinate,
            visit(comparison_endpoints(node)[0]),
        )
    if isinstance(params, BindProject):
        return (
            "field",
            params.ref,
            params.field_contract,
            params.metric_contract,
            params.path_contracts,
            params.expression_bodies,
            params.measure_unit,
        )
    if isinstance(params, (OriginalReduce, RowState)):

        def coordinate_template(coordinates: tuple[Coordinate, ...]) -> tuple[Coordinate, ...]:
            return tuple(
                replace(key, field="time") if period and key.role == "anchor" else key
                for key in coordinates
            )

        def mapping_template() -> object:
            assert isinstance(params, OriginalReduce)
            if not period or not params.time_mapping:
                return params.time_mapping
            source = comparison_endpoints(node)[0].signature.domain.time_grid
            target = params.output_domain.time_grid
            assert source is not None and target is not None
            return tuple(
                (
                    grid.grain_token,
                    grid.report_timezone,
                    grid.boundary_timezone,
                    grid.snapshot_digest,
                    grid.scope_digest,
                )
                for grid in (source, target)
            )

        return (
            type(params).__name__,
            tuple(
                (
                    item.name,
                    coordinate_template(params.coordinates)
                    if isinstance(params, OriginalReduce) and item.name == "coordinates"
                    else mapping_template()
                    if isinstance(params, OriginalReduce) and item.name == "time_mapping"
                    else getattr(params, item.name),
                )
                for item in fields(params)
                if item.name
                not in (
                    "output_domain",
                    "definition_id",
                    "partition_check_id",
                    "coverage_check_id",
                    "numeric_check_id",
                )
            ),
            coordinate_template(params.output_domain.instance_key),
            tuple(visit(child) for child in comparison_endpoints(node)),
        )
    if isinstance(params, (OriginalRatio, OccurrenceCombine)):
        return (
            type(params).__name__,
            params.quantity.metric_ref,
            params.quantity.graph_fingerprint,
            params.quantity.unit,
            params.quantity.value_policy,
            tuple(sign for _, sign in params.terms)
            if isinstance(params, OccurrenceCombine)
            else (),
            tuple(visit(child) for child in comparison_endpoints(node)),
        )
    raise DatasetConstructionError(
        expected="a registered comparison rule for every frozen quantity node",
        received="quantity template has no registered comparison rule",
        repair="Read relation.contract() and choose one of its currently supported continuations.",
        location="analysis.graph_composition",
    )


def comparison_endpoints(node: MethodNode) -> tuple[MethodNode, ...]:
    """Recover explicit endpoint definitions without following Artifact history."""
    children: tuple[Node, ...] = node.retained_endpoints or tuple(edge.node for edge in node.inputs)
    if len(children) != len(node.inputs) or any(
        not isinstance(child, MethodNode) for child in children
    ):
        raise _invalid_composition("comparison lacks two complete frozen endpoint definitions")
    if node.retained_endpoints:
        fingerprints = definition_fingerprints(node)
        for child, edge in zip(children, node.inputs, strict=True):
            expected = (
                edge.node.definition_fingerprint
                if isinstance(edge.node, FixedLeaf)
                else fingerprints[edge.node.identity]
            )
            if fingerprints[child.identity] != expected:
                raise _invalid_composition(
                    "retained endpoint does not match its exact input receipt definition"
                )

    return tuple(child for child in children if isinstance(child, MethodNode))


def comparison_bindings(node: MethodNode, *, period: bool = False) -> tuple[tuple[str, str], ...]:
    """Keep independent captures distinct, even when their definitions are equal."""
    params = node.parameters
    if isinstance(params, PreparedObservation):
        params = params.observation
    if isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
        target = node.inputs[0].node
        while isinstance(target, MethodNode) and isinstance(target.parameters, AttachCategory):
            target = target.inputs[0].node
        if period and isinstance(target, MethodNode) and isinstance(target.parameters, TimeProduct):
            target = target.inputs[0].node
        return ((target.identity, params.quantity.time_scope),)
    if isinstance(params, BindProject):
        target = node.inputs[0].node
        if period and isinstance(target, MethodNode) and isinstance(target.parameters, TimeProduct):
            target = target.inputs[0].node
        return ((target.identity, params.attribute_time),)
    if isinstance(params, (AttachCategory, CompleteGroups)):
        return comparison_bindings(comparison_endpoints(node)[0], period=period)
    if isinstance(
        params, (CellDerive, OriginalReduce, RowState, OriginalRatio, OccurrenceCombine)
    ) or (isinstance(params, PartsTransport) and params.keep_quantity):
        return tuple(
            item
            for child in comparison_endpoints(node)
            for item in comparison_bindings(child, period=period)
        )
    raise _invalid_composition("quantity has no retained target realization")


def validate_time_comparison(
    current: MethodNode, baseline: MethodNode, design: Literal["time", "cohort", "period"] = "time"
) -> None:
    """Validate recursively before key checks or any governed business reads."""
    if comparison_template(current, period=design == "period") != comparison_template(
        baseline, period=design == "period"
    ):
        raise _invalid_composition("comparison endpoints have different quantity templates")
    left, right = (
        comparison_bindings(current, period=design == "period"),
        comparison_bindings(baseline, period=design == "period"),
    )
    if design == "cohort":
        if (
            current.signature.domain.kind not in ("group", "singleton")
            or current.signature.domain.instance_key != baseline.signature.domain.instance_key
        ):
            raise _invalid_composition(
                "CohortContrast requires common Group or Singleton coordinates"
            )
        if tuple(item[1] for item in left) != tuple(item[1] for item in right):
            raise _invalid_composition("CohortContrast requires identical observation time roles")
        return
    if tuple(item[0] for item in left) != tuple(item[0] for item in right):
        raise _invalid_composition("endpoints do not share the same frozen member node")
    if tuple(item[1] for item in left) == tuple(item[1] for item in right):
        raise _invalid_composition("comparison requires distinct time bindings")


def combine_observations(
    current: MemberGraph,
    baseline: MemberGraph,
    method: Literal["difference", "relative_change", "spearman", "ratio", "relation_ratio"],
    *,
    ratio: ObservedQuantity | None = None,
    design: Literal["time", "cohort", "period"] = "time",
    pairing: Literal["exact", "keep", "metric_empty"] = "exact",
    relationship: TargetRelationshipContract | None = None,
    verification: Literal["check", "assume"] = "check",
    union_targets: bool = False,
) -> MemberGraph:
    """Share identical frozen nodes, preserving independent ordered endpoints."""

    def invalid(received: str) -> DatasetConstructionError:
        return _invalid_composition(received)

    if current.runtime is not baseline.runtime or current.registry is not baseline.registry:
        raise invalid("different Session or Semantic binding")
    known = {node.identity: node for node in topology(current.root)}
    for node in topology(baseline.root):
        prior = known.get(node.identity)
        if prior is not None:
            if not same_node_definition(prior, node):
                raise invalid("one node identity has different frozen definitions")
            continue
        if isinstance(node, MethodNode):
            sources = tuple(known[source.identity] for source in node.sources)
            if any(not isinstance(source, SourceLeaf) for source in sources):
                raise invalid("non-source dependency in a source node")
            node = replace(
                node,
                inputs=tuple(Edge(edge.role, known[edge.node.identity]) for edge in node.inputs),
                sources=tuple(source for source in sources if isinstance(source, SourceLeaf)),
            )
        known[node.identity] = node
    first, second = current.root, known[baseline.root.identity]
    quantity = first.signature.quantity
    if quantity is None or second.signature.quantity is None:
        raise invalid("an endpoint has no observation quantity")
    if (
        method not in ("difference", "relative_change", "relation_ratio")
        and first.signature.domain.binding != second.signature.domain.binding
    ):
        raise invalid("endpoints do not share the exact frozen member binding")
    if not isinstance(second, MethodNode):
        raise invalid("endpoint has no typed method definition")
    if method in ("difference", "relative_change"):
        validate_time_comparison(first, second, design)
    elif method == "relation_ratio":
        if design != "period" and quantity.time_scope != second.signature.quantity.time_scope:
            raise invalid("ordinary ratio requires corresponding time roles")
    else:
        if (
            not isinstance(second, MethodNode)
            or not isinstance(first.parameters, (ObserveMetric, ObserveCount))
            or not isinstance(second.parameters, (ObserveMetric, ObserveCount))
        ):
            raise invalid("endpoints are not exact original observations")
        left_member, right_member = first.inputs[0].node, second.inputs[0].node
        if (
            left_member.identity != right_member.identity
            or left_member.fingerprint != right_member.fingerprint
        ):
            raise invalid("endpoints do not share the same frozen member node")
    definition = digest(method + first.fingerprint + second.fingerprint)
    if method == "ratio":
        if (
            ratio is None
            or not isinstance(quantity, ObservedQuantity)
            or not isinstance(second.signature.quantity, ObservedQuantity)
        ):
            raise invalid("missing exact original ratio definition")
        root = method_node(
            (Edge("quantity", first), Edge("quantity", second)),
            OriginalRatio(
                ratio, quantity.metric_ref, second.signature.quantity.metric_ref, union_targets
            ),
            value_type=ScalarType("float64"),
        )
    elif method in ("difference", "relative_change", "relation_ratio"):
        from marivo.semantic.unit_algebra import ratio_unit

        arithmetic: Literal["difference", "relative_change", "ratio"] = (
            "ratio" if method == "relation_ratio" else method
        )
        try:
            result_type = output_type(arithmetic, first.value_type, second.value_type)
        except ValueError as error:
            raise invalid(str(error)) from error
        root = method_node(
            (Edge("current", first), Edge("baseline", second)),
            CellDerive(
                arithmetic,
                definition,
                "strict",
                ratio_unit(quantity.unit, second.signature.quantity.unit)
                if arithmetic == "ratio"
                else quantity.unit,
                quantity.time_scope,
                "source.exact_pairing@v1" if pairing == "exact" else "source.unique_key@v1",
                "source.finite_numeric@v1",
                "ratio" if method == "relation_ratio" else design,
                pairing,
                comparison_empty_rules(first, second) if pairing == "metric_empty" else (),
                time_index=period_mapping(first, second)[0] if design == "period" else None,
                bucket_mapping=period_mapping(first, second)[1] if design == "period" else (),
                relationship=relationship,
                verification=verification,
            ),
            value_type=result_type,
        )
    else:
        domain = DomainSignature(first.signature.domain.binding, "singleton", (), (), definition)
        root = method_node(
            (Edge("quantity", first), Edge("quantity", second)),
            AssociationScore(
                domain, definition, "source.exact_pairing@v1", "source.finite_numeric@v1"
            ),
            value_type=ScalarType("float64"),
        )
    schemas = {leaf.identity: schema for schema, leaf in (*current.sources, *baseline.sources)}
    source_nodes = tuple(node for node in topology(root) if isinstance(node, SourceLeaf))
    bindings = tuple((schemas[node.identity], node) for node in source_nodes)
    return replace(current, root=root, sources=bindings)
