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
from marivo._definition.types import TimeInstantFieldRead, TimeScopeFieldRead
from marivo.analysis.core.model import reject
from marivo.analysis.dsl.temporal import exact_instant
from marivo.refs import SemanticKind

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


def _row(node: RowExpression, timezone: str, location: str) -> RowExpression:
    inputs = list(node.inputs)
    instant_slots: dict[str, str] = {}
    changed = False

    def time_slot(recipe: Variable) -> bool:
        value = inputs[int(recipe.name.removeprefix("slot_"))]
        return (
            isinstance(value, RowField)
            and value.ref.ref.kind is SemanticKind.TIME_DIMENSION
            and not isinstance(value.read, TimeScopeFieldRead)
        )

    def temporal(recipe: Resolver) -> bool:
        if isinstance(recipe, Variable):
            return time_slot(recipe)
        if isinstance(recipe, Call):
            if isinstance(recipe.func, Attr):
                name = attribute_name(recipe.func)
                if name in ("coalesce", "nullif", "name"):
                    return temporal(recipe.func.obj)
                if name == "ifelse":
                    return any(temporal(arg) for arg in (*recipe.args, *recipe.kwargs.values()))
                if name != "cases":
                    return False
            return any(
                isinstance(arg, Sequence) and len(arg.values) == 2 and temporal(arg.values[1])
                for arg in recipe.args
            ) or any(temporal(value) for value in recipe.kwargs.values())
        return False

    def boundary(recipe: Just) -> Resolver:
        nonlocal changed
        value = recipe.value
        if not isinstance(value, (str, date)):
            reject(
                "an ISO boundary, date or aware datetime",
                repr(value),
                "Use the same boundary inputs as session.time_scope(...).",
                location,
                canonical_id="session.instant",
            )
        changed = True
        return Just(exact_instant(value, timezone, location=location))

    def visit(recipe: Resolver, as_time: bool = False) -> Resolver:
        if as_time and isinstance(recipe, Just):
            return recipe if recipe.value is None else boundary(recipe)
        if as_time and isinstance(recipe, Variable) and time_slot(recipe):
            original = inputs[int(recipe.name.removeprefix("slot_"))]
            assert isinstance(original, RowField)
            if isinstance(original.read, TimeInstantFieldRead):
                return recipe
            name = instant_slots.get(recipe.name)
            if name is None:
                read = original.read
                instant_read = (
                    TimeInstantFieldRead(original.ref, original.root)
                    if read is None
                    else TimeInstantFieldRead(read.field, read.entity, read.path, read.at)
                )
                name = f"slot_{len(inputs)}"
                inputs.append(replace(original, read=instant_read))
                instant_slots[recipe.name] = name
            return Variable(name)
        if isinstance(recipe, BinaryOperator) and recipe.func in _COMPARISONS:
            if (
                temporal(recipe.left)
                and isinstance(recipe.right, Just)
                and recipe.right.value is not None
            ):
                return BinaryOperator(recipe.func, visit(recipe.left, True), boundary(recipe.right))
            if (
                temporal(recipe.right)
                and isinstance(recipe.left, Just)
                and recipe.left.value is not None
            ):
                return BinaryOperator(recipe.func, boundary(recipe.left), visit(recipe.right, True))
        if as_time and isinstance(recipe, Call):
            name = attribute_name(recipe.func) if isinstance(recipe.func, Attr) else "cases"
            receiver = recipe.func
            if isinstance(receiver, Attr):
                receiver = Attr(
                    visit(
                        receiver.obj,
                        name in ("coalesce", "nullif", "name", "cases") and temporal(receiver.obj),
                    ),
                    receiver.name,
                )

            def argument(arg: Resolver) -> Resolver:
                if name == "cases" and isinstance(arg, Sequence) and len(arg.values) == 2:
                    match_time = isinstance(recipe.func, Attr) and temporal(recipe.func.obj)
                    return Sequence((visit(arg.values[0], match_time), visit(arg.values[1], True)))
                return visit(arg, name != "name")

            return Call(
                receiver,
                *(argument(arg) for arg in recipe.args),
                **{key: visit(value, name != "name") for key, value in recipe.kwargs.items()},
            )
        return _children(recipe, visit)

    recipe = visit(node.recipe)
    if not changed:
        return node
    active: list[RowField | MeasureAssertion] = []
    names: dict[str, str] = {}

    def compact(value: Resolver) -> Resolver:
        if isinstance(value, Variable):
            name = names.get(value.name)
            if name is None:
                name = f"slot_{len(active)}"
                active.append(inputs[int(value.name.removeprefix("slot_"))])
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
