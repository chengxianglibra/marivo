"""Typed Event authoring values and participant-role identities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

import ibis
import ibis.expr.types as ir

from marivo.refs import EventKind, Ref, RelationshipKind, SemanticKind
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    _raise,
    repair,
)

if TYPE_CHECKING:
    from marivo.semantic._expression_binding import CompiledExpressionSidecar
    from marivo.semantic.validator import Registry

_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class Participant:
    """One Event participant declaration owned by its Event."""

    name: str
    path: tuple[Ref[RelationshipKind], ...] | None
    cardinality: Literal["one", "optional_one"]

    def __post_init__(self) -> None:
        if not _ROLE_NAME.fullmatch(self.name):
            _raise(
                ErrorKind.INVALID_EVENT_PARTICIPANT_PATH,
                "participant name must match [a-z][a-z0-9_]*",
                cls=SemanticDecoratorError,
                expected="a lowercase snake_case role name",
                received=repr(self.name),
                repair_value=repair(
                    kind="reauthor",
                    canonical_id="participant",
                    action="Replace name with a stable lowercase snake_case role name.",
                    snippet="ms.participant(name='buyer', cardinality='one')",
                ),
            )
        if self.cardinality not in {"one", "optional_one"}:
            _raise(
                ErrorKind.INVALID_EVENT_PARTICIPANT_CARDINALITY,
                "participant cardinality must be 'one' or 'optional_one'",
                cls=SemanticDecoratorError,
                expected="'one' or 'optional_one'",
                received=repr(self.cardinality),
                repair_value=repair(
                    kind="reauthor",
                    canonical_id="participant",
                    action="Choose cardinality='one' or cardinality='optional_one'.",
                    snippet="ms.participant(name='buyer', cardinality='one')",
                ),
            )
        if self.path == ():
            _raise(
                ErrorKind.INVALID_EVENT_PARTICIPANT_PATH,
                "participant path must be omitted, not an empty tuple",
                cls=SemanticDecoratorError,
                expected="omit path= for the source Entity, or pass a non-empty tuple",
                received="()",
                repair_value=repair(
                    kind="reauthor",
                    canonical_id="participant",
                    action=(
                        "Omit path= for the source Entity, or pass a non-empty "
                        "directed Relationship tuple."
                    ),
                    snippet=(
                        "ms.participant(name='buyer', path=(event_to_buyer,), cardinality='one')"
                    ),
                ),
            )
        if self.path is not None:
            if type(self.path) is not tuple:
                _raise(
                    ErrorKind.INVALID_EVENT_PARTICIPANT_PATH,
                    "participant path must be a tuple of relationship refs",
                    cls=SemanticDecoratorError,
                    expected="tuple[Ref[relationship], ...]",
                    received=type(self.path).__name__,
                    repair_value=repair(
                        kind="reauthor",
                        canonical_id="participant",
                        action="Pass participant path as a non-empty Relationship tuple.",
                        snippet=(
                            "ms.participant(name='buyer', "
                            "path=(event_to_buyer,), cardinality='one')"
                        ),
                    ),
                )
            for relationship in self.path:
                if (
                    type(relationship) is not Ref
                    or relationship.kind is not SemanticKind.RELATIONSHIP
                ):
                    _raise(
                        ErrorKind.INVALID_EVENT_PARTICIPANT_PATH,
                        "participant path must contain exact Ref[relationship] values",
                        cls=SemanticDecoratorError,
                        expected="tuple[Ref[relationship], ...]",
                        received=repr(relationship),
                        repair_value=repair(
                            kind="reauthor",
                            canonical_id="participant",
                            action=(
                                "Replace each path item with an exact directed Relationship ref."
                            ),
                            snippet=(
                                "ms.participant(name='buyer', "
                                "path=(event_to_buyer,), cardinality='one')"
                            ),
                        ),
                    )


@dataclass(frozen=True, slots=True)
class ParticipantRoleHandle:
    """Immutable identity of one named participant role on an Event."""

    event: Ref[EventKind]
    name: str

    def __post_init__(self) -> None:
        if type(self.event) is not Ref or self.event.kind is not SemanticKind.EVENT:
            _raise(
                ErrorKind.INVALID_EVENT_PARTICIPANT_PATH,
                "participant_role event must be an exact Ref[event]",
                cls=SemanticDecoratorError,
                expected="an exact Ref[event]",
                received=repr(self.event),
                constraint_id=ConstraintId.EVENT_PARTICIPANT_MEMBERSHIP,
                repair_value=repair(
                    kind="retry",
                    canonical_id="participant_role",
                    action=(
                        "Pass an exact Event ref and the lowercase snake_case name "
                        "of one of its declared participant roles."
                    ),
                    snippet=("ms.participant_role(event=payment_succeeded, name='buyer')"),
                ),
            )
        if not _ROLE_NAME.fullmatch(self.name):
            _raise(
                ErrorKind.INVALID_EVENT_PARTICIPANT_PATH,
                "participant role name must match [a-z][a-z0-9_]*",
                cls=SemanticDecoratorError,
                expected="a declared lowercase snake_case participant role name",
                received=repr(self.name),
                constraint_id=ConstraintId.EVENT_PARTICIPANT_MEMBERSHIP,
                repair_value=repair(
                    kind="retry",
                    canonical_id="participant_role",
                    action=(
                        "Use the exact lowercase snake_case role name declared "
                        "by ms.participant(...) on this Event."
                    ),
                    snippet=("ms.participant_role(event=payment_succeeded, name='buyer')"),
                ),
            )

    @property
    def key(self) -> str:
        return f"{self.event.key}#participant:{self.name}"


def participant(
    *,
    name: str,
    path: tuple[Ref[RelationshipKind], ...] | None = None,
    cardinality: Literal["one", "optional_one"],
) -> Participant:
    """Declare one named participant role inside ``@ms.event(...)``.

    Args:
        name: Stable lowercase snake-case role name.
        path: Directed non-empty relationship path from the Event source.
            Omit when the source Entity itself plays this role.
        cardinality: ``"one"`` or ``"optional_one"``.

    Returns:
        Immutable participant authoring value.

    Example:
        >>> buyer = ms.participant(
        ...     name="buyer",
        ...     path=(event_to_buyer,),
        ...     cardinality="one",
        ... )
    """

    return Participant(name=name, path=path, cardinality=cardinality)


def participant_role(*, event: Ref[EventKind], name: str) -> ParticipantRoleHandle:
    """Create the typed handle for one named Event participant role.

    Args:
        event: Exact Event ref returned by ``@ms.event(...)``.
        name: Declared participant role name.

    Returns:
        Immutable role handle resolved against the catalog by consumers.

    Example:
        >>> buyer = ms.participant_role(event=payment_succeeded, name="buyer")
    """

    return ParticipantRoleHandle(event=event, name=name)


def _event_fingerprint(
    event_ref: Ref[EventKind],
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
) -> str:
    """Return the canonical transitive fingerprint for one loaded Event."""
    from marivo.semantic.metric_graph_canonical import fingerprint
    from marivo.semantic.metric_graph_lowering import dependency_digest

    digest = dependency_digest(
        registry,
        sidecar=sidecar,
        semantic_refs=(event_ref,),
    )
    return f"sha256:{fingerprint(digest)}"


def all_rows() -> ir.BooleanValue:
    """Return the explicit unfiltered Event predicate.

    This value is valid only as the complete return expression of an
    ``@ms.event(...)`` body.
    """

    return cast("ir.BooleanValue", ibis.literal(True))


__all__ = [
    "Participant",
    "ParticipantRoleHandle",
    "all_rows",
    "participant",
    "participant_role",
]
