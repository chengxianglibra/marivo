"""Validate the declarations that own a Measure's unit and additivity."""

from __future__ import annotations

from typing import Literal

from ibis.common.deferred import Deferred

from marivo._definition.deferred import (
    DeclarationRecord,
    declare,
    inspect_deferred,
)
from marivo._definition.types import (
    SourceLocation as DefinitionSourceLocation,
)
from marivo._definition.types import (
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
)
from marivo._kernel.refs import (
    SemanticKind,
)
from marivo._kernel.validation import is_instance
from marivo.semantic._deferred_facts import RowFacts
from marivo.semantic._deferred_semantics import consume_row
from marivo.semantic.errors import ErrorKind
from marivo.semantic.expressions.objects import (
    _fail,
)
from marivo.semantic.ir import (
    SourceLocation,
)
from marivo.semantic.policies import (
    AdditiveAllV1,
    AdditiveOverV1,
    AdditivityPolicy,
    NonAdditiveV1,
)
from marivo.semantic.typing import AiContextValue
from marivo.semantic.unit_algebra import (
    linear_units_conflict,
    render_unit,
    unit_state,
)


def _measure_permission(
    row: Deferred, value: RowFacts, *, name: str, additivity: AdditivityPolicy
) -> SumPermission:
    permission = value.permission
    axes = {axis.ref.path: axis for axis in value.coordinates}
    paths = (
        additivity.coordinates
        if isinstance(additivity, AdditiveOverV1)
        else additivity.exceptions
        if isinstance(additivity, AdditiveAllV1)
        else ()
    )
    missing = tuple(path for path in paths if path not in axes)
    if missing:
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            f"Measure {name!r} grants permission on unknown or foreign axes.",
            expected=f"declared coordinates on {value.root.ref.path}",
            received=", ".join(missing),
            action=f"Choose existing coordinates: {', '.join(sorted(axes)) or '(none)'}.",
        )
    if isinstance(additivity, AdditiveOverV1):
        permission = SumAxes(tuple(axes[path] for path in paths))
    elif isinstance(additivity, AdditiveAllV1):
        permission = SumAllExcept(tuple(axes[path] for path in paths)) if paths else SumAll()
    elif isinstance(additivity, NonAdditiveV1):
        permission = SumNone()
    else:
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            "Measure additivity requires a closed additivity policy.",
            expected="ms.additive_over(...), ms.additive_except(...), ms.additive_all(), or ms.non_additive()",
            received=type(additivity).__name__,
            action="Construct additivity with the public policy helper before declaring the Measure.",
        )
    # Arithmetic can lose sum permission, but cannot lose a leaf's fixed time axis.
    fixed = tuple(
        dict.fromkeys(
            axis
            for use in inspect_deferred(row).uses
            if isinstance(use.input.permission, SumAllExcept)
            for axis in use.input.permission.axes
            if axis.ref.kind is SemanticKind.TIME_DIMENSION
        )
    )
    erased = tuple(
        axis
        for axis in fixed
        if (
            isinstance(permission, SumAll)
            or (
                isinstance(permission, SumAxes)
                and axis.ref in {item.ref for item in permission.axes}
            )
            or (
                isinstance(permission, SumAllExcept)
                and axis.ref not in {item.ref for item in permission.axes}
            )
        )
    )
    if erased:
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            f"Measure {name!r} cannot erase fixed time constraints.",
            expected="sum permission that keeps the dependency's time axes fixed",
            received=", ".join(axis.ref.path for axis in erased),
            action="Keep these time axes in additive_except(...), or use non_additive().",
        )
    return permission


def declare_measure(
    row: Deferred,
    *,
    name: str,
    unit: str | None,
    additivity: AdditivityPolicy | None,
    ai_context: AiContextValue | None,
    location: SourceLocation,
) -> Deferred:
    """Attach local metadata and only genuinely new, compatible business assertions."""

    if additivity is not None and type(additivity) not in {
        AdditiveOverV1,
        AdditiveAllV1,
        NonAdditiveV1,
    }:
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            "Measure additivity requires a closed additivity policy.",
            expected="ms.additive_over(...), ms.additive_except(...), ms.additive_all(), or ms.non_additive()",
            received=type(additivity).__name__,
            action="Call the public policy helper, or omit additivity to inherit the expression's permission.",
        )
    inspection = inspect_deferred(row)
    if inspection.symbols:
        policy: tuple[Literal["over", "all", "none"], tuple[str, ...]] | None = None
        if isinstance(additivity, AdditiveOverV1):
            policy = ("over", additivity.coordinates)
        elif isinstance(additivity, AdditiveAllV1):
            policy = ("all", additivity.exceptions)
        elif isinstance(additivity, NonAdditiveV1):
            policy = ("none", ())
        return declare(
            row,
            DeclarationRecord(
                kind="measure",
                name=name,
                unit=unit,
                pending_additivity=policy,
                pending_assertion=True,
                ai_context=ai_context,
                location=DefinitionSourceLocation(location.file, location.line),
            ),
        )
    value = consume_row(row)
    declared_unit = render_unit(unit_state(unit)) if unit is not None else value.unit
    if unit is not None and value.unit is not None:
        if linear_units_conflict((unit, value.unit)):
            _fail(
                ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                f"Measure {name!r} cannot relabel its inferred unit.",
                expected=f"a unit compatible with {value.unit}",
                received=unit,
                action=f"Keep unit={value.unit!r}, or change the row expression explicitly.",
            )
        declared_unit = value.unit
    permission = value.permission
    if additivity is not None:
        permission = _measure_permission(row, value, name=name, additivity=additivity)
    asserted_unit = declared_unit if declared_unit != value.unit else None
    if (
        isinstance(permission, (SumAxes, SumAllExcept))
        and type(permission) is type(value.permission)
        and is_instance(value.permission, (SumAxes, SumAllExcept))
        and frozenset(permission.axes) == frozenset(value.permission.axes)
    ):
        permission = value.permission
    asserted_permission = permission if permission != value.permission else None
    return declare(
        row,
        DeclarationRecord(
            kind="measure",
            name=name,
            unit=asserted_unit,
            permission=asserted_permission,
            ai_context=ai_context,
            location=DefinitionSourceLocation(location.file, location.line),
        ),
    )
