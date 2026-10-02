"""History construction binds full members and each distinct modeled trigger."""

from dataclasses import replace
from datetime import datetime

from marivo._temporal import TimeScope
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import Edge, MethodNode, method_node
from marivo.analysis.core.rules import HistoryReplay
from marivo.analysis.domains.completeness import CompletenessDeclaration
from marivo.analysis.event import sequence, step
from marivo.analysis.lifecycle import FromInception
from marivo.analysis.materialization.graph_journey import prepare
from marivo.analysis.materialization.graph_observation import _window_bounds
from marivo.analysis.materialization.graph_relation import LiveBinding, Relation
from marivo.analysis.methods.physical import ScalarType
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.refs import Ref, SemanticKind, StateModelKind, ref
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
