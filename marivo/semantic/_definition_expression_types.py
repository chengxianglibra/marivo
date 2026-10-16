"""Typed description of already validated restricted expression syntax.

These values describe a declaration body; they never execute it or certify
data. They back the bounded expression facts on resolved values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.refs import (
    EntityKind,
    FieldKind,
    Ref,
    SemanticKindTag,
)
from marivo.render import Card, RenderableResult


class _Value(RenderableResult):
    def _repr_identity(self) -> str:
        return type(self).__name__.removeprefix("_")

    def _card(self) -> Card:
        return Card(identity=self._repr_identity(), available=(".show()",))


@dataclass(frozen=True, repr=False)
class _Column(_Value):
    entity: Ref[EntityKind]
    name: str
    kind: Literal["column"] = "column"


@dataclass(frozen=True, repr=False)
class _Field(_Value):
    ref: Ref[FieldKind]
    entity: Ref[EntityKind]
    kind: Literal["field"] = "field"


@dataclass(frozen=True, repr=False)
class _Literal(_Value):
    value_type: Literal["str", "int", "float", "bool", "none"]
    value: str | int | float | bool | None
    kind: Literal["literal"] = "literal"


@dataclass(frozen=True, repr=False)
class _Cast(_Value):
    operand: ExpressionNode
    data_type: str
    kind: Literal["cast"] = "cast"


@dataclass(frozen=True, repr=False)
class _Unary(_Value):
    operator: Literal["positive", "negative", "invert"]
    operand: ExpressionNode
    kind: Literal["unary"] = "unary"


@dataclass(frozen=True, repr=False)
class _Binary(_Value):
    operator: Literal[
        "+",
        "-",
        "*",
        "/",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "&",
        "|",
        "^",
    ]
    left: ExpressionNode
    right: ExpressionNode
    kind: Literal["binary"] = "binary"


@dataclass(frozen=True, repr=False)
class _IfElse(_Value):
    condition: ExpressionNode
    when_true: ExpressionNode
    when_false: ExpressionNode
    kind: Literal["ifelse"] = "ifelse"


ExpressionNode: TypeAlias = _Column | _Field | _Literal | _Cast | _Unary | _Binary | _IfElse


@dataclass(frozen=True, repr=False)
class _ExpressionDisplay(_Value):
    text: str
    bindings: tuple[tuple[str, Ref[SemanticKindTag]], ...]


@dataclass(frozen=True, repr=False)
class _SupportedExpression(_Value):
    expression: ExpressionNode
    display: _ExpressionDisplay | None = None
    status: Literal["supported"] = "supported"
    kind: Literal["expression"] = "expression"


@dataclass(frozen=True, repr=False)
class _UnsupportedExpression(_Value):
    reason: Literal["unsupported_syntax", "description_unavailable", "limit_exceeded"]
    display: _ExpressionDisplay | None = None
    status: Literal["unsupported"] = "unsupported"
    kind: Literal["expression"] = "expression"


ExpressionDescription: TypeAlias = _SupportedExpression | _UnsupportedExpression
