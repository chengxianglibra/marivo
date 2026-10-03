"""Bind exact return inputs before any local Anchor consumption."""

from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256

from marivo._temporal import time_scope
from marivo.analysis.anchors import AnyAnchor, CalendarWindow, ElapsedWindow, EveryAnchor, deadline
from marivo.analysis.core.domain_captures import DomainPreparationError, fail
from marivo.analysis.core.graph import MethodNode, SourceLeaf, method_node, topology
from marivo.analysis.core.model import (
    AnchorDomainPart,
    DomainSignature,
    OccurrencePart,
    SubjectPart,
    require_part,
)
from marivo.analysis.core.rules import AnchorRetention, OccurrencePrepare, RetentionBySubject
from marivo.analysis.domains.completeness import CompletenessDeclaration
from marivo.analysis.event import EventPattern, step
from marivo.analysis.materialization.graph_journey import prepare
from marivo.analysis.materialization.graph_relation import LiveBinding, Relation
from marivo.analysis.methods.physical import ScalarType
from marivo.semantic.event import ParticipantRoleHandle


def bind(
    anchors: Relation,
    returning: ParticipantRoleHandle,
    window: ElapsedWindow | CalendarWindow,
    completeness: tuple[CompletenessDeclaration, ...],
) -> Relation:
    if not isinstance(anchors.binding, LiveBinding):
        raise DomainPreparationError(
            "r7.retention_inputs",
            "construction",
            "logical Anchors with a same-Subject returning Event capture",
            "a starts-only MaterializedAnchorDomain without captured return inputs",
            "Construct anchors.retention(returning, within=window, completeness=claims) before executing Anchors.",
        )
    if type(returning) is not ParticipantRoleHandle or type(window) not in (
        ElapsedWindow,
        CalendarWindow,
    ):
        fail("retention_binding", "use an exact ParticipantRoleHandle and closed relative window")
    part = require_part(anchors.root.signature, "anchor")
    assert isinstance(part, AnchorDomainPart)
    subject = require_part(anchors.root.signature, "subject")
    assert isinstance(subject, SubjectPart)
    original = next(
        (
            node.inputs[0].node
            for node in topology(anchors.root)
            if isinstance(node, MethodNode)
            and isinstance(node.parameters, OccurrencePrepare)
            and any(
                isinstance(captured, OccurrencePart)
                and captured.preparation_id == part.preparation.preparation_id
                for captured in node.signature.parts
            )
        ),
        None,
    )
    if not isinstance(original, MethodNode):
        fail("retention_binding", "the exact original population is required")
    live = anchors.binding
    source_ids = {node.identity for node in topology(original) if isinstance(node, SourceLeaf)}
    population = Relation(
        anchors.runtime,
        original,
        replace(
            live,
            graph=replace(
                live.graph,
                root=original,
                sources=tuple(
                    (schema, leaf)
                    for schema, leaf in live.graph.sources
                    if leaf.identity in source_ids
                ),
            ),
        ),
    )
    start, end = datetime.fromisoformat(part.during_start), datetime.fromisoformat(part.during_end)
    try:
        upper = (
            deadline(end, window)
            if isinstance(window, ElapsedWindow)
            else end + timedelta(days=window.days, hours=48)
        )
    except OverflowError:
        fail("window_overflow", "return preparation envelope overflows the captured instant range")
    from marivo.analysis.session.core import Session

    owner = Session._from_runtime(anchors.runtime)._sources()._owner
    capture = prepare(
        population,
        owner,
        EventPattern(steps=(step(participant=returning, key="return"),)),
        cohort_window=time_scope(start=start.isoformat(), end=upper.isoformat()),
        completion_through=upper,
        order_use="prepare",
        business_order=None if part.preparation.order is None else part.preparation.order.ref,
        completeness=completeness,
    )
    from marivo.analysis.methods.domain_coverage import coverage

    assert isinstance(capture.root, MethodNode) and isinstance(
        capture.root.parameters, OccurrencePrepare
    )
    coverage(capture.root.parameters)
    known = {node.identity: node for node in topology(anchors.root)}

    def source(identity: str) -> SourceLeaf:
        leaf = known[identity]
        assert isinstance(leaf, SourceLeaf)
        return leaf

    for candidate in topology(capture.root):
        prior = known.get(candidate.identity)
        if prior is not None:
            if prior.fingerprint != candidate.fingerprint:
                fail("retention_binding", "shared source or member input changed between captures")
            continue
        if isinstance(candidate, MethodNode):
            candidate = replace(
                candidate,
                inputs=tuple(
                    replace(edge, node=known[edge.node.identity]) for edge in candidate.inputs
                ),
                sources=tuple(source(leaf.identity) for leaf in candidate.sources),
            )
        known[candidate.identity] = candidate
    capture = replace(capture, root=known[capture.root.identity])
    node = method_node(
        (anchors._edge(), capture._edge()),
        AnchorRetention(window),
        value_type=ScalarType("boolean"),
    )
    assert isinstance(capture.binding, LiveBinding)
    sources = {
        leaf.identity: (schema, source(leaf.identity))
        for schema, leaf in (*live.graph.sources, *capture.binding.graph.sources)
    }
    return Relation(
        anchors.runtime,
        node,
        replace(live, graph=replace(live.graph, root=node, sources=tuple(sources.values()))),
    )


def by_subject(result: Relation, rule: AnyAnchor | EveryAnchor) -> Relation:
    if type(rule) not in (AnyAnchor, EveryAnchor):
        raise DomainPreparationError(
            "r7.retention_rule",
            "construction",
            "an explicit AnyAnchor or EveryAnchor rule",
            repr(rule),
            "Pass rule=mv.any_anchor() or rule=mv.every_anchor() to retention.by_subject().",
        )
    subject = require_part(result.root.signature, "subject")
    assert isinstance(subject, SubjectPart)
    keys = subject.subject_key
    identity = sha256(repr((result.definition.fingerprint, rule)).encode()).hexdigest()
    output = DomainSignature(result.root.signature.domain.binding, "entity", keys, keys, identity)
    return result._with(
        method_node(
            (result._edge(),), RetentionBySubject(output, rule), value_type=ScalarType("boolean")
        )
    )
