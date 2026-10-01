"""Source-free binding of funnel methods and shared-assignment axis preparation."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal
from uuid import uuid4

from marivo.analysis.core.domain_captures import EntryAxisCapture, fail
from marivo.analysis.core.graph import (
    Edge,
    MethodNode,
    SourceDefinition,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.model import (
    Coordinate,
    DomainSignature,
    FunnelComparisonPart,
    FunnelPart,
    JourneyPart,
    require_part,
)
from marivo.analysis.core.rules import (
    FunnelAttribute,
    FunnelAxesPrepare,
    FunnelCompare,
    FunnelField,
    FunnelRead,
    FunnelReduce,
    JourneyMatch,
    OccurrencePrepare,
    entity_members,
)
from marivo.analysis.materialization.graph_preflight import preflight_entities
from marivo.analysis.materialization.graph_protocol import digest, schema_text
from marivo.analysis.materialization.graph_relation import (
    FrozenBinding,
    LiveBinding,
    Relation,
    _shared_root,
)
from marivo.analysis.methods.physical import ScalarType
from marivo.analysis.observation.coordinates import functional_path
from marivo.refs import DimensionKind, Ref, SemanticKind, ref
from marivo.semantic.validator import normalize_target_dimension, normalize_target_relationship


def reduce(relation: Relation, axes: tuple[Ref[DimensionKind], ...]) -> Relation:
    part = require_part(relation.root.signature, "journey")
    assert isinstance(part, JourneyPart)
    if (
        type(axes) is not tuple
        or len(set(axes)) != len(axes)
        or any(type(a) is not Ref or a.kind is not SemanticKind.DIMENSION for a in axes)
    ):
        fail("funnel_axes", "axes must be an ordered tuple of unique exact Dimensions")
    if part.policy != "first_per_subject" or not part.complete:
        fail("funnel_binding", "funnel requires a full first-per-subject assignment")
    definition = relation.definition
    if not isinstance(definition.parameters, JourneyMatch):
        fail("funnel_binding", "funnel requires the original canonical Journey realization")
    capture = definition.inputs[0].node
    assert isinstance(capture, MethodNode) and isinstance(capture.parameters, OccurrencePrepare)
    population_id = capture.inputs[0].node.identity
    captures: tuple[EntryAxisCapture, ...] = ()
    edges = [relation._edge()]
    if axes:
        if not isinstance(relation.binding, LiveBinding):
            fail(
                "funnel_axes",
                "fixed Journey lacks retained entry axes; execute the grouped funnel before materializing",
            )
        live = relation.binding
        registry = live.graph.registry
        subject = part.preparation.events[0].subject
        dimensions = tuple(normalize_target_dimension(registry, a.path) for a in axes)
        paths = tuple(
            functional_path(
                registry,
                subject.ref.path,
                a.entity_ref.path,
                allow_versioned_target=True,
                allow_versioned_source=True,
                allow_versioned_intermediates=True,
            )
            for a in dimensions
        )
        routes = tuple(
            tuple(normalize_target_relationship(registry, name) for name in path) for path in paths
        )
        entity_paths = []
        for route in routes:
            names = [subject.ref.path]
            for hop in route:
                names.append(
                    hop.to_entity_ref.path
                    if hop.from_entity_ref.path == names[-1]
                    else hop.from_entity_ref.path
                )
            entity_paths.append(tuple(names))
        schemas = preflight_entities(
            registry,
            relation.runtime.store.project_root,
            tuple(sorted({name for names in entity_paths for name in names})),
        )
        entries = {leaf.definition.ref.path: (schema, leaf) for schema, leaf in live.graph.sources}
        shape = part.preparation.events[0].source_id
        shape_leaf = next(
            n for n in topology(relation.root) if isinstance(n, SourceLeaf) and n.identity == shape
        )
        for schema in schemas:
            if schema.contract.ref.path in entries:
                continue
            leaf = SourceLeaf(
                SourceDefinition(
                    ref.entity(schema.contract.ref.path),
                    digest(schema.contract.dependency_fingerprint + schema_text(schema.schema)),
                    ref.datasource(schema.contract.datasource_ref.path),
                    replace(schema.shape, time=shape_leaf.definition.shape.time),
                    schema.contract.version,
                ),
                replace(
                    entity_members(
                        replace(schema.contract, version=None),
                        ref.entity(schema.contract.ref.path),
                        part.binding,
                    ),
                    obligations=(),
                ),
                schema.identity_type,
            )
            entries[schema.contract.ref.path] = (schema, leaf)
        captures = tuple(
            EntryAxisCapture(
                replace(
                    dimension,
                    logical_type=str(
                        entries[dimension.entity_ref.path][0]
                        .schema.field(dimension.source_column)
                        .type
                    ),
                ),
                subject,
                route,
                tuple(entries[name][0].contract for name in names),
                tuple(entries[name][1].identity for name in names),
            )
            for dimension, route, names in zip(dimensions, routes, entity_paths, strict=True)
        )
        axis_node = method_node(
            (Edge("subject", capture),),
            FunnelAxesPrepare(part.cohort_start, part.cohort_end, captures, part.events[0]),
            sources=tuple(
                entries[name][1]
                for name in sorted({name for names in entity_paths for name in names})
            ),
            value_type=ScalarType("int64"),
        )
        edges.append(Edge("subject", axis_node))
        relation = replace(
            relation,
            binding=replace(
                live,
                graph=replace(live.graph, sources=tuple(entries[name] for name in sorted(entries))),
            ),
        )
    owner = ref.entity(part.preparation.events[0].subject.ref.path)
    assert owner.kind is SemanticKind.ENTITY
    keys = (
        Coordinate(ref.entity(owner.path), "funnel:step", "group"),
        *(Coordinate(ref.entity(owner.path), "funnel:axis:" + a.path, "group") for a in axes),
    )
    domain = DomainSignature(part.binding, "group", keys, keys, uuid4().hex)
    return relation._with(
        method_node(
            tuple(edges),
            FunnelReduce(domain, capture.signature.domain.definition_id, captures, population_id),
            value_type=ScalarType("int64"),
        )
    )


def compare(current: Relation, baseline: Relation) -> Relation:
    if (
        current.runtime.session_ref != baseline.runtime.session_ref
        or current.runtime.store.store_id != baseline.runtime.store.store_id
    ):
        fail("funnel_binding", "comparison endpoints belong to different Sessions")
    if isinstance(current.binding, FrozenBinding) != isinstance(baseline.binding, FrozenBinding):
        fail("input_mode", "live and fixed funnel endpoints cannot mix")
    for relation in (current, baseline):
        if not isinstance(require_part(relation.root.signature, "funnel_state"), FunnelPart):
            fail("funnel_period", "compare requires two full FunnelResult endpoints")
    domain = replace(current.root.signature.domain, definition_id=uuid4().hex)
    baseline_root = _shared_root(current.root, baseline.root)
    baseline_definition = (
        _shared_root(current.definition, baseline.definition)
        if isinstance(current.binding, FrozenBinding)
        else baseline.definition
    )
    assert isinstance(baseline_definition, MethodNode)
    node = method_node(
        (Edge("current", current.root), Edge("baseline", baseline_root)),
        FunnelCompare(domain),
        value_type=ScalarType("float64"),
        retained_endpoints=(current.definition, baseline_definition)
        if isinstance(current.binding, FrozenBinding)
        else (),
    )
    return current._with(node)._with_sources(baseline)


def read(relation: Relation, field: FunnelField, step: int | None = None) -> Relation:
    return relation._with(
        method_node(
            (relation._edge(),),
            FunnelRead(field, step),
            value_type=ScalarType("int64" if field.endswith("_count") else "float64"),
            retained_endpoints=(relation.definition,)
            if isinstance(relation.binding, FrozenBinding)
            else (),
        )
    )


def attribute(
    relation: Relation,
    *,
    axes: tuple[Ref[DimensionKind], ...],
    target_step: int,
    mode: Literal["joint", "hierarchy"],
    top_k: int | None,
) -> Relation:
    part = require_part(relation.root.signature, "funnel_state")
    if not isinstance(part, FunnelComparisonPart) or not part.complete:
        fail("funnel_allocation", "attribute requires the complete funnel-period comparison")
    if (
        type(axes) is not tuple
        or not axes
        or any(type(a) is not Ref or a.kind is not SemanticKind.DIMENSION for a in axes)
        or len(set(axes)) != len(axes)
    ):
        fail("funnel_axes", "allocation requires nonempty unique ordered Dimensions")
    expanded = relation
    if tuple(ref.dimension(a.dimension.ref.path) for a in part.current.axes) != axes:
        if not isinstance(relation.binding, LiveBinding):
            fail("funnel_axes", "fixed comparison lacks retained axes; lineage cannot supply them")
        definition = relation.definition
        if not isinstance(definition.parameters, FunnelCompare):
            fail("funnel_allocation", "selected comparison cannot expand its original scope")
        endpoints = []
        for edge in definition.inputs:
            if not isinstance(edge.node, MethodNode) or not isinstance(
                edge.node.parameters, FunnelReduce
            ):
                fail("funnel_allocation", "missing original assignment dependency")
            endpoints.append(reduce(replace(relation, root=edge.node.inputs[0].node), axes))
        expanded = compare(endpoints[0], endpoints[1])
    subject = part.current.journey.preparation.events[0].subject.ref.path
    keys = (
        Coordinate(ref.entity(subject), "funnel:resolution", "group"),
        *(Coordinate(ref.entity(subject), "funnel:axis:" + a.path, "group") for a in axes),
        Coordinate(ref.entity(subject), "funnel:other_mask", "group"),
        Coordinate(ref.entity(subject), "funnel:contribution_kind", "group"),
    )
    domain = DomainSignature(
        relation.root.signature.domain.binding, "group", keys, keys, uuid4().hex
    )
    node = method_node(
        (relation._edge(), Edge("subject", _shared_root(relation.root, expanded.root))),
        FunnelAttribute(domain, axes, target_step, mode, top_k),
        value_type=ScalarType("float64"),
        retained_endpoints=(relation.definition, expanded.definition)
        if isinstance(relation.binding, FrozenBinding)
        else (),
    )
    return relation._with(node)._with_sources(expanded)
