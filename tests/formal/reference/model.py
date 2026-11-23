"""Finite mathematical inputs and observable outputs for analysis evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from math import isnan
from typing import Literal, TypeAlias

Number: TypeAlias = int | Fraction | Decimal | float
Scalar: TypeAlias = Number | bool | str | date
Value: TypeAlias = Scalar | None
Key: TypeAlias = tuple[Value, ...]
DemandMask: TypeAlias = frozenset[Key]
FailureKind: TypeAlias = Literal[
    "coverage",
    "strict_null",
    "missing_order_key",
    "duplicate_order_key",
    "missing_identity",
    "invalid_expression",
    "invalid_correspondence",
]
ReducerKind: TypeAlias = Literal[
    "sum",
    "mean",
    "count",
    "count_distinct",
    "row_count",
    "min",
    "max",
    "quantile",
    "median",
    "weighted_mean",
    "corr",
    "first",
    "last",
]


def valid_nan(value: Value) -> bool:
    return isinstance(value, float) and isnan(value)


def key_rank(key: Key) -> tuple[tuple[int, Scalar], ...]:
    """Compare same-carrier coordinates with SQL NULL first and a stable NaN class."""
    return tuple(
        (0, 0) if value is None else (2, 0) if valid_nan(value) else (1, value) for value in key
    )


def observation_key_rank(key: Key) -> tuple[tuple[int, Scalar], ...]:
    """Source observations and their witnesses sort missing coordinates last."""
    return tuple((3 if rank == 0 else rank, value) for rank, value in key_rank(key))


@dataclass(frozen=True, slots=True)
class Obligation:
    category: FailureKind
    witness: Key


@dataclass(frozen=True, slots=True)
class Occurrence:
    identity: Key
    value: Value
    weight: Value = None
    time: int | datetime | None = None


Bag: TypeAlias = tuple[Occurrence, ...]


@dataclass(frozen=True, slots=True)
class Column:
    """The domain of x is V; absence from x means NA at an existing row."""

    x: Mapping[Key, Scalar]

    @property
    def valid(self) -> frozenset[Key]:
        return frozenset(self.x)

    def at(self, key: Key) -> Value:
        return self.x.get(key)

    @classmethod
    def from_values(cls, values: Mapping[Key, Value]) -> Column:
        return cls({key: value for key, value in values.items() if value is not None})


@dataclass(frozen=True, slots=True)
class Result:
    """An ordered K, each branch's C_k, its columns (V, x), and obligations E."""

    keys: tuple[Key, ...]
    contributions: Mapping[str, Mapping[Key, Bag]]
    columns: Mapping[str, Column]
    obligations: frozenset[Obligation] = frozenset()
    presence: Mapping[str, frozenset[Key]] = field(default_factory=dict)

    def witness(self, category: FailureKind) -> Key | None:
        return min(
            (item.witness for item in self.obligations if item.category == category),
            key=observation_key_rank,
            default=None,
        )


@dataclass(frozen=True, slots=True)
class Reducer:
    kind: ReducerKind
    strict: bool = False
    include_null: bool = False
    quantile: Fraction = Fraction(1, 2)


@dataclass(frozen=True, slots=True)
class Branch:
    reducer: Reducer
    contributions: Mapping[Key, Bag]
    timed: bool = False
    required_keys: frozenset[Key] | None = None


@dataclass(frozen=True, slots=True)
class Scope:
    mode: Literal["explicit", "scalar", "grouped", "calendar"]
    population: tuple[Key, ...] = ()
    calendar_position: int | None = None


@dataclass(frozen=True, slots=True)
class LiteralValue:
    value: Value


@dataclass(frozen=True, slots=True)
class Read:
    name: str


@dataclass(frozen=True, slots=True)
class Binary:
    operation: Literal["add", "subtract", "multiply", "divide", "lt", "eq", "and", "or"]
    left: Expression
    right: Expression


@dataclass(frozen=True, slots=True)
class Unary:
    operation: Literal["not", "is_null"]
    operand: Expression


@dataclass(frozen=True, slots=True)
class IfElse:
    condition: Expression
    yes: Expression
    otherwise: Expression


@dataclass(frozen=True, slots=True)
class Coalesce:
    left: Expression
    right: Expression


Expression: TypeAlias = LiteralValue | Read | Binary | Unary | IfElse | Coalesce
