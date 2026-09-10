"""Private paired Event journey states and source-free Pattern construction."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import ibis.expr.datatypes as dt

from marivo._temporal import TimeScope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.base import (
    Dataset,
    LogicalDataset,
    MaterializedDataset,
    _dataset_repr,
    _make_logical_dataset,
)
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.datasets.registry import (
    ConsumerRegistration,
    DatasetFamilyRegistration,
    DatasetFamilyRegistry,
)
from marivo.analysis.datasets.state import MaterializedDatasetState, _validate_materialized_state
from marivo.analysis.domains.completeness import (
    CompletenessDeclaration,
    aware,
    validate_declarations,
)
from marivo.analysis.domains.contracts import (
    EventDefinition,
    EventFunnelPayload,
    EventFunnelSemantics,
    EventJourneySemantics,
    EventPayload,
    EventStepBinding,
    EventTimeToEventPayload,
    EventTimeToEventSemantics,
    journey_semantics,
)
from marivo.analysis.domains.errors import EventConstructionError, event_error
from marivo.analysis.domains.event_reducers import validate_reducer
from marivo.analysis.domains.subject import PopulationInput, admit_population
from marivo.analysis.event import EventPattern, EveryStart, FirstPerSubject, PatternStep
from marivo.analysis.observation import coordinates
from marivo.analysis.observation.contracts import (
    IDENTITY_FIELD_ID,
    DimensionInput,
    ObservationOwner,
    RetainedRowsPayload,
    entity_ref,
    identity_field,
    owner_of,
    path_dependency_fingerprint,
    producer_contract,
)
from marivo.analysis.observation.population import LogicalPopulationDataset, make_population
from marivo.analysis.observation.predicates import AnalysisPredicate
from marivo.analysis.operators.delta import LogicalDeltaDataset
from marivo.analysis.subject import DroppedBefore
from marivo.refs import Ref, SemanticKind
from marivo.semantic.event import ParticipantRoleHandle
from marivo.semantic.metric_graph_lowering import dependency_digest
from marivo.semantic.validator import normalize_target_dimension, normalize_target_entity

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage


class LogicalEventDataset(LogicalDataset, _token=d._CORE_TOKEN, family_id="event"):
    """Dense logical journey assignments with immutable source authority."""

    __slots__ = ()

    def compare(
        self, baseline: LogicalEventDataset | MaterializedEventDataset
    ) -> LogicalDeltaDataset:
        """Compare compatible funnel cells against baseline.

        Args: baseline: Logical or materialized Event funnel with compatible axes and follow-up.
        Returns: Logical Event Delta. Example: ``current.compare(baseline)``.
        Constraints: Exact funnel contracts and complete follow-up are required.
        """
        from marivo.analysis.domains.event_comparison import compare

        return compare(self, baseline)

    def funnel(
        self, *, axes: list[DimensionInput] | tuple[DimensionInput, ...] = ()
    ) -> LogicalEventDataset:
        """Summarize exact subject journeys by optional governed axes.

        Args: axes: Ordered governed non-time Dimensions evaluated at journey entry.
        Returns: Logical Event funnel. Example: ``journeys.funnel()``.
        Constraints: first_per_subject; axes resolve at the first occurrence.
        """
        from marivo.analysis.domains.event_reducers import funnel

        return funnel(self, axes)

    def time_to_event(self, *, from_step: PatternStep, to_step: PatternStep) -> LogicalEventDataset:
        """Classify each journey between exact from_step and to_step values.

        Args:
            from_step: Exact retained PatternStep defining entry into the selected pair.
            to_step: Exact retained PatternStep after from_step defining completion.
        Returns: Logical Event duration rows. Example: ``journeys.time_to_event(from_step=a, to_step=b)``.
        Constraints: Both retained steps must be unique and ordered.
        """
        from marivo.analysis.domains.event_reducers import time_to_event

        return time_to_event(self, from_step, to_step)

    def select_subjects(self, selection: DroppedBefore) -> LogicalPopulationDataset:
        """Select complete subject membership using a typed selection.

        Args: selection: DroppedBefore for one exact non-initial retained PatternStep.
        Returns: Logical Population. Example: ``journeys.select_subjects(dropped_before(step=b))``.
        Constraints: first_per_subject; any unknown selection truth fails the action.
        """
        from marivo.analysis.domains.event_reducers import select_subjects

        return select_subjects(self, selection)

    def where(self, *predicates: AnalysisPredicate) -> LogicalEventDataset:
        """Filter complete summary rows using AND-combined predicates.

        Args: predicates: Admitted retained-field predicates, combined with AND.
        Returns: Logical Event. Example: ``funnel.where(eq(funnel.fields.get('step_key'), 'paid'))``.
        Constraints: Structural journeys and identity operands are not filterable.
        """
        from marivo.analysis.domains.event_reducers import where

        return where(self, predicates)

    def execute(self) -> MaterializedEventDataset:
        """Execute and return committed Event journey rows; no parameters.

        Example: ``journeys.execute()``. Constraints: Exact native source capability is required.
        """
        return owner_of(self).action_port.execute_event(self)


class MaterializedEventDataset(MaterializedDataset, _token=d._CORE_TOKEN, family_id="event"):
    """Committed exact journey rows, independent of their source catalog."""

    __slots__ = ()

    def compare(
        self, baseline: LogicalEventDataset | MaterializedEventDataset
    ) -> LogicalDeltaDataset:
        """Compare compatible funnel cells against baseline.

        Args: baseline: Logical or materialized Event funnel with compatible axes and follow-up.
        Returns: Logical Event Delta. Example: ``current.compare(baseline)``.
        Constraints: Exact funnel contracts and complete follow-up are required.
        """
        from marivo.analysis.domains.event_comparison import compare

        return compare(self, baseline)

    def funnel(
        self, *, axes: list[DimensionInput] | tuple[DimensionInput, ...] = ()
    ) -> LogicalEventDataset:
        """Summarize exact subject journeys by optional governed axes.

        Args: axes: Ordered governed non-time Dimensions evaluated at journey entry.
        Returns: Logical Event funnel. Example: ``journeys.funnel()``.
        Constraints: first_per_subject; axes resolve at the first occurrence.
        """
        from marivo.analysis.domains.event_reducers import funnel

        return funnel(self, axes)

    def time_to_event(self, *, from_step: PatternStep, to_step: PatternStep) -> LogicalEventDataset:
        """Classify each journey between exact from_step and to_step values.

        Args:
            from_step: Exact retained PatternStep defining entry into the selected pair.
            to_step: Exact retained PatternStep after from_step defining completion.
        Returns: Logical Event duration rows. Example: ``journeys.time_to_event(from_step=a, to_step=b)``.
        Constraints: Both retained steps must be unique and ordered.
        """
        from marivo.analysis.domains.event_reducers import time_to_event

        return time_to_event(self, from_step, to_step)

    def select_subjects(self, selection: DroppedBefore) -> LogicalPopulationDataset:
        """Select complete subject membership using a typed selection.

        Args: selection: DroppedBefore for one exact non-initial retained PatternStep.
        Returns: Logical Population. Example: ``journeys.select_subjects(dropped_before(step=b))``.
        Constraints: first_per_subject; any unknown selection truth fails the action.
        """
        from marivo.analysis.domains.event_reducers import select_subjects

        return select_subjects(self, selection)

    def where(self, *predicates: AnalysisPredicate) -> LogicalEventDataset:
        """Filter complete summary rows using AND-combined predicates.

        Args: predicates: Admitted retained-field predicates, combined with AND.
        Returns: Logical Event. Example: ``funnel.where(eq(funnel.fields.get('step_key'), 'paid'))``.
        Constraints: Structural journeys and identity operands are not filterable.
        """
        from marivo.analysis.domains.event_reducers import where

        return where(self, predicates)

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print retained rows bounded by max_output_bytes and return None.

        Example: ``journeys.show()``. Constraints: Runtime collection guards apply.
        """
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated complete retained DataFrame; no parameters.

        Example: ``rows = journeys.to_pandas()``. Constraints: Runtime collection guards apply.
        """
        return owner_of(self).action_port.to_pandas(self)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        """Return committed aggregate Event Evidence without rematching sources."""
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Return a retained Finding page with limit and cursor.

        Example: ``journeys.findings()``. Constraints: Journey Evidence has zero Findings.
        """
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        """Read the exact finding_id through the retained evidence owner.

        Example: ``journeys.finding('id')``. Constraints: Journey Findings are empty.
        """
        return owner_of(self).action_port.finding(self, finding_id)


def _normalize_steps(
    owner: ObservationOwner, pattern: EventPattern
) -> tuple[EventStepBinding, ...]:
    if (
        type(pattern) is not EventPattern
        or not pattern.steps
        or any(type(step) is not PatternStep for step in pattern.steps)
    ):
        raise event_error("a non-empty exact EventPattern of exact PatternSteps", "invalid Pattern")
    try:
        EventPattern.model_validate_json(pattern.model_dump_json())
    except (TypeError, ValueError) as exc:
        raise event_error(
            "a fully validated immutable EventPattern", "invalid Pattern values"
        ) from exc
    if len({step.key for step in pattern.steps}) != len(pattern.steps):
        raise event_error("unique ordered Pattern step keys", "duplicate step keys")
    result: list[EventStepBinding] = []
    for step in pattern.steps:
        if (
            type(step.participant) is not ParticipantRoleHandle
            or type(step.event) is not Ref
            or step.event.kind is not SemanticKind.EVENT
        ):
            raise event_error("exact governed Event participant role", "invalid participant")
        event = owner.semantic_registry.events.get(step.event.path)
        if event is None or step.event not in owner.sidecar.catalog_refs:
            raise event_error(
                "an exact Event from the current semantic registry", "Event is not loaded"
            )
        participant = next(
            (item for item in event.participants if item.name == step.participant.name), None
        )
        if participant is None or participant.cardinality != "one":
            raise event_error(
                "an exact cardinality-one participant declared by the Event",
                "missing or optional participant",
            )
        path = tuple(participant.path or ())
        endpoint = event.source_entity
        for name in path:
            relationship = owner.semantic_registry.relationships.get(name)
            if relationship is None or relationship.from_entity != endpoint:
                raise event_error(
                    "a continuous directed participant path", "broken Event participant path"
                )
            endpoint = relationship.to_entity
        if not coordinates.path_is_functional(owner.semantic_registry, event.source_entity, path):
            raise event_error(
                "a governed to-one participant path",
                "participant path lacks exact identity authority",
            )
        source = normalize_target_entity(owner.semantic_registry, event.source_entity)
        subject = normalize_target_entity(owner.semantic_registry, endpoint)
        if not subject.identity_signature:
            raise event_error(
                "a complete non-empty subject primary key", "source-only participant Entity"
            )
        raw_identity = tuple(
            normalize_target_dimension(owner.semantic_registry, name) for name in event.identity
        )
        if (
            not raw_identity
            or len({item.ref.path for item in raw_identity}) != len(raw_identity)
            or any(item.entity_ref != source.ref or item.is_time_dimension for item in raw_identity)
        ):
            raise event_error(
                "non-empty distinct identity Dimensions on the occurrence source",
                "invalid Event identity",
            )
        # Event identity is non-null by its owning semantic contract even when
        # the physical source permits nulls; action-time scalar proofs enforce it.
        identity = tuple(
            replace(
                item,
                logical_type=str(dt.dtype(item.logical_type).copy(nullable=True)),
                nullable=False,
            )
            for item in raw_identity
        )
        instant = normalize_target_dimension(owner.semantic_registry, event.occurred_at)
        if (
            instant.entity_ref != source.ref
            or not instant.is_time_dimension
            or instant.logical_type != "timestamp"
        ):
            raise event_error(
                "a governed timestamp occurrence axis on the Event source",
                "invalid Event time authority",
            )
        if result and (
            subject.ref != result[0].subject.ref
            or subject.identity_signature != result[0].subject.identity_signature
        ):
            raise event_error(
                "one exact subject Entity and ordered identity signature",
                "Pattern participant subjects differ",
            )
        if result and tuple(item.logical_type for item in identity) != tuple(
            item.logical_type for item in result[0].identity
        ):
            raise event_error(
                "homogeneous occurrence identity arity and ordered logical types",
                "incompatible Event identity signatures",
            )
        body = owner.sidecar.bodies.get(step.event)
        if body is None:
            raise event_error("a frozen executable Event predicate", "missing Event body")
        dependency = dependency_digest(
            owner.semantic_registry,
            sidecar=owner.sidecar,
            dimension_ids=(*event.identity, event.occurred_at),
            semantic_refs=tuple(binding.to_ref() for binding in body.bindings),
        )
        fingerprint = d._canonical_digest(
            (
                "event.source-definition@v1",
                event.semantic_id,
                event.source_entity,
                event.identity,
                event.occurred_at,
                tuple((item.name, item.path, item.cardinality) for item in event.participants),
                event.predicate_kind,
                event.body_ast_hash,
                body.body_ast_hash,
                tuple(
                    (binding.field_ref.kind.value, binding.field_ref.path, binding.entity_position)
                    for binding in body.bindings
                ),
                dependency.digest,
                path_dependency_fingerprint(
                    owner,
                    source.ref.path,
                    tuple(tuple(item.path or ()) for item in event.participants),
                ),
            )
        )
        result.append(
            EventStepBinding(
                step,
                source,
                identity,
                instant,
                subject,
                path,
                fingerprint,
            )
        )
    return tuple(result)


def make_match(
    owner: ObservationOwner,
    registry: DatasetFamilyRegistry,
    pattern: EventPattern,
    *,
    cohort_window: TimeScope,
    completion_through: datetime,
    matching: FirstPerSubject | EveryStart,
    population: PopulationInput | None = None,
    completeness: tuple[CompletenessDeclaration, ...] = (),
) -> LogicalEventDataset:
    """Construct a source-free Event journey with independent subject membership."""
    steps = _normalize_steps(owner, pattern)
    if (
        not isinstance(cohort_window, TimeScope)
        or not isinstance(cohort_window.start, datetime)
        or not isinstance(cohort_window.end, datetime)
    ):
        raise event_error("a non-empty aware half-open cohort TimeScope", "invalid cohort window")
    start = aware(cohort_window.start, "events.match.cohort_window.start")
    end = aware(cohort_window.end, "events.match.cohort_window.end")
    through = aware(completion_through, "events.match.completion_through")
    if start >= end or through < end:
        raise event_error(
            "cohort start < end <= completion_through", "incompatible Event temporal bounds"
        )
    if type(matching) not in (FirstPerSubject, EveryStart):
        raise event_error(
            "an exact first_per_subject or every_start policy", "invalid matching policy"
        )
    try:
        type(matching).model_validate_json(matching.model_dump_json())
    except (TypeError, ValueError) as exc:
        raise event_error(
            "a fully validated closed matching policy", "invalid policy values"
        ) from exc
    entity = steps[0].subject
    if population is None:
        if entity.version is not None:
            raise event_error(
                "explicit admitted membership for a versioned subject Entity",
                "omitted versioned population",
            )
        population = make_population(owner, registry, entity_ref(entity.ref.path))
    admit_population(owner, registry, population, required_entity=entity)
    paths = tuple((item.source.ref.path, item.participant_path) for item in steps)
    source_ids = {
        name
        for source, path in paths
        for name in coordinates.path_entities(owner.semantic_registry, source, (path,))
    }
    captures = owner.binding_scopes.capture(
        tuple(normalize_target_entity(owner.semantic_registry, name) for name in sorted(source_ids))
    )
    population_definition = (
        population.definition_fingerprint
        if isinstance(population, LogicalDataset)
        else population.state.artifact_ref.ref
    )
    root = population._root
    sampling_authority = (
        "inherited_materialized"
        if isinstance(population, MaterializedDataset)
        else "sampled"
        if isinstance(root, LogicalRootHandle) and root.has_realizations
        else "exact"
    )
    definition = EventDefinition(
        entity,
        pattern,
        steps,
        cohort_window,
        through,
        matching,
        population_definition,
        completeness,
        d._canonical_digest(
            tuple(path_dependency_fingerprint(owner, source, (path,)) for source, path in paths)
        ),
        sampling_authority,
    )
    validate_declarations(definition)
    row, rows = event_contracts(definition, registry.get("event").ids)
    result = _make_logical_dataset(
        owner=owner,
        registry=registry,
        family_id="event",
        operator_id="session.events.match",
        inputs=(population,),
        input_roles=("population",),
        row_contract=row,
        row_set_contract=rows,
        payload=EventPayload(_token=d._CORE_TOKEN, definition=definition, captures=captures),
        requirements=(
            "event.exact_subject_membership@v1",
            "event.native_matching@v1",
            "event.complete_coverage@v1",
        ),
        dependency_facts=tuple(dict.fromkeys(f"event:{item.step.event.path}" for item in steps)),
        contract_versions=producer_contract("session.events.match").versions,
    )
    if not isinstance(result, LogicalEventDataset):
        raise event_error("paired Logical Event registration", "invalid family registration")
    return result


_FIELDS = (
    ("journey_id", "journey_identity", "string", False),
    ("completion_status", "status", "string", False),
    ("step_key", "pattern_step_identity", "string", False),
    ("event_identity", "event_occurrence_identity", "identity_tuple", True),
    ("occurred_at", "time_coordinate", "timestamp", True),
    ("elapsed_from_start", "duration_value", "duration", True),
    ("elapsed_from_previous", "duration_value", "duration", True),
)


def event_field_id(name: str) -> d.DatasetFieldId:
    return (
        IDENTITY_FIELD_ID
        if name == "entity_identity"
        else d._make_field_id(f"generated.events.match.{name}@v1")
    )


def event_contracts(
    definition: EventDefinition, ids: d._StableIdRegistry
) -> tuple[d.DatasetRowContract, d.DatasetRowSetContract]:
    fields = []
    for name, role, logical, nullable in _FIELDS:
        field_id = event_field_id(name)
        fields.append(
            d._make_field(
                field_id=field_id,
                name=name,
                role_id=role,
                identity=d._generated_identity(field_id),
                derivation_identity=field_id.value,
                logical_type_id=logical,
                physical_type_state=d._deferred_type(logical, ids=ids),
                nullable=nullable,
                ids=ids,
            )
        )
    fields.insert(2, identity_field(definition.entity, ids))
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("event", "journey", 1, ids=ids),
        schema=d._make_schema(tuple(fields)),
        coordinate_field_ids=tuple(
            event_field_id(name) for name in ("journey_id", "entity_identity", "step_key")
        ),
        key_field_ids=(event_field_id("journey_id"), event_field_id("step_key")),
        family_semantics=journey_semantics(definition),
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    event_field_id(name),
                    direction="ascending",
                    nulls="last",
                    value_order_contract_id=order,
                    ids=ids,
                )
                for name, order in (
                    ("entity_identity", "observation.identity_tuple@v1"),
                    ("journey_id", "event.journey_anchor@v1"),
                    ("step_key", "event.pattern_step@v1"),
                )
            )
        ),
    )
    return row, rows


def _validate_event(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
    semantics = row.family_semantics
    if isinstance(semantics, (EventFunnelSemantics, EventTimeToEventSemantics)):
        validate_reducer(row, rows)
        return
    expected = (
        "journey_id",
        "completion_status",
        "entity_identity",
        "step_key",
        "event_identity",
        "occurred_at",
        "elapsed_from_start",
        "elapsed_from_previous",
    )
    if (
        type(semantics) is not EventJourneySemantics
        or tuple(item.name for item in row.schema.columns) != expected
        or tuple(item.field_id for item in row.schema.columns)
        != tuple(event_field_id(name) for name in expected)
    ):
        raise event_error(
            "the exact eight-column dense Event journey schema", "invalid Event row contract"
        )
    for parameter, value in (
        ("pattern", semantics.pattern_json),
        ("matching", semantics.matching_json),
        ("completeness", semantics.completeness_json),
    ):
        size = len(value.encode("utf-8"))
        if size > 4096:
            raise EventConstructionError(
                expected="each retained Event JSON authority is at most 4096 UTF-8 bytes",
                received=f"{parameter} metadata requires {size} UTF-8 bytes",
                repair="Shorten the Pattern steps or completeness declarations so each retained JSON authority fits within 4096 UTF-8 bytes.",
                location=f"events.match.{parameter}",
            )
    if (
        row.key_field_ids != (event_field_id("journey_id"), event_field_id("step_key"))
        or row.coordinate_field_ids
        != tuple(event_field_id(name) for name in ("journey_id", "entity_identity", "step_key"))
        or not isinstance(rows.cardinality, d._KeyedCardinality)
        or rows.cardinality.row_bound.kind != "unknown"
        or rows.ordering.kind != "ordered"
    ):
        raise event_error(
            "the canonical Event journey key and derived order", "invalid Event row-set contract"
        )
    if (
        row.schema_version != 1
        or rows.schema_version != 1
        or (row.shape_id.family_id, row.shape_id.local_shape_id, row.shape_id.semantic_version)
        != ("event", "journey", 1)
        or not isinstance(rows.ordering, d._OrderedOrdering)
        or tuple(
            (term.field_id, term.direction, term.nulls, term.value_order_contract_id)
            for term in rows.ordering.terms
        )
        != tuple(
            (event_field_id(name), "ascending", "last", order)
            for name, order in (
                ("entity_identity", "observation.identity_tuple@v1"),
                ("journey_id", "event.journey_anchor@v1"),
                ("step_key", "event.pattern_step@v1"),
            )
        )
    ):
        raise event_error(
            "the exact registered journey version and derived ordering", "changed journey order"
        )
    for name, role, logical, nullable in _FIELDS:
        column = next(item for item in row.schema.columns if item.name == name)
        if (
            column.role_id != role
            or column.logical_type_id != logical
            or column.nullable is not nullable
            or column.identity != d._generated_identity(event_field_id(name))
            or column.derivation_identity != event_field_id(name).value
        ):
            raise event_error(
                "the exact Event field role, type, identity and nullability",
                "changed generated field",
            )
    subject = row.schema.columns[2]
    if (
        type(subject.identity) is not d._EntityFieldIdentity
        or subject.identity.entity_ref.path != semantics.subject_entity_ref
        or subject.identity.identity_signature != semantics.subject_identity_signature
        or subject.role_id != "entity_identity"
        or subject.logical_type_id != "identity_tuple"
        or subject.nullable
        or subject.derivation_identity != "identity.entity_identity@v1"
    ):
        raise event_error(
            "the canonical complete governed subject identity", "changed subject binding"
        )
    if (
        len(semantics.pattern.steps) != len(semantics.step_event_fingerprints)
        or len(semantics.pattern.steps) != len(semantics.source_origins)
        or not semantics.occurrence_identity_types
    ):
        raise event_error(
            "closed exact Pattern and occurrence identity authority", "incomplete journey semantics"
        )


def _facts(dataset: Dataset) -> tuple[tuple[str, str], ...]:
    semantics = dataset.row_contract.family_semantics
    if isinstance(semantics, (EventFunnelSemantics, EventTimeToEventSemantics)):
        return (
            ("shape", semantics.kind),
            ("subject", semantics.journey.subject_entity_ref),
            ("continuation", "retained field filtering and state actions"),
        )
    if not isinstance(semantics, EventJourneySemantics):
        raise event_error("Event journey row meaning", "invalid family semantics")
    return (
        ("journey", "dense exact Pattern positions"),
        ("subject", semantics.subject_entity_ref),
        ("matching", semantics.matching.kind),
        ("coverage", "require execution"),
        (
            "continuation",
            "funnel, time_to_event, select_subjects and state actions"
            if type(semantics.matching) is FirstPerSubject
            else "time_to_event and state actions",
        ),
    )


def register_event(registry: DatasetFamilyRegistry, ids: d._StableIdRegistry) -> None:
    def decode_state(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    registry.register(
        DatasetFamilyRegistration(
            family_id="event",
            logical_type=LogicalEventDataset,
            materialized_type=MaterializedEventDataset,
            shape_ids=tuple(
                d._make_shape_id("event", shape, 1, ids=ids)
                for shape in ("journey", "funnel", "time-to-event")
            ),
            owner_id="domains.event",
            ids=ids,
            row_validator=_validate_event,
            consumers=(
                ConsumerRegistration(
                    "event.compare",
                    ("current", "baseline"),
                    "delta",
                    (d._make_shape_id("event", "funnel", 1, ids=ids),),
                    ("event.complete_funnel@v1",),
                ),
                *(
                    ConsumerRegistration(
                        f"event.{method}",
                        ("input",),
                        output,
                        (d._make_shape_id("event", "journey", 1, ids=ids),),
                        ("event.exact_journey@v1",),
                    )
                    for method, output in (
                        ("funnel", "event"),
                        ("time_to_event", "event"),
                        ("select_subjects", "population"),
                    )
                ),
                ConsumerRegistration(
                    "event.where",
                    ("input",),
                    "event",
                    tuple(
                        d._make_shape_id("event", shape, 1, ids=ids)
                        for shape in ("funnel", "time-to-event")
                    ),
                    ("event.current_rows@v1",),
                ),
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode_state,
            node_payload_types=(
                EventPayload,
                EventFunnelPayload,
                EventTimeToEventPayload,
                RetainedRowsPayload,
            ),
            consumer_admission=lambda dataset, method: (
                isinstance(dataset.row_contract.family_semantics, EventJourneySemantics)
                and type(dataset.row_contract.family_semantics.matching) is FirstPerSubject
                if method in ("event.funnel", "event.select_subjects")
                else True
            ),
            contract_facts=_facts,
        )
    )
