"""Closed value predicates and contribution routes used by public relations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from marivo.analysis.core.graph import Node
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.refs import EntityKind, Ref, RelationshipKind, SemanticKind


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="an exact typed field predicate or contribution route",
        received=received,
        repair="Build predicates from relation.value and routes from declared Refs.",
        location="analysis.dsl",
    )


@dataclass(frozen=True, slots=True, eq=False)
class CategoryPredicate:
    root: Node
    value: str


@dataclass(frozen=True, slots=True, eq=False)
class CategoryField:
    root: Node

    def eq(self, value: str) -> CategoryPredicate:
        if type(value) is not str:
            raise _invalid("category literal is not a string")
        return CategoryPredicate(self.root, value)


@dataclass(frozen=True, slots=True, eq=False)
class NumericPredicate:
    root: Node
    operation: Literal["lt", "lte", "gt", "gte", "eq"]
    threshold: int | float


@dataclass(frozen=True, slots=True, eq=False)
class NumericField:
    root: Node

    def _predicate(
        self, operation: Literal["lt", "lte", "gt", "gte", "eq"], threshold: int | float
    ) -> NumericPredicate:
        if not (
            (type(threshold) is int and -(2**63) <= threshold < 2**63)
            or (type(threshold) is float and math.isfinite(threshold))
        ):
            raise _invalid("numeric threshold is not a finite int64 or float64")
        return NumericPredicate(self.root, operation, threshold)

    def lt(self, threshold: int | float) -> NumericPredicate:
        return self._predicate("lt", threshold)

    def lte(self, threshold: int | float) -> NumericPredicate:
        return self._predicate("lte", threshold)

    def gt(self, threshold: int | float) -> NumericPredicate:
        return self._predicate("gt", threshold)

    def gte(self, threshold: int | float) -> NumericPredicate:
        return self._predicate("gte", threshold)

    def eq(self, threshold: int | float) -> NumericPredicate:
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
            or len(self.routes) != 2
            or any(type(item) is not RootRouteValue for item in self.routes)
            or self.routes[0].root == self.routes[1].root
        ):
            raise _invalid("ratio requires two ordered routes with distinct contribution roots")


def root_route(
    root: Ref[EntityKind], *, through: tuple[Ref[RelationshipKind], ...]
) -> RootRouteValue:
    return RootRouteValue(root, through)


def root_routes(*items: RootRouteValue) -> RootRoutesValue:
    return RootRoutesValue(items)
