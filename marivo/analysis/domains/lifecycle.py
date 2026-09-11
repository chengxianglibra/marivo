"""Private Lifecycle history construction and immutable replay authority."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from marivo._temporal import TimeScope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.base import (
    LogicalDataset,
    MaterializedDataset,
    _dataset_repr,
    _make_logical_dataset,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import CanonicalValue, LogicalRootHandle, _LogicalNodePayload
from marivo.analysis.datasets.registry import DatasetFamilyRegistration, DatasetFamilyRegistry
from marivo.analysis.datasets.state import MaterializedDatasetState, _validate_materialized_state
from marivo.analysis.domains.completeness import (
    CompletenessDeclaration,
    SourceOriginCompletenessDeclarationV1,
)
from marivo.analysis.domains.contracts import (
    EventDefinition,
    EventJourneySemantics,
    EventPayload,
    decode_journey_semantics,
    journey_semantics,
)
from marivo.analysis.domains.event import make_match
from marivo.analysis.domains.subject import PopulationInput
from marivo.analysis.event import first_per_subject, sequence, step
from marivo.analysis.lifecycle import FromInception
from marivo.analysis.observation.contracts import (
    IDENTITY_FIELD_ID,
    ObservationOwner,
    RetainedRowsPayload,
    identity_field,
    owner_of,
    producer_contract,
)
from marivo.analysis.observation.source_bindings import BoundSourceParametersV1
from marivo.refs import Ref, SemanticKind, StateModelKind, ref
from marivo.semantic.catalog import StateModelEntry
from marivo.semantic.event import participant_role

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.domains.lifecycle_reducers import InState
    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage
    from marivo.analysis.observation.contracts import DimensionInput
    from marivo.analysis.observation.population import LogicalPopulationDataset
    from marivo.analysis.observation.predicates import AnalysisPredicate

ROLES = (
    "lifecycle_legal_transition_trace",
    "lifecycle_subject_coverage",
    "lifecycle_violation_trace",
)
PART_COLUMNS = (
    (
        "entity_identity",
        "transition_ordinal",
        "occurred_at",
        "from_model_state",
        "to_model_state",
        "trigger_event_ref",
        "trigger_event_identity",
    ),
    ("entity_identity", "classification", "inception_at", "known_through"),
    (
        "entity_identity",
        "trigger_event_ref",
        "trigger_event_identity",
        "occurred_at",
        "model_state_at_event",
        "violation_kind",
    ),
)
PART_KEYS = (
    ("entity_identity", "transition_ordinal"),
    ("entity_identity",),
    ("trigger_event_ref", "trigger_event_identity"),
)


HISTORY_FIELDS = (
    ("model_state", "status", "string", False),
    ("valid_from", "time_coordinate", "timestamp", False),
    ("valid_to", "time_coordinate", "timestamp", False),
    ("entered_by_event_ref", "event_occurrence_identity", "string", False),
    ("entered_by_event_identity", "event_occurrence_identity", "identity_tuple", False),
    ("exited_by_event_ref", "event_occurrence_identity", "string", True),
    ("exited_by_event_identity", "event_occurrence_identity", "identity_tuple", True),
    ("interval_status", "status", "string", False),
    ("left_clipped", "status", "boolean", False),
)


class LifecycleConstructionError(DatasetConstructionError):
    """A replay definition lacks exact model, identity or inception authority."""


def lifecycle_error(expected: str, received: str) -> LifecycleConstructionError:
    return LifecycleConstructionError(
        expected=expected,
        received=received,
        location="lifecycle.replay",
        repair="Use an exact current StateModel, from_inception(), explicit aware window and matching governed subject membership; provide source-origin coverage for its trigger Events.",
    )


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class LifecycleSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    source_json: str
    model_ref: str
    states: tuple[str, ...]
    initial: str
    terminals: tuple[str, ...]
    inceptions: tuple[str, ...]
    transitions: tuple[tuple[str, str, str], ...]
    seed_fingerprint: str
    kind: Literal["lifecycle/history@v1"] = field(default="lifecycle/history@v1", init=False)

    @property
    def transition_pairs(self) -> tuple[tuple[str, str], ...]:
        """Distinct modeled state pairs in first-declaration order."""
        return tuple(dict.fromkeys((a, b) for a, _, b in self.transitions))

    @property
    def source(self) -> EventJourneySemantics:
        return decode_journey_semantics(self.source_json)


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class LifecyclePayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    definition: EventDefinition
    semantics: LifecycleSemantics
    captures: tuple[BoundSourceParametersV1, ...]

    @property
    def identity_payload(self) -> CanonicalValue:
        return (
            self.definition.identity_payload(),
            ("record_and_continue@v1", ROLES, PART_COLUMNS, PART_KEYS, 1),
            json.dumps(asdict(self.semantics), sort_keys=True),
            tuple(item.identity_payload() for item in self.captures),
        )


class LogicalLifecycleDataset(LogicalDataset, _token=d._CORE_TOKEN, family_id="lifecycle"):
    """A source-free definition of canonical replay history."""

    __slots__ = ()

    def distribution(
        self,
        *,
        at: tuple[datetime, ...],
        axes: list[DimensionInput] | tuple[DimensionInput, ...] = (),
    ) -> LogicalLifecycleDataset:
        """Count states at explicit aware at instants, optionally grouped by axes.

        Args:
            at: Unique aware checkpoints inside the replay window, including its end.
            axes: Governed to-one Dimensions evaluated at each requested checkpoint.

        Returns a logical distribution. Example: ``history.distribution(at=(checkpoint,))``.
        Constraints: Exact retained subject coverage and to-one axes are required.
        """
        from marivo.analysis.domains.lifecycle_reducers import reduce

        return reduce(self, "distribution", at=at, axes=axes)

    def transitions(self) -> LogicalLifecycleDataset:
        """Count declared transition pairs; no parameters; return a logical summary.

        Example: ``history.transitions()``. Constraints: Exact legal trace required.
        """
        from marivo.analysis.domains.lifecycle_reducers import reduce

        return reduce(self, "transitions")

    def dwell(self) -> LogicalLifecycleDataset:
        """Summarize completed clipped fragments; no parameters; return a logical summary.

        Example: ``history.dwell()``. Constraints: Exact positive history intervals required.
        """
        from marivo.analysis.domains.lifecycle_reducers import reduce

        return reduce(self, "dwell")

    def violations(self) -> LogicalLifecycleDataset:
        """Project illegal triggers; no parameters; return logical violation observations.

        Example: ``history.violations()``. Constraints: Exact violation trace required.
        """
        from marivo.analysis.domains.lifecycle_reducers import reduce

        return reduce(self, "violations")

    def select_subjects(self, selection: InState) -> LogicalPopulationDataset:
        """Return complete Population membership using the exact InState selection.

        Args:
            selection: Private domain InState bound to the exact retained StateModel.

        Example: ``history.select_subjects(in_state(paid, at=checkpoint))``.
        Constraints: Any unknown member blocks execution atomically.
        """
        from marivo.analysis.domains.lifecycle_reducers import select_subjects

        return select_subjects(self, selection)

    def where(self, *predicates: AnalysisPredicate) -> LogicalLifecycleDataset:
        """Filter independent summary rows by predicates, returning a logical Dataset.

        Args:
            predicates: Conjoined predicates over the shape's admitted summary fields.

        Example: ``summary.where(gt(summary.fields.get("subject_count"), 0))``.
        Constraints: History and identity predicates are forbidden.
        """
        from marivo.analysis.domains.lifecycle_reducers import where

        return where(self, predicates)

    def execute(self) -> MaterializedLifecycleDataset:
        """Execute history and its required parts; no parameters.

        Returns: Committed history. Example: ``history.execute()``.
        Constraints: A native DuckDB source and exact replay authority are required.
        """
        return owner_of(self).action_port.execute_lifecycle(self)


class MaterializedLifecycleDataset(
    MaterializedDataset, _token=d._CORE_TOKEN, family_id="lifecycle"
):
    """Immutable retained replay history with atomic private parts."""

    __slots__ = ()

    def distribution(
        self,
        *,
        at: tuple[datetime, ...],
        axes: list[DimensionInput] | tuple[DimensionInput, ...] = (),
    ) -> LogicalLifecycleDataset:
        """Count states at explicit aware at instants, optionally grouped by axes.

        Args:
            at: Unique aware checkpoints inside the replay window, including its end.
            axes: Governed to-one Dimensions evaluated at each requested checkpoint.

        Returns a logical distribution. Example: ``history.distribution(at=(checkpoint,))``.
        Constraints: Exact retained subject coverage and to-one axes are required.
        """
        from marivo.analysis.domains.lifecycle_reducers import reduce

        return reduce(self, "distribution", at=at, axes=axes)

    def transitions(self) -> LogicalLifecycleDataset:
        """Count declared transition pairs; no parameters; return a logical summary.

        Example: ``history.transitions()``. Constraints: Exact legal trace required.
        """
        from marivo.analysis.domains.lifecycle_reducers import reduce

        return reduce(self, "transitions")

    def dwell(self) -> LogicalLifecycleDataset:
        """Summarize completed clipped fragments; no parameters; return a logical summary.

        Example: ``history.dwell()``. Constraints: Exact positive history intervals required.
        """
        from marivo.analysis.domains.lifecycle_reducers import reduce

        return reduce(self, "dwell")

    def violations(self) -> LogicalLifecycleDataset:
        """Project illegal triggers; no parameters; return logical violation observations.

        Example: ``history.violations()``. Constraints: Exact violation trace required.
        """
        from marivo.analysis.domains.lifecycle_reducers import reduce

        return reduce(self, "violations")

    def select_subjects(self, selection: InState) -> LogicalPopulationDataset:
        """Return complete Population membership using the exact InState selection.

        Args:
            selection: Private domain InState bound to the exact retained StateModel.

        Example: ``history.select_subjects(in_state(paid, at=checkpoint))``.
        Constraints: Any unknown member blocks execution atomically.
        """
        from marivo.analysis.domains.lifecycle_reducers import select_subjects

        return select_subjects(self, selection)

    def where(self, *predicates: AnalysisPredicate) -> LogicalLifecycleDataset:
        """Filter independent summary rows by predicates, returning a logical Dataset.

        Args:
            predicates: Conjoined predicates over the shape's admitted summary fields.

        Example: ``summary.where(gt(summary.fields.get("subject_count"), 0))``.
        Constraints: History and identity predicates are forbidden.
        """
        from marivo.analysis.domains.lifecycle_reducers import where

        return where(self, predicates)

    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated retained history copy; no parameters.

        Example: ``history.to_pandas()``. Constraints: Runtime read budgets apply.
        """
        return owner_of(self).action_port.to_pandas(self)

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print history bounded by max_output_bytes and return None.

        Example: ``history.show()``. Constraints: Authorized Runtime read budgets apply.
        """
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        """Return committed aggregate Lifecycle Evidence without rematching sources."""
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Return a retained Finding page with limit and cursor.

        Example: ``history.findings()``. Constraints: Lifecycle history Evidence has zero Findings.
        """
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        """Read the exact finding_id through the retained evidence owner.

        Example: ``history.finding('id')``. Constraints: Lifecycle history Findings are empty.
        """
        return owner_of(self).action_port.finding(self, finding_id)


def field_id(name: str) -> d.DatasetFieldId:
    return (
        IDENTITY_FIELD_ID
        if name == "entity_identity"
        else d._make_field_id(f"generated.lifecycle.replay.{name}@v1")
    )


def history_contracts(
    payload: LifecyclePayload, ids: d._StableIdRegistry
) -> tuple[d.DatasetRowContract, d.DatasetRowSetContract]:
    fields = (
        identity_field(payload.definition.entity, ids),
        *(
            d._make_field(
                field_id=field_id(name),
                name=name,
                role_id=role,
                identity=d._generated_identity(field_id(name)),
                derivation_identity=field_id(name).value,
                logical_type_id=logical,
                physical_type_state=d._deferred_type(logical, ids=ids),
                nullable=nullable,
                ids=ids,
            )
            for name, role, logical, nullable in HISTORY_FIELDS
        ),
    )
    keys = (field_id("entity_identity"), field_id("valid_from"))
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("lifecycle", "history", 1, ids=ids),
        schema=d._make_schema(fields),
        coordinate_field_ids=keys,
        key_field_ids=keys,
        family_semantics=payload.semantics,
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    key,
                    direction="ascending",
                    nulls="last",
                    value_order_contract_id="observation.identity_tuple@v1"
                    if key == IDENTITY_FIELD_ID
                    else "observation.scalar_order@v1",
                    ids=ids,
                )
                for key in keys
            )
        ),
    )
    return row, rows


def make_replay(
    owner: ObservationOwner,
    registry: DatasetFamilyRegistry,
    model: Ref[StateModelKind] | StateModelEntry,
    *,
    window: TimeScope,
    seed: FromInception,
    population: PopulationInput | None = None,
    completeness: tuple[CompletenessDeclaration, ...] = (),
) -> LogicalLifecycleDataset:
    if isinstance(model, StateModelEntry) and model._catalog is not owner.catalog_identity:
        raise lifecycle_error(
            "a StateModel entry from the exact current catalog", "foreign catalog entry"
        )
    model_ref = model.ref if isinstance(model, StateModelEntry) else model
    if (
        type(model_ref) is not Ref
        or model_ref.kind is not SemanticKind.STATE_MODEL
        or model_ref not in owner.sidecar.catalog_refs
    ):
        raise lifecycle_error(
            "an exact current StateModel entry or ref", "invalid or unloaded model"
        )
    model_ir = owner.semantic_registry.state_models.get(model_ref.path)
    if (
        model_ir is None
        or model_ir.semantic_id != model_ref.path
        or type(seed) is not FromInception
        or seed != FromInception()
    ):
        raise lifecycle_error(
            "a loaded StateModel and exact from_inception seed", "invalid replay authority"
        )
    if type(completeness) is not tuple or any(
        type(item) is not SourceOriginCompletenessDeclarationV1 for item in completeness
    ):
        raise lifecycle_error(
            "source-origin completeness declarations", "bounded or invalid inception coverage"
        )
    if (
        not isinstance(window, TimeScope)
        or not isinstance(window.start, datetime)
        or not isinstance(window.end, datetime)
    ):
        raise lifecycle_error("an aware datetime window", "invalid replay window")
    states = tuple(state.name for state in model_ir.states)
    initials = tuple(state.name for state in model_ir.states if state.initial)
    if (
        not states
        or len(set(states)) != len(states)
        or len(initials) != 1
        or not model_ir.inceptions
    ):
        raise lifecycle_error(
            "unique closed states, one initial state and inception triggers", "invalid StateModel"
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
        raise lifecycle_error(
            "all triggers at the exact StateModel subject", "different trigger subject"
        )
    transitions = tuple(
        (item.from_state, keys[item.trigger], item.to_state) for item in model_ir.transitions
    )
    if len({(a, b) for a, b, _ in transitions}) != len(transitions) or any(
        a not in states or c not in states for a, _, c in transitions
    ):
        raise lifecycle_error(
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


def validate_lifecycle_semantics(semantics: LifecycleSemantics) -> None:
    """Check the closed model and trigger meaning without Runtime imports."""
    if len(semantics.source_json.encode("utf-8")) > 4096:
        raise lifecycle_error(
            "at most 4096 UTF-8 bytes of retained source authority",
            "oversized Lifecycle trigger definition",
        )
    keys = {step.key for step in semantics.source.pattern.steps}
    if (
        not semantics.states
        or len(set(semantics.states)) != len(semantics.states)
        or semantics.initial not in semantics.states
        or not set(semantics.terminals).issubset(semantics.states)
        or not semantics.inceptions
        or not set(semantics.inceptions).issubset(keys)
        or semantics.seed_fingerprint != FromInception().fingerprint
    ):
        raise lifecycle_error(
            "closed model states and exact inception seed", "invalid Lifecycle model authority"
        )
    if any(
        a not in semantics.states or b not in keys or c not in semantics.states
        for a, b, c in semantics.transitions
    ) or len({(a, b) for a, b, _ in semantics.transitions}) != len(semantics.transitions):
        raise lifecycle_error(
            "deterministic declared trigger transitions", "invalid Lifecycle transition authority"
        )


def validate_history(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
    if (
        type(row.family_semantics) is not LifecycleSemantics
        or tuple(f.name for f in row.schema.columns)
        != ("entity_identity", *(f[0] for f in HISTORY_FIELDS))
        or row.key_field_ids != (IDENTITY_FIELD_ID, field_id("valid_from"))
        or rows.ordering.kind != "ordered"
    ):
        raise lifecycle_error("canonical ordered Lifecycle history", "invalid history contract")

    semantics = row.family_semantics
    validate_lifecycle_semantics(semantics)
    keys = (IDENTITY_FIELD_ID, field_id("valid_from"))
    if (
        row.schema_version != 1
        or rows.schema_version != 1
        or row.coordinate_field_ids != keys
        or (row.shape_id.family_id, row.shape_id.local_shape_id, row.shape_id.semantic_version)
        != ("lifecycle", "history", 1)
        or not isinstance(rows.cardinality, d._KeyedCardinality)
        or rows.cardinality.row_bound.kind != "unknown"
        or not isinstance(rows.ordering, d._OrderedOrdering)
    ):
        raise lifecycle_error(
            "exact history version, keys and unknown cardinality",
            "changed history row-set contract",
        )
    if tuple(
        (term.field_id, term.direction, term.nulls, term.value_order_contract_id)
        for term in rows.ordering.terms
    ) != tuple(
        (
            key,
            "ascending",
            "last",
            "observation.identity_tuple@v1"
            if key == IDENTITY_FIELD_ID
            else "observation.scalar_order@v1",
        )
        for key in keys
    ):
        raise lifecycle_error("canonical subject and interval ordering", "changed history ordering")
    for column, (name, role, logical, nullable) in zip(
        row.schema.columns[1:], HISTORY_FIELDS, strict=True
    ):
        if (
            column.field_id != field_id(name)
            or column.role_id != role
            or column.logical_type_id != logical
            or column.nullable is not nullable
            or column.identity != d._generated_identity(field_id(name))
            or column.derivation_identity != field_id(name).value
        ):
            raise lifecycle_error(
                "exact history field identity, role, type and nullability", "changed history field"
            )
    subject = row.schema.columns[0]
    if (
        type(subject.identity) is not d._EntityFieldIdentity
        or subject.identity.entity_ref.path != semantics.source.subject_entity_ref
        or subject.identity.identity_signature != semantics.source.subject_identity_signature
        or subject.field_id != IDENTITY_FIELD_ID
        or subject.role_id != "entity_identity"
        or subject.logical_type_id != "identity_tuple"
        or subject.nullable
        or subject.derivation_identity != "identity.entity_identity@v1"
    ):
        raise lifecycle_error("the complete governed subject identity", "changed history subject")


def register_lifecycle(registry: DatasetFamilyRegistry, ids: d._StableIdRegistry) -> None:
    from marivo.analysis.datasets.registry import ConsumerRegistration
    from marivo.analysis.domains.lifecycle_reducers import (
        REDUCER_TYPES,
        LifecycleReducerPayload,
        validate_reducer,
    )

    def validate(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
        if isinstance(row.family_semantics, REDUCER_TYPES):
            validate_reducer(row, rows)
        else:
            validate_history(row, rows)

    def decode(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    registry.register(
        DatasetFamilyRegistration(
            family_id="lifecycle",
            logical_type=LogicalLifecycleDataset,
            materialized_type=MaterializedLifecycleDataset,
            shape_ids=tuple(
                d._make_shape_id("lifecycle", shape, 1, ids=ids)
                for shape in ("history", "distribution", "transitions", "dwell", "violations")
            ),
            owner_id="domains.lifecycle",
            ids=ids,
            row_validator=validate,
            consumers=(
                *(
                    ConsumerRegistration(
                        f"lifecycle.{method}",
                        ("input",),
                        output,
                        (d._make_shape_id("lifecycle", "history", 1, ids=ids),),
                        ("lifecycle.exact_history@v1",),
                    )
                    for method, output in (
                        ("distribution", "lifecycle"),
                        ("transitions", "lifecycle"),
                        ("dwell", "lifecycle"),
                        ("violations", "lifecycle"),
                        ("select_subjects", "population"),
                    )
                ),
                ConsumerRegistration(
                    "lifecycle.where",
                    ("input",),
                    "lifecycle",
                    tuple(
                        d._make_shape_id("lifecycle", shape, 1, ids=ids)
                        for shape in ("distribution", "transitions", "dwell", "violations")
                    ),
                    ("lifecycle.current_rows@v1",),
                ),
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode,
            node_payload_types=(LifecyclePayload, LifecycleReducerPayload, RetainedRowsPayload),
            contract_facts=lambda dataset: (
                (
                    "meaning",
                    {
                        "history": "canonical intervals with exact legal-transition, coverage and violation roles",
                        "distribution": "known state counts and separately disclosed unknown membership at explicit checkpoints",
                        "transitions": "observed legal trigger counts; retained coverage qualifies interpretation",
                        "dwell": "completed_window_fragment_duration@v1; left-clipped completed fragments included",
                        "violations": "recorded modeled-trigger observations; zero Findings",
                    }[dataset.row_contract.shape_id.local_shape_id],
                ),
            ),
        )
    )


def decode_lifecycle_semantics(value: object) -> LifecycleSemantics:
    """Decode the closed pure history value without importing execution owners."""
    names = {
        "kind",
        "source_json",
        "model_ref",
        "states",
        "initial",
        "terminals",
        "inceptions",
        "transitions",
        "seed_fingerprint",
    }
    if not isinstance(value, dict) or set(value) != names:
        raise lifecycle_error("exact history semantics", "invalid retained model fields")

    def text(item: object) -> str:
        if type(item) is not str or not item:
            raise lifecycle_error("non-empty semantic text", "invalid model value")
        return item

    def texts(item: object) -> tuple[str, ...]:
        if not isinstance(item, (tuple, list)):
            raise lifecycle_error("immutable semantic sequence", "invalid model sequence")
        return tuple(text(x) for x in item)

    transitions: list[tuple[str, str, str]] = []
    incoming: object = value["transitions"]
    if not isinstance(incoming, (tuple, list)):
        raise lifecycle_error("exact transition triples", "invalid transition sequence")
    for item in incoming:
        parts = texts(item)
        if len(parts) != 3:
            raise lifecycle_error("exact transition triple", "invalid transition arity")
        transitions.append((parts[0], parts[1], parts[2]))
    result = LifecycleSemantics(
        _token=d._CORE_TOKEN,
        source_json=text(value["source_json"]),
        model_ref=text(value["model_ref"]),
        states=texts(value["states"]),
        initial=text(value["initial"]),
        terminals=texts(value["terminals"]),
        inceptions=texts(value["inceptions"]),
        transitions=tuple(transitions),
        seed_fingerprint=text(value["seed_fingerprint"]),
    )
    if value["kind"] != result.kind:
        raise lifecycle_error("canonical Lifecycle history", "invalid model kind")
    validate_lifecycle_semantics(result)
    return result
