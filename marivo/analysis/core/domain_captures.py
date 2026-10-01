"""Frozen domain inputs for the single graph, independent of live callables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, NoReturn

from pydantic import ConfigDict

from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import BusinessOrderKind, EventKind, Ref, StateModelKind
from marivo.semantic.ir import (
    BusinessOrderIR,
    EventIR,
    StateModelIR,
    TargetDimensionContract,
    TargetEntityContract,
    TargetRelationshipContract,
)


class DomainPreparationError(AnalysisError):
    """A bound R7 input, order, coverage or execution obligation failed."""

    def __init__(self, constraint_id: str, stage: str, expected: str, received: str, repair: str):
        self.constraint_id = constraint_id
        self.stage = stage
        super().__init__(
            message="Domain preparation failed.",
            expected=expected,
            received=received,
            location=f"analysis.{stage}.{constraint_id}",
            repair=AnalysisRepair(
                kind="retry", action=repair, help_target=LiveHelpTarget(surface="analysis")
            ),
        )


def fail(constraint: str, received: str, *, stage: str = "construction") -> NoReturn:
    raise DomainPreparationError(
        f"r7.{constraint}",
        stage,
        "exact bound domain inputs and a qualified preparation",
        received,
        "Repair the named Event, participant, order, coverage or preparation binding.",
    )


@dataclass(frozen=True, slots=True)
class EntryAxisCapture:
    """Exact historical Dimension path prepared before local Journey matching."""

    dimension: TargetDimensionContract
    subject: TargetEntityContract
    path: tuple[TargetRelationshipContract, ...]
    entities: tuple[TargetEntityContract, ...]
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.dimension.is_time_dimension
            or self.dimension.parse is not None
            or self.dimension.logical_type not in ("string", "int64")
            or len(self.entities) != len(self.path) + 1
            or len(self.source_ids) != len(self.entities)
            or self.entities[0].ref != self.subject.ref
            or self.entities[-1].ref != self.dimension.entity_ref
        ):
            fail("funnel_axes", "a complete governed string/int64 entry-axis path is required")
        for left, hop, right in zip(self.entities[:-1], self.path, self.entities[1:], strict=True):
            if not (
                (
                    hop.from_entity_ref == left.ref
                    and hop.to_entity_ref == right.ref
                    and hop.cardinality in ("many_to_one", "one_to_one")
                )
                or (
                    hop.to_entity_ref == left.ref
                    and hop.from_entity_ref == right.ref
                    and hop.cardinality in ("one_to_many", "one_to_one")
                )
            ):
                fail("funnel_axes", "entry-axis paths must be directed and to-one")


@dataclass(frozen=True, slots=True)
class EventCapture:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    ref: Ref[EventKind]
    fingerprint: str
    source: TargetEntityContract
    source_id: str
    identity: tuple[TargetDimensionContract, ...]
    occurred_at: TargetDimensionContract
    participant: str
    subject: TargetEntityContract
    path: tuple[TargetRelationshipContract, ...]
    predicate_hash: str
    predicate_kind: Literal["all_rows", "filtered"]
    dependency_fingerprint: str
    definition: EventIR

    def __post_init__(self) -> None:
        if (
            not all(
                (
                    self.fingerprint,
                    self.source_id,
                    self.participant,
                    self.predicate_hash,
                    self.dependency_fingerprint,
                )
            )
            or not self.identity
        ):
            fail("input_binding", "incomplete Event capture")
        if self.occurred_at.entity_ref != self.source.ref or any(
            field.entity_ref != self.source.ref for field in self.identity
        ):
            fail("input_binding", f"Event fields do not belong to {self.source.ref.path}")
        definition = self.definition
        roles = tuple(role for role in definition.participants if role.name == self.participant)
        if (
            self.ref.path != definition.semantic_id
            or definition.source_entity != self.source.ref.path
            or definition.identity != tuple(field.ref.path for field in self.identity)
            or definition.occurred_at != self.occurred_at.ref.path
            or definition.predicate_kind != self.predicate_kind
            or definition.body_ast_hash != self.predicate_hash
            or len(roles) != 1
            or roles[0].cardinality != "one"
            or tuple(hop.ref.path for hop in self.path) != (roles[0].path or ())
            or not self.occurred_at.is_time_dimension
            or len({field.ref for field in self.identity}) != len(self.identity)
            or any(field.logical_type not in ("string", "int64") for field in self.identity)
        ):
            fail("input_binding", "Event definition, role, identity or time differs from capture")
        current = self.source.ref
        for hop in self.path:
            if hop.from_entity_ref != current or hop.cardinality not in (
                "many_to_one",
                "one_to_one",
            ):
                fail("input_binding", f"non-to-one participant path for {self.ref.path}")
            current = hop.to_entity_ref
        if current != self.subject.ref:
            fail("input_binding", f"participant does not reach {self.subject.ref.path}")


@dataclass(frozen=True, slots=True)
class OrderCapture:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    ref: Ref[BusinessOrderKind]
    fingerprint: str
    definition: BusinessOrderIR
    fields: tuple[TargetDimensionContract, ...]
    dependencies: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.fingerprint or len(self.fields) != len(self.definition.sequences):
            fail("business_order", "incomplete order capture")
        if self.ref.path != self.definition.semantic_id or any(
            field.ref.path != item.value_ref
            for field, item in zip(self.fields, self.definition.sequences, strict=True)
        ):
            fail("business_order", "order definition or sequence fields differ")
        if len({item.event_ref for item in self.definition.sequences}) != len(
            self.definition.sequences
        ):
            fail("business_order", "multiple sequence authorities for one Event")
        orders = {item.order for item in self.definition.sequences}
        if len(orders) > 1:
            fail("business_order", "incomparable sequence scales across Events")


@dataclass(frozen=True, slots=True)
class StateModelCapture:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    ref: Ref[StateModelKind]
    fingerprint: str
    definition: StateModelIR
    triggers: tuple[EventCapture, ...]
    order: OrderCapture | None
    dependencies: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        expected = {
            (item.trigger.event_ref, item.trigger.participant_role)
            for item in self.definition.inceptions
        } | {
            (item.trigger.event_ref, item.trigger.participant_role)
            for item in self.definition.transitions
        }
        actual = {(item.ref.path, item.participant) for item in self.triggers}
        if not self.fingerprint or expected != actual or len(actual) != len(self.triggers):
            fail("input_binding", "StateModel distinct trigger capture differs")
        if self.definition.business_order != (None if self.order is None else self.order.ref.path):
            fail("business_order", "StateModel order capture differs")
        if self.ref.path != self.definition.semantic_id or any(
            event.subject.ref.path != self.definition.subject for event in self.triggers
        ):
            fail("input_binding", "StateModel definition or Subject differs")
        if any(
            (event.ref.path, event.fingerprint) not in self.dependencies for event in self.triggers
        ):
            fail("input_binding", "StateModel trigger fingerprint differs")


@dataclass(frozen=True, slots=True)
class CaptureFile:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    source_id: str
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class CaptureAuthority:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    kind: Literal["duckdb_transaction", "immutable_manifest"]
    capture_id: str
    files: tuple[CaptureFile, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class EventPrecision:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    event: str
    declared_unit: Literal["s", "ms", "us", "ns"]
    source_unit: Literal["s", "ms", "us", "ns"]
    effective_unit: Literal["us"]
    conversion: Literal["native_driver_datetime"]
    possible_loss: bool
    behavior: str
