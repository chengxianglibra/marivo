"""Small governed Lifecycle model and source setup for private acceptance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marivo.analysis import time_scope
from marivo.analysis.domains.completeness import (
    BoundedCoverageStartV1,
    EventCoverageReceiptV1,
    EventCoverageRequestV1,
    SourceOriginCompletenessDeclarationV1,
    SourceOriginCoverageStartV1,
)
from marivo.analysis.domains.lifecycle import LogicalLifecycleDataset
from marivo.analysis.domains.subject import PopulationInput
from marivo.analysis.lifecycle import FromInception
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import AiContextIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import (
    LifecycleStateIR,
    SourceLocation,
    StateInceptionIR,
    StateModelIR,
    StateTransitionIR,
    StateTriggerIR,
)
from marivo.semantic.validator import Registry
from tests.lazy_event_fixtures import make_event_registry
from tests.lazy_event_runtime_fixtures import setup_event
from tests.lazy_observation_fixtures import NoIoActionPort

START = datetime(2026, 2, 1, tzinfo=timezone.utc)
END = datetime(2026, 2, 2, tzinfo=timezone.utc)
MODEL = ref.state_model("sales.purchase")


def lifecycle_registry(database: Path) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = make_event_registry(database)
    model = StateModelIR(
        "sales.purchase",
        "sales",
        "purchase",
        "sales.customers",
        (LifecycleStateIR("open", True, False), LifecycleStateIR("done", False, True)),
        (StateInceptionIR(StateTriggerIR("sales.started", "buyer")),),
        (StateTransitionIR("open", StateTriggerIR("sales.finished", "buyer"), "done"),),
        AiContextIR(),
        "purchase",
        SourceLocation("lazy_lifecycle_fixture.py", 1),
    )
    registry = replace(registry, state_models={model.semantic_id: model})
    registry.freeze()
    return registry, replace(sidecar, catalog_refs=sidecar.catalog_refs | {MODEL})


def sources_without_io() -> LazySources:
    registry, sidecar = lifecycle_registry(Path("/nonexistent/lifecycle.duckdb"))
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="lifecycle",
        store_id="lifecycle",
    )


def setup_lifecycle(
    project: Path, *, engine: bool = False, event: Callable[[str], None] | None = None
) -> tuple[DatasetRuntime, LazySources, Path]:
    runtime, _, database = setup_event(project, engine=engine, event=event)
    registry, sidecar = lifecycle_registry(database)
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database


def history(
    sources: LazySources,
    *,
    complete: bool = True,
    population: PopulationInput | None = None,
    window=None,
) -> LogicalLifecycleDataset:
    """Build only the legacy pure declaration demanded by internal R7.6 reducers.

    This fixture cannot compile, execute or recover a History producer.
    """
    import json
    from dataclasses import asdict

    from marivo.analysis.datasets import descriptors as d
    from marivo.analysis.datasets.base import _make_logical_dataset
    from marivo.analysis.datasets.handles import LogicalRootHandle
    from marivo.analysis.datasets.registry import DatasetFamilyRegistry
    from marivo.analysis.domains.contracts import journey_semantics
    from marivo.analysis.domains.event import EventPayload, make_match
    from marivo.analysis.domains.lifecycle import (
        LifecyclePayload,
        LifecycleSemantics,
        history_contracts,
        register_lifecycle,
    )
    from marivo.analysis.event import first_per_subject, sequence, step
    from marivo.analysis.observation.contracts import producer_contract
    from marivo.semantic.event import participant_role

    owner = sources._owner
    registry = DatasetFamilyRegistry()
    for registration in sources._registry.registrations:
        registry.register(registration)
    register_lifecycle(registry, sources._registry.get("event").ids)
    registry.freeze()
    model_ref = MODEL
    model_ir = owner.semantic_registry.state_models[MODEL.path]
    states = tuple(state.name for state in model_ir.states)
    initials = tuple(state.name for state in model_ir.states if state.initial)
    window = window or time_scope(start=START.isoformat(), end=END.isoformat())
    seed = FromInception()
    completeness = (
        (
            SourceOriginCompletenessDeclarationV1(
                inputs=(ref.event("sales.started"), ref.event("sales.finished")),
                source_origin_ref=ref.datasource("warehouse"),
                complete_through=window.end,
                rationale="Internal reducer fixture origin.",
            ),
        )
        if complete
        else ()
    )
    triggers = tuple(
        dict.fromkeys(
            [item.trigger for item in model_ir.inceptions]
            + [item.trigger for item in model_ir.transitions]
        )
    )
    keys = {trigger: f"trigger_{index}" for index, trigger in enumerate(triggers)}
    pattern = sequence(
        *(
            step(
                participant=participant_role(
                    event=ref.event(trigger.event_ref), name=trigger.participant_role
                ),
                key=keys[trigger],
            )
            for trigger in triggers
        )
    )
    event = make_match(
        owner,
        registry,
        pattern,
        cohort_window=window,
        completion_through=window.end,
        matching=first_per_subject(),
        population=population,
        completeness=completeness,
    )
    assert isinstance(event._root, LogicalRootHandle) and isinstance(
        event._root.payload, EventPayload
    )
    source = event._root.payload
    if source.definition.entity.ref.path != model_ir.subject:
        raise ValueError(
            "all triggers at the exact StateModel subject", "different trigger subject"
        )
    transitions = tuple(
        (item.from_state, keys[item.trigger], item.to_state) for item in model_ir.transitions
    )
    if len({(a, b) for a, b, _ in transitions}) != len(transitions) or any(
        a not in states or c not in states for a, _, c in transitions
    ):
        raise ValueError(
            "deterministic transitions between declared states", "invalid transition rules"
        )
    semantics = LifecycleSemantics(
        _token=d._CORE_TOKEN,
        source_json=json.dumps(asdict(journey_semantics(source.definition)), sort_keys=True),
        model_ref=model_ref.path,
        states=states,
        initial=initials[0],
        terminals=tuple(state.name for state in model_ir.states if state.terminal),
        inceptions=tuple(keys[item.trigger] for item in model_ir.inceptions),
        transitions=transitions,
        seed_fingerprint=seed.fingerprint,
    )
    payload = LifecyclePayload(
        _token=d._CORE_TOKEN,
        definition=source.definition,
        semantics=semantics,
        captures=source.captures,
    )
    row, rows = history_contracts(payload, registry.get("lifecycle").ids)
    result = _make_logical_dataset(
        owner=owner,
        registry=registry,
        family_id="lifecycle",
        operator_id="session.lifecycle.replay",
        inputs=event._inputs,
        input_roles=("population",),
        row_contract=row,
        row_set_contract=rows,
        payload=payload,
        requirements=("lifecycle.exact_subject_membership@v1", "lifecycle.native_replay@v1"),
        dependency_facts=(
            model_ref.key,
            *(item.step.event.key for item in source.definition.steps),
        ),
        contract_versions=producer_contract("session.lifecycle.replay").versions,
    )
    assert isinstance(result, LogicalLifecycleDataset)
    return result


def receipt(
    request: EventCoverageRequestV1, *, bounded: bool = False, prefix: bool = False
) -> EventCoverageReceiptV1:
    return EventCoverageReceiptV1(
        event_ref=request.event_ref,
        event_fingerprint=request.event_fingerprint,
        source_entity_ref=request.source_entity_ref,
        source_origin_ref=request.source_origin_ref,
        occurred_at_ref=request.occurred_at_ref,
        coverage_start=BoundedCoverageStartV1(complete_from=START - timedelta(days=100))
        if bounded
        else SourceOriginCoverageStartV1(source_origin_ref=request.source_origin_ref),
        complete_through=START + timedelta(hours=4) if prefix else END,
        authority="fixture.origin@v1",
        observed_at=END,
        source_binding_fingerprint=request.source_binding_fingerprint,
        execution_domain_id=request.execution_domain_id,
    )
