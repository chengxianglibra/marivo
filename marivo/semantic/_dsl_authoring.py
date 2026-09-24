"""Closed, versioned semantic declarations for the first Analysis DSL slice."""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from typing import Literal, TypeAlias

from marivo.refs import DimensionKind, Ref, SemanticKind, TimeDimensionKind

_TOKEN = object()
CoordinateRef: TypeAlias = Ref[DimensionKind | TimeDimensionKind]


def _coordinates(values: tuple[CoordinateRef, ...], *, nonempty: bool) -> tuple[str, ...]:
    from marivo.semantic.constraints import ConstraintId
    from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise

    if type(values) is not tuple or (nonempty and not values):
        _raise(
            ErrorKind.INVALID_REF,
            "additivity coordinates require a tuple of Dimension refs; over= cannot be empty.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    paths: list[str] = []
    for value in values:
        if type(value) is not Ref or value.kind not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            _raise(
                ErrorKind.INVALID_REF,
                "additivity coordinates require Dimension or TimeDimension refs.",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.REF_SHAPE,
            )
        paths.append(value.path)
    if len(set(paths)) != len(paths):
        _raise(
            ErrorKind.INVALID_REF,
            "additivity coordinates must be unique.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return tuple(paths)


@dataclass(frozen=True, slots=True)
class AdditiveOverV1:
    coordinates: tuple[str, ...]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.additive(over=...) to construct an additivity policy")


@dataclass(frozen=True, slots=True)
class AdditiveAllV1:
    exceptions: tuple[str, ...]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.additive_all(except_=...) to construct an additivity policy")


@dataclass(frozen=True, slots=True)
class NonAdditiveV1:
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.non_additive() to construct an additivity policy")


AdditivityPolicy: TypeAlias = AdditiveOverV1 | AdditiveAllV1 | NonAdditiveV1


def additive(*, over: tuple[CoordinateRef, ...]) -> AdditiveOverV1:
    """Declare exactly which native coordinates may be merged.

    Args: over: Non-empty tuple of governed Dimension refs.
    Returns: A versioned additive policy.
    Example: ``ms.additive(over=(Region,))``.
    Constraints: Each coordinate must belong to the declared native support.
    """
    return AdditiveOverV1(_coordinates(over, nonempty=True), _token=_TOKEN)


def additive_all(*, except_: tuple[CoordinateRef, ...] = ()) -> AdditiveAllV1:
    """Declare additivity over native coordinates except named fixed axes.

    Args: ``except_`` names native coordinates that must remain fixed.
    Returns: A versioned additive policy.
    Example: ``ms.additive_all(except_=(SnapshotAt,))``.
    Constraints: It does not prove contribution partition or coverage.
    """
    return AdditiveAllV1(_coordinates(except_, nonempty=False), _token=_TOKEN)


def non_additive() -> NonAdditiveV1:
    """Deny summing displayed values across native coordinates.

    Returns: A versioned non-additive policy.
    Example: ``ms.non_additive()``.
    Constraints: It does not deny new current-row statistics.
    """
    return NonAdditiveV1(_token=_TOKEN)


@dataclass(frozen=True, slots=True)
class NullInputPolicyV1:
    kind: Literal["reject", "ignore"]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.nulls.reject() or ms.nulls.ignore()")


@dataclass(frozen=True, slots=True)
class EmptyContributionPolicyV1:
    kind: Literal["zero", "null"]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.empty.zero() or ms.empty.null()")


@dataclass(frozen=True, slots=True)
class ZeroDenominatorPolicyV1:
    kind: Literal["undefined", "error"]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.zero_denominator.undefined() or .error()")


class _Nulls:
    def reject(self) -> NullInputPolicyV1:
        """Declare that selected Null inputs reject aggregation.

        Returns: A versioned Null-input policy.
        Example: ``nulls=ms.nulls.reject()``.
        Constraints: Execution requires a method that supports this policy.
        """
        return NullInputPolicyV1("reject", _token=_TOKEN)

    def ignore(self) -> NullInputPolicyV1:
        """Declare that aggregate inputs ignore Null values.

        Returns: A versioned Null-input policy.
        Example: ``nulls=ms.nulls.ignore()``.
        Constraints: Unknown and Undefined remain distinct from Null.
        """
        return _nulls_ignore()


class _Empty:
    def zero(self) -> EmptyContributionPolicyV1:
        """Declare zero for a complete empty contribution.

        Returns: A versioned empty-contribution policy.
        Example: ``empty=ms.empty.zero()``.
        Constraints: Unknown coverage cannot be treated as empty.
        """
        return EmptyContributionPolicyV1("zero", _token=_TOKEN)

    def null(self) -> EmptyContributionPolicyV1:
        """Declare Null for a complete empty contribution.

        Returns: A versioned empty-contribution policy.
        Example: ``empty=ms.empty.null()``.
        Constraints: Unknown coverage cannot be treated as empty.
        """
        return _empty_null()


class _ZeroDenominator:
    def undefined(self) -> ZeroDenominatorPolicyV1:
        """Declare Undefined for division by zero.

        Returns: A versioned denominator policy.
        Example: ``zero_denominator=ms.zero_denominator.undefined()``.
        Constraints: Execution requires a method that supports this policy.
        """
        return _zero_denominator_undefined()

    def error(self) -> ZeroDenominatorPolicyV1:
        """Reject division by zero.

        Returns: A versioned denominator policy.
        Example: ``zero_denominator=ms.zero_denominator.error()``.
        Constraints: Execution requires a method that supports this policy.
        """
        return ZeroDenominatorPolicyV1("error", _token=_TOKEN)


def _nulls_ignore() -> NullInputPolicyV1:
    """Construct the ignore-Null policy for ``ms.nulls.ignore()``."""
    return NullInputPolicyV1("ignore", _token=_TOKEN)


def _empty_null() -> EmptyContributionPolicyV1:
    """Construct the empty-Null policy for ``ms.empty.null()``."""
    return EmptyContributionPolicyV1("null", _token=_TOKEN)


def _zero_denominator_undefined() -> ZeroDenominatorPolicyV1:
    """Construct the Undefined-zero policy for ``ms.zero_denominator.undefined()``."""
    return ZeroDenominatorPolicyV1("undefined", _token=_TOKEN)


nulls = _Nulls()
empty = _Empty()
zero_denominator = _ZeroDenominator()
