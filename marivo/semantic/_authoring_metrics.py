"""Derived-metric compositions and cumulative anchors for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, NoReturn

from marivo._temporal import Grain
from marivo.semantic._authoring_context import (
    _register_authoring_file,
)
from marivo.semantic._authoring_retired import retired_factory
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    _GRAIN_TO_DATE_RESETS,
    _TRAILING_FIXED_UNITS,
    Composition,
    RatioComposition,
)
from marivo.semantic.ir import (
    CumulativeComposition as CumulativeComposition,
)

_GRAIN_TO_DATE_INTERNAL = ContextVar("marivo_grain_to_date_internal", default=False)


@dataclass(frozen=True)
class GrainToDate:
    """Value object selecting a grain-to-date cumulative anchor (MTD/QTD/YTD)."""

    grain: Grain
    kind: Literal["grain_to_date"] = "grain_to_date"

    def __post_init__(self) -> None:
        if not _GRAIN_TO_DATE_INTERNAL.get():
            raise TypeError(
                "GrainToDate values are returned by ms.grain_to_date(...); direct "
                "construction is not supported"
            )
        if not isinstance(self.grain, Grain):
            raise TypeError("GrainToDate.grain must be a Grain value")
        if self.grain.kind == "builtin" and self.grain.unit not in _GRAIN_TO_DATE_RESETS:
            _raise(
                ErrorKind.INVALID_REF,
                f"ms.grain_to_date(grain={self.grain.to_token()!r}) is not a reset grain; "
                "expected one of: week, month, quarter, year. Use a plain "
                "ms.cumulative(...) for all-history, or ms.trailing(count=..., unit='day') "
                "for a fixed-size rolling window.",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.CUMULATIVE_ANCHOR,
            )
        if self.grain.kind == "semantic" and not self.grain.level:
            raise ValueError("semantic GrainToDate requires a non-empty calendar level")


@dataclass(frozen=True)
class Trailing:
    """Value object selecting a fixed-size trailing cumulative anchor (rolling N)."""

    count: int
    unit: str
    kind: Literal["trailing"] = "trailing"

    def __post_init__(self) -> None:
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 1:
            _raise(
                ErrorKind.INVALID_REF,
                f"ms.trailing(count={self.count!r}) requires a positive integer count.",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.CUMULATIVE_ANCHOR,
            )
        if self.unit not in _TRAILING_FIXED_UNITS:
            _raise(
                ErrorKind.INVALID_REF,
                f"ms.trailing(unit={self.unit!r}) is a calendar-variable unit; trailing "
                "windows accept fixed-size units only (second, minute, hour, day, week). "
                "For a sliding-months reset use ms.grain_to_date(grain=mv.grain('month')); for a "
                "fixed-length month window use ms.trailing(count=..., unit='day').",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.CUMULATIVE_ANCHOR,
            )


def grain_to_date(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired grain_to_date declaration."""
    retired_factory("grain_to_date", kwargs)


def trailing(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired trailing declaration."""
    retired_factory("trailing", kwargs)


def _compute_composition_hash(composition: Composition) -> str:
    if isinstance(composition, RatioComposition):
        identity: tuple[object, ...] = ("ratio", composition.numerator, composition.denominator)
        if composition.zero_denominator_policy is not None:
            identity = (*identity, composition.zero_denominator_policy)
        text = repr(identity)
    elif isinstance(composition, CumulativeComposition):
        text = repr(("cumulative", composition.base, composition.over, composition.anchor))
    else:  # LinearComposition
        text = repr(("linear", tuple((t.sign, t.metric) for t in composition.terms)))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def ratio(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired ratio declaration."""
    retired_factory("ratio", kwargs)


def linear(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired linear declaration."""
    retired_factory("linear", kwargs)


def cumulative(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired cumulative declaration."""
    retired_factory("cumulative", kwargs)


_register_authoring_file(__file__)
