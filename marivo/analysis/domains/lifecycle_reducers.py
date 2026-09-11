"""Private Lifecycle reducers over exact immutable history authority."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.fields import DatasetFieldRef, validate_field_ref
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.domains.contracts import EventAxisBinding
from marivo.analysis.domains.lifecycle import HISTORY_FIELDS, ROLES, LifecycleSemantics
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
from marivo.semantic.state_model import ModelStateHandle
from marivo.semantic.validator import normalize_target_entity

if TYPE_CHECKING:
    from marivo.analysis.domains.lifecycle import LogicalLifecycleDataset


class LifecycleReducerError(DatasetConstructionError):
    """A Lifecycle reducer lacks exact history, checkpoint or state authority."""


def reducer_error(expected: str, received: str) -> LifecycleReducerError:
    return LifecycleReducerError(
        expected=expected,
        received=received,
        location="lifecycle.reducer",
        repair="Use an intact canonical Lifecycle history, exact retained model state and explicit aware checkpoints inside its replay window. Add only governed to-one Dimension axes; filter independent summary rows.",
    )


@dataclass(frozen=True, slots=True)
class InState:
    """An exact retained model state at one aware instant."""

    state: ModelStateHandle
    at: datetime

    def __post_init__(self) -> None:
        if type(self.state) is not ModelStateHandle:
            raise reducer_error("an exact ModelStateHandle", "invalid state selector")
        object.__setattr__(self, "at", instant(self.at))


def instant(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise reducer_error("a timezone-aware datetime", "invalid Lifecycle instant")
    return value.astimezone(timezone.utc)


def in_state(state: ModelStateHandle, *, at: datetime) -> InState:
    """Select state at an aware at instant, returning an immutable InState value.

    Args:
        state: Exact StateModel state handle.
        at: A timezone-aware checkpoint inside the receiver's replay window.

    Example: ``in_state(paid, at=checkpoint)``. The receiver validates model and window.
    """
    return InState(state, at)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DistributionSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    history_json: str
    at: tuple[str, ...]
    axis_refs: tuple[str, ...]
    axis_dependency_fingerprints: tuple[str, ...]
    kind: Literal["lifecycle/distribution@v1"] = field(
        default="lifecycle/distribution@v1", init=False
    )


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class TransitionsSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    history_json: str
    kind: Literal["lifecycle/transitions@v1"] = field(
        default="lifecycle/transitions@v1", init=False
    )


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DwellSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    history_json: str
    estimand: Literal["completed_window_fragment_duration@v1"] = field(
        default="completed_window_fragment_duration@v1", init=False
    )
    kind: Literal["lifecycle/dwell@v1"] = field(default="lifecycle/dwell@v1", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ViolationsSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    history_json: str
    kind: Literal["lifecycle/violations@v1"] = field(default="lifecycle/violations@v1", init=False)


ReducerSemantics = (
    DistributionSemantics | TransitionsSemantics | DwellSemantics | ViolationsSemantics
)
REDUCER_TYPES = (DistributionSemantics, TransitionsSemantics, DwellSemantics, ViolationsSemantics)


def history_semantics(value: ReducerSemantics) -> LifecycleSemantics:
    from marivo.analysis.domains.lifecycle import decode_lifecycle_semantics

    return decode_lifecycle_semantics(json.loads(value.history_json))


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class LifecycleReducerPayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    semantics: ReducerSemantics
    axes: tuple[EventAxisBinding, ...] = ()
    captures: tuple[BoundSourceParametersV1, ...] = ()

    @property
    def identity_payload(self) -> CanonicalValue:
        return (
            json.dumps(asdict(self.semantics), sort_keys=True),
            tuple(a.identity_payload() for a in self.axes),
            tuple(c.identity_payload() for c in self.captures),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class LifecycleSelectionPayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    history: LifecycleSemantics
    selection: InState

    @property
    def identity_payload(self) -> CanonicalValue:
        return (
            json.dumps(asdict(self.history), sort_keys=True),
            self.selection.state.model.key,
            self.selection.state.name,
            self.selection.at.isoformat(),
        )


def consumed_roles(payload: LifecycleReducerPayload | LifecycleSelectionPayload) -> set[str]:
    if isinstance(payload, LifecycleSelectionPayload) or isinstance(
        payload.semantics, DistributionSemantics
    ):
        return {ROLES[1]}
    if isinstance(payload.semantics, TransitionsSemantics):
        return {ROLES[0]}
    return {ROLES[2]} if isinstance(payload.semantics, ViolationsSemantics) else set()


FIELDS = {
    "distribution": (
        ("as_of", "time_coordinate", "timestamp", False),
        ("model_state", "status", "string", False),
        ("subject_count", "additive_count", "int64", False),
        ("known_subject_count", "additive_count", "int64", False),
        ("coverage_censored_subject_count", "additive_count", "int64", False),
        ("share", "rate_value", "float64", True),
    ),
    "transitions": (
        ("from_model_state", "status", "string", False),
        ("to_model_state", "status", "string", False),
        ("transition_count", "additive_count", "int64", False),
        ("share_of_modeled_transitions", "rate_value", "float64", True),
    ),
    "dwell": (
        ("model_state", "status", "string", False),
        *(
            (name, "additive_count", "int64", False)
            for name in (
                "interval_count",
                "completed_count",
                "right_censored_count",
                "coverage_censored_count",
                "left_clipped_completed_count",
            )
        ),
        *(
            (name, "duration_value", "duration", True)
            for name in ("mean_duration", "median_duration", "p90_duration")
        ),
    ),
    "violations": (
        ("trigger_event_ref", "event_occurrence_identity", "string", False),
        ("trigger_event_identity", "event_occurrence_identity", "identity_tuple", False),
        ("occurred_at", "time_coordinate", "timestamp", False),
        ("model_state_at_event", "status", "string", False),
        ("violation_kind", "status", "string", False),
    ),
}
KEYS = {
    "distribution": ("as_of", "model_state"),
    "transitions": ("from_model_state", "to_model_state"),
    "dwell": ("model_state",),
    "violations": ("trigger_event_ref", "trigger_event_identity"),
}


def field_id(shape: str, name: str) -> d.DatasetFieldId:
    return (
        IDENTITY_FIELD_ID
        if name == "entity_identity"
        else d._make_field_id(f"generated.lifecycle.{shape}.{name}@v1")
    )


def require_history(dataset: Dataset) -> LifecycleSemantics:
    meaning = dataset.row_contract.family_semantics
    if type(meaning) is not LifecycleSemantics:
        raise reducer_error("canonical Lifecycle history", "a non-history input shape")
    return meaning


def check_at(history: LifecycleSemantics, at: datetime) -> datetime:
    at = instant(at)
    if (
        not datetime.fromisoformat(history.source.cohort_start)
        <= at
        <= datetime.fromisoformat(history.source.cohort_end)
    ):
        raise reducer_error("an instant in the closed replay window", "out-of-window instant")
    return at


def _checked(dataset: Dataset) -> LogicalLifecycleDataset:
    from marivo.analysis.domains.lifecycle import LogicalLifecycleDataset

    if not isinstance(dataset, LogicalLifecycleDataset):
        raise reducer_error("paired Lifecycle registration", "invalid reducer family")
    return dataset


def reduce(
    dataset: Dataset,
    shape: Literal["distribution", "transitions", "dwell", "violations"],
    *,
    at: tuple[datetime, ...] = (),
    axes: list[DimensionInput] | tuple[DimensionInput, ...] = (),
) -> LogicalLifecycleDataset:
    history = require_history(dataset)
    history_json = json.dumps(asdict(history), sort_keys=True)
    owner, ids = owner_of(dataset), dataset._registration.ids
    axis_fields: list[d.DatasetField] = []
    bindings: list[EventAxisBinding] = []
    captures: tuple[BoundSourceParametersV1, ...] = ()
    if type(axes) not in (tuple, list):
        raise reducer_error("a list or tuple of Dimensions", "invalid axes")
    if axes:
        owner = source_owner_of(dataset)
        subject = normalize_target_entity(
            owner.semantic_registry, history.source.subject_entity_ref
        )
        if subject.identity_signature != history.source.subject_identity_signature:
            raise reducer_error("retained subject signature", "changed subject signature")
        for operand in axes:
            axis = coordinates.normalize_dimension_input(owner, operand, time=False)
            path = coordinates.functional_path(
                owner.semantic_registry,
                subject.ref.path,
                axis.entity_ref.path,
                allow_versioned_source=True,
                allow_versioned_target=True,
                allow_versioned_intermediates=True,
            )
            fingerprint = path_dependency_fingerprint(owner, subject.ref.path, (path,))
            bindings.append(EventAxisBinding(axis, subject, path, fingerprint))
            axis_fields.append(dimension_field(axis, ids, dependency_fingerprint=fingerprint))
        reserved = {
            "entity_identity",
            "classification",
            "inception_at",
            "known_through",
            *(f[0] for f in HISTORY_FIELDS),
            *(f[0] for fields in FIELDS.values() for f in fields),
        }
        names = [f.name for f in axis_fields]
        if len(set(names)) != len(names) or set(names) & reserved:
            raise reducer_error(
                "distinct non-colliding Dimension names", "duplicate or reserved axes"
            )
        captures = owner.binding_scopes.capture(
            tuple(
                normalize_target_entity(owner.semantic_registry, n)
                for n in coordinates.path_entities(
                    owner.semantic_registry, subject.ref.path, tuple(b.path for b in bindings)
                )
            )
        )
    semantics: ReducerSemantics
    if shape == "distribution":
        if type(at) is not tuple or not at:
            raise reducer_error("a non-empty tuple of aware instants", "invalid checkpoints")
        points = tuple(sorted(check_at(history, p) for p in at))
        if len(set(points)) != len(points):
            raise reducer_error("duplicate-free instants", "duplicate checkpoints")
        semantics = DistributionSemantics(
            _token=d._CORE_TOKEN,
            history_json=history_json,
            at=tuple(p.isoformat() for p in points),
            axis_refs=tuple(b.dimension.ref.path for b in bindings),
            axis_dependency_fingerprints=tuple(b.dependency_fingerprint for b in bindings),
        )
    elif shape == "transitions":
        semantics = TransitionsSemantics(_token=d._CORE_TOKEN, history_json=history_json)
    elif shape == "dwell":
        semantics = DwellSemantics(_token=d._CORE_TOKEN, history_json=history_json)
    else:
        semantics = ViolationsSemantics(_token=d._CORE_TOKEN, history_json=history_json)
    generated = tuple(
        d._make_field(
            field_id=field_id(shape, n),
            name=n,
            role_id=r,
            identity=d._generated_identity(field_id(shape, n)),
            derivation_identity=field_id(shape, n).value,
            logical_type_id=t,
            physical_type_state=d._deferred_type(t, ids=ids),
            nullable=null,
            ids=ids,
        )
        for n, r, t, null in FIELDS[shape]
    )
    identity = (dataset.schema.columns[0],) if shape == "violations" else ()
    fields = (*axis_fields, *identity, *generated)
    keys = (*tuple(f.field_id for f in axis_fields), *(field_id(shape, n) for n in KEYS[shape]))
    order = (
        (
            field_id(shape, "as_of"),
            *tuple(f.field_id for f in axis_fields),
            field_id(shape, "model_state"),
        )
        if shape == "distribution"
        else keys
        if shape != "violations"
        else (IDENTITY_FIELD_ID, field_id(shape, "occurred_at"), *keys)
    )
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("lifecycle", shape, 1, ids=ids),
        schema=d._make_schema(fields),
        coordinate_field_ids=keys
        if shape != "violations"
        else (IDENTITY_FIELD_ID, *keys, field_id(shape, "occurred_at")),
        key_field_ids=keys,
        family_semantics=semantics,
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._unknown_row_bound()),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    k,
                    direction="ascending",
                    nulls="last",
                    value_order_contract_id="observation.identity_tuple@v1"
                    if next(f for f in fields if f.field_id == k).logical_type_id
                    == "identity_tuple"
                    else "observation.scalar_order@v1",
                    ids=ids,
                )
                for k in order
            )
        ),
    )
    return _checked(
        construct_operator(
            owner=owner,
            registry=dataset._registry,
            operator_id=f"lifecycle.{shape}",
            inputs=(dataset,),
            row_contract=row,
            row_set_contract=rows,
            contract_versions=producer_contract(f"lifecycle.{shape}").versions,
            payload=LifecycleReducerPayload(
                _token=d._CORE_TOKEN, semantics=semantics, axes=tuple(bindings), captures=captures
            ),
        )
    )


def select_subjects(dataset: Dataset, selection: InState) -> LogicalPopulationDataset:
    history = require_history(dataset)
    if type(selection) is not InState:
        raise reducer_error("an exact private InState", "unsupported selector")
    selection = InState(selection.state, check_at(history, selection.at))
    if (
        selection.state.model.path != history.model_ref
        or selection.state.name not in history.states
    ):
        raise reducer_error("a state of the exact retained StateModel", "state/model mismatch")
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id(
            "population", "entity-membership", 1, ids=dataset._registration.ids
        ),
        schema=d._make_schema((dataset.schema.columns[0],)),
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
        operator_id="lifecycle.select_subjects",
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=rows,
        contract_versions=producer_contract("lifecycle.select_subjects").versions,
        payload=LifecycleSelectionPayload(
            _token=d._CORE_TOKEN, history=history, selection=selection
        ),
    )
    if not isinstance(result, LogicalPopulationDataset):
        raise reducer_error("canonical Population registration", "invalid selection family")
    return result


def where(dataset: Dataset, predicates: tuple[AnalysisPredicate, ...]) -> LogicalLifecycleDataset:
    semantics = dataset.row_contract.family_semantics
    if not isinstance(semantics, REDUCER_TYPES):
        raise reducer_error("independent Lifecycle summary rows", "structural history filtering")

    def resolve(operand: PredicateField) -> d.DatasetField:
        f = (
            validate_field_ref(dataset, operand)
            if isinstance(operand, DatasetFieldRef)
            else retained_field(dataset, operand)
        )
        if f.role_id in ("entity_identity", "event_occurrence_identity"):
            raise reducer_error("an admitted summary field", "identity predicate")
        return f

    predicate = bind_predicates(predicates, resolve)
    return _checked(
        construct_operator(
            owner=owner_of(dataset),
            registry=dataset._registry,
            operator_id="lifecycle.where",
            inputs=(dataset,),
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            contract_versions=producer_contract("lifecycle.where").versions,
            payload=RetainedRowsPayload(_token=d._CORE_TOKEN, predicate=predicate),
        )
    )


def validate_reducer(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
    semantics = row.family_semantics
    if not isinstance(semantics, REDUCER_TYPES):
        raise reducer_error("closed Lifecycle reducer semantics", "invalid semantics")
    history = history_semantics(semantics)
    shape = row.shape_id.local_shape_id
    if shape not in FIELDS or str(row.shape_id) != semantics.kind:
        raise reducer_error("the exact reducer shape", "changed shape")
    axes = len(semantics.axis_refs) if isinstance(semantics, DistributionSemantics) else 0
    fields = row.schema.columns
    generated = fields[axes + (1 if shape == "violations" else 0) :]
    if len(generated) != len(FIELDS[shape]) or any(
        (f.name, f.role_id, f.logical_type_id, f.nullable) != definition
        or f.field_id != field_id(shape, f.name)
        or f.derivation_identity != f.field_id.value
        or f.identity != d._generated_identity(f.field_id)
        for f, definition in zip(generated, FIELDS[shape], strict=True)
    ):
        raise reducer_error("the exact ordered reducer schema", "changed fields")
    if isinstance(semantics, DistributionSemantics):
        points = tuple(check_at(history, datetime.fromisoformat(p)) for p in semantics.at)
        if (
            not points
            or tuple(sorted(set(points))) != points
            or len(set(semantics.axis_refs)) != len(semantics.axis_refs)
            or len(semantics.axis_refs) != len(semantics.axis_dependency_fingerprints)
        ):
            raise reducer_error(
                "canonical unique checkpoints and axes", "invalid distribution meaning"
            )
        for f, ref in zip(fields[:axes], semantics.axis_refs, strict=True):
            if (
                f.role_id != "dimension"
                or f.name != ref.rsplit(".", 1)[-1]
                or not isinstance(f.identity, d._CatalogFieldIdentity)
                or f.identity.identity_id != f"dimension:{ref}"
            ):
                raise reducer_error("exact governed axis fields", "changed axis binding")
    expected_keys = (
        *tuple(f.field_id for f in fields[:axes]),
        *(field_id(shape, n) for n in KEYS[shape]),
    )
    expected_coordinates = (
        expected_keys
        if shape != "violations"
        else (IDENTITY_FIELD_ID, *expected_keys, field_id(shape, "occurred_at"))
    )
    order = (
        (
            field_id(shape, "as_of"),
            *tuple(f.field_id for f in fields[:axes]),
            field_id(shape, "model_state"),
        )
        if shape == "distribution"
        else (IDENTITY_FIELD_ID, field_id(shape, "occurred_at"), *expected_keys)
        if shape == "violations"
        else expected_keys
    )
    if (
        row.key_field_ids != expected_keys
        or row.coordinate_field_ids != expected_coordinates
        or not isinstance(rows.ordering, d._OrderedOrdering)
        or tuple(t.field_id for t in rows.ordering.terms) != order
        or any(t.direction != "ascending" or t.nulls != "last" for t in rows.ordering.terms)
    ):
        raise reducer_error("exact reducer coordinates, keys and ordering", "changed row contract")
    if shape == "violations":
        subject = fields[0]
        if (
            subject.field_id != IDENTITY_FIELD_ID
            or subject.name != "entity_identity"
            or subject.nullable
            or subject.role_id != "entity_identity"
            or not isinstance(subject.identity, d._EntityFieldIdentity)
            or subject.identity.entity_ref.path != history.source.subject_entity_ref
            or subject.identity.identity_signature != history.source.subject_identity_signature
        ):
            raise reducer_error("exact retained subject identity", "changed violation identity")


def filterable_field(value: d.DatasetField) -> bool:
    return any(
        value.field_id == field_id(shape, name)
        and (value.name, value.role_id, value.logical_type_id, value.nullable)
        == (name, role, logical, nullable)
        for shape, definitions in FIELDS.items()
        for name, role, logical, nullable in definitions
        if role != "event_occurrence_identity"
    )


def is_fragment_duration(row: d.DatasetRowContract, field: d.DatasetField) -> bool:
    """Own floating microseconds only for the three completed-fragment statistics."""
    return (
        isinstance(row.family_semantics, DwellSemantics)
        and field.name in ("mean_duration", "median_duration", "p90_duration")
        and field.field_id == field_id("dwell", field.name)
        and field.logical_type_id == "duration"
    )
