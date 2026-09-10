"""Source-free Slice 7b authoring and exact retained reducer contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from marivo.analysis import time_scope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventFunnelSemantics,
    EventSelectionPayload,
    EventTimeToEventSemantics,
)
from marivo.analysis.domains.event import LogicalEventDataset, _validate_event
from marivo.analysis.event import EventPattern, every_start, first_per_subject, sequence, step
from marivo.analysis.observation.population import LogicalPopulationDataset
from marivo.analysis.observation.predicates import eq, gt, is_null
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.analysis.subject import dropped_before
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.event import participant_role
from tests.lazy_event_fixtures import make_event_sources
from tests.lazy_observation_fixtures import NoIoActionPort


@pytest.fixture
def journey() -> LogicalEventDataset:
    sources = make_event_sources()
    pattern = sequence(
        *(
            step(
                participant=participant_role(event=ref.event(f"sales.{name}"), name="buyer"),
                key=name,
            )
            for name in ("started", "finished")
        )
    )
    return sources.events.match(
        pattern,
        cohort_window=time_scope(
            start="2026-02-01T00:00:00+00:00", end="2026-03-01T00:00:00+00:00"
        ),
        completion_through=datetime(2026, 3, 2, tzinfo=timezone.utc),
        matching=first_per_subject(),
    )


def _pattern(journey: LogicalEventDataset) -> EventPattern:
    root = journey._root
    assert isinstance(root, LogicalRootHandle)
    from marivo.analysis.domains.contracts import EventPayload

    assert isinstance(root.payload, EventPayload)
    return root.payload.definition.pattern


def test_source_free_reducers_preserve_input_and_exact_closed_shapes(
    journey: LogicalEventDataset,
) -> None:
    pattern = _pattern(journey)
    funnel = journey.funnel()
    duration = journey.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    selected = journey.select_subjects(dropped_before(step=pattern.steps[1]))
    assert tuple(field.name for field in funnel.schema.columns) == (
        "step_key",
        "cohort_count",
        "resolved_cohort_count",
        "entry_count",
        "resolved_entry_count",
        "reached_count",
        "lost_count",
        "conversion_from_first",
        "conversion_from_previous",
        "loss_rate_from_previous",
        "coverage_censored_count",
    )
    assert tuple(field.name for field in duration.schema.columns) == (
        "journey_id",
        "entity_identity",
        "from_event_identity",
        "from_time",
        "to_event_identity",
        "to_time",
        "duration",
        "followup_until",
        "observed_duration",
        "completion_status",
    )
    assert isinstance(selected, LogicalPopulationDataset)
    assert tuple(field.name for field in selected.schema.columns) == ("entity_identity",)
    assert selected.row_contract.family_semantics.kind == "complete_from_schema"
    for result in (funnel, duration, selected):
        assert result._inputs == (journey,)
        assert isinstance(result._root, LogicalRootHandle)
        with pytest.raises(AssertionError, match="execution"):
            result.execute()
    assert isinstance(selected._root, LogicalRootHandle)
    assert isinstance(selected._root.payload, EventSelectionPayload)
    assert selected._root.payload.selection == dropped_before(step=pattern.steps[1])


def test_equal_recovered_steps_are_admitted_but_foreign_same_key_is_rejected(
    journey: LogicalEventDataset,
) -> None:
    pattern = _pattern(journey)
    copied = EventPattern.model_validate_json(pattern.model_dump_json())
    original = journey.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    equal = journey.time_to_event(from_step=copied.steps[0], to_step=copied.steps[1])
    assert original.definition_fingerprint == equal.definition_fingerprint
    assert (
        journey.select_subjects(dropped_before(step=copied.steps[1])).definition_fingerprint
        == journey.select_subjects(dropped_before(step=pattern.steps[1])).definition_fingerprint
    )
    foreign = step(
        participant=participant_role(event=ref.event("sales.finished"), name="seller"),
        key="finished",
    )
    with pytest.raises(DatasetConstructionError, match="foreign or repeated"):
        journey.time_to_event(from_step=pattern.steps[0], to_step=foreign)
    with pytest.raises(DatasetConstructionError, match="foreign or repeated"):
        journey.select_subjects(dropped_before(step=foreign))
    with pytest.raises(DatasetConstructionError, match="unordered"):
        journey.time_to_event(from_step=pattern.steps[1], to_step=pattern.steps[0])
    with pytest.raises(DatasetConstructionError, match="initial"):
        journey.select_subjects(dropped_before(step=pattern.steps[0]))


def test_every_start_only_admits_attempt_duration(journey: LogicalEventDataset) -> None:
    sources = make_event_sources()
    pattern = _pattern(journey)
    repeated = sources.events.match(
        pattern,
        cohort_window=time_scope(
            start="2026-02-01T00:00:00+00:00", end="2026-03-01T00:00:00+00:00"
        ),
        completion_through=datetime(2026, 3, 2, tzinfo=timezone.utc),
        matching=every_start(completion_assignment="exclusive"),
    )
    repeated.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    with pytest.raises(DatasetConstructionError, match="every_start"):
        repeated.funnel()
    with pytest.raises(DatasetConstructionError, match="every_start"):
        repeated.select_subjects(dropped_before(step=pattern.steps[1]))


def test_grouped_axis_binding_is_exact_and_rejects_collisions(journey: LogicalEventDataset) -> None:
    region = ref.dimension("sales.customers.region")
    grouped = journey.funnel(axes=(region,))
    assert grouped.schema.columns[0].name == "region"
    assert grouped.schema.columns[0].role_id == "dimension"
    assert isinstance(grouped._root, LogicalRootHandle)
    assert isinstance(grouped._root.payload, EventFunnelPayload)
    binding = grouped._root.payload.axes[0]
    assert binding.path == ()
    assert binding.subject.ref.path == "sales.customers"
    assert binding.dimension.ref.path == region.path
    semantics = grouped.row_contract.family_semantics
    assert isinstance(semantics, EventFunnelSemantics)
    assert semantics.axis_dependency_fingerprints == (binding.dependency_fingerprint,)
    with pytest.raises(DatasetConstructionError, match="duplicate"):
        journey.funnel(axes=(region, region))
    with pytest.raises(DatasetConstructionError):
        journey.funnel(axes=(ref.dimension("sales.orders.channel"),))


@pytest.mark.parametrize(
    "name",
    (
        "journey_id",
        "entity_identity",
        "occurred_at",
        "event_identity",
        "completion_status",
        "elapsed_from_start",
        "elapsed_from_previous",
    ),
)
def test_axis_names_cannot_collide_with_journey_or_native_reducer_columns(
    journey: LogicalEventDataset, name: str
) -> None:
    original = make_event_sources()._owner
    current = ref.dimension("sales.customers.region")
    aliased = ref.dimension("sales.customers." + name)
    registry = replace(
        original.semantic_registry,
        dimensions={
            **original.semantic_registry.dimensions,
            aliased.path: replace(
                original.semantic_registry.dimensions[current.path],
                semantic_id=aliased.path,
                name=name,
            ),
        },
    )
    registry.freeze()
    sidecar = CompiledExpressionSidecar(
        bodies={**original.sidecar.bodies, aliased: original.sidecar.bodies[current]},
        field_owners={**original.sidecar.field_owners, aliased: ref.entity("sales.customers")},
        catalog_refs=original.sidecar.catalog_refs | {aliased},
    )
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id=original.session_id,
        store_id=original.store_id,
    )
    receiver = sources.events.match(
        _pattern(journey),
        cohort_window=time_scope(
            start="2026-02-01T00:00:00+00:00", end="2026-03-01T00:00:00+00:00"
        ),
        completion_through=datetime(2026, 3, 2, tzinfo=timezone.utc),
        matching=first_per_subject(),
    )
    with pytest.raises(DatasetConstructionError, match="colliding"):
        receiver.funnel(axes=(aliased,))


@pytest.mark.parametrize("name", ("__unknown", "__axis_subject", "__rows", "__preceding_reached"))
def test_retained_axis_name_cannot_forge_native_scratch_columns(
    journey: LogicalEventDataset, name: str
) -> None:
    with pytest.raises(ValueError, match="lowercase snake_case"):
        ref.dimension("sales.customers." + name)
    funnel = journey.funnel(axes=(ref.dimension("sales.customers.region"),))
    axis = replace(funnel.schema.columns[0], _token=d._CORE_TOKEN, name=name)
    row = replace(
        funnel.row_contract,
        _token=d._CORE_TOKEN,
        schema=d._make_schema((axis, *funnel.schema.columns[1:])),
    )
    with pytest.raises(DatasetConstructionError, match="invalid funnel semantics"):
        _validate_event(row, funnel.row_set_contract)


def test_summary_filter_matrix_is_bound_locally(journey: LogicalEventDataset) -> None:
    pattern = _pattern(journey)
    funnel = journey.funnel()
    funnel.where(gt(funnel.fields.get("lost_count"), 0))
    funnel.where(is_null(funnel.fields.get("conversion_from_first")))
    funnel.where(eq(funnel.fields.get("step_key"), "finished"))
    duration = journey.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    duration.where(is_null(duration.fields.get("duration")))
    duration.where(eq(duration.fields.get("completion_status"), "complete"))
    duration.where(gt(duration.fields.get("from_time"), datetime(2026, 2, 1, tzinfo=timezone.utc)))
    with pytest.raises(DatasetConstructionError, match="structural journey"):
        journey.where(eq(journey.fields.get("step_key"), "started"))
    for name in ("journey_id", "entity_identity", "from_event_identity", "to_event_identity"):
        with pytest.raises(DatasetConstructionError, match="identity or structural"):
            duration.where(eq(duration.fields.get(name), "opaque"))
    with pytest.raises(DatasetConstructionError):
        duration.where(gt(duration.fields.get("duration"), 10))
    with pytest.raises(DatasetConstructionError):
        duration.where(gt(duration.fields.get("completion_status"), "complete"))


def test_tte_sorting_uses_only_retained_pair_coordinates(journey: LogicalEventDataset) -> None:
    pattern = _pattern(journey)
    duration = journey.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1])
    assert isinstance(duration.row_contract.family_semantics, EventTimeToEventSemantics)
    order = duration.row_set_contract.ordering
    assert isinstance(order, d._OrderedOrdering)
    names = {field.field_id: field.name for field in duration.schema.columns}
    assert tuple(names[term.field_id] for term in order.terms) == (
        "entity_identity",
        "from_time",
        "from_event_identity",
        "journey_id",
    )
    assert all(term.nulls == "last" for term in order.terms)
    changed = replace(order, _token=d._CORE_TOKEN, terms=tuple(reversed(order.terms)))
    with pytest.raises(DatasetConstructionError, match="canonical ordering"):
        _validate_event(
            duration.row_contract,
            replace(duration.row_set_contract, _token=d._CORE_TOKEN, ordering=changed),
        )


def test_reducer_validator_rejects_generated_contract_drift(journey: LogicalEventDataset) -> None:
    funnel = journey.funnel()
    field = funnel.schema.columns[1]
    changed = replace(field, _token=d._CORE_TOKEN, nullable=True)
    row = replace(
        funnel.row_contract,
        _token=d._CORE_TOKEN,
        schema=d._make_schema((funnel.schema.columns[0], changed, *funnel.schema.columns[2:])),
    )
    with pytest.raises(DatasetConstructionError, match="changed reducer field"):
        _validate_event(row, funnel.row_set_contract)
    with pytest.raises(DatasetConstructionError, match="non-journey"):
        funnel.funnel()
