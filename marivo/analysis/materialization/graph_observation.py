"""Frozen window observation construction over the qualified Entity graph."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timezone
from itertools import pairwise

import pyarrow as pa

from marivo._temporal import TimeScope
from marivo.analysis.core.graph import (
    Edge,
    MethodNode,
    Node,
    SourceDefinition,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.model import ObservedQuantity
from marivo.analysis.core.rules import (
    BindProject,
    DirectMetricDefinition,
    EntityObservationTarget,
    GroupObservationTarget,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    entity_members,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.graph_members import MemberGraph
from marivo.analysis.materialization.graph_preflight import preflight_entities
from marivo.analysis.materialization.graph_protocol import digest, schema_text
from marivo.analysis.methods.physical import ScalarType, TimeShape
from marivo.analysis.observation.temporal import civil_bound
from marivo.refs import DimensionKind, MetricKind, Ref, RelationshipKind, ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import TimestampParse
from marivo.semantic.metric_graph import AggregateNodeV1, component_node
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.validator import (
    normalize_target_dimension,
    normalize_target_relationship,
)


def _reject(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="a qualified direct sum Metric, member route and UTC event window",
        received=received,
        repair="Use the exact single-root Metric and contribution-to-member relationship.",
        location="analysis.graph_observation",
        help_target="dsl.LogicalAnalysisDomain.observe",
    )


def observe_members(
    members: MemberGraph,
    metric_ref: Ref[MetricKind],
    *,
    during: TimeScope,
    via: Ref[RelationshipKind] | tuple[Ref[RelationshipKind], ...],
    sidecar: CompiledExpressionSidecar,
    report_timezone: str,
    coordinates: tuple[Ref[DimensionKind], ...] = (),
) -> MemberGraph:
    """Resolve schema only and capture all observation inputs before admission."""
    registry = members.registry
    metric = normalize_target_metric(registry, metric_ref.path, sidecar=sidecar)
    route_refs = via if isinstance(via, tuple) else (via,)
    path = tuple(normalize_target_relationship(registry, item.path) for item in route_refs)
    if (
        len(metric.components) != 1
        or len(metric.computation_roots) != 1
        or len(path) not in (1, 2)
        or metric.components[0].event_time_dimension is None
        or path[0].from_entity_ref.path != metric.computation_roots[0].path
        or path[-1].to_entity_ref.path != members.entity_schema.contract.ref.path
        or any(
            relationship.cardinality not in ("many_to_one", "one_to_one")
            or len(relationship.keys) != 1
            or relationship.from_version_resolution_required
            or relationship.to_version_resolution_required
            for relationship in path
        )
        or any(first.to_entity_ref != second.from_entity_ref for first, second in pairwise(path))
        or metric.null_rule != "ignore_null_inputs"
        or metric.components[0].empty_rule not in ("null", "zero")
        or metric.components[0].spatial_merge != "sum"
        or metric.requires_source_recompute
        or metric.cumulative
    ):
        raise _reject("Metric roots or relationship endpoints differ")
    aggregate = component_node(metric.graph, metric.components[0].node_id)
    if (
        not isinstance(aggregate, AggregateNodeV1)
        or aggregate.agg not in ("sum", "count")
        or aggregate.filter
    ):
        raise _reject("Metric is not an unsliced sum or Entity count")
    if aggregate.agg == "count" and (
        metric.components[0].empty_rule != "zero"
        or aggregate.target_ref.kind != "entity"
        or aggregate.target_ref.path != metric.computation_roots[0].path
    ):
        raise _reject("Metric empty policy differs from the qualified method")
    body = next(
        (
            body
            for key, body in sidecar.bodies.items()
            if key.path == aggregate.target_ref.path and key.kind == aggregate.target_ref.kind
        ),
        None,
    )
    if aggregate.agg == "sum" and (body is None or body.source_column is None):
        raise _reject("Measure is not a frozen direct column")
    event_ref = metric.components[0].event_time_dimension
    assert event_ref is not None
    event = normalize_target_dimension(registry, event_ref.path)
    entity_paths = tuple(
        dict.fromkeys(
            (
                members.entity_schema.contract.ref.path,
                *(relationship.from_entity_ref.path for relationship in path),
                *(schema.contract.ref.path for schema, _ in members.sources),
            )
        )
    )
    if len(coordinates) > 2 or len(set(coordinates)) != len(coordinates):
        raise _reject("one or two distinct string contribution coordinates are qualified")
    coordinate_fields = tuple(
        normalize_target_dimension(registry, item.path) for item in coordinates
    )
    if any(
        field.entity_ref.path not in entity_paths
        or field.parse is not None
        or field.is_time_dimension
        for field in coordinate_fields
    ):
        raise _reject("coordinate must be a direct Dimension on the bound contribution route")
    selected_schemas = preflight_entities(
        registry, members.runtime.store.project_root, entity_paths
    )
    schemas = {schema.contract.ref.path: schema for schema in selected_schemas}
    member_schema = schemas[members.entity_schema.contract.ref.path]
    contribution_schema = schemas[path[0].from_entity_ref.path]
    if event.entity_ref.path not in schemas:
        raise _reject("event time is outside the qualified contribution route")
    if member_schema != members.entity_schema:
        raise _reject("member schema changed after construction")
    amount_type = (
        contribution_schema.field_type(body.source_column)
        if aggregate.agg == "sum" and body is not None and body.source_column is not None
        else ScalarType("int64")
    )
    if amount_type.name not in ("int64", "float64"):
        raise _reject("unqualified amount physical type")
    event_type = schemas[event.entity_ref.path].schema.field(event.source_column).type
    if (
        event.timezone != "UTC"
        or (event.parse is not None and event.parse != TimestampParse(timezone="UTC"))
        or event_type
        not in (pa.timestamp("us"), pa.timestamp("us", "UTC"), pa.timestamp("us", "Etc/UTC"))
    ):
        raise _reject("unqualified event physical type or timezone")
    for relationship in path:
        from_column, to_column = relationship.keys[0]
        first, second = (
            schemas[relationship.from_entity_ref.path],
            schemas[relationship.to_entity_ref.path],
        )
        if (
            to_column != second.contract.primary_key[0]
            or first.field_type(from_column) != second.identity_type
        ):
            raise _reject("relationship key differs from the exact destination identity")
    for field in coordinate_fields:
        if schemas[field.entity_ref.path].field_type(field.source_column) != ScalarType("string"):
            raise _reject("coordinate physical type is not string")
    coordinate_fields = tuple(replace(field, logical_type="string") for field in coordinate_fields)
    utc_bounds = tuple(
        civil_bound(value, report=report_timezone, boundary="UTC")
        for value in (during.start, during.end)
    )
    start, end = tuple(
        (value if isinstance(value, datetime) else datetime.combine(value, time()))
        .replace(tzinfo=timezone.utc)
        .isoformat()
        for value in utc_bounds
    )
    window = canonical_json({"start": start, "end": end})
    temporal = TimeShape("instant", "us", "UTC")
    nodes: dict[str, Node] = {}
    for node in topology(members.root):
        if isinstance(node, SourceLeaf):
            nodes[node.identity] = replace(
                node,
                definition=replace(
                    node.definition, shape=replace(node.definition.shape, time=temporal)
                ),
            )
        elif isinstance(node, MethodNode):
            sources = tuple(nodes[source.identity] for source in node.sources)
            if any(not isinstance(source, SourceLeaf) for source in sources):
                raise _reject("invalid member source dependency")
            nodes[node.identity] = replace(
                node,
                inputs=tuple(Edge(edge.role, nodes[edge.node.identity]) for edge in node.inputs),
                sources=tuple(source for source in sources if isinstance(source, SourceLeaf)),
            )
        else:
            raise _reject("fixed members cannot open a live observation source")
    member_root = nodes[members.root.identity]
    member_leaf = nodes[members.leaf.identity]
    assert isinstance(member_root, MethodNode) and isinstance(member_leaf, SourceLeaf)
    target: EntityObservationTarget | GroupObservationTarget = EntityObservationTarget(
        member_root.signature.domain
    )
    if isinstance(member_root.parameters, MapCorrespond) and member_root.parameters.mode == "group":
        projection = member_root.inputs[0].node
        if (
            not isinstance(projection, MethodNode)
            or not isinstance(projection.parameters, BindProject)
            or projection.parameters.field_contract is None
        ):
            raise _reject("Group has no exact member field projection")
        target = GroupObservationTarget(
            member_root.signature.domain, projection.parameters.field_contract
        )
        member_root = projection
    contribution_ref = ref.entity(contribution_schema.contract.ref.path)
    binding = member_root.signature.domain.binding
    source_entries = [(member_schema, member_leaf)]
    for schema in selected_schemas:
        if schema.contract.ref.path == member_schema.contract.ref.path:
            continue
        entity_ref = ref.entity(schema.contract.ref.path)
        leaf = SourceLeaf(
            SourceDefinition(
                entity_ref,
                digest(schema.contract.dependency_fingerprint + schema_text(schema.schema)),
                ref.datasource(schema.contract.datasource_ref.path),
                replace(schema.shape, time=temporal),
            ),
            entity_members(
                replace(
                    schema.contract,
                    columns=tuple((field.name, str(field.type)) for field in schema.schema),
                ),
                entity_ref,
                binding,
            ),
            schema.identity_type,
            identity=digest(
                "contribution:"
                + member_leaf.identity
                + entity_ref.path
                + schema.contract.dependency_fingerprint
                + schema_text(schema.schema)
            ),
        )
        prior = nodes.get(leaf.identity)
        if prior is not None:
            if not isinstance(prior, SourceLeaf) or prior.fingerprint != leaf.fingerprint:
                raise _reject("an existing source node has different frozen binding facts")
            leaf = prior
        source_entries.append((schema, leaf))
    contribution = next(
        leaf for schema, leaf in source_entries if schema.contract.ref.path == contribution_ref.path
    )
    quantity = ObservedQuantity(
        digest(metric.bound_graph_fingerprint + window + member_root.fingerprint),
        metric_ref,
        metric.bound_graph_fingerprint,
        metric.unit,
        window,
        digest(
            contribution.definition.fingerprint
            + window
            + ",".join(item.path for item in route_refs)
            + member_root.fingerprint
        ),
        "ignore_null_inputs",
        "sum_zero@v1"
        if aggregate.agg == "sum" and metric.components[0].empty_rule == "zero"
        else f"{aggregate.agg}@v1",
    )
    definition = DirectMetricDefinition(
        metric_ref,
        metric.graph,
        metric.components[0].node_id,
        metric.bound_graph_fingerprint,
        metric.dependency_fingerprint,
        contribution_ref,
        ref.time_dimension(event.ref.path),
        metric.unit,
        metric.components[0].empty_rule,
        tuple(ref.relationship(item.path) for item in metric.components[0].event_time_path),
    )
    parameters: ObserveMetric | ObserveCount
    if aggregate.agg == "count":
        parameters = ObserveCount(
            definition,
            target,
            quantity,
            contribution_ref,
            path,
            event,
            start,
            end,
            coordinate_fields,
        )
    else:
        assert body is not None and body.source_column is not None
        parameters = ObserveMetric(
            definition,
            target,
            quantity,
            contribution_ref,
            path,
            event,
            start,
            end,
            body.source_column,
            "int64" if amount_type == ScalarType("int64") else "float64",
            coordinate_fields,
        )
    root = method_node(
        (Edge("subject", member_root),),
        parameters,
        sources=tuple(leaf for _, leaf in source_entries),
        value_type=amount_type,
    )

    return replace(
        members,
        root=root,
        leaf=member_leaf,
        sources=tuple(source_entries),
    )


def observe_ratio_members(
    members: MemberGraph,
    metric_ref: Ref[MetricKind],
    *,
    during: TimeScope,
    paths: tuple[tuple[Ref[RelationshipKind], ...], tuple[Ref[RelationshipKind], ...]],
    sidecar: CompiledExpressionSidecar,
    report_timezone: str,
    coordinates: tuple[Ref[DimensionKind], ...] = (),
) -> MemberGraph:
    """Bind a declared two-root ratio to its independently aggregated components."""
    from marivo.analysis.materialization.graph_composition import combine_observations
    from marivo.semantic.ir import RatioComposition
    from marivo.semantic.metric_graph import RatioNodeV1

    registry = members.registry
    metric = normalize_target_metric(registry, metric_ref.path, sidecar=sidecar)
    declaration = registry.metrics[metric_ref.path]
    composition = declaration.composition
    root = next(
        record.node for record in metric.graph.nodes if record.node_id == metric.graph.roots[0]
    )
    if (
        not isinstance(composition, RatioComposition)
        or not isinstance(root, RatioNodeV1)
        or root.zero_division != "undefined"
        or len(metric.components) != 2
        or len(metric.computation_roots) != 2
        or metric.requires_source_recompute
        or metric.cumulative
    ):
        raise _reject("ratio requires two original roots and Undefined zero-denominator policy")
    components = tuple(
        observe_members(
            members,
            ref.metric(metric_path),
            during=during,
            via=path,
            sidecar=sidecar,
            report_timezone=report_timezone,
            coordinates=coordinates,
        )
        for metric_path, path in zip(
            (composition.numerator, composition.denominator), paths, strict=True
        )
    )
    first, second = components
    numerator = first.root.signature.quantity
    denominator = second.root.signature.quantity
    assert isinstance(numerator, ObservedQuantity) and isinstance(denominator, ObservedQuantity)
    quantity = ObservedQuantity(
        digest(metric.bound_graph_fingerprint + numerator.time_scope + members.root.fingerprint),
        metric_ref,
        metric.bound_graph_fingerprint,
        metric.unit,
        numerator.time_scope,
        digest(numerator.contribution_id + denominator.contribution_id),
        "strict",
        "ratio@v1",
    )
    return combine_observations(first, second, "ratio", ratio=quantity)
