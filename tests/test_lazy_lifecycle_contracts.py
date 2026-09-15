"""Source-free Lifecycle construction and canonical schema contracts."""

from __future__ import annotations

import json

import pytest

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.lifecycle import ROLES, LifecyclePayload, LogicalLifecycleDataset
from marivo.analysis.materialization.contracts import _semantics, _semantics_payload
from marivo.analysis.observation.contracts import producer_contract
from tests.lazy_lifecycle_fixtures import history, sources_without_io


def test_source_free_history_contract() -> None:
    dataset = history(sources_without_io())
    assert isinstance(dataset, LogicalLifecycleDataset)
    assert isinstance(dataset._root, LogicalRootHandle)
    assert isinstance(dataset._root.payload, LifecyclePayload)
    assert tuple(f.name for f in dataset.schema.columns) == (
        "entity_identity",
        "model_state",
        "valid_from",
        "valid_to",
        "entered_by_event_ref",
        "entered_by_event_identity",
        "exited_by_event_ref",
        "exited_by_event_identity",
        "interval_status",
        "left_clipped",
    )
    assert producer_contract("session.lifecycle.replay").retained_contract_ids == ROLES
    from marivo.analysis.datasets.errors import DatasetConstructionError

    with pytest.raises(DatasetConstructionError):
        dataset.where()
    assert callable(dataset.distribution)
    assert "\n" not in repr(dataset)
    semantics = dataset.row_contract.family_semantics
    assert _semantics(json.loads(json.dumps(_semantics_payload(semantics)))) == semantics


def test_execute_is_explicit() -> None:
    with pytest.raises(AssertionError, match="Lifecycle execution"):
        history(sources_without_io()).execute()


@pytest.mark.parametrize(
    "case",
    [
        "unloaded_model",
        "invalid_seed",
        "bounded_coverage",
        "foreign_session",
        "wrong_subject",
        "reduced_metric",
    ],
)
def test_invalid_replay_authority_is_rejected_before_io(case: str) -> None:
    from marivo.analysis import time_scope
    from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetOwnershipError
    from marivo.analysis.domains.completeness import BoundedCompletenessDeclarationV1
    from marivo.analysis.lifecycle import FromInception
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from marivo.refs import ref
    from tests.lazy_lifecycle_fixtures import END, MODEL, START
    from tests.lazy_observation_fixtures import NoIoActionPort

    sources = sources_without_io()
    from marivo.analysis.domains.subject import PopulationInput

    population: PopulationInput | None = None
    if case == "foreign_session":
        foreign = make_lazy_sources(
            semantic_registry=sources._owner.semantic_registry,
            sidecar=sources._owner.sidecar,
            action_port=NoIoActionPort(),
            session_id="foreign",
            store_id="lifecycle",
        )
        population = foreign.population(ref.entity("sales.customers"))
    elif case == "wrong_subject":
        population = sources.population(ref.entity("sales.orders"))
    elif case == "reduced_metric":
        population = sources.observe(
            ref.metric("sales.revenue"),
            population=sources.population(ref.entity("sales.customers")),
        ).aggregate()
    with pytest.raises((DatasetConstructionError, DatasetOwnershipError)):
        sources.lifecycle.replay(
            ref.state_model("sales.unloaded") if case == "unloaded_model" else MODEL,
            window=time_scope(start=START.isoformat(), end=END.isoformat()),
            seed=FromInception.model_construct(kind="invalid")
            if case == "invalid_seed"
            else FromInception(),
            population=population,
            completeness=(
                BoundedCompletenessDeclarationV1(
                    inputs=(ref.event("sales.started"), ref.event("sales.finished")),
                    complete_from=START,
                    complete_through=END,
                    rationale="Bounded coverage cannot seed inception.",
                ),
            )
            if case == "bounded_coverage"
            else (),
        )


@pytest.mark.parametrize(
    "case",
    [
        "missing_initial",
        "multiple_initial",
        "missing_inception",
        "nondeterministic",
        "different_subject",
    ],
)
def test_invalid_state_model_is_rejected(case: str) -> None:
    from dataclasses import replace

    from marivo.analysis.domains.lifecycle import LifecycleConstructionError
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from tests.lazy_lifecycle_fixtures import MODEL
    from tests.lazy_observation_fixtures import NoIoActionPort

    sources = sources_without_io()
    owner = sources._owner
    model = owner.semantic_registry.state_models[MODEL.path]
    if case == "missing_initial":
        model = replace(
            model, states=tuple(replace(state, initial=False) for state in model.states)
        )
    elif case == "multiple_initial":
        model = replace(model, states=tuple(replace(state, initial=True) for state in model.states))
    elif case == "missing_inception":
        model = replace(model, inceptions=())
    elif case == "nondeterministic":
        model = replace(
            model, transitions=(*model.transitions, replace(model.transitions[0], to_state="open"))
        )
    else:
        model = replace(model, subject="sales.orders")
    registry = replace(owner.semantic_registry, state_models={MODEL.path: model})
    registry.freeze()
    other = make_lazy_sources(
        semantic_registry=registry,
        sidecar=owner.sidecar,
        action_port=NoIoActionPort(),
        session_id="lifecycle",
        store_id="lifecycle",
    )
    with pytest.raises(LifecycleConstructionError):
        history(other)


def test_versioned_subject_requires_its_own_explicit_population_scope() -> None:
    from dataclasses import replace

    from marivo.analysis import time_scope
    from marivo.analysis.domains.errors import EventConstructionError
    from marivo.analysis.lifecycle import FromInception
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from marivo.refs import ref
    from tests.lazy_lifecycle_fixtures import END, MODEL, START
    from tests.lazy_observation_fixtures import NoIoActionPort

    sources = sources_without_io()
    owner = sources._owner
    original = owner.semantic_registry
    relationships = {
        **original.relationships,
        **{
            f"sales.{name}_customer": replace(
                original.relationships[f"sales.{name}_customer"],
                keys=(
                    replace(
                        original.relationships[f"sales.{name}_customer"].keys[0],
                        to_key="sales.snapshots.id",
                    ),
                ),
                to_entity="sales.snapshots",
            )
            for name in ("started", "finished")
        },
    }
    model = replace(original.state_models[MODEL.path], subject="sales.snapshots")
    registry = replace(original, relationships=relationships, state_models={MODEL.path: model})
    registry.freeze()
    other = make_lazy_sources(
        semantic_registry=registry,
        sidecar=owner.sidecar,
        action_port=NoIoActionPort(),
        session_id="lifecycle",
        store_id="lifecycle",
    )
    with pytest.raises(EventConstructionError, match="versioned"):
        history(other)
    scope = time_scope(start="2026-01-01", end="2026-02-01")
    membership = other.population(ref.entity("sales.snapshots"), time_scope=scope)
    result = other.lifecycle.replay(
        MODEL,
        window=time_scope(start=START.isoformat(), end=END.isoformat()),
        seed=FromInception(),
        population=membership,
    )
    assert isinstance(result._root, LogicalRootHandle)
    assert result._root.inputs[0].root is membership._root
    assert isinstance(result._root.payload, LifecyclePayload)
    assert result._root.payload.definition.cohort_window != scope


def test_composite_subject_key_order_is_preserved() -> None:
    from dataclasses import replace

    from marivo.analysis.datasets import descriptors as d
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from marivo.datasource.ir import TableColumnBindingIR, TableSourceIR
    from marivo.semantic.ir import JoinKey
    from tests.lazy_lifecycle_fixtures import MODEL
    from tests.lazy_observation_fixtures import NoIoActionPort

    sources = sources_without_io()
    original = sources._owner.semantic_registry
    entities = dict(original.entities)
    dimensions = dict(original.dimensions)
    relationships = dict(original.relationships)
    for name in ("started", "finished"):
        entity = entities[f"sales.{name}_rows"]
        assert isinstance(entity.source, TableSourceIR)
        entities[entity.semantic_id] = replace(
            entity,
            source=replace(
                entity.source,
                columns=(
                    *entity.source.columns,
                    ("tenant", TableColumnBindingIR("tenant", "string")),
                ),
            ),
        )
        tenant_path = f"sales.{name}_rows.tenant"
        dimensions[tenant_path] = replace(
            original.dimensions["sales.composite.tenant"],
            semantic_id=tenant_path,
            entity=f"sales.{name}_rows",
        )
        relationship = relationships[f"sales.{name}_customer"]
        relationships[relationship.semantic_id] = replace(
            relationship,
            to_entity="sales.composite",
            keys=(
                JoinKey(f"sales.{name}_rows.tenant", "sales.composite.tenant"),
                JoinKey(f"sales.{name}_rows.customer_id", "sales.composite.id"),
            ),
        )
    model = replace(original.state_models[MODEL.path], subject="sales.composite")
    registry = replace(
        original,
        entities=entities,
        dimensions=dimensions,
        relationships=relationships,
        state_models={MODEL.path: model},
    )
    registry.freeze()
    other = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sources._owner.sidecar,
        action_port=NoIoActionPort(),
        session_id="lifecycle",
        store_id="lifecycle",
    )
    identity = history(other).schema.columns[0].identity
    assert isinstance(identity, d._EntityFieldIdentity)
    assert identity.identity_signature == (("tenant", "string"), ("id", "int64"))


@pytest.mark.parametrize("field", ["initial", "seed_fingerprint"])
def test_retained_model_validation_uses_integrity_errors(field: str) -> None:
    from marivo.analysis.materialization.errors import IntegrityError

    semantics = history(sources_without_io()).row_contract.family_semantics
    payload = json.loads(json.dumps(_semantics_payload(semantics)))
    payload[field] = "invalid-retained-authority"
    with pytest.raises(IntegrityError):
        _semantics(payload)
