"""Describe exact level Attribution methods and complete support."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Literal, TypeAlias

from typing_extensions import override

from marivo.analysis.core.frame_method_checks import _divisor, _quotient_unit, _total
from marivo.analysis.model.method_values import (
    FrameMethodValue,
    _FactRecord,
    _fail,
    _short,
    _text,
)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelSupport(_FactRecord):
    """Captured support for one endpoint, with no synthetic baseline."""

    root_id: str
    policy: Literal[
        "rows", "non_null_value", "co_valid_value_weight", "identity", "preserved_sample"
    ]
    input_count: int
    effective_count: int

    def __post_init__(self) -> None:
        _text(self.root_id)
        if self.policy not in (
            "rows",
            "non_null_value",
            "co_valid_value_weight",
            "identity",
            "preserved_sample",
        ):
            _fail("a closed sample policy", repr(self.policy))
        if (
            type(self.input_count) is not int
            or type(self.effective_count) is not int
            or not 0 <= self.effective_count <= self.input_count
        ):
            _fail("exact nonnegative endpoint support counts", repr(self.input_count))

    @override
    def _summary(self) -> str:
        return f"kind=level_support, root={_short(self.root_id)!r}, policy={self.policy}"


def _level_support(support: tuple[FrameLevelSupport, ...]) -> None:
    if (
        type(support) is not tuple
        or not support
        or any(type(item) is not FrameLevelSupport for item in support)
        or len({item.root_id for item in support}) != len(support)
    ):
        _fail("immutable unique one-endpoint supports", repr(support))


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelAdditiveMethod(_FactRecord):
    """A one-endpoint additive rule and its original total."""

    kind: Literal["level_additive"]
    value: FrameMethodValue
    support: tuple[FrameLevelSupport, ...]

    def __post_init__(self) -> None:
        if self.kind != "level_additive":
            _fail("kind='level_additive'", repr(self.kind))
        _total(self.value)
        _level_support(self.support)

    @override
    def _summary(self) -> str:
        return f"kind=level_additive, column={_short(self.value.column.name)!r}, roots={len(self.support)}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelRatioMethod(_FactRecord):
    """A one-endpoint ratio with independently captured components."""

    kind: Literal["level_ratio_component"]
    value: FrameMethodValue
    numerator: FrameMethodValue
    denominator: FrameMethodValue
    support: tuple[FrameLevelSupport, ...]

    def __post_init__(self) -> None:
        if self.kind != "level_ratio_component":
            _fail("kind='level_ratio_component'", repr(self.kind))
        for value in (self.value, self.numerator, self.denominator):
            _total(value)
        if (
            self.denominator.value == 0
            or _quotient_unit(self.numerator.column.unit, self.denominator.column.unit)
            != self.value.column.unit
        ):
            _fail("nonzero denominator and matching ratio units", self.value.column.name)
        _level_support(self.support)

    @override
    def _summary(self) -> str:
        return f"kind=level_ratio_component, column={_short(self.value.column.name)!r}, roots={len(self.support)}"


_LevelMethod: TypeAlias = "FrameLevelAdditiveMethod | FrameLevelRatioMethod | FrameLevelLinearMethod | FrameLevelDivisionMethod"


def _level_method(method: _LevelMethod) -> None:
    if type(method) not in (
        FrameLevelAdditiveMethod,
        FrameLevelRatioMethod,
        FrameLevelLinearMethod,
        FrameLevelDivisionMethod,
    ):
        _fail("a closed one-endpoint rule", type(method).__name__)


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelDivisionMethod(_FactRecord):
    """Explain one-endpoint division with its original divisor and child method.

    Args: kind, value, divisor, operation, method. Returns immutable inert facts.
    Example: read ``metric.method.divisor`` when kind is ``level_division``.
    Constraints: divide preserves units; rescale retains its declared result unit.
    """

    kind: Literal["level_division"]
    value: FrameMethodValue
    divisor: int | float | Decimal
    operation: Literal["divide", "rescale"]
    method: (
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
    )

    def __post_init__(self) -> None:
        if self.kind != "level_division":
            _fail("kind='level_division'", repr(self.kind))
        _total(self.value)
        _divisor(self.divisor, self.operation)
        _level_method(self.method)
        if self.operation == "divide" and self.method.value.column.unit != self.value.column.unit:
            _fail("division preserving the child unit", self.value.column.name)
        if self.operation == "rescale" and self.value.column.unit is None:
            _fail("an explicit rescale result unit", self.value.column.name)

    @override
    def _summary(self) -> str:
        return f"kind=level_division, column={_short(self.value.column.name)!r}, operation={self.operation}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelLinearTerm(_FactRecord):
    """An original ordered coefficient and one-endpoint child rule."""

    coefficient: int | float | Decimal
    method: (
        FrameLevelAdditiveMethod
        | FrameLevelRatioMethod
        | FrameLevelLinearMethod
        | FrameLevelDivisionMethod
    )

    def __post_init__(self) -> None:
        if (
            type(self.coefficient) not in (int, float, Decimal)
            or (isinstance(self.coefficient, float) and not isfinite(self.coefficient))
            or (isinstance(self.coefficient, Decimal) and not self.coefficient.is_finite())
        ):
            _fail("a finite original coefficient", repr(self.coefficient))
        _level_method(self.method)

    @override
    def _summary(self) -> str:
        return f"kind=level_linear_term, child={self.method.kind}, column={_short(self.method.value.column.name)!r}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameLevelLinearMethod(_FactRecord):
    """A one-endpoint fixed linear rule retaining child order."""

    kind: Literal["level_linear"]
    value: FrameMethodValue
    terms: tuple[FrameLevelLinearTerm, ...]

    def __post_init__(self) -> None:
        if self.kind != "level_linear":
            _fail("kind='level_linear'", repr(self.kind))
        _total(self.value)
        if (
            type(self.terms) is not tuple
            or not self.terms
            or any(type(term) is not FrameLevelLinearTerm for term in self.terms)
        ):
            _fail("nonempty exact ordered level terms", repr(self.terms))
        if any(term.method.value.column.unit != self.value.column.unit for term in self.terms):
            _fail("linear child units matching parent", self.value.column.name)

    @override
    def _summary(self) -> str:
        return (
            f"kind=level_linear, column={_short(self.value.column.name)!r}, terms={len(self.terms)}"
        )
