"""Exact Event reducer authoring, source bindings, and retained row contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.fields import DatasetFieldRef, validate_field_ref
from marivo.analysis.domains.contracts import (
    EventAxisBinding,
    EventFunnelPayload,
    EventFunnelSemantics,
    EventJourneySemantics,
    EventSelectionPayload,
    EventTimeToEventPayload,
    EventTimeToEventSemantics,
    encode_journey_semantics,
)
from marivo.analysis.domains.errors import reducer_error
from marivo.analysis.event import FirstPerSubject, PatternStep
from marivo.analysis.observation import coordinates
from marivo.analysis.observation.contracts import (
    IDENTITY_FIELD_ID,
    DimensionInput,
    RetainedRowsPayload,
    dimension_field,
    owner_of,
    path_dependency_fingerprint,
    producer_contract,
    retained_field,
    source_owner_of,
)
from marivo.analysis.observation.population import LogicalPopulationDataset
from marivo.analysis.observation.predicates import (
    AnalysisPredicate,
    PredicateField,
    bind_predicates,
)
from marivo.analysis.observation.source_bindings import BoundSourceParametersV1
from marivo.analysis.subject import DroppedBefore
from marivo.semantic.validator import normalize_target_entity

if TYPE_CHECKING:
    from marivo.analysis.domains.event import LogicalEventDataset


FUNNEL_FIELDS = (
    ("step_key", "pattern_step_identity", "string", False),
    ("cohort_count", "additive_count", "int64", False),
    ("resolved_cohort_count", "additive_count", "int64", False),
    ("entry_count", "additive_count", "int64", False),
    ("resolved_entry_count", "additive_count", "int64", False),
    ("reached_count", "additive_count", "int64", False),
    ("lost_count", "additive_count", "int64", False),
    ("conversion_from_first", "rate_value", "float64", True),
    ("conversion_from_previous", "rate_value", "float64", True),
    ("loss_rate_from_previous", "rate_value", "float64", True),
    ("coverage_censored_count", "additive_count", "int64", False),
)
TIME_TO_EVENT_FIELDS = (
    ("journey_id", "journey_identity", "string", False),
    ("from_event_identity", "event_occurrence_identity", "identity_tuple", True),
    ("from_time", "time_coordinate", "timestamp", True),
    ("to_event_identity", "event_occurrence_identity", "identity_tuple", True),
    ("to_time", "time_coordinate", "timestamp", True),
    ("duration", "duration_value", "duration", True),
    ("followup_until", "time_coordinate", "timestamp", True),
    ("observed_duration", "duration_value", "duration", True),
    ("completion_status", "status", "string", False),
)


TIME_TO_EVENT_ORDER = (
    ("entity_identity", "observation.identity_tuple@v1"),
    ("from_time", "observation.scalar_order@v1"),
    ("from_event_identity", "observation.identity_tuple@v1"),
    ("journey_id", "observation.scalar_order@v1"),
)


def _reserved_axis_name(name: str) -> bool:
    from marivo.analysis.domains.event import _FIELDS

    # Enrichment retains the structural input until shared reach classification.
    names = {
        "entity_identity",
        *(field[0] for field in _FIELDS),
        *(field[0] for field in FUNNEL_FIELDS),
    }
    return name in names


def reducer_field_id(producer: str, name: str) -> d.DatasetFieldId:
    return (
        IDENTITY_FIELD_ID
        if name == "entity_identity"
        else d._make_field_id(f"generated.events.{producer}.{name}@v1")
    )


def _fields(
    producer: str,
    definitions: tuple[tuple[str, str, str, bool], ...],
    ids: d._StableIdRegistry,
) -> tuple[d.DatasetField, ...]:
    return tuple(
        d._make_field(
            field_id=reducer_field_id(producer, name),
            name=name,
            role_id=role,
            identity=d._generated_identity(reducer_field_id(producer, name)),
            derivation_identity=reducer_field_id(producer, name).value,
            logical_type_id=logical,
            physical_type_state=d._deferred_type(logical, ids=ids),
            nullable=nullable,
            ids=ids,
        )
        for name, role, logical, nullable in definitions
    )


def _rows(
    terms: tuple[tuple[d.DatasetFieldId, str], ...], ids: d._StableIdRegistry
) -> d.DatasetRowSetContract:
    return d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    field_id,
                    direction="ascending",
                    nulls="last",
                    value_order_contract_id=order,
                    ids=ids,
                )
                for field_id, order in terms
            )
        ),
    )


def require_journey(dataset: Dataset, *, subject_unique: bool = False) -> EventJourneySemantics:
    semantics = dataset.row_contract.family_semantics
    if type(semantics) is not EventJourneySemantics:
        raise reducer_error("an unfiltered event/journey@v1 receiver", "non-journey Event shape")
    if subject_unique and type(semantics.matching) is not FirstPerSubject:
        raise reducer_error("first_per_subject journey assignments", "every_start matching")
    return semantics


def step_index(journey: EventJourneySemantics, step: PatternStep) -> int:
    if type(step) is not PatternStep:
        raise reducer_error("one complete retained PatternStep value", "invalid step type")
    try:
        PatternStep.model_validate_json(step.model_dump_json())
    except (TypeError, ValueError) as error:
        raise reducer_error("one fully validated PatternStep", "invalid step values") from error
    matches = tuple(index for index, member in enumerate(journey.pattern.steps) if member == step)
    if len(matches) != 1:
        raise reducer_error(
            "exactly one matching Event, participant and key", "foreign or repeated step"
        )
    return matches[0]


def _checked(dataset: Dataset) -> LogicalEventDataset:
    from marivo.analysis.domains.event import LogicalEventDataset

    if not isinstance(dataset, LogicalEventDataset):
        raise reducer_error("paired Logical Event registration", "invalid reducer family")
    return dataset


def funnel(
    dataset: Dataset, axes: list[DimensionInput] | tuple[DimensionInput, ...]
) -> LogicalEventDataset:
    journey = require_journey(dataset, subject_unique=True)
    if type(axes) not in (list, tuple):
        raise reducer_error(
            "a list or tuple of exact non-time Dimensions", "invalid axes container"
        )
    ids = dataset._registration.ids
    bindings: list[EventAxisBinding] = []
    axis_fields: list[d.DatasetField] = []
    captures: tuple[BoundSourceParametersV1, ...] = ()
    owner = owner_of(dataset)
    if axes:
        source_owner = source_owner_of(dataset)
        owner = source_owner
        subject = normalize_target_entity(
            source_owner.semantic_registry, journey.subject_entity_ref
        )
        if subject.identity_signature != journey.subject_identity_signature:
            raise reducer_error(
                "the retained exact subject identity signature", "changed subject identity"
            )
        for operand in axes:
            axis = coordinates.normalize_dimension_input(source_owner, operand, time=False)
            path = coordinates.functional_path(
                source_owner.semantic_registry,
                subject.ref.path,
                axis.entity_ref.path,
                allow_versioned_source=True,
                allow_versioned_target=True,
                allow_versioned_intermediates=True,
            )
            fingerprint = path_dependency_fingerprint(source_owner, subject.ref.path, (path,))
            bindings.append(EventAxisBinding(axis, subject, path, fingerprint))
            axis_fields.append(dimension_field(axis, ids, dependency_fingerprint=fingerprint))
        names = tuple(field.name for field in axis_fields)
        if len(set(names)) != len(names) or any(_reserved_axis_name(name) for name in names):
            raise reducer_error("distinct non-colliding axis names", "duplicate or colliding axes")
        if len({binding.dimension.ref for binding in bindings}) != len(bindings):
            raise reducer_error("distinct exact governed Dimensions", "duplicate axis refs")
        captures = source_owner.binding_scopes.capture(
            tuple(
                normalize_target_entity(source_owner.semantic_registry, name)
                for name in coordinates.path_entities(
                    source_owner.semantic_registry,
                    subject.ref.path,
                    tuple(binding.path for binding in bindings),
                )
            )
        )
    semantics = EventFunnelSemantics(
        _token=d._CORE_TOKEN,
        journey_json=encode_journey_semantics(journey),
        axis_refs=tuple(binding.dimension.ref.path for binding in bindings),
        axis_dependency_fingerprints=tuple(binding.dependency_fingerprint for binding in bindings),
    )
    fields = (*axis_fields, *_fields("funnel", FUNNEL_FIELDS, ids))
    keys = (*tuple(field.field_id for field in axis_fields), reducer_field_id("funnel", "step_key"))
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("event", "funnel", 1, ids=ids),
        schema=d._make_schema(fields),
        coordinate_field_ids=keys,
        key_field_ids=keys,
        family_semantics=semantics,
    )
    rows = _rows(
        (
            *tuple((field.field_id, "observation.scalar_order@v1") for field in axis_fields),
            (reducer_field_id("funnel", "step_key"), "event.pattern_step@v1"),
        ),
        ids,
    )
    return _checked(
        construct_operator(
            owner=owner,
            registry=dataset._registry,
            operator_id="event.funnel",
            inputs=(dataset,),
            row_contract=row,
            row_set_contract=rows,
            contract_versions=producer_contract("event.funnel").versions,
            payload=EventFunnelPayload(
                _token=d._CORE_TOKEN, semantics=semantics, axes=tuple(bindings), captures=captures
            ),
        )
    )


def time_to_event(
    dataset: Dataset, from_step: PatternStep, to_step: PatternStep
) -> LogicalEventDataset:
    journey = require_journey(dataset)
    if step_index(journey, from_step) >= step_index(journey, to_step):
        raise reducer_error("from_step strictly before to_step", "unordered selected step pair")
    ids = dataset._registration.ids
    semantics = EventTimeToEventSemantics(
        _token=d._CORE_TOKEN,
        journey_json=encode_journey_semantics(journey),
        from_step_json=from_step.model_dump_json(),
        to_step_json=to_step.model_dump_json(),
    )
    generated = _fields("time_to_event", TIME_TO_EVENT_FIELDS, ids)
    subject = next(field for field in dataset.schema.columns if field.field_id == IDENTITY_FIELD_ID)
    fields = (generated[0], subject, *generated[1:])
    journey_id = reducer_field_id("time_to_event", "journey_id")
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("event", "time-to-event", 1, ids=ids),
        schema=d._make_schema(fields),
        coordinate_field_ids=(journey_id, IDENTITY_FIELD_ID),
        key_field_ids=(journey_id,),
        family_semantics=semantics,
    )
    rows = _rows(
        tuple(
            (reducer_field_id("time_to_event", name), order) for name, order in TIME_TO_EVENT_ORDER
        ),
        ids,
    )
    return _checked(
        construct_operator(
            owner=owner_of(dataset),
            registry=dataset._registry,
            operator_id="event.time_to_event",
            inputs=(dataset,),
            row_contract=row,
            row_set_contract=rows,
            contract_versions=producer_contract("event.time_to_event").versions,
            payload=EventTimeToEventPayload(_token=d._CORE_TOKEN, semantics=semantics),
        )
    )


def select_subjects(dataset: Dataset, selection: DroppedBefore) -> LogicalPopulationDataset:
    journey = require_journey(dataset, subject_unique=True)
    if type(selection) is not DroppedBefore:
        raise reducer_error("an exact DroppedBefore selection", "unsupported selection kind")
    try:
        DroppedBefore.model_validate_json(selection.model_dump_json())
    except (TypeError, ValueError) as error:
        raise reducer_error(
            "a fully validated DroppedBefore value", "invalid selection values"
        ) from error
    if step_index(journey, selection.step) == 0:
        raise reducer_error("a non-initial retained PatternStep", "initial step selection")
    subject = next(field for field in dataset.schema.columns if field.field_id == IDENTITY_FIELD_ID)
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id(
            "population", "entity-membership", 1, ids=dataset._registration.ids
        ),
        schema=d._make_schema((subject,)),
        coordinate_field_ids=(IDENTITY_FIELD_ID,),
        key_field_ids=(IDENTITY_FIELD_ID,),
        family_semantics=d._complete_from_schema(),
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._unordered_ordering(),
    )
    result = construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id="event.select_subjects",
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=rows,
        contract_versions=producer_contract("event.select_subjects").versions,
        payload=EventSelectionPayload(_token=d._CORE_TOKEN, journey=journey, selection=selection),
    )
    if not isinstance(result, LogicalPopulationDataset):
        raise reducer_error("the canonical Logical Population family", "invalid selection family")
    return result


def event_filterable_field(field: d.DatasetField) -> bool:
    return any(
        field.name == name
        and field.role_id == role
        and field.logical_type_id == logical
        and field.nullable is nullable
        and field.field_id == reducer_field_id(producer, name)
        and field.identity == d._generated_identity(field.field_id)
        and field.derivation_identity == field.field_id.value
        for producer, definitions in (
            ("funnel", FUNNEL_FIELDS),
            ("time_to_event", TIME_TO_EVENT_FIELDS),
        )
        for name, role, logical, nullable in definitions
        if role not in ("journey_identity", "event_occurrence_identity")
    )


def where(dataset: Dataset, predicates: tuple[AnalysisPredicate, ...]) -> LogicalEventDataset:
    if type(dataset.row_contract.family_semantics) not in (
        EventFunnelSemantics,
        EventTimeToEventSemantics,
    ):
        raise reducer_error(
            "independent funnel or time-to-event summary rows", "structural journey filtering"
        )

    def resolve(operand: PredicateField) -> d.DatasetField:
        field = (
            validate_field_ref(dataset, operand)
            if isinstance(operand, DatasetFieldRef)
            else retained_field(dataset, operand)
        )
        if field.role_id != "dimension" and not event_filterable_field(field):
            raise reducer_error(
                "an admitted retained Event value or axis", "identity or structural predicate field"
            )
        return field

    bound = bind_predicates(predicates, resolve)
    return _checked(
        construct_operator(
            owner=owner_of(dataset),
            registry=dataset._registry,
            operator_id="event.where",
            inputs=(dataset,),
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            contract_versions=producer_contract("event.where").versions,
            payload=RetainedRowsPayload(_token=d._CORE_TOKEN, predicate=bound),
        )
    )


def validate_reducer(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
    semantics = row.family_semantics
    fields = row.schema.columns
    definitions: tuple[tuple[str, str, str, bool], ...]
    if type(semantics) is EventFunnelSemantics:
        journey = semantics.journey
        if len(fields) != len(semantics.axis_refs) + len(FUNNEL_FIELDS):
            raise reducer_error("the exact ordered funnel schema", "missing or additional fields")
        axes = fields[: len(semantics.axis_refs)]
        generated = fields[len(semantics.axis_refs) :]
        producer, shape, definitions = "funnel", "funnel", FUNNEL_FIELDS
        keys = tuple(field.field_id for field in (*axes, generated[0]))
        coordinates_ids = keys
        terms = (
            *tuple((field.field_id, "observation.scalar_order@v1") for field in axes),
            (reducer_field_id("funnel", "step_key"), "event.pattern_step@v1"),
        )
        if (
            type(journey.matching) is not FirstPerSubject
            or len(semantics.axis_refs) != len(semantics.axis_dependency_fingerprints)
            or len(set(semantics.axis_refs)) != len(semantics.axis_refs)
            or len(axes) != len(semantics.axis_refs)
            or any(_reserved_axis_name(field.name) for field in axes)
            or any(
                field.role_id != "dimension"
                or field.name != reference.rsplit(".", 1)[-1]
                or not isinstance(field.identity, d._CatalogFieldIdentity)
                or field.identity.identity_id != f"dimension:{reference}"
                for field, reference in zip(axes, semantics.axis_refs, strict=True)
            )
        ):
            raise reducer_error(
                "first-per-subject funnel and exact retained axis bindings",
                "invalid funnel semantics",
            )
    elif type(semantics) is EventTimeToEventSemantics:
        journey = semantics.journey
        if step_index(journey, semantics.from_step) >= step_index(journey, semantics.to_step):
            raise reducer_error(
                "an ordered exact retained step pair", "invalid time-to-event semantics"
            )
        if len(fields) != 10:
            raise reducer_error("the exact ten-column time-to-event schema", "changed schema")
        subject = fields[1]
        if (
            subject.field_id != IDENTITY_FIELD_ID
            or subject.name != "entity_identity"
            or subject.role_id != "entity_identity"
            or subject.logical_type_id != "identity_tuple"
            or subject.nullable
            or not isinstance(subject.identity, d._EntityFieldIdentity)
            or subject.identity.entity_ref.path != journey.subject_entity_ref
            or subject.identity.identity_signature != journey.subject_identity_signature
            or subject.derivation_identity != "identity.entity_identity@v1"
        ):
            raise reducer_error(
                "canonical complete retained subject identity", "changed subject binding"
            )
        generated = (fields[0], *fields[2:])
        producer, shape, definitions = "time_to_event", "time-to-event", TIME_TO_EVENT_FIELDS
        keys = (reducer_field_id(producer, "journey_id"),)
        coordinates_ids = (*keys, IDENTITY_FIELD_ID)
        terms = tuple(
            (reducer_field_id(producer, name), order) for name, order in TIME_TO_EVENT_ORDER
        )
    else:
        raise reducer_error("a closed Event reducer semantics variant", "unknown Event semantics")
    if len(generated) != len(definitions):
        raise reducer_error("the exact ordered reducer schema", "missing or additional fields")
    for field, (name, role, logical, nullable) in zip(generated, definitions, strict=True):
        field_id = reducer_field_id(producer, name)
        if (
            field.name != name
            or field.field_id != field_id
            or field.role_id != role
            or field.logical_type_id != logical
            or field.nullable is not nullable
            or field.identity != d._generated_identity(field_id)
            or field.derivation_identity != field_id.value
        ):
            raise reducer_error(
                "exact generated Event field binding, type and nullability", "changed reducer field"
            )
    if (
        row.schema_version != 1
        or rows.schema_version != 1
        or (row.shape_id.family_id, row.shape_id.local_shape_id, row.shape_id.semantic_version)
        != ("event", shape, 1)
        or row.key_field_ids != keys
        or row.coordinate_field_ids != coordinates_ids
        or not isinstance(rows.cardinality, d._KeyedCardinality)
        or rows.cardinality.row_bound.kind != "unknown"
        or not isinstance(rows.ordering, d._OrderedOrdering)
        or tuple(
            (term.field_id, term.direction, term.nulls, term.value_order_contract_id)
            for term in rows.ordering.terms
        )
        != tuple((field_id, "ascending", "last", order) for field_id, order in terms)
    ):
        raise reducer_error(
            "the exact reducer shape, key and canonical ordering", "changed reducer row contract"
        )
