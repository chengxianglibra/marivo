"""Source-free Event, shared identity admission, and exact coverage contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis import time_scope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetOwnershipError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    BoundedCoverageStartV1,
    EventCoverageReceiptV1,
    EventCoverageRequestV1,
    EventObservedWatermarkV1,
    SourceOriginCompletenessDeclarationV1,
    SourceOriginCoverageStartV1,
    declarations_json,
    resolve_event_coverage,
)
from marivo.analysis.domains.contracts import (
    EventJourneySemantics,
    EventPayload,
    journey_identity_digest,
)
from marivo.analysis.domains.errors import EventCompletenessError, EventConstructionError
from marivo.analysis.domains.event import LogicalEventDataset, _validate_event
from marivo.analysis.event import (
    EventPattern,
    EveryStart,
    FirstPerSubject,
    every_start,
    first_per_subject,
    sequence,
    step,
)
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import JsonSourceIR, SourceParamIR, TableColumnBindingIR, TableSourceIR
from marivo.refs import ref
from marivo.semantic.event import participant_role
from marivo.semantic.ir import JoinKey
from tests.lazy_event_fixtures import make_event_sources
from tests.lazy_observation_fixtures import NoIoActionPort

START = datetime(2026, 2, 1, tzinfo=timezone.utc)
END = datetime(2026, 3, 1, tzinfo=timezone.utc)
THROUGH = datetime(2026, 3, 2, tzinfo=timezone.utc)
WINDOW = time_scope(start=START.isoformat(), end=END.isoformat())
EVENTS = (ref.event("sales.started"), ref.event("sales.finished"))


def pattern(*names: str) -> EventPattern:
    return sequence(
        *(
            step(
                participant=participant_role(event=ref.event(f"sales.{name}"), name="buyer"),
                key=f"position_{index}",
            )
            for index, name in enumerate(names or ("started", "finished"))
        )
    )


def match(sources: LazySources) -> LogicalEventDataset:
    return sources.events.match(
        pattern(), cohort_window=WINDOW, completion_through=THROUGH, matching=first_per_subject()
    )


def payload(dataset: LogicalEventDataset) -> EventPayload:
    assert isinstance(dataset._root, LogicalRootHandle)
    assert isinstance(dataset._root.payload, EventPayload)
    return dataset._root.payload


def with_owner(owner: ObservationOwner) -> LazySources:
    return make_lazy_sources(
        semantic_registry=owner.semantic_registry,
        sidecar=owner.sidecar,
        action_port=NoIoActionPort(),
        session_id=owner.session_id,
        store_id=owner.store_id,
    )


def test_source_construction_is_complete_immutable_and_source_free() -> None:
    sources = make_event_sources()
    dataset = match(sources)
    assert isinstance(dataset, LogicalEventDataset)
    assert tuple(item.name for item in dataset.schema.columns) == (
        "journey_id",
        "completion_status",
        "entity_identity",
        "step_key",
        "event_identity",
        "occurred_at",
        "elapsed_from_start",
        "elapsed_from_previous",
    )
    assert tuple(item.nullable for item in dataset.schema.columns) == (
        False,
        False,
        False,
        False,
        True,
        True,
        True,
        True,
    )
    assert payload(dataset).definition.entity.ref.path == "sales.customers"
    assert payload(dataset).captures == ()
    assert payload(dataset).definition.steps[0].participant_path == ("sales.started_customer",)
    assert payload(dataset).definition.steps[0].identity[0].nullable is False
    assert isinstance(dataset.row_contract.family_semantics, EventJourneySemantics)
    assert dataset.row_contract.family_semantics.pattern == pattern()
    assert dataset.row_contract.family_semantics.matching == first_per_subject()
    assert dataset.row_contract.key_field_ids == (
        dataset.schema.columns[0].field_id,
        dataset.schema.columns[3].field_id,
    )
    assert "require execution" in dataset.contract().render()
    attribute = "population_definition"
    with pytest.raises(FrozenInstanceError):
        setattr(payload(dataset).definition, attribute, "changed")
    with pytest.raises(AssertionError, match="execution"):
        dataset.execute()


def test_shared_population_admission_preserves_exact_entity_and_metric_shape() -> None:
    sources = make_event_sources()
    customer = sources.population(ref.entity("sales.customers"))
    metric = sources.observe(ref.metric("sales.revenue"), population=customer)
    for population in (customer, metric, metric.discover.entity_outliers()):
        dataset = sources.events.match(
            pattern(),
            cohort_window=WINDOW,
            completion_through=THROUGH,
            matching=first_per_subject(),
            population=population,
        )
        assert (
            payload(dataset).definition.population_definition == population.definition_fingerprint
        )
    with pytest.raises(DatasetConstructionError, match="different subject"):
        sources.events.match(
            pattern(),
            cohort_window=WINDOW,
            completion_through=THROUGH,
            matching=first_per_subject(),
            population=sources.population(ref.entity("sales.orders")),
        )
    with pytest.raises(DatasetConstructionError, match="Entity-reduced"):
        sources.events.match(
            pattern(),
            cohort_window=WINDOW,
            completion_through=THROUGH,
            matching=first_per_subject(),
            population=metric.aggregate(),
        )
    foreign = make_event_sources(session_id="other").population(ref.entity("sales.customers"))
    with pytest.raises(DatasetOwnershipError):
        sources.events.match(
            pattern(),
            cohort_window=WINDOW,
            completion_through=THROUGH,
            matching=first_per_subject(),
            population=foreign,
        )


def test_composite_subject_identity_preserves_key_order() -> None:
    sources = make_event_sources()
    original = sources._owner.semantic_registry
    entities = dict(original.entities)
    dimensions = dict(original.dimensions)
    relationships = dict(original.relationships)
    for name in ("started", "finished"):
        source_entity = entities[f"sales.{name}_rows"]
        assert isinstance(source_entity.source, TableSourceIR)
        entities[source_entity.semantic_id] = replace(
            source_entity,
            source=replace(
                source_entity.source,
                columns=(
                    *source_entity.source.columns,
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
    registry = replace(
        original, entities=entities, dimensions=dimensions, relationships=relationships
    )
    registry.freeze()
    dataset = match(with_owner(replace(sources._owner, semantic_registry=registry)))
    assert payload(dataset).definition.entity.identity_signature == (
        ("tenant", "string"),
        ("id", "int64"),
    )
    identity = dataset.schema.columns[2].identity
    assert isinstance(identity, d._EntityFieldIdentity)
    assert identity.identity_signature == (("tenant", "string"), ("id", "int64"))


def test_event_parameter_capture_is_frozen_and_redacted() -> None:
    sources = make_event_sources()
    original = sources._owner.semantic_registry
    source_entity = original.entities["sales.started_rows"]
    assert isinstance(source_entity.source, TableSourceIR)
    source = JsonSourceIR(
        path="https://fixture.invalid/events",
        schema=tuple((name, binding.data_type) for name, binding in source_entity.source.columns),
        query_params=(("tenant", SourceParamIR("tenant")),),
    )
    registry = replace(
        original,
        entities={
            **original.entities,
            source_entity.semantic_id: replace(source_entity, source=source),
        },
    )
    registry.freeze()
    other = with_owner(replace(sources._owner, semantic_registry=registry))
    values = {ref.entity(source_entity.semantic_id): {"tenant": "sensitive-membership-canary"}}
    with other.source_bindings(values):
        dataset = match(other)
    values[ref.entity(source_entity.semantic_id)]["tenant"] = "changed"
    capture = payload(dataset).captures[0]
    assert "sensitive-membership-canary" not in repr(capture)
    assert capture.exact_value_digest != "changed"
    with other.source_bindings(values):
        changed = match(other)
    assert dataset.definition_fingerprint != changed.definition_fingerprint
    assert (
        payload(dataset).captures[0].exact_value_digest
        != payload(changed).captures[0].exact_value_digest
    )


@pytest.mark.parametrize(
    "policy",
    [
        first_per_subject(),
        every_start(completion_assignment="shared"),
        every_start(completion_assignment="exclusive"),
    ],
)
def test_all_policies_and_repeated_event_patterns_remain_distinct(
    policy: FirstPerSubject | EveryStart,
) -> None:
    sources = make_event_sources()
    dataset = sources.events.match(
        pattern("started", "started", "finished"),
        cohort_window=WINDOW,
        completion_through=THROUGH,
        matching=policy,
    )
    assert len(payload(dataset).definition.steps) == 3
    assert (
        payload(dataset).definition.steps[0].event_fingerprint
        == payload(dataset).definition.steps[1].event_fingerprint
    )
    assert payload(dataset).definition.matching == policy


def test_temporal_bounds_are_explicit_and_distinct_from_membership_scope() -> None:
    sources = make_event_sources()
    with pytest.raises(EventConstructionError, match="cohort"):
        sources.events.match(
            pattern(),
            cohort_window=time_scope(start="2026-02-01", end="2026-03-01"),
            completion_through=THROUGH,
            matching=first_per_subject(),
        )
    with pytest.raises(EventCompletenessError, match="timezone-aware"):
        sources.events.match(
            pattern(),
            cohort_window=WINDOW,
            completion_through=THROUGH.replace(tzinfo=None),
            matching=first_per_subject(),
        )
    with pytest.raises(EventConstructionError, match="temporal"):
        sources.events.match(
            pattern(), cohort_window=WINDOW, completion_through=START, matching=first_per_subject()
        )


def test_source_admission_rejects_mixed_subjects_optional_roles_and_identity_types() -> None:
    sources = make_event_sources()
    original = sources._owner.semantic_registry
    finished = original.events["sales.finished"]
    mutations = (
        replace(
            finished, participants=(replace(finished.participants[0], cardinality="optional_one"),)
        ),
        replace(finished, participants=(replace(finished.participants[0], path=None),)),
        replace(
            finished,
            identity=("sales.finished_rows.occurrence_id", "sales.finished_rows.occurred_at"),
        ),
    )
    for changed in mutations:
        registry = replace(original, events={**original.events, "sales.finished": changed})
        registry.freeze()
        other = with_owner(replace(sources._owner, semantic_registry=registry))
        with pytest.raises(EventConstructionError):
            match(other)
    entity = original.entities["sales.finished_rows"]
    assert isinstance(entity.source, TableSourceIR)
    source = replace(
        entity.source,
        columns=tuple(
            (name, replace(binding, data_type="string") if name == "occurrence_id" else binding)
            for name, binding in entity.source.columns
        ),
    )
    registry = replace(
        original, entities={**original.entities, entity.semantic_id: replace(entity, source=source)}
    )
    registry.freeze()
    with pytest.raises(EventConstructionError, match="homogeneous"):
        match(with_owner(replace(sources._owner, semantic_registry=registry)))


def test_default_versioned_subject_requires_explicit_membership() -> None:
    sources = make_event_sources()
    original = sources._owner.semantic_registry
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
    registry = replace(original, relationships=relationships)
    registry.freeze()
    other = with_owner(replace(sources._owner, semantic_registry=registry))
    with pytest.raises(EventConstructionError, match="versioned"):
        match(other)
    population = other.population(
        ref.entity("sales.snapshots"), time_scope=time_scope(start="2026-01-01", end="2026-02-01")
    )
    dataset = other.events.match(
        pattern(),
        cohort_window=WINDOW,
        completion_through=THROUGH,
        matching=first_per_subject(),
        population=population,
    )
    assert payload(dataset).definition.entity.version is not None
    assert payload(dataset).definition.cohort_window == WINDOW


def test_declaration_values_are_local_and_source_binding_occurs_at_match() -> None:
    declaration = BoundedCompletenessDeclarationV1(
        inputs=EVENTS, complete_from=START, complete_through=THROUGH, rationale=" fixture coverage "
    )
    assert declaration.rationale == "fixture coverage"
    sources = make_event_sources()
    dataset = sources.events.match(
        pattern(),
        cohort_window=WINDOW,
        completion_through=THROUGH,
        matching=first_per_subject(),
        completeness=(declaration,),
    )
    resolved = resolve_event_coverage(payload(dataset).definition)
    assert resolved.complete and resolved.basis == "declared"
    assert len(resolved.events) == 2
    assert all(item.rationale == "fixture coverage" for item in resolved.events)
    assert resolve_event_coverage(payload(match(sources)).definition).basis == "unknown"
    unrelated = BoundedCompletenessDeclarationV1(
        inputs=(ref.event("sales.unrelated"),),
        complete_from=START,
        complete_through=THROUGH,
        rationale="unrelated",
    )
    wrong_origin = SourceOriginCompletenessDeclarationV1(
        inputs=EVENTS,
        source_origin_ref=ref.datasource("other"),
        complete_through=THROUGH,
        rationale="wrong",
    )
    for declarations in ((declaration, declaration), (unrelated,), (wrong_origin,)):
        with pytest.raises(EventCompletenessError):
            sources.events.match(
                pattern(),
                cohort_window=WINDOW,
                completion_through=THROUGH,
                matching=first_per_subject(),
                completeness=declarations,
            )


def test_declaration_constructor_rejects_invalid_values_without_catalog() -> None:
    for inputs in ((), (EVENTS[0], EVENTS[0])):
        with pytest.raises(EventCompletenessError):
            BoundedCompletenessDeclarationV1(
                inputs=inputs, complete_from=START, complete_through=THROUGH, rationale="fixture"
            )
    with pytest.raises(EventCompletenessError, match="reversed"):
        BoundedCompletenessDeclarationV1(
            inputs=EVENTS, complete_from=THROUGH, complete_through=START, rationale="fixture"
        )
    with pytest.raises(EventCompletenessError, match="timezone-aware"):
        BoundedCompletenessDeclarationV1(
            inputs=EVENTS,
            complete_from=START.replace(tzinfo=None),
            complete_through=THROUGH,
            rationale="fixture",
        )
    with pytest.raises(EventCompletenessError, match="empty"):
        SourceOriginCompletenessDeclarationV1(
            inputs=EVENTS,
            source_origin_ref=ref.datasource("warehouse"),
            complete_through=THROUGH,
            rationale="  ",
        )


def test_receipt_reads_once_per_event_and_binds_all_source_authority() -> None:
    sources = make_event_sources()
    dataset = sources.events.match(
        pattern("started", "started", "finished"),
        cohort_window=WINDOW,
        completion_through=THROUGH,
        matching=first_per_subject(),
    )
    requests: list[EventCoverageRequestV1] = []

    def provider(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        requests.append(request)
        return EventCoverageReceiptV1(
            event_ref=request.event_ref,
            event_fingerprint=request.event_fingerprint,
            source_entity_ref=request.source_entity_ref,
            source_origin_ref=request.source_origin_ref,
            occurred_at_ref=request.occurred_at_ref,
            coverage_start=BoundedCoverageStartV1(complete_from=START),
            complete_through=THROUGH,
            authority="fixture watermark",
            observed_at=THROUGH,
            source_binding_fingerprint=request.source_binding_fingerprint,
            execution_domain_id=request.execution_domain_id,
        )

    resolved = resolve_event_coverage(
        payload(dataset).definition,
        provider=provider,
        backend=Backend(),
        source_binding_fingerprint="binding-1",
        execution_domain_id="domain-1",
    )
    assert len(requests) == 2 and resolved.complete and resolved.basis == "observed"
    assert all(item.source_binding_fingerprint == "binding-1" for item in resolved.events)

    def wrong(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        return replace(provider(backend, request), execution_domain_id="other-domain")

    with pytest.raises(EventCompletenessError, match="mismatched"):
        resolve_event_coverage(
            payload(dataset).definition,
            provider=wrong,
            backend=Backend(),
            source_binding_fingerprint="binding-1",
            execution_domain_id="domain-1",
        )

    def source_origin(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        return replace(
            provider(backend, request),
            coverage_start=SourceOriginCoverageStartV1(source_origin_ref=request.source_origin_ref),
        )

    assert resolve_event_coverage(
        payload(dataset).definition,
        provider=source_origin,
        backend=Backend(),
        source_binding_fingerprint="binding-1",
        execution_domain_id="domain-1",
    ).complete


def test_journey_identity_is_cold_reproducible_without_population_or_window() -> None:
    dataset = match(make_event_sources())
    semantics = dataset.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    assert journey_identity_digest(semantics) == journey_identity_digest(
        replace(
            semantics,
            _token=d._CORE_TOKEN,
            population_definition="other",
            cohort_start="2026-01-01T00:00:00+00:00",
        )
    )
    assert journey_identity_digest(semantics) != journey_identity_digest(
        replace(
            semantics,
            _token=d._CORE_TOKEN,
            matching_json=every_start(completion_assignment="shared").model_dump_json(),
        )
    )


@pytest.mark.parametrize("insufficient", ["through", "start", "origin_through", None])
def test_declaration_retains_the_observed_watermark_it_supplements(
    insufficient: str | None,
) -> None:
    sources = make_event_sources()
    logical = sources.events.match(
        pattern(),
        cohort_window=WINDOW,
        completion_through=THROUGH,
        matching=first_per_subject(),
        completeness=(
            BoundedCompletenessDeclarationV1(
                inputs=EVENTS,
                complete_from=START,
                complete_through=THROUGH,
                rationale="Declared authoritative coverage.",
            ),
        ),
    )

    def provider(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        coverage_start = (
            SourceOriginCoverageStartV1(source_origin_ref=request.source_origin_ref)
            if insufficient == "origin_through"
            else BoundedCoverageStartV1(
                complete_from=START + timedelta(microseconds=1)
                if insufficient == "start"
                else START
            )
        )
        return EventCoverageReceiptV1(
            event_ref=request.event_ref,
            event_fingerprint=request.event_fingerprint,
            source_entity_ref=request.source_entity_ref,
            source_origin_ref=request.source_origin_ref,
            occurred_at_ref=request.occurred_at_ref,
            coverage_start=coverage_start,
            complete_through=THROUGH - timedelta(microseconds=1)
            if insufficient in ("through", "origin_through")
            else THROUGH,
            authority="observed.fixture@v1",
            observed_at=THROUGH,
            source_revision="revision@v1",
            source_binding_fingerprint=request.source_binding_fingerprint,
            execution_domain_id=request.execution_domain_id,
        )

    resolution = resolve_event_coverage(
        payload(logical).definition,
        provider=provider,
        backend=Backend(),
        source_binding_fingerprint="binding",
        execution_domain_id="domain",
    )
    assert resolution.complete
    assert resolution.basis == ("declared" if insufficient is not None else "observed")
    for fact in resolution.events:
        if insufficient is None:
            assert fact.supplemented_observation is None
        else:
            assert fact.supplemented_observation == EventObservedWatermarkV1(
                None
                if insufficient == "origin_through"
                else (
                    START + timedelta(microseconds=1) if insufficient == "start" else START
                ).isoformat(),
                (
                    THROUGH - timedelta(microseconds=1)
                    if insufficient in ("through", "origin_through")
                    else THROUGH
                ).isoformat(),
                "observed.fixture@v1",
                THROUGH.isoformat(),
                "revision@v1",
            )
            assert fact.authority is None and fact.observed_at is None
            assert fact.rationale == "Declared authoritative coverage."


def test_row_contract_rejects_field_and_order_mutation() -> None:
    dataset = match(make_event_sources())
    column = dataset.schema.columns[-1]
    changed = replace(column, _token=d._CORE_TOKEN, nullable=False)
    row = replace(
        dataset.row_contract,
        _token=d._CORE_TOKEN,
        schema=d._make_schema((*dataset.schema.columns[:-1], changed)),
    )
    with pytest.raises(EventConstructionError, match="field"):
        _validate_event(row, dataset.row_set_contract)
    ordering = dataset.row_set_contract.ordering
    assert isinstance(ordering, d._OrderedOrdering)
    changed_order = replace(ordering, _token=d._CORE_TOKEN, terms=tuple(reversed(ordering.terms)))
    with pytest.raises(EventConstructionError, match="ordering"):
        _validate_event(
            dataset.row_contract,
            replace(dataset.row_set_contract, _token=d._CORE_TOKEN, ordering=changed_order),
        )
    with pytest.raises(EventConstructionError):
        _validate_event(
            dataset.row_contract,
            replace(
                dataset.row_set_contract,
                _token=d._CORE_TOKEN,
                cardinality=d._keyed_cardinality(d._static_row_bound(4)),
            ),
        )


@pytest.mark.parametrize("parameter", ["pattern", "completeness"])
@pytest.mark.parametrize("size", [4096, 4097])
def test_retained_authority_size_is_checked_without_source_work(parameter: str, size: int) -> None:
    sources = make_event_sources()
    selected = pattern("started")
    declarations: tuple[BoundedCompletenessDeclarationV1, ...] = ()
    if parameter == "pattern":
        role = participant_role(event=EVENTS[0], name="buyer")
        minimal = sequence(step(participant=role, key="s"))
        key = "s" + "x" * (size - len(minimal.model_dump_json().encode("utf-8")))
        selected = sequence(step(participant=role, key=key))
        assert len(selected.model_dump_json().encode("utf-8")) == size
        body_canary = key
    else:
        minimal_declaration = BoundedCompletenessDeclarationV1(
            inputs=(EVENTS[0],), complete_from=START, complete_through=THROUGH, rationale="x"
        )
        rationale = "x" * (
            size - len(declarations_json((minimal_declaration,)).encode("utf-8")) + 1
        )
        declarations = (replace(minimal_declaration, rationale=rationale),)
        assert len(declarations_json(declarations).encode("utf-8")) == size
        body_canary = rationale
    if size > 4096:
        with pytest.raises(EventConstructionError, match="4096 UTF-8 bytes") as caught:
            sources.events.match(
                selected,
                cohort_window=WINDOW,
                completion_through=THROUGH,
                matching=first_per_subject(),
                completeness=declarations,
            )
        assert body_canary not in str(caught.value)
        assert caught.value.location == f"events.match.{parameter}"
        assert caught.value.hint is not None and "Shorten" in caught.value.hint
    else:
        result = sources.events.match(
            selected,
            cohort_window=WINDOW,
            completion_through=THROUGH,
            matching=first_per_subject(),
            completeness=declarations,
        )
        semantics = result.row_contract.family_semantics
        assert isinstance(semantics, EventJourneySemantics)
        retained = semantics.pattern_json if parameter == "pattern" else semantics.completeness_json
        assert len(retained.encode("utf-8")) == size


def test_event_semantic_digest_pins_authored_relationship_authority() -> None:
    from marivo.analysis.observation.contracts import semantic_dependency_digest

    # Pins the source-free definition with endpoint-owned Dimension join refs.
    assert semantic_dependency_digest(match(make_event_sources())) == (
        "526e483769db0c6859fc75929abfdfb40560d8c2cfbb6182ddf55f7657efdce0"
    )
