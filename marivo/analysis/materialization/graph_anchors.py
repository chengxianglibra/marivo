"""Construction-only Anchor binding over captured Event/Journey identities."""

from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256

from marivo._temporal import TimeScope, time_scope
from marivo.analysis.anchors import CalendarWindow, ElapsedWindow, deadline
from marivo.analysis.core.anchor_rules import observation_template
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import (
    Edge,
    MethodNode,
    Node,
    SourceLeaf,
    method_node,
    retained_nodes,
    topology,
)
from marivo.analysis.core.model import (
    AnchorDomainPart,
    DomainSignature,
    JourneyPart,
    OccurrencePart,
    SubjectPart,
    require_part,
)
from marivo.analysis.core.rules import (
    AnchorBind,
    AnchorObserve,
    ObserveCount,
    ObserveMetric,
    OccurrenceCombine,
    OccurrencePrepare,
    OriginalRatio,
)
from marivo.analysis.event import EventPattern, step
from marivo.analysis.materialization.graph_journey import prepare
from marivo.analysis.materialization.graph_observation import _window_bounds
from marivo.analysis.materialization.graph_relation import LiveBinding, Relation
from marivo.analysis.methods.physical import ScalarType
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.refs import BusinessOrderKind, MetricKind, Ref, RelationshipKind
from marivo.semantic.event import ParticipantRoleHandle
from marivo.semantic.runtime_metric import RuntimeMetricExpr


def bind(
    source: ParticipantRoleHandle | Relation,
    population: Relation,
    *,
    during: TimeScope,
    owner: ObservationOwner | None,
    business_order: Ref[BusinessOrderKind] | None,
) -> Relation:
    if isinstance(source, ParticipantRoleHandle):
        if owner is None:
            fail("input_mode", "live Event Anchor requires a logical population")
        captured = prepare(
            population,
            owner,
            EventPattern(steps=(step(participant=source, key="anchor"),)),
            cohort_window=during,
            completion_through=datetime.fromisoformat(
                _window_bounds(
                    during,
                    population.binding.report_timezone
                    if isinstance(population.binding, LiveBinding)
                    else "UTC",
                )[1]
                or ""
            ),
            order_use="prepare",
            business_order=business_order,
            completeness=(),
        )
        subject = require_part(captured.root.signature, "subject")
        assert isinstance(subject, SubjectPart)
        keys = (*subject.subject_key, *captured.root.signature.domain.instance_key)
    else:
        if business_order is not None:
            fail(
                "business_order",
                "Journey Anchor inherits the original order; no override is accepted",
            )
        if not any(isinstance(p, JourneyPart) for p in source.root.signature.parts):
            fail("anchor_binding", "an exact canonical Journey start input is required")
        subject = require_part(source.root.signature, "subject")
        assert isinstance(subject, SubjectPart)
        if population.root.signature.domain.instance_key != subject.subject_key:
            fail("anchor_binding", "population differs from the Journey's full Subject identity")
        if isinstance(source.binding, LiveBinding) != isinstance(population.binding, LiveBinding):
            fail("input_mode", "Journey and population mix fixed and source inputs")
        if source.runtime.session_ref != population.runtime.session_ref:
            fail("input_binding", "Journey and population belong to different Sessions")
        if population.root.signature.domain.binding != source.root.signature.domain.binding:
            fail(
                "anchor_binding",
                "population authority differs from the retained Journey population",
            )
        journey = require_part(source.root.signature, "journey")
        assert isinstance(journey, JourneyPart)
        original_population = next(
            (
                node.inputs[0].node
                for node in retained_nodes(source.definition)
                if isinstance(node, MethodNode)
                and isinstance(node.parameters, OccurrencePrepare)
                and any(
                    isinstance(part, OccurrencePart)
                    and part.preparation_id == journey.preparation.preparation_id
                    for part in node.signature.parts
                )
            ),
            None,
        )
        if (
            original_population is None
            or original_population.identity != population.definition.identity
        ):
            fail(
                "anchor_binding",
                "population is not the exact original Journey population definition",
            )
        captured, keys = source, source.root.signature.domain.instance_key
    start, end = _window_bounds(
        during,
        captured.binding.report_timezone if isinstance(captured.binding, LiveBinding) else "UTC",
    )
    assert start is not None and end is not None
    identity = sha256(repr((captured.root.fingerprint, start, end, keys)).encode()).hexdigest()
    output = DomainSignature(captured.root.signature.domain.binding, "anchor", keys, keys, identity)
    node = method_node(
        (captured._edge(),), AnchorBind(output, start, end), value_type=ScalarType("int64")
    )
    return captured._with(node)


def observe(
    anchors: Relation,
    metric: Ref[MetricKind] | RuntimeMetricExpr,
    *,
    window: ElapsedWindow | CalendarWindow,
    paths: tuple[tuple[Ref[RelationshipKind], ...], ...],
) -> Relation:
    if not isinstance(anchors.binding, LiveBinding):
        fail(
            "input_mode",
            "fixed Anchors have no captured new Metric input; continue the retained observation instead",
        )
    part = require_part(anchors.root.signature, "anchor")
    if not isinstance(part, AnchorDomainPart):
        fail("anchor_binding", "observe requires an Anchor domain")
    graph = anchors.binding.graph
    subject = require_part(anchors.root.signature, "subject")
    assert isinstance(subject, SubjectPart)
    original = next(
        (
            node
            for node in topology(anchors.root)
            if isinstance(node, MethodNode)
            and node.signature.domain.kind == "entity"
            and node.signature.domain.instance_key == subject.subject_key
            and node.signature.domain.binding == anchors.root.signature.domain.binding
        ),
        None,
    )
    if original is None:
        fail("preparation_bounds", "the original source member envelope is not retained")
    member_sources = {node.identity for node in topology(original) if isinstance(node, SourceLeaf)}
    members = Relation(
        anchors.runtime,
        original,
        replace(
            anchors.binding,
            graph=replace(
                graph,
                root=original,
                sources=tuple(
                    (schema, leaf)
                    for schema, leaf in graph.sources
                    if leaf.identity in member_sources
                ),
            ),
        ),
    )
    start, end = datetime.fromisoformat(part.during_start), datetime.fromisoformat(part.during_end)
    if isinstance(window, ElapsedWindow):
        upper = deadline(end, window)
    else:
        # Every Python tzinfo offset is strictly within 24 hours. Therefore a
        # calendar-day deadline lies strictly below start + days + 48 hours.
        # This finite envelope also covers every transition between the bounds;
        # each actual wall-time deadline is independently round-trip checked.
        try:
            upper = end + timedelta(days=window.days, hours=48)
        except OverflowError:
            fail("preparation_bounds", "calendar candidate envelope overflows the instant range")
    envelope = time_scope(start=start.isoformat(), end=upper.isoformat())
    from marivo.analysis.materialization.graph_observation import (
        _observe_component,
        normalize_metric_input,
        observe_linear_members,
        observe_ratio_members,
    )
    from marivo.semantic.metric_graph import LinearNodeV1

    assert isinstance(members.binding, LiveBinding)
    live = members.binding
    metric_contract = normalize_metric_input(live.graph.registry, metric, sidecar=live.sidecar)
    root_definition = next(
        item.node
        for item in metric_contract.graph.nodes
        if item.node_id == metric_contract.graph.roots[0]
    )
    if isinstance(root_definition, LinearNodeV1):
        prototype_graph = observe_linear_members(
            live.graph,
            metric,
            metric=metric_contract,
            during=envelope,
            paths=paths,
            sidecar=live.sidecar,
            report_timezone=live.report_timezone,
            relative=True,
        )
    elif len(metric_contract.components) > 1:
        prototype_graph = observe_ratio_members(
            live.graph,
            metric,
            metric=metric_contract,
            during=envelope,
            paths=paths,
            sidecar=live.sidecar,
            report_timezone=live.report_timezone,
            relative=True,
        )
    else:
        if len(paths) != 1:
            fail("anchor_metric", "one route is required for one contribution root")
        prototype_graph = _observe_component(
            live.graph,
            metric,
            metric=metric_contract,
            during=envelope,
            via=paths[0],
            sidecar=live.sidecar,
            report_timezone=live.report_timezone,
            relative=True,
        )
    prototype = Relation(
        anchors.runtime, prototype_graph.root, replace(live, graph=prototype_graph)
    )
    observations = tuple(
        node.parameters
        for node in topology(prototype.root)
        if isinstance(node, MethodNode)
        and isinstance(node.parameters, (ObserveCount, ObserveMetric))
    )
    if not observations or any(
        isinstance(o, ObserveMetric)
        and (o.method != "sum" or o.cumulative is not None or o.fold is not None)
        for o in observations
    ):
        fail(
            "physical_qualification",
            "relative observation qualifies count and additive sum/ratio/linear components",
        )
    composition = (
        prototype.root.parameters
        if isinstance(prototype.root, MethodNode)
        and isinstance(prototype.root.parameters, (OriginalRatio, OccurrenceCombine))
        else None
    )
    assert isinstance(prototype.binding, LiveBinding)
    nodes: dict[str, Node] = {item.identity: item for item in topology(prototype.root)}
    for item in topology(anchors.root):
        if isinstance(item, MethodNode):
            nodes[item.identity] = replace(
                item,
                inputs=tuple(Edge(e.role, nodes[e.node.identity]) for e in item.inputs),
                sources=tuple(
                    n for s in item.sources if isinstance((n := nodes[s.identity]), SourceLeaf)
                ),
            )
        elif item.identity not in nodes:
            nodes[item.identity] = item
    anchor_root = nodes[anchors.root.identity]
    original_node = nodes[original.identity]
    sources = tuple(leaf for _, leaf in prototype.binding.graph.sources)
    node = method_node(
        (Edge("subject", anchor_root), Edge("subject", original_node)),
        AnchorObserve(
            window,
            observations,
            observation_template(original_node.signature, observations, composition),
            composition,
        ),
        value_type=prototype.root.value_type,
        sources=sources,
    )
    combined = {
        leaf.identity: (schema, leaf)
        for schema, leaf in (*graph.sources, *prototype.binding.graph.sources)
    }
    combined = {
        identity: (schema, n)
        for identity, (schema, _) in combined.items()
        if isinstance((n := nodes[identity]), SourceLeaf)
    }
    return Relation(
        anchors.runtime,
        node,
        replace(
            anchors.binding,
            graph=replace(prototype.binding.graph, root=node, sources=tuple(combined.values())),
        ),
    )
