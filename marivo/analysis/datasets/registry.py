"""Private closed family and consumer registration; no public Help activation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo.analysis.datasets.descriptors import (
    DatasetFieldId,
    DatasetRowContract,
    DatasetRowSetContract,
    DatasetShapeId,
    _StableIdRegistry,
    _validate_registered_contract,
    _validate_row_contract_pair,
)
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.datasets.handles import (
    _check_label,
    _LogicalNodePayload,
    _validate_payload_type,
)
from marivo.analysis.datasets.state import MaterializedDatasetState

if TYPE_CHECKING:
    from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset


def _registration_error(expected: str, received: str) -> DatasetRegistrationError:
    return DatasetRegistrationError(
        expected=expected,
        received=received,
        repair="Assemble one complete family pair and its exact consumer contracts before construction.",
        location="dataset.registry",
    )


def _immutable_sequence(value: object) -> None:
    if type(value) is not tuple:
        raise _registration_error(
            "immutable tuple registration facts", "mutable registration container"
        )


@dataclass(frozen=True, slots=True)
class ConsumerRegistration:
    id: str
    input_roles: tuple[str, ...]
    output_family: str
    accepted_shape_ids: tuple[DatasetShapeId, ...]
    requirements: tuple[str, ...] = ()
    discoverable: bool = True
    operand_shape_ids: tuple[tuple[DatasetShapeId, ...], ...] = ()

    def __post_init__(self) -> None:
        if type(self.discoverable) is not bool:
            raise _registration_error("exact consumer disclosure flag", "invalid disclosure flag")
        _immutable_sequence(self.operand_shape_ids)
        if self.operand_shape_ids:
            for shapes in self.operand_shape_ids:
                _immutable_sequence(shapes)
                if not shapes or len(set(shapes)) != len(shapes):
                    raise _registration_error(
                        "nonempty unique shapes for each role", "invalid role shapes"
                    )
            if (
                len(self.operand_shape_ids) != len(self.input_roles)
                or self.operand_shape_ids[0] != self.accepted_shape_ids
            ):
                raise _registration_error(
                    "exact receiver and ordered operand shapes", "inconsistent role shapes"
                )
        for sequence in (self.input_roles, self.accepted_shape_ids, self.requirements):
            _immutable_sequence(sequence)
        for value in (self.id, self.output_family, *self.input_roles, *self.requirements):
            _check_label(value)
        if (
            not self.id
            or not self.output_family
            or not self.input_roles
            or (len(set(self.input_roles)) != len(self.input_roles))
            or not self.accepted_shape_ids
        ):
            raise _registration_error(
                "complete consumer and unique ordered input roles", "invalid consumer"
            )


@dataclass(frozen=True, slots=True)
class DatasetFamilyRegistration:
    family_id: str
    logical_type: type[LogicalDataset]
    materialized_type: type[MaterializedDataset]
    shape_ids: tuple[DatasetShapeId, ...]
    owner_id: str
    ids: _StableIdRegistry
    row_validator: Callable[[DatasetRowContract, DatasetRowSetContract], None]
    consumers: tuple[ConsumerRegistration, ...]
    repr_renderer: Callable[[Dataset], str]
    materialized_state_decoder: Callable[[MaterializedDatasetState], MaterializedDatasetState]
    unique_tie_breakers: tuple[tuple[DatasetFieldId, ...], ...] = ()
    node_payload_types: tuple[type[_LogicalNodePayload], ...] = ()
    consumer_admission: Callable[[Dataset, str], bool] | None = None
    contract_facts: Callable[[Dataset], tuple[tuple[str, str], ...]] | None = None

    def __post_init__(self) -> None:
        from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset

        for sequence in (
            self.shape_ids,
            self.consumers,
            self.unique_tie_breakers,
            self.node_payload_types,
        ):
            _immutable_sequence(sequence)
        if len(set(self.node_payload_types)) != len(self.node_payload_types):
            raise _registration_error(
                "unique exact owner payload classes", "duplicate payload type"
            )
        for payload_type in self.node_payload_types:
            _validate_payload_type(payload_type)
        for callback in (self.consumer_admission, self.contract_facts):
            if callback is not None and not callable(callback):
                raise _registration_error(
                    "callable owner admission and contract facts", "invalid callback"
                )
        for tie_breaker in self.unique_tie_breakers:
            _immutable_sequence(tie_breaker)

        _check_label(self.family_id)
        _check_label(self.owner_id)

        if (
            not self.family_id
            or not self.owner_id
            or not self.shape_ids
            or len(set(self.shape_ids)) != len(self.shape_ids)
            or any(shape.family_id != self.family_id for shape in self.shape_ids)
        ):
            raise _registration_error(
                "one family with unique qualified shapes", "invalid family shape registration"
            )
        if not isinstance(self.logical_type, type) or not isinstance(self.materialized_type, type):
            raise _registration_error("paired concrete state classes", "missing family partner")
        if (
            not issubclass(self.logical_type, LogicalDataset)
            or not issubclass(self.materialized_type, MaterializedDataset)
            or issubclass(self.logical_type, MaterializedDataset)
            or issubclass(self.materialized_type, LogicalDataset)
            or self.logical_type is LogicalDataset
            or self.materialized_type is MaterializedDataset
            or self.logical_type._declared_family != self.family_id
            or self.materialized_type._declared_family != self.family_id
            or self.logical_type.__abstractmethods__
            or self.materialized_type.__abstractmethods__
        ):
            raise _registration_error(
                "sealed concrete logical/materialized pair of this family", "invalid state pair"
            )
        if not all(
            callable(value)
            for value in (self.row_validator, self.repr_renderer, self.materialized_state_decoder)
        ):
            raise _registration_error(
                "family validator, renderer and state decoder", "missing owner callback"
            )
        for family_type in (self.logical_type, self.materialized_type):
            forbidden = (
                "render",
                "__getitem__",
                "__iter__",
                "__len__",
                "__array__",
                "__dataframe__",
                "__add__",
                "__sub__",
                "__mul__",
                "__truediv__",
                "__matmul__",
                "plan",
                "sql",
                "ibis",
                "relation",
                "future",
                "receipt",
                "task",
                "shape",
            )
            if any(hasattr(family_type, name) for name in forbidden) or (
                family_type.__eq__ is not object.__eq__
                or family_type.__hash__ is not None
                or family_type.__setattr__ is not Dataset.__setattr__
                or family_type.__delattr__ is not Dataset.__delattr__
                or family_type.__dictoffset__ != 0
            ):
                raise _registration_error(
                    "immutable non-collecting Dataset protocol", "invalid family protocol"
                )
        if any(
            hasattr(self.logical_type, name)
            for name in ("show", "to_pandas", "evidence_digest", "findings", "finding")
        ) or hasattr(self.materialized_type, "execute"):
            raise _registration_error(
                "state-specific execution and read methods", "wrong-state family method"
            )
        ids = tuple(consumer.id for consumer in self.consumers)
        if len(set(ids)) != len(ids):
            raise _registration_error("unique family consumers", "duplicate consumer")
        for consumer in self.consumers:
            if not set(consumer.accepted_shape_ids).issubset(self.shape_ids):
                raise _registration_error(
                    "consumer shapes belonging to the registered family", "foreign consumer shape"
                )
            if not consumer.discoverable:
                continue
            method_name = consumer.id.rsplit(".", 1)[-1]
            if not callable(getattr(self.logical_type, method_name, None)) or not callable(
                getattr(self.materialized_type, method_name, None)
            ):
                raise _registration_error(
                    "each consumer method on both paired states", "missing paired operator"
                )

    def validate(self, row: DatasetRowContract, row_set: DatasetRowSetContract) -> None:
        if row.shape_id not in self.shape_ids:
            raise _registration_error("registered family/shape/version", "unregistered row shape")
        _validate_registered_contract(row, row_set, ids=self.ids)
        _validate_row_contract_pair(row, row_set, unique_tie_breakers=self.unique_tie_breakers)
        self.row_validator(row, row_set)

    def validate_payload(self, payload: _LogicalNodePayload | None) -> None:
        if payload is not None and type(payload) not in self.node_payload_types:
            raise _registration_error("an exact family-owned node payload", "unregistered payload")

    def admits(self, dataset: Dataset, consumer_id: str) -> bool:
        if self.consumer_admission is None:
            return True
        admitted = self.consumer_admission(dataset, consumer_id)
        if type(admitted) is not bool:
            raise _registration_error("a boolean owner admission decision", "invalid admission")
        return admitted

    def facts_for(self, dataset: Dataset) -> tuple[tuple[str, str], ...]:
        facts = () if self.contract_facts is None else self.contract_facts(dataset)
        _immutable_sequence(facts)
        for fact in facts:
            _immutable_sequence(fact)
            if len(fact) != 2 or any(type(value) is not str for value in fact):
                raise _registration_error("immutable named owner contract facts", "invalid fact")
        return facts


class DatasetFamilyRegistry:
    """Private assembly registry explicitly frozen before pure Dataset construction."""

    __slots__ = ("_frozen", "_registrations")

    def __init__(self) -> None:
        self._registrations: dict[str, DatasetFamilyRegistration] = {}
        self._frozen = False

    @property
    def registrations(self) -> tuple[DatasetFamilyRegistration, ...]:
        return tuple(self._registrations[key] for key in sorted(self._registrations))

    def register(self, registration: DatasetFamilyRegistration) -> None:
        if self._frozen:
            raise _registration_error(
                "registry assembly before Dataset construction", "frozen registry"
            )
        if type(registration) is not DatasetFamilyRegistration:
            raise _registration_error("exact paired family registration", "invalid registration")
        for existing in self.registrations:
            if (
                existing.family_id == registration.family_id
                or existing.owner_id == registration.owner_id
                or existing.logical_type is registration.logical_type
                or existing.materialized_type is registration.materialized_type
                or set(existing.shape_ids).intersection(registration.shape_ids)
            ):
                raise _registration_error(
                    "one owner and paired classes per family", "duplicate registration"
                )
        self._registrations[registration.family_id] = registration

    def get(self, family_id: str) -> DatasetFamilyRegistration:
        try:
            return self._registrations[family_id]
        except KeyError:
            raise _registration_error("registered Dataset family", "unknown family") from None

    def freeze(self) -> None:
        """Finish assembly after all registered output families are available."""
        if self._frozen:
            return
        for registration in self.registrations:
            for consumer in registration.consumers:
                self.get(consumer.output_family)
                for shapes in consumer.operand_shape_ids:
                    for shape in shapes:
                        if shape not in self.get(shape.family_id).shape_ids:
                            raise _registration_error(
                                "registered exact operand shapes", "unregistered role shape"
                            )
        self._frozen = True

    def require_frozen(self) -> None:
        """Check the construction precondition without changing assembly state."""
        if not self._frozen:
            raise DatasetRegistrationError(
                expected="an explicitly frozen family registry",
                received="unfrozen registry",
                repair=(
                    "Finish private family registration and call registry.freeze() "
                    "before constructing a Dataset."
                ),
                location="dataset.registry",
            )

    def consumers_for(
        self, dataset: Dataset, *, include_internal: bool = False
    ) -> tuple[ConsumerRegistration, ...]:
        registration = self.get(dataset.kind)
        return tuple(
            sorted(
                (
                    item
                    for item in registration.consumers
                    if dataset.row_contract.shape_id in item.accepted_shape_ids
                    and registration.admits(dataset, item.id)
                    and (include_internal or item.discoverable)
                ),
                key=lambda item: item.id,
            )
        )

    def consumer(self, dataset: Dataset, consumer_id: str) -> ConsumerRegistration:
        for consumer in self.consumers_for(dataset, include_internal=True):
            if consumer.id == consumer_id:
                return consumer
        raise _registration_error(
            "registered consumer of the current shape", "unavailable operator"
        )


REGISTRY = DatasetFamilyRegistry()
