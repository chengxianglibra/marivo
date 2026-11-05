"""Lexical time slots and immutable substitution in semantic recipes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, NoReturn

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Item,
    Resolver,
    Sequence,
    UnaryOperator,
)

from marivo._definition.deferred import (
    DeclarationResolver,
    NamedResolver,
    RescaleResolver,
    TimeSlotResolver,
    attribute_name,
)
from marivo._definition.types import ExactRef
from marivo._symbolic import SymbolResolver
from marivo.refs import SemanticKind
from marivo.semantic.errors import ErrorKind, SemanticExpressionError


class TimeParameter:
    """Annotation for a keyword-only time slot in a Metric declaration.

    Use `def revenue(*, event: ms.TimeParameter): ...` in a Metric declaration.
    Bind it inside another Metric body, or bind the loaded Metric to a loaded
    time field. Declaration syntax creates the slots. They cannot be
    instantiated or used as general row fields.
    """

    def __new__(cls) -> NoReturn:
        raise TypeError("Declare a keyword-only ms.TimeParameter on an @ms.metric() function.")


@dataclass(frozen=True, slots=True)
class _TimeUse:
    kind: Literal["contribution", "native_sample", "calendar_sample", "sample_order"]
    root: ExactRef
    required_field: ExactRef | None = None


def binding_error(
    message: str,
    *,
    expected: str,
    received: str,
    parameters: tuple[str, ...],
    action: str,
    refs: tuple[str, ...] = (),
    unbound: bool = False,
) -> NoReturn:
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_EXPRESSION_VALUE,
        message=message,
        expected=expected,
        received=received,
        action=action,
        help_target="metric.bind_time",
        refs=refs,
        details={
            "parameters": parameters,
            **({"unbound_time_parameters": True} if unbound else {}),
        },
    )


def _map(recipe: Resolver, visit: Callable[[Resolver], Resolver]) -> Resolver:
    if isinstance(recipe, NamedResolver):
        return NamedResolver(recipe.reference, visit(recipe.recipe))
    if isinstance(recipe, DeclarationResolver):
        return DeclarationResolver(visit(recipe.recipe), recipe.declaration)
    if isinstance(recipe, RescaleResolver):
        return RescaleResolver(visit(recipe.recipe), recipe.divisor, recipe.unit)
    if isinstance(recipe, Call):
        return Call(
            visit(recipe.func),
            *(visit(arg) for arg in recipe.args),
            **{key: visit(value) for key, value in recipe.kwargs.items()},
        )
    if isinstance(recipe, Attr):
        return Attr(visit(recipe.obj), attribute_name(recipe))
    if isinstance(recipe, BinaryOperator):
        return BinaryOperator(recipe.func, visit(recipe.left), visit(recipe.right))
    if isinstance(recipe, UnaryOperator):
        return UnaryOperator(recipe.func, visit(recipe.arg))
    if isinstance(recipe, Sequence):
        values = tuple(visit(item) for item in recipe.values)
        return Sequence(list(values) if recipe.typ is list else values)
    if isinstance(recipe, Item):
        indexer: object = recipe.indexer
        if isinstance(indexer, Resolver):
            return Item(visit(recipe.obj), visit(indexer))
    return recipe


def _slot_refs(recipe: Resolver) -> tuple[TimeSlotResolver, ...]:
    slots: dict[TimeSlotResolver, None] = {}

    def visit(child: Resolver) -> Resolver:
        if isinstance(child, TimeSlotResolver):
            slots.setdefault(child, None)
        else:
            _map(child, visit)
        return child

    visit(recipe)
    return tuple(slots)


def _interface(slots: tuple[TimeSlotResolver, ...]) -> dict[str, TimeSlotResolver]:
    names: dict[str, TimeSlotResolver] = {}
    for slot in slots:
        previous = names.setdefault(slot.name, slot)
        if previous != slot:
            binding_error(
                "Different Metric time slots have the same name.",
                expected="one lexical slot per exposed parameter name",
                received=f"{previous.owner}.{slot.name} and {slot.owner}.{slot.name}",
                parameters=(slot.name,),
                refs=(previous.owner, slot.owner),
                action=(
                    f"On each operand separately, use .bind_time({slot.name}=...) with its chosen "
                    f"TimeDimension. To publish a shared formula, declare (*, {slot.name}: "
                    f"ms.TimeParameter) on the outer Metric and connect each child using "
                    f".bind_time({slot.name}={slot.name}) before composing them."
                ),
            )
    return names


def _has_unresolved_symbol(recipe: Resolver, *, metric_only: bool = False) -> bool:
    found = False

    def visit(child: Resolver) -> Resolver:
        nonlocal found
        if isinstance(child, SymbolResolver):
            found |= not metric_only or child.reference.kind is SemanticKind.METRIC
        else:
            _map(child, visit)
        return child

    visit(recipe)
    return found


def _replace_slots(recipe: Resolver, bindings: Mapping[TimeSlotResolver, Resolver]) -> Resolver:
    if isinstance(recipe, TimeSlotResolver):
        return bindings.get(recipe, recipe)
    if isinstance(recipe, (NamedResolver, DeclarationResolver)) and any(
        slot in bindings for slot in _slot_refs(recipe)
    ):
        # This is a distinct anonymous instance; retaining a DeclaredMetricRef
        # here would collapse two bindings of the same template into one node.
        return _replace_slots(recipe.recipe, bindings)
    return _map(recipe, lambda child: _replace_slots(child, bindings))


def _bind(recipe: Resolver, bindings: Mapping[str, Resolver]) -> Resolver:
    from marivo._definition.deferred import _captured_field_kind

    if not bindings:
        binding_error(
            "No Metric time parameters were supplied.",
            expected="at least one remaining time parameter and its field",
            received="bind_time()",
            parameters=tuple(_interface(_slot_refs(recipe))),
            action="Pass a remaining parameter by name, or use the expression as it is.",
        )
    for name, value in bindings.items():
        if _captured_field_kind(value) is not SemanticKind.TIME_DIMENSION:
            received = type(value).__name__
            from ibis.common.deferred import Just

            if isinstance(value, Just):
                received = type(value.value).__name__
            binding_error(
                "Metric time bindings require a time field.",
                expected="a resolved TimeDimension or a lexical TimeParameter in a declaration",
                received=f"{name}={received}",
                parameters=(name,),
                action=f"Pass the chosen TimeDimension to bind_time({name}=...).",
            )
    if _has_unresolved_symbol(recipe, metric_only=True):
        return Call(Attr(recipe, "bind_time"), **dict(bindings))
    slots = _interface(_slot_refs(recipe))
    unexpected = tuple(name for name in bindings if name not in slots)
    if unexpected:
        binding_error(
            "Metric time parameter is unknown or already bound.",
            expected=", ".join(slots) or "no remaining time parameters",
            received=", ".join(unexpected),
            parameters=unexpected,
            action="Bind only the remaining names shown by .show(), or start with the original template.",
        )
    replacements = {slots[name]: value for name, value in bindings.items()}
    for source, value in replacements.items():
        if isinstance(value, TimeSlotResolver) and value in slots.values():
            binding_error(
                "Metric time parameters cannot refer to their own template slots.",
                expected="a slot declared by the outer Metric",
                received=f"{source.name}={value.name}",
                parameters=(source.name,),
                action="Connect the child parameter to a distinct outer declaration slot.",
            )
    _validate_concrete_bindings(recipe, replacements)
    return _replace_slots(recipe, replacements)


def _validate_concrete_bindings(
    recipe: Resolver, bindings: Mapping[TimeSlotResolver, Resolver]
) -> None:
    from ibis.common.deferred import Deferred

    from marivo._definition.types import RowAttributeTime
    from marivo.semantic._deferred_semantics import consume_field, consume_metric

    concrete = {
        slot: value
        for slot, value in bindings.items()
        if not isinstance(value, TimeSlotResolver) and not _has_unresolved_symbol(value)
    }
    if not concrete or _has_unresolved_symbol(recipe):
        return
    template = consume_metric(Deferred(recipe), allow_open=True, allow_boolean=True)
    for slot, uses in template.time_parameters:
        value = concrete.get(slot)
        if value is None:
            continue
        field = consume_field(Deferred(value))
        read = field.field_read
        for use in uses:
            if field.root.ref != use.root.ref:
                expected = f"a TimeDimension on {use.root.ref.key}"
            elif use.kind != "contribution" and read is not None and (read.path or read.at):
                expected = f"a native unanchored TimeDimension on {use.root.ref.key}"
            elif use.required_field is not None and field.reference.ref != use.required_field.ref:
                expected = use.required_field.ref.key
            elif read is not None and isinstance(read.at, RowAttributeTime):
                expected = "a native TimeDimension or a supported fixed attribute anchor"
            else:
                continue
            binding_error(
                "Time field does not satisfy the Metric parameter constraints.",
                expected=expected,
                received=f"{slot.name}={field.reference.ref.key} at {use.kind}",
                parameters=(slot.name,),
                refs=(slot.owner, field.reference.ref.key),
                action=f"Start from the original template and pass {expected} as {slot.name}.",
            )


def prepare_time_recipe(recipe: Resolver) -> tuple[Resolver, tuple[TimeSlotResolver, ...]]:
    """Resolve bind calls and return free slots in first occurrence order."""

    def visit(child: Resolver) -> Resolver:
        if isinstance(child, Call) and isinstance(child.func, Attr):
            name = attribute_name(child.func)
            if name == "bind_time":
                return _bind(
                    visit(child.func.obj),
                    {name: visit(value) for name, value in child.kwargs.items()},
                )
        return _map(child, visit)

    prepared = visit(recipe)
    return prepared, tuple(_interface(_slot_refs(prepared)).values())


def validate_time_positions(recipe: Resolver) -> None:
    """Keep slots out of general expressions and helper arguments."""

    def visit(child: Resolver, allowed: bool = False) -> Resolver:
        if isinstance(child, TimeSlotResolver):
            if not allowed:
                binding_error(
                    "TimeParameter used outside a supported position.",
                    expected="reduction time, per field, sample order_by, or child bind_time value",
                    received=f"{child.owner}.{child.name}",
                    parameters=(child.name,),
                    action="Use a concrete TimeDimension for predicates, attribute anchors, or row arithmetic.",
                )
            return child
        if isinstance(child, Call) and isinstance(child.func, Attr):
            name = attribute_name(child.func)
            visit(child.func.obj)

            def visit_arg(value: Resolver, per_field: bool) -> None:
                if per_field and isinstance(value, Sequence):
                    for item in value.values:
                        visit(item, True)
                else:
                    visit(value, per_field)

            for index, value in enumerate(child.args):
                visit_arg(value, name == "per" and index == 0)
            for key, value in child.kwargs.items():
                if name == "per" and key == "by":
                    visit_arg(value, True)
                    continue
                visit(
                    value,
                    name == "bind_time"
                    or (
                        key in {"time", "order_by"}
                        and name
                        in {
                            "sum",
                            "mean",
                            "min",
                            "max",
                            "median",
                            "quantile",
                            "count",
                            "count_distinct",
                            "approx_nunique",
                            "approx_median",
                            "approx_quantile",
                            "first",
                            "last",
                            "weighted_mean",
                        }
                    ),
                )
            return child
        _map(child, visit)
        return child

    visit(recipe)


def validate_declaration_slots(
    recipe: Resolver, declared: tuple[TimeSlotResolver, ...], *, unresolved: bool = False
) -> None:
    validate_time_positions(recipe)
    _, slots = prepare_time_recipe(recipe)
    escaped = tuple(slot.name for slot in slots if slot not in declared)
    unused = tuple(slot.name for slot in declared if slot not in slots)
    if escaped or (unused and not unresolved):
        binding_error(
            "Metric declaration does not match its lexical time slots.",
            expected=", ".join(slot.name for slot in declared) or "no free time parameters",
            received="; ".join(
                item
                for item in (
                    f"foreign: {', '.join(escaped)}" if escaped else "",
                    f"unused: {', '.join(unused)}" if unused else "",
                )
                if item
            ),
            parameters=(*escaped, *unused),
            action="Use each declared slot and bind or explicitly connect every child parameter.",
        )


def qualify_declaration_slots(recipe: DeclarationResolver, owner: str) -> DeclarationResolver:
    """Replace process-local source coordinates with the published identity."""
    slots = recipe.declaration.time_slots
    if not slots:
        return recipe
    qualified = tuple(TimeSlotResolver(owner, slot.name, owner) for slot in slots)
    bindings = dict(zip(slots, qualified, strict=True))

    def visit(child: Resolver) -> Resolver:
        if isinstance(child, TimeSlotResolver):
            return bindings.get(child, child)
        return _map(child, visit)

    return DeclarationResolver(
        visit(recipe.recipe), replace(recipe.declaration, time_slots=qualified)
    )


def time_parameter_ref(value: ExactRef) -> bool:
    """Identify private stand-ins in the declaration validation graph."""
    return value.version.startswith("lexical-time-slot:")
