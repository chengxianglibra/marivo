"""Closed value predicates and contribution routes used by public relations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo.analysis.core.graph import Node
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.refs import EntityKind, Ref, RelationshipKind, SemanticKind

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_relation import Relation


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="an exact typed field predicate or contribution route",
        received=received,
        repair="Build predicates from relation.value and routes from declared Refs.",
        location="analysis.dsl",
    )


class _BoundValue:
    __slots__ = ()

    def __repr__(self) -> str:
        if isinstance(self, CompositePredicate):
            identity = f"{self.operation} operands={len(self.children)}"
        elif isinstance(
            self,
            (
                CategoryField,
                NumericField,
                BooleanField,
                TemporalField,
                CategoryPredicate,
                NumericPredicate,
                ScalarPredicate,
                StatePredicate,
            ),
        ):
            identity = "input=" + self.root.identity[:24]
        else:
            identity = "bound"
        return f"<{type(self).__name__} kind=bound {identity}; use .show()>"

    def show(self) -> None:
        """Display this bound field or predicate identity.

        Args: None.
        Returns: None.
        Example: ``values.value.is_defined().show()``.
        Constraints: No business rows are read; all input checks occur at execution.
        """
        print(repr(self))


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class CategoryPredicate(_BoundValue):
    root: Node
    value: str | int | CategoryField
    relation: Relation | None = None

    def __bool__(self) -> bool:
        raise _invalid("predicates have no implicit truth value")


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class CategoryField(_BoundValue):
    root: Node

    relation: Relation | None = None

    def is_defined(self) -> StatePredicate:
        """Check this field's Cell label without consuming its value.

        Args: None.
        Returns: A bound StatePredicate, true only for Defined.
        Example: ``values.where(values.value.is_defined())``.
        Constraints: Does not exempt another predicate from its consumption requirements.
        """
        return StatePredicate(self.root, self.relation)

    def eq(self, value: str | int | CategoryField) -> CategoryPredicate:
        """Build a bound eq comparison for this field.

        Args: value: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.eq(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        if type(value) not in (str, int, CategoryField):
            raise _invalid("category literal is not a string")
        return CategoryPredicate(self.root, value, self.relation)


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class NumericPredicate(_BoundValue):
    root: Node
    operation: Literal["lt", "lte", "gt", "gte", "eq"]
    threshold: int | float | Decimal | NumericField
    relation: Relation | None = None

    def __bool__(self) -> bool:
        raise _invalid("predicates have no implicit truth value")


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class NumericField(_BoundValue):
    root: Node
    relation: Relation | None = None

    def is_defined(self) -> StatePredicate:
        """Check this field's Cell label without consuming its value.

        Args: None.
        Returns: A bound StatePredicate, true only for Defined.
        Example: ``values.where(values.value.is_defined())``.
        Constraints: Does not exempt another predicate from its consumption requirements.
        """
        return StatePredicate(self.root, self.relation)

    def _predicate(
        self,
        operation: Literal["lt", "lte", "gt", "gte", "eq"],
        threshold: int | float | Decimal | NumericField,
    ) -> NumericPredicate:
        if not (
            (type(threshold) is int and -(2**63) <= threshold < 2**63)
            or (type(threshold) is float and math.isfinite(threshold))
            or (type(threshold) is Decimal and threshold.is_finite())
            or isinstance(threshold, NumericField)
        ):
            raise _invalid("numeric threshold is not a finite int64 or float64")
        return NumericPredicate(self.root, operation, threshold, self.relation)

    def lt(self, threshold: int | float | Decimal | NumericField) -> NumericPredicate:
        """Build a bound lt comparison for this field.

        Args: threshold: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.lt(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("lt", threshold)

    def lte(self, threshold: int | float | Decimal | NumericField) -> NumericPredicate:
        """Build a bound lte comparison for this field.

        Args: threshold: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.lte(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("lte", threshold)

    def gt(self, threshold: int | float | Decimal | NumericField) -> NumericPredicate:
        """Build a bound gt comparison for this field.

        Args: threshold: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.gt(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("gt", threshold)

    def gte(self, threshold: int | float | Decimal | NumericField) -> NumericPredicate:
        """Build a bound gte comparison for this field.

        Args: threshold: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.gte(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("gte", threshold)

    def eq(self, threshold: int | float | Decimal | NumericField) -> NumericPredicate:
        """Build a bound eq comparison for this field.

        Args: threshold: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.eq(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("eq", threshold)


@dataclass(frozen=True, slots=True)
class RootRouteValue:
    root: Ref[EntityKind]
    through: tuple[Ref[RelationshipKind], ...]

    def __post_init__(self) -> None:
        if (
            type(self.root) is not Ref
            or self.root.kind is not SemanticKind.ENTITY
            or type(self.through) is not tuple
            or not self.through
            or any(
                type(item) is not Ref or item.kind is not SemanticKind.RELATIONSHIP
                for item in self.through
            )
        ):
            raise _invalid("route requires an Entity and nonempty ordered Relationship Refs")


@dataclass(frozen=True, slots=True)
class RootRoutesValue:
    routes: tuple[RootRouteValue, ...]

    def __post_init__(self) -> None:
        if (
            type(self.routes) is not tuple
            or not self.routes
            or any(type(item) is not RootRouteValue for item in self.routes)
            or len({item.root for item in self.routes}) != len(self.routes)
        ):
            raise _invalid("one to sixteen ordered routes over distinct contribution roots")


def root_route(
    root: Ref[EntityKind], *, through: tuple[Ref[RelationshipKind], ...]
) -> RootRouteValue:
    return RootRouteValue(root, through)


def root_routes(*items: RootRouteValue) -> RootRoutesValue:
    return RootRoutesValue(items)


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ScalarPredicate(_BoundValue):
    root: Node
    operator: Literal["eq", "lt", "le", "gt", "ge"]
    value: bool | date | datetime | BooleanField | TemporalField
    relation: Relation | None = None

    def __bool__(self) -> bool:
        raise _invalid("predicates have no implicit truth value")


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class BooleanField(_BoundValue):
    root: Node

    relation: Relation | None = None

    def is_defined(self) -> StatePredicate:
        """Check this field's Cell label without consuming its value.

        Args: None.
        Returns: A bound StatePredicate, true only for Defined.
        Example: ``values.where(values.value.is_defined())``.
        Constraints: Does not exempt another predicate from its consumption requirements.
        """
        return StatePredicate(self.root, self.relation)

    def eq(self, value: bool | BooleanField) -> ScalarPredicate:
        """Build a bound eq comparison for this field.

        Args: value: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.eq(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        if type(value) not in (bool, BooleanField):
            raise _invalid("boolean equality requires a bool literal")
        return ScalarPredicate(self.root, "eq", value, self.relation)


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class TemporalField(_BoundValue):
    root: Node

    relation: Relation | None = None

    def is_defined(self) -> StatePredicate:
        """Check this field's Cell label without consuming its value.

        Args: None.
        Returns: A bound StatePredicate, true only for Defined.
        Example: ``values.where(values.value.is_defined())``.
        Constraints: Does not exempt another predicate from its consumption requirements.
        """
        return StatePredicate(self.root, self.relation)

    def _predicate(
        self,
        operator: Literal["eq", "lt", "le", "gt", "ge"],
        value: date | datetime | TemporalField,
    ) -> ScalarPredicate:
        if type(value) not in (date, datetime, TemporalField):
            raise _invalid("temporal predicates require a date or datetime literal")
        if isinstance(value, datetime) and value.utcoffset() is None:
            raise _invalid("timestamp predicates require a timezone-aware datetime literal")
        return ScalarPredicate(self.root, operator, value, self.relation)

    def eq(self, value: date | datetime | TemporalField) -> ScalarPredicate:
        """Build a bound eq comparison for this field.

        Args: value: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.eq(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("eq", value)

    def lt(self, value: date | datetime | TemporalField) -> ScalarPredicate:
        """Build a bound lt comparison for this field.

        Args: value: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.lt(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("lt", value)

    def lte(self, value: date | datetime | TemporalField) -> ScalarPredicate:
        """Build a bound lte comparison for this field.

        Args: value: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.lte(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("le", value)

    def gt(self, value: date | datetime | TemporalField) -> ScalarPredicate:
        """Build a bound gt comparison for this field.

        Args: value: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.gt(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("gt", value)

    def gte(self, value: date | datetime | TemporalField) -> ScalarPredicate:
        """Build a bound gte comparison for this field.

        Args: value: A compatible typed literal or field.
        Returns: An immutable bound predicate.
        Example: ``predicate = values.value.gte(other.value)``.
        Constraints: Requires matching types, units and complete keys; no implicit conversion.
        """
        return self._predicate("ge", value)


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class StatePredicate(_BoundValue):
    root: Node
    relation: Relation | None = None

    def __bool__(self) -> bool:
        raise _invalid("predicates have no implicit truth value")


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class CompositePredicate(_BoundValue):
    operation: Literal["all_of", "any_of", "not_"]
    children: tuple[BoundPredicate, ...]

    def __bool__(self) -> bool:
        raise _invalid("predicates have no implicit truth value")


BoundPredicate: TypeAlias = (
    CategoryPredicate | NumericPredicate | ScalarPredicate | StatePredicate | CompositePredicate
)


def all_of(*predicates: BoundPredicate) -> BoundPredicate:
    """Require every bound condition after checking all inputs.

    Args: predicates: At least two typed relation predicates.
    Returns: An immutable conjunction.
    Example: ``mv.all_of(values.value.gt(0), categories.value.eq("A"))``.
    Constraints: No child exempts another child from consumption checks.
    """
    return _composite("all_of", predicates)


def any_of(*predicates: BoundPredicate) -> BoundPredicate:
    """Accept any bound condition after checking all inputs.

    Args: predicates: At least two typed relation predicates.
    Returns: An immutable disjunction.
    Example: ``mv.any_of(values.value.lt(0), values.value.gt(10))``.
    Constraints: Every child must be consumable on the complete receiver domain.
    """
    return _composite("any_of", predicates)


def not_(predicate: BoundPredicate) -> BoundPredicate:
    """Negate one bound condition after checking its inputs.

    Args: predicate: One typed relation predicate.
    Returns: An immutable negation.
    Example: ``mv.not_(values.value.eq(0))``.
    Constraints: Negation does not change input consumption requirements.
    """
    return _composite("not_", (predicate,))


def _composite(
    operation: Literal["all_of", "any_of", "not_"], children: tuple[BoundPredicate, ...]
) -> CompositePredicate:
    if len(children) < (1 if operation == "not_" else 2) or any(
        not isinstance(
            child,
            (
                CategoryPredicate,
                NumericPredicate,
                ScalarPredicate,
                StatePredicate,
                CompositePredicate,
            ),
        )
        for child in children
    ):
        raise _invalid("a combinator requires its closed bound predicate operands")
    return CompositePredicate(operation, children)
