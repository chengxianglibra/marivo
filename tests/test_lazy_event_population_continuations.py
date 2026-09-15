"""Complete selected membership keeps shared pure filtering and sampling rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from marivo.analysis import time_scope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.base import _make_materialized_dataset
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.datasets.state import _materialized_state
from marivo.analysis.domains.contracts import EventFunnelPayload
from marivo.analysis.event import first_per_subject, sequence, step
from marivo.analysis.observation.contracts import (
    ObservationOwner,
    ObservationRuntimeOwner,
    ObservationSourceContext,
    PopulationPayload,
    owner_of,
)
from marivo.analysis.observation.coordinates import functional_path
from marivo.analysis.observation.population import (
    LogicalPopulationDataset,
    MaterializedPopulationDataset,
)
from marivo.analysis.observation.population_sample import PopulationSamplePayload
from marivo.analysis.observation.predicates import eq
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.analysis.subject import dropped_before
from marivo.refs import ref
from marivo.semantic.event import participant_role
from marivo.semantic.ir import JoinKey
from tests.lazy_event_fixtures import make_event_sources


def _selection(sources: LazySources, *, sampled: bool = False) -> LogicalPopulationDataset:
    pattern = sequence(
        *(
            step(
                participant=participant_role(event=ref.event(f"sales.{name}"), name="buyer"),
                key=name,
            )
            for name in ("started", "finished")
        )
    )
    population = sources.population(ref.entity("sales.customers"))
    if sampled:
        population = population.sample(engine_sample(target_rows=2))
    journey = sources.events.match(
        pattern,
        population=population,
        cohort_window=time_scope(
            start="2026-02-01T00:00:00+00:00", end="2026-03-01T00:00:00+00:00"
        ),
        completion_through=datetime(2026, 3, 2, tzinfo=timezone.utc),
        matching=first_per_subject(),
    )
    return journey.select_subjects(dropped_before(step=pattern.steps[1]))


def _retained(
    dataset: LogicalPopulationDataset,
    *,
    sampled: bool = False,
    context: ObservationSourceContext | None = None,
) -> MaterializedPopulationDataset:
    """Construct trusted test state without claiming persistence or source execution."""
    ids = dataset._registration.ids
    schema = d._make_schema(
        tuple(
            replace(
                field,
                _token=d._CORE_TOKEN,
                physical_type_state=d._resolved_type(field.logical_type_id, ids=ids),
            )
            for field in dataset.schema.columns
        )
    )
    state = _materialized_state(
        artifact_ref=ArtifactRef(ref="art_selected_fixture"),
        artifact_session_ref=dataset._owner.session_id,
        content_authority_digest="fixture-content",
        storage_kind_id="engine",
        realized_schema=schema,
        realized_row_count=2,
        realized_byte_count=d._exact_byte_count(64),
        producing_run_ref="run_fixture",
        quality_authority_digest="fixture-quality",
        evidence_authority_digest="fixture-evidence",
        ids=ids,
    )
    source_owner = owner_of(dataset)
    owner = ObservationRuntimeOwner(
        session_id=source_owner.session_id,
        store_id=source_owner.store_id,
        action_port=source_owner.action_port,
        source_context=context,
        sampling_authority_snapshot=sampled,
    )
    result = _make_materialized_dataset(
        owner=owner,
        registry=dataset._registry,
        family_id="population",
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        state=state,
        definition_fingerprint=dataset.definition_fingerprint,
    )
    assert isinstance(result, MaterializedPopulationDataset)
    return result


def test_selected_population_sample_is_source_free_and_fenced() -> None:
    selected = _selection(make_event_sources())
    sample = selected.sample(engine_sample(target_rows=2, seed=17))
    assert isinstance(sample._root, LogicalRootHandle)
    assert isinstance(sample._root.payload, PopulationSamplePayload)
    assert (
        sample._root.payload.target_population_definition_fingerprint
        == selected.definition_fingerprint
    )
    assert sample._root.payload.policy.target_rows == 2
    assert sample._root.has_realizations
    assert sample._inputs == (selected,)
    assert sample.row_contract == selected.row_contract
    assert sample.row_set_contract == selected.row_set_contract
    with pytest.raises(DatasetConstructionError, match="second sampling"):
        sample.sample(engine_sample(target_rows=1))
    with pytest.raises(DatasetConstructionError, match="where after"):
        sample.where(eq(ref.dimension("sales.customers.region"), "EU"))
    with pytest.raises(AssertionError, match="execution"):
        sample.execute()


def test_retained_population_sample_needs_no_catalog_context() -> None:
    retained = _retained(_selection(make_event_sources()))
    sample = retained.sample(engine_sample(target_rows=1))
    assert not isinstance(owner_of(sample), ObservationOwner)
    assert owner_of(sample).source_context is None
    assert isinstance(sample._root, LogicalRootHandle)
    assert isinstance(sample._root.payload, PopulationSamplePayload)
    assert isinstance(sample._root.inputs[0].root, MaterializedScanLeafHandle)
    assert sample._inputs == (retained,)
    with pytest.raises(DatasetConstructionError, match="catalog-free"):
        retained.where(eq(ref.dimension("sales.customers.region"), "EU"))


def test_selection_inherits_no_resampling_boundary() -> None:
    inherited = _selection(make_event_sources(), sampled=True)
    with pytest.raises(DatasetConstructionError, match="second sampling"):
        inherited.sample(engine_sample(target_rows=1))
    with pytest.raises(DatasetConstructionError, match="where after"):
        inherited.where(eq(ref.dimension("sales.customers.region"), "EU"))
    retained = _retained(_selection(make_event_sources()), sampled=True)
    with pytest.raises(DatasetConstructionError, match="second sampling"):
        retained.sample(engine_sample(target_rows=1))
    with pytest.raises(DatasetConstructionError, match="where after"):
        retained.where(eq(ref.dimension("sales.customers.region"), "EU"))


def test_current_enrichment_context_is_fixed_when_authored() -> None:
    sources = make_event_sources()
    selected = _selection(sources)
    context = ObservationSourceContext(current=sources._owner)
    retained = _retained(selected, context=context)
    filtered = retained.where(eq(ref.dimension("sales.customers.region"), "EU"))
    assert filtered._inputs == (retained,)
    assert filtered._owner is sources._owner
    assert isinstance(filtered._root, LogicalRootHandle)
    assert isinstance(filtered._root.payload, PopulationPayload)
    assert filtered._root.payload.time_scope is None
    before = filtered.definition_fingerprint
    context.current = None
    assert filtered.definition_fingerprint == before
    assert filtered._owner is sources._owner
    with pytest.raises(DatasetConstructionError, match="catalog-free"):
        retained.where(eq(ref.dimension("sales.customers.region"), "EU"))
    filtered.sample(engine_sample(target_rows=1))


def test_event_axes_admit_temporal_intermediates_without_widening_shared_default() -> None:
    original = make_event_sources()
    owner = original._owner
    relationship = owner.semantic_registry.relationships["sales.order_customer"]
    registry = replace(
        owner.semantic_registry,
        relationships={
            **owner.semantic_registry.relationships,
            "sales.customer_snapshot": replace(
                relationship,
                semantic_id="sales.customer_snapshot",
                from_entity="sales.customers",
                to_entity="sales.snapshots",
                keys=(JoinKey("sales.customers.id", "sales.snapshots.id"),),
            ),
            "sales.snapshot_order": replace(
                relationship,
                semantic_id="sales.snapshot_order",
                from_entity="sales.snapshots",
                to_entity="sales.orders",
                keys=(JoinKey("sales.snapshots.order_id", "sales.orders.id"),),
            ),
        },
    )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=owner.sidecar,
        action_port=owner.action_port,
        session_id=owner.session_id,
        store_id=owner.store_id,
    )
    with pytest.raises(DatasetConstructionError, match="versioned"):
        functional_path(registry, "sales.customers", "sales.orders")
    selected = _selection(sources)
    journey = selected._inputs[0]
    from marivo.analysis.domains.event import LogicalEventDataset

    assert isinstance(journey, LogicalEventDataset)
    grouped = journey.funnel(axes=(ref.dimension("sales.orders.channel"),))
    assert isinstance(grouped._root, LogicalRootHandle)
    assert isinstance(grouped._root.payload, EventFunnelPayload)
    assert grouped._root.payload.axes[0].path == ("sales.customer_snapshot", "sales.snapshot_order")
    ambiguous = replace(
        registry,
        relationships={
            **registry.relationships,
            "sales.customer_snapshot_other": replace(
                registry.relationships["sales.customer_snapshot"],
                semantic_id="sales.customer_snapshot_other",
            ),
        },
    )
    ambiguous.freeze()
    other = make_lazy_sources(
        semantic_registry=ambiguous,
        sidecar=owner.sidecar,
        action_port=owner.action_port,
        session_id=owner.session_id,
        store_id=owner.store_id,
    )
    other_journey = _selection(other)._inputs[0]
    assert isinstance(other_journey, LogicalEventDataset)
    with pytest.raises(DatasetConstructionError, match="ambiguous"):
        other_journey.funnel(axes=(ref.dimension("sales.orders.channel"),))
