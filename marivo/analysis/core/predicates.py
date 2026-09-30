"""Closed value predicates carried in the private definition graph."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Annotated, Literal, TypeAlias

from pydantic import BeforeValidator, PlainSerializer

from marivo.analysis.core.model import Binding, reject


def _decode_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, dict) and set(value) == {"kind", "value"}:
        kind: object = value["kind"]
        text: object = value["value"]
        if kind == "decimal" and isinstance(text, str):
            try:
                return Decimal(text)
            except InvalidOperation:
                raise ValueError("Expected a valid Decimal predicate literal") from None
    raise ValueError("Expected a tagged Decimal predicate literal")


def _decimal_payload(value: Decimal) -> dict[str, str]:
    if not isinstance(value, Decimal):
        raise ValueError("Expected a Decimal predicate literal")
    return {"kind": "decimal", "value": str(value)}


_DecimalValue: TypeAlias = Annotated[
    Decimal,
    BeforeValidator(_decode_decimal),
    PlainSerializer(_decimal_payload, when_used="json"),
]


@dataclass(frozen=True, slots=True)
class TemporalLiteral:
    kind: Literal["date", "timestamp"]
    iso: str

    def __post_init__(self) -> None:
        self.resolve()

    def resolve(self) -> date | datetime:
        if self.kind == "date":
            return date.fromisoformat(self.iso)
        if self.kind == "timestamp":
            return datetime.fromisoformat(self.iso)
        raise ValueError("Expected a date or timestamp literal")


@dataclass(frozen=True, slots=True)
class ValuePredicate:
    binding: Binding
    operator: Literal["eq", "ne", "lt", "le", "gt", "ge", "is_defined", "all_of", "any_of", "not_"]
    value: int | float | _DecimalValue | str | bool | TemporalLiteral
    unknown: Literal["reject", "drop"] = "reject"

    input_index: int = 0
    right_index: int | None = None
    children: tuple[ValuePredicate, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not Binding
            or type(self.input_index) is not int
            or self.input_index < 0
            or (
                self.right_index is not None
                and (type(self.right_index) is not int or self.right_index < 0)
            )
            or type(self.children) is not tuple
            or any(type(child) is not ValuePredicate for child in self.children)
        ):
            reject(
                "exact binding, typed input positions and closed predicate children",
                repr(self),
                "Build predicates from bound fields.",
                "analysis.predicate",
            )
        if self.operator in ("all_of", "any_of", "not_"):
            if len(self.children) < (1 if self.operator == "not_" else 2) or (
                self.operator == "not_" and len(self.children) != 1
            ):
                reject(
                    "a complete closed predicate tree",
                    repr(self),
                    "Bind all operands.",
                    "analysis.predicate",
                )
            return
        if (
            bool(self.children)
            or self.operator not in ("eq", "ne", "lt", "le", "gt", "ge", "is_defined")
            or (
                (type(self.value) is int and not -(2**63) <= self.value < 2**63)
                or (type(self.value) is str and self.operator != "eq")
                or (type(self.value) is float and not isfinite(self.value))
                or (type(self.value) is Decimal and not self.value.is_finite())
                or type(self.value) not in (int, float, Decimal, str, bool, TemporalLiteral)
            )
            or self.unknown not in ("reject", "drop")
        ):
            reject(
                "a bound int64 or finite float64 predicate or exact string equality with explicit unknown policy",
                repr(self),
                "Bind an exact numeric comparison or string equality in the input scope.",
                "analysis.predicate",
            )

    @property
    def literal(self) -> int | float | Decimal | str | bool | date | datetime:
        return self.value.resolve() if isinstance(self.value, TemporalLiteral) else self.value


def leaves(predicate: ValuePredicate) -> Iterator[ValuePredicate]:
    """Visit every consuming leaf before truth composition."""
    if predicate.children:
        for child in predicate.children:
            yield from leaves(child)
    else:
        yield predicate


def compose(predicate: ValuePredicate, values: tuple[bool | None, ...]) -> bool | None:
    """Compose prevalidated leaf truth values without skipping consumption checks."""
    iterator = iter(values)

    def visit(node: ValuePredicate) -> bool | None:
        if not node.children:
            return next(iterator)
        children = tuple(visit(child) for child in node.children)
        if node.operator == "not_":
            return None if children[0] is None else not children[0]
        if node.operator == "all_of":
            return False if False in children else None if None in children else True
        return True if True in children else None if None in children else False

    return visit(predicate)
