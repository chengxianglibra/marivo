"""Immutable row-expression nodes and their field provenance."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TypeAlias

from ibis.common.deferred import Resolver

from marivo._definition.types import (
    Authorization,
    FieldRead,
    SumPermission,
)
from marivo._kernel.errors import invalid
from marivo._kernel.refs import ExactRef, SemanticKind

_FIELD_KINDS = frozenset(
    {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION, SemanticKind.MEASURE}
)


@dataclass(frozen=True, slots=True)
class RowField:
    """Declared field on one contribution root, with its inherited unit and sum permission."""

    ref: ExactRef
    root: ExactRef
    unit: str | None
    permission: SumPermission
    read: FieldRead | None = None
    origins: tuple[ExactRef, ...] = dataclass_field(default=(), compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.ref.ref.kind not in _FIELD_KINDS:
            invalid(
                f"row field expected a dimension, time_dimension, or measure reference; "
                f"received {self.ref.ref.key}. Reference a declared field."
            )
        if self.root.ref.kind is not SemanticKind.ENTITY:
            invalid(
                f"row field {self.ref.ref.key} expected an entity contribution root; "
                f"received {self.root.ref.key}. Bind the field to its owning entity."
            )


@dataclass(frozen=True, slots=True)
class MeasureAssertion:
    """Explicit new unit or sum permission declared on a row expression.

    ``None`` keeps the inherited fact; only asserted values enter the
    computation fingerprint, together with their authorization source.
    """

    operand: RowNode
    unit: str | None
    permission: SumPermission | None
    authorization: Authorization
    origins: tuple[ExactRef, ...] = dataclass_field(default=(), compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.unit is None and self.permission is None:
            invalid(
                "measure assertion expected a unit or sum permission; received neither. "
                "Omit the assertion when only naming the expression."
            )


@dataclass(frozen=True, slots=True)
class RowExpression:
    """An Ibis numeric or predicate recipe over ordered semantic input slots.

    ``slot_0``, ``slot_1``, ... bind the corresponding inputs. The recipe is
    Ibis's own Resolver structure; this record introduces no numeric opcode.
    Physical values and types are supplied only during execution preparation.
    """

    recipe: Resolver
    inputs: tuple[RowField | MeasureAssertion, ...]
    origins: tuple[ExactRef, ...] = dataclass_field(default=(), compare=False, repr=False)


RowNode: TypeAlias = RowField | MeasureAssertion | RowExpression
