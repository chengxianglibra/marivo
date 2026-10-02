"""History construction binds full members and each distinct modeled trigger."""

from dataclasses import replace
from datetime import datetime

from marivo._temporal import TimeScope
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import Edge, MethodNode, method_node
from marivo.analysis.core.history_types import Distribution, HistoryRequest
from marivo.analysis.core.rules import HistoryReplay
from marivo.analysis.domains.completeness import CompletenessDeclaration
from marivo.analysis.event import sequence, step
from marivo.analysis.lifecycle import FromInception
from marivo.analysis.materialization.graph_journey import prepare
from marivo.analysis.materialization.graph_observation import _window_bounds
from marivo.analysis.materialization.graph_relation import LiveBinding, Relation
from marivo.analysis.methods.physical import ScalarType
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.refs import DimensionKind, Ref, SemanticKind, StateModelKind, ref
from marivo.semantic.event import participant_role


def construct(
    population: Relation,
    owner: ObservationOwner,
    model: Ref[StateModelKind],
    *,
    window: TimeScope,
    seed: FromInception,
    completeness: tuple[CompletenessDeclaration, ...],
) -> Relation:
    if type(model) is not Ref or model.kind is not SemanticKind.STATE_MODEL:
        fail("history_model", "replay requires an exact StateModel Ref")
    definition = owner.semantic_registry.state_models.get(model.path)
    if definition is None or model not in owner.sidecar.catalog_refs:
        fail("history_model", "StateModel is not loaded in this Session")
    if type(seed) is not FromInception or seed != FromInception():
        fail("history_seed", "replay requires from_inception()")
    if not isinstance(population.binding, LiveBinding):
        fail("input_mode", "replay requires one logical Subject domain")
    triggers = tuple(
        dict.fromkeys(
            (
                *(
                    (item.trigger.event_ref, item.trigger.participant_role)
                    for item in definition.inceptions
                ),
                *(
                    (item.trigger.event_ref, item.trigger.participant_role)
                    for item in definition.transitions
                ),
            )
        )
    )
    pattern = sequence(
        *(
            step(
                participant=participant_role(event=ref.event(event), name=role),
                key=f"trigger_{index}",
            )
            for index, (event, role) in enumerate(triggers)
        )
    )
    start, end = _window_bounds(window, population.binding.report_timezone)
    assert start is not None and end is not None
    captured = prepare(
        population,
        owner,
        pattern,
        cohort_window=window,
        completion_through=datetime.fromisoformat(end),
        business_order=None
        if definition.business_order is None
        else ref.business_order(definition.business_order),
        completeness=completeness,
        order_use="prepare",
        model_ref=model,
    )
    assert isinstance(captured.binding, LiveBinding) and isinstance(captured.root, MethodNode)
    members = captured.root.inputs[0].node
    node = method_node(
        (Edge("subject", members), Edge("subject", captured.root)),
        HistoryReplay(members.signature.domain, start, end),
        value_type=ScalarType("int64"),
    )
    return Relation(
        population.runtime,
        node,
        replace(captured.binding, graph=replace(captured.binding.graph, root=node)),
    )


def view(
    relation: Relation, request: HistoryRequest, prepared: MethodNode | None = None
) -> Relation:
    """Bind one exact named projection to the retained canonical History."""
    from hashlib import sha256

    from marivo.analysis.core.history_types import (
        Distribution,
        Dwell,
        Intervals,
        StateAt,
        Transitions,
    )
    from marivo.analysis.core.model import Coordinate, DomainSignature, HistoryPart, require_part
    from marivo.analysis.core.rules import HistoryView
    from marivo.analysis.methods.history_view_physical import output_type

    history = require_part(relation.root.signature, "history")
    assert isinstance(history, HistoryPart)
    model = history.preparation.model
    assert model is not None
    subject = ref.entity(model.triggers[0].subject.ref.path)
    domain = relation.root.signature.domain
    if isinstance(request, StateAt):
        output = domain
    else:
        coordinates = (
            (
                Coordinate(subject, "history:checkpoint", "group"),
                Coordinate(subject, "history:state", "group"),
                *(
                    Coordinate(subject, "history:axis:" + axis.dimension.ref.path, "group")
                    for axis in request.axes
                ),
            )
            if isinstance(request, Distribution)
            else (Coordinate(subject, "history:state", "group"),)
            if isinstance(request, Dwell)
            else (
                Coordinate(subject, "history:from", "group"),
                Coordinate(subject, "history:to", "group"),
            )
            if isinstance(request, Transitions)
            else (*domain.instance_key, Coordinate(subject, "history:interval", "instance"))
            if isinstance(request, Intervals)
            else history.capture_domain.instance_key
        )
        output = DomainSignature(
            domain.binding,
            "group"
            if isinstance(request, (Distribution, Dwell, Transitions))
            else "interval"
            if isinstance(request, Intervals)
            else "occurrence",
            coordinates,
            coordinates,
            sha256(repr((domain.definition_id, request)).encode()).hexdigest(),
        )
    edges: tuple[Edge, ...] = (relation._edge(),)
    if prepared is not None:
        edges += (Edge("subject", prepared),)
    params = HistoryView(output, request)
    return relation._with(method_node(edges, params, value_type=output_type(params)))


def distribution(
    relation: Relation, request: Distribution, axes: tuple[Ref[DimensionKind], ...]
) -> Relation:
    from marivo.analysis.core.model import HistoryPart, require_part
    from marivo.analysis.core.rules import HistoryAxesPrepare, HistoryReplay
    from marivo.analysis.materialization.graph_axes import capture_axes
    from marivo.refs import SemanticKind

    if (
        type(axes) is not tuple
        or len(set(axes)) != len(axes)
        or any(type(a) is not Ref or a.kind is not SemanticKind.DIMENSION for a in axes)
    ):
        fail("history_axes", "axes require an ordered tuple of unique exact Dimensions")
    if not axes:
        return view(relation, request)
    if not isinstance(relation.binding, LiveBinding):
        fail(
            "history_axes",
            "fixed History lacks checkpoint axes; execute grouped distribution before materializing",
        )
    history = require_part(relation.root.signature, "history")
    assert isinstance(history, HistoryPart) and history.preparation.model is not None
    definition = relation.definition
    assert isinstance(definition.parameters, HistoryReplay)
    subject = history.preparation.model.triggers[0].subject
    relation, captures, sources = capture_axes(
        relation, axes, subject, history.binding, history.preparation.model.triggers[0].source_id
    )
    request = replace(request, axes=captures)
    prepared = method_node(
        (Edge("subject", definition.inputs[0].node),),
        HistoryAxesPrepare(history, request),
        sources=sources,
        value_type=ScalarType("int64"),
    )
    return view(relation, request, prepared)
