"""Source-free binding of Journey matching to the governed occurrence prefix."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import uuid4

import ibis.expr.datatypes as dt

from marivo._temporal import TimeScope
from marivo.analysis.core.domain_captures import EventCapture, OrderCapture, StateModelCapture, fail
from marivo.analysis.core.graph import (
    Edge,
    MethodNode,
    Node,
    SourceDefinition,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.model import Coordinate, DomainSignature
from marivo.analysis.core.rules import JourneyMatch, OccurrencePrepare, entity_candidates
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.domains.completeness import CompletenessDeclaration
from marivo.analysis.domains.errors import event_error
from marivo.analysis.event import EventPattern, EveryStart, FirstPerSubject, PatternStep
from marivo.analysis.materialization.graph_observation import _window_bounds
from marivo.analysis.materialization.graph_preflight import preflight_entities
from marivo.analysis.materialization.graph_protocol import digest, schema_text
from marivo.analysis.materialization.graph_relation import LiveBinding, Relation
from marivo.analysis.methods.physical import ScalarType, TimeShape
from marivo.analysis.observation import coordinates
from marivo.analysis.observation.contracts import ObservationOwner, path_dependency_fingerprint
from marivo.refs import BusinessOrderKind, Ref, SemanticKind, StateModelKind, ref
from marivo.semantic.event import ParticipantRoleHandle
from marivo.semantic.ir import TargetDimensionContract, TargetEntityContract
from marivo.semantic.metric_graph_lowering import dependency_digest
from marivo.semantic.validator import (
    normalize_target_dimension,
    normalize_target_entity,
    normalize_target_relationship,
)


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPatternStep:
    step: PatternStep
    source: TargetEntityContract
    identity: tuple[TargetDimensionContract, ...]
    occurred_at: TargetDimensionContract
    subject: TargetEntityContract
    participant_path: tuple[str, ...]
    event_fingerprint: str


def _normalize_steps(
    owner: ObservationOwner, pattern: EventPattern
) -> tuple[PreparedPatternStep, ...]:
    if (
        type(pattern) is not EventPattern
        or not pattern.steps
        or any(type(step) is not PatternStep for step in pattern.steps)
    ):
        raise event_error("a non-empty exact EventPattern of exact PatternSteps", "invalid Pattern")
    try:
        EventPattern.model_validate_json(pattern.model_dump_json())
    except (TypeError, ValueError) as exc:
        raise event_error(
            "a fully validated immutable EventPattern", "invalid Pattern values"
        ) from exc
    if len({step.key for step in pattern.steps}) != len(pattern.steps):
        raise event_error("unique ordered Pattern step keys", "duplicate step keys")
    result: list[PreparedPatternStep] = []
    for step in pattern.steps:
        if (
            type(step.participant) is not ParticipantRoleHandle
            or type(step.event) is not Ref
            or step.event.kind is not SemanticKind.EVENT
        ):
            raise event_error("exact governed Event participant role", "invalid participant")
        event = owner.semantic_registry.events.get(step.event.path)
        if event is None or step.event not in owner.sidecar.catalog_refs:
            raise event_error(
                "an exact Event from the current semantic registry", "Event is not loaded"
            )
        participant = next(
            (item for item in event.participants if item.name == step.participant.name), None
        )
        if participant is None or participant.cardinality != "one":
            raise event_error(
                "an exact cardinality-one participant declared by the Event",
                "missing or optional participant",
            )
        path = tuple(participant.path or ())
        endpoint = event.source_entity
        for name in path:
            relationship = owner.semantic_registry.relationships.get(name)
            if relationship is None or relationship.from_entity != endpoint:
                raise event_error(
                    "a continuous directed participant path", "broken Event participant path"
                )
            endpoint = relationship.to_entity
        if not coordinates.path_is_functional(owner.semantic_registry, event.source_entity, path):
            raise event_error(
                "a governed to-one participant path",
                "participant path lacks exact identity authority",
            )
        source = normalize_target_entity(owner.semantic_registry, event.source_entity)
        subject = normalize_target_entity(owner.semantic_registry, endpoint)
        if not subject.identity_signature:
            raise event_error(
                "a complete non-empty subject primary key", "source-only participant Entity"
            )
        raw_identity = tuple(
            normalize_target_dimension(owner.semantic_registry, name) for name in event.identity
        )
        if (
            not raw_identity
            or len({item.ref.path for item in raw_identity}) != len(raw_identity)
            or any(item.entity_ref != source.ref or item.is_time_dimension for item in raw_identity)
        ):
            raise event_error(
                "non-empty distinct identity Dimensions on the occurrence source",
                "invalid Event identity",
            )
        # Event identity is non-null by its owning semantic contract even when
        # the physical source permits nulls; action-time scalar proofs enforce it.
        identity = tuple(
            replace(
                item,
                logical_type=(
                    "unknown"
                    if item.logical_type == "unknown"
                    else str(dt.dtype(item.logical_type).copy(nullable=True))
                ),
                nullable=False,
            )
            for item in raw_identity
        )
        instant = normalize_target_dimension(owner.semantic_registry, event.occurred_at)
        if (
            instant.entity_ref != source.ref
            or not instant.is_time_dimension
            or instant.logical_type != "timestamp"
        ):
            raise event_error(
                "a governed timestamp occurrence axis on the Event source",
                "invalid Event time authority",
            )
        if result and (
            subject.ref != result[0].subject.ref
            or subject.identity_signature != result[0].subject.identity_signature
        ):
            raise event_error(
                "one exact subject Entity and ordered identity signature",
                "Pattern participant subjects differ",
            )
        if result and tuple(item.logical_type for item in identity) != tuple(
            item.logical_type for item in result[0].identity
        ):
            raise event_error(
                "homogeneous occurrence identity arity and ordered logical types",
                "incompatible Event identity signatures",
            )
        body = owner.sidecar.bodies.get(step.event)
        if body is None:
            raise event_error("a frozen executable Event predicate", "missing Event body")
        dependency = dependency_digest(
            owner.semantic_registry,
            sidecar=owner.sidecar,
            dimension_ids=(*event.identity, event.occurred_at),
            semantic_refs=tuple(binding.to_ref() for binding in body.bindings),
        )
        fingerprint = d._canonical_digest(
            (
                "event.source-definition@v1",
                event.semantic_id,
                event.source_entity,
                event.identity,
                event.occurred_at,
                tuple((item.name, item.path, item.cardinality) for item in event.participants),
                event.predicate_kind,
                event.body_ast_hash,
                body.body_ast_hash,
                tuple(
                    (binding.field_ref.kind.value, binding.field_ref.path, binding.entity_position)
                    for binding in body.bindings
                ),
                dependency.digest,
                path_dependency_fingerprint(
                    owner,
                    source.ref.path,
                    tuple(tuple(item.path or ()) for item in event.participants),
                ),
            )
        )
        result.append(
            PreparedPatternStep(
                step,
                source,
                identity,
                instant,
                subject,
                path,
                fingerprint,
            )
        )
    return tuple(result)


def prepare(
    population: Relation,
    owner: ObservationOwner,
    pattern: EventPattern,
    *,
    cohort_window: TimeScope,
    completion_through: datetime,
    order_use: Literal["prepare", "ordered", "one_step_every_start"],
    model_ref: Ref[StateModelKind] | None = None,
    business_order: Ref[BusinessOrderKind] | None,
    completeness: tuple[CompletenessDeclaration, ...],
) -> Relation:
    if (
        not isinstance(population.binding, LiveBinding)
        or population.root.signature.domain.kind != "entity"
    ):
        fail("input_mode", "matching requires one logical Entity population")
    if (
        owner.session_id != population.runtime.session_ref
        or owner.store_id != population.runtime.store.store_id
    ):
        fail("input_binding", "foreign Session population")
    live = population.binding
    steps = _normalize_steps(owner, pattern)
    if population.root.signature.domain.instance_key != tuple(
        Coordinate(ref.entity(steps[0].subject.ref.path), key, "identity")
        for key in steps[0].subject.primary_key
    ):
        fail("input_binding", "population differs from the exact participant Subject")
    start, end = _window_bounds(cohort_window, live.report_timezone)
    assert start is not None and end is not None
    if completion_through.utcoffset() is None or completion_through < datetime.fromisoformat(end):
        fail("preparation_bounds", "follow-up must be aware and include the cohort window")
    registry = owner.semantic_registry
    paths = {step.source.ref.path for step in steps} | {step.subject.ref.path for step in steps}
    for step in steps:
        paths.update(registry.relationships[name].to_entity for name in step.participant_path)
    schemas = preflight_entities(
        registry,
        population.runtime.store.project_root,
        tuple(sorted(paths)),
        frozen_reader=live.graph.entity_schema.reader_timezone,
    )
    temporal = TimeShape("instant", "us", "UTC")
    nodes: dict[str, Node] = {}
    for node in topology(population.root):
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
                fail("input_binding", "invalid member source dependency")
            nodes[node.identity] = replace(
                node,
                inputs=tuple(Edge(edge.role, nodes[edge.node.identity]) for edge in node.inputs),
                sources=tuple(source for source in sources if isinstance(source, SourceLeaf)),
            )
        else:
            fail("input_mode", "fixed members cannot match live Events")
    population_root = nodes[population.root.identity]
    assert isinstance(population_root, MethodNode)
    binding = population_root.signature.domain.binding
    by_entity = {
        node.definition.ref.path: node for node in nodes.values() if isinstance(node, SourceLeaf)
    }
    entries = []
    for schema in schemas:
        source = by_entity.get(schema.contract.ref.path)
        if source is None:
            source = SourceLeaf(
                SourceDefinition(
                    ref.entity(schema.contract.ref.path),
                    digest(schema.contract.dependency_fingerprint + schema_text(schema.schema)),
                    ref.datasource(schema.contract.datasource_ref.path),
                    replace(schema.shape, time=temporal),
                    schema.contract.version,
                ),
                replace(
                    entity_candidates(
                        replace(
                            schema.contract,
                            columns=tuple((field.name, str(field.type)) for field in schema.schema),
                        ),
                        ref.entity(schema.contract.ref.path),
                        binding,
                    ),
                    obligations=(),
                ),
                schema.identity_type,
            )
            by_entity[schema.contract.ref.path] = source
        entries.append((schema, source))
    captures: dict[str, EventCapture] = {}
    for step in steps:
        definition = registry.events[step.step.event.path]
        capture = EventCapture(
            step.step.event,
            step.event_fingerprint,
            replace(
                step.source,
                dependency_fingerprint=by_entity[step.source.ref.path].definition.fingerprint,
            ),
            by_entity[step.source.ref.path].identity,
            tuple(
                replace(
                    field,
                    logical_type=str(
                        next(
                            schema
                            for schema in schemas
                            if schema.contract.ref.path == step.source.ref.path
                        )
                        .schema.field(field.source_column)
                        .type
                    ),
                )
                for field in step.identity
            ),
            step.occurred_at,
            step.step.participant.name,
            replace(
                step.subject,
                dependency_fingerprint=by_entity[step.subject.ref.path].definition.fingerprint,
            ),
            tuple(normalize_target_relationship(registry, name) for name in step.participant_path),
            definition.body_ast_hash,
            definition.predicate_kind,
            step.event_fingerprint,
            definition,
        )
        prior = captures.get(capture.ref.path)
        if prior is not None and prior != capture:
            fail("input_binding", "repeated Event uses have different participants")
        captures[capture.ref.path] = capture
    order = None
    if business_order is not None:
        definition_order = registry.business_orders.get(business_order.path)
        if definition_order is None:
            fail("business_order", "order is not loaded in this Session")
        order = OrderCapture(
            business_order,
            digest(repr(definition_order)),
            definition_order,
            tuple(
                normalize_target_dimension(registry, sequence.value_ref)
                for sequence in definition_order.sequences
            ),
            tuple((capture.ref.path, capture.fingerprint) for capture in captures.values()),
        )
    first = next(iter(captures.values()))
    keys = (
        Coordinate(ref.entity(first.source.ref.path), "event_binding", "identity"),
        *(
            Coordinate(
                ref.entity(first.source.ref.path), field.ref.path.rsplit(".", 1)[-1], "identity"
            )
            for field in first.identity
        ),
    )
    domain = DomainSignature(binding, "occurrence", keys, keys, uuid4().hex)
    capture_node = method_node(
        (Edge("subject", population_root),),
        OccurrencePrepare(
            domain,
            tuple(captures.values()),
            None if model_ref is not None else start,
            completion_through.isoformat(),
            order=order,
            model=None
            if model_ref is None
            else StateModelCapture(
                model_ref,
                digest(repr(registry.state_models[model_ref.path])),
                registry.state_models[model_ref.path],
                tuple(captures.values()),
                order,
                tuple((capture.ref.path, capture.fingerprint) for capture in captures.values()),
            ),
            completeness=completeness,
            order_use=order_use,
        ),
        value_type=ScalarType("int64"),
        sources=tuple(by_entity[path] for path in sorted(paths)),
    )
    member_leaf = nodes[live.graph.leaf.identity]
    assert isinstance(member_leaf, SourceLeaf)
    graph = replace(
        live.graph,
        root=capture_node,
        leaf=member_leaf,
        sources=tuple(
            {
                leaf.identity: (schema, leaf)
                for schema, leaf in (
                    *((schema, nodes[leaf.identity]) for schema, leaf in live.graph.sources),
                    *entries,
                )
                if isinstance(leaf, SourceLeaf)
            }.values()
        ),
        expression_sidecar=owner.sidecar,
    )
    return Relation(population.runtime, capture_node, replace(live, graph=graph))


def construct(
    population: Relation,
    owner: ObservationOwner,
    pattern: EventPattern,
    *,
    cohort_window: TimeScope,
    completion_through: datetime,
    matching: FirstPerSubject | EveryStart,
    business_order: Ref[BusinessOrderKind] | None,
    completeness: tuple[CompletenessDeclaration, ...],
) -> Relation:
    captured = prepare(
        population,
        owner,
        pattern,
        cohort_window=cohort_window,
        completion_through=completion_through,
        business_order=business_order,
        completeness=completeness,
        order_use="one_step_every_start"
        if len(pattern.steps) == 1 and isinstance(matching, EveryStart)
        else "ordered",
    )
    assert isinstance(captured.binding, LiveBinding) and isinstance(captured.root, MethodNode)
    capture_node = captured.root
    population_root = capture_node.inputs[0].node
    keys = capture_node.signature.domain.instance_key
    binding = population_root.signature.domain.binding
    steps = _normalize_steps(owner, pattern)
    start, end = _window_bounds(cohort_window, captured.binding.report_timezone)
    assert start is not None and end is not None
    journey_keys = (*population_root.signature.domain.instance_key, *keys)
    journey_domain = DomainSignature(binding, "journey", journey_keys, journey_keys, uuid4().hex)
    result = method_node(
        (Edge("subject", capture_node),),
        JourneyMatch(
            journey_domain,
            tuple(step.step.fingerprint for step in steps),
            tuple(step.step.event.path for step in steps),
            "first_per_subject"
            if isinstance(matching, FirstPerSubject)
            else matching.completion_assignment,
            start,
            end,
            completion_through.isoformat(),
        ),
        value_type=ScalarType("int64"),
    )
    return Relation(
        population.runtime,
        result,
        replace(captured.binding, graph=replace(captured.binding.graph, root=result)),
    )
