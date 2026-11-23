"""Freeze semantic time comparisons in the receiving Session's report timezone."""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import replace
from datetime import date

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Just,
    Resolver,
    Sequence,
    UnaryOperator,
    Variable,
)

from marivo._definition.deferred import RescaleResolver, attribute_name
from marivo._definition.expressions import (
    MeasureAssertion,
    PredicateExpression,
    RowExpression,
    RowField,
)
from marivo._definition.types import TimeInstantFieldRead
from marivo._kernel.refs import SemanticKind
from marivo.analysis.model.errors import reject
from marivo.analysis.model.time_grid import exact_instant

_COMPARISONS = (operator.eq, operator.ne, operator.lt, operator.le, operator.gt, operator.ge)


def _children(recipe: Resolver, visit: Callable[[Resolver], Resolver]) -> Resolver:
    if isinstance(recipe, BinaryOperator):
        return BinaryOperator(recipe.func, visit(recipe.left), visit(recipe.right))
    if isinstance(recipe, UnaryOperator):
        return UnaryOperator(recipe.func, visit(recipe.arg))
    if isinstance(recipe, Attr):
        return Attr(visit(recipe.obj), recipe.name)
    if isinstance(recipe, Call):
        return Call(
            visit(recipe.func),
            *(visit(arg) for arg in recipe.args),
            **{key: visit(value) for key, value in recipe.kwargs.items()},
        )
    if isinstance(recipe, Sequence):
        return Sequence(tuple(visit(value) for value in recipe.values))
    if isinstance(recipe, RescaleResolver):
        return RescaleResolver(visit(recipe.recipe), recipe.divisor, recipe.unit)
    return recipe


class _TemporalPredicate:
    def __init__(self, node: RowExpression, timezone: str, location: str) -> None:
        self.inputs = list(node.inputs)
        self.instant_slots: dict[str, str] = {}
        self.changed = False
        self.timezone = timezone
        self.location = location

    def time_slot(self, recipe: Variable) -> bool:
        value = self.inputs[int(recipe.name.removeprefix("slot_"))]
        return isinstance(value, RowField) and value.ref.ref.kind is SemanticKind.TIME_DIMENSION

    def temporal(self, recipe: Resolver) -> bool:
        if isinstance(recipe, Variable):
            return self.time_slot(recipe)
        if isinstance(recipe, Call):
            if isinstance(recipe.func, Attr):
                name = attribute_name(recipe.func)
                if name in ("coalesce", "nullif", "name"):
                    return self.temporal(recipe.func.obj)
                if name == "ifelse":
                    return any(
                        self.temporal(arg) for arg in (*recipe.args, *recipe.kwargs.values())
                    )
                if name != "cases":
                    return False
            return any(
                isinstance(arg, Sequence) and len(arg.values) == 2 and self.temporal(arg.values[1])
                for arg in recipe.args
            ) or any(self.temporal(value) for value in recipe.kwargs.values())
        return False

    def boundary(self, recipe: Just) -> Resolver:
        value = recipe.value
        if not isinstance(value, (str, date)):
            reject(
                "an ISO boundary, date or aware datetime",
                repr(value),
                "Use the same boundary inputs as session.time_scope(...).",
                self.location,
                canonical_id="session.instant",
            )
        self.changed = True
        return Just(exact_instant(value, self.timezone, location=self.location))

    def time_call(self, recipe: Call) -> Resolver:
        name = attribute_name(recipe.func) if isinstance(recipe.func, Attr) else "cases"
        receiver = recipe.func
        if isinstance(receiver, Attr):
            receiver = Attr(
                self.visit(
                    receiver.obj,
                    name in ("coalesce", "nullif", "name", "cases") and self.temporal(receiver.obj),
                ),
                receiver.name,
            )

        def argument(arg: Resolver) -> Resolver:
            if name == "cases" and isinstance(arg, Sequence) and len(arg.values) == 2:
                match_time = isinstance(recipe.func, Attr) and self.temporal(recipe.func.obj)
                return Sequence(
                    (self.visit(arg.values[0], match_time), self.visit(arg.values[1], True))
                )
            return self.visit(arg, name != "name")

        return Call(
            receiver,
            *(argument(arg) for arg in recipe.args),
            **{key: self.visit(value, name != "name") for key, value in recipe.kwargs.items()},
        )

    def instant_input(self, recipe: Variable) -> Resolver:
        original = self.inputs[int(recipe.name.removeprefix("slot_"))]
        assert isinstance(original, RowField)
        if isinstance(original.read, TimeInstantFieldRead):
            return recipe
        name = self.instant_slots.get(recipe.name)
        if name is None:
            read = original.read
            instant_read = (
                TimeInstantFieldRead(original.ref, original.root)
                if read is None
                else TimeInstantFieldRead(read.field, read.entity, read.path, read.at)
            )
            name = f"slot_{len(self.inputs)}"
            self.inputs.append(replace(original, read=instant_read))
            self.instant_slots[recipe.name] = name
        return Variable(name)

    def visit(self, recipe: Resolver, as_time: bool = False) -> Resolver:
        if as_time and isinstance(recipe, Just):
            return recipe if recipe.value is None else self.boundary(recipe)
        if as_time and isinstance(recipe, Variable) and self.time_slot(recipe):
            return self.instant_input(recipe)
        if isinstance(recipe, BinaryOperator) and recipe.func in _COMPARISONS:
            if (
                self.temporal(recipe.left)
                and isinstance(recipe.right, Just)
                and recipe.right.value is not None
            ):
                return BinaryOperator(
                    recipe.func, self.visit(recipe.left, True), self.boundary(recipe.right)
                )
            if (
                self.temporal(recipe.right)
                and isinstance(recipe.left, Just)
                and recipe.left.value is not None
            ):
                return BinaryOperator(
                    recipe.func, self.boundary(recipe.left), self.visit(recipe.right, True)
                )
        if as_time and isinstance(recipe, Call):
            return self.time_call(recipe)
        return _children(recipe, self.visit)


def _row(node: RowExpression, timezone: str, location: str) -> RowExpression:
    normalizer = _TemporalPredicate(node, timezone, location)
    recipe = normalizer.visit(node.recipe)
    if not normalizer.changed:
        return node
    active: list[RowField | MeasureAssertion] = []
    names: dict[str, str] = {}

    def compact(value: Resolver) -> Resolver:
        if isinstance(value, Variable):
            name = names.get(value.name)
            if name is None:
                name = f"slot_{len(active)}"
                active.append(normalizer.inputs[int(value.name.removeprefix("slot_"))])
                names[value.name] = name
            return Variable(name)
        return _children(value, compact)

    return replace(node, recipe=compact(recipe), inputs=tuple(active))


def normalize_temporal_predicate(
    predicate: PredicateExpression, timezone: str, *, location: str
) -> PredicateExpression:
    """Copy time comparisons to exact instants without changing ordinary fields."""
    return replace(
        predicate,
        inputs=tuple(
            _row(item, timezone, location) if isinstance(item, RowExpression) else item
            for item in predicate.inputs
        ),
    )
