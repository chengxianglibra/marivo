"""Closed, versioned semantic declarations for the first Analysis DSL slice."""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from typing import Literal, TypeAlias

from ibis.common.deferred import Deferred

from marivo._symbolic import DimensionSymbol, TimeDimensionSymbol
from marivo.refs import Ref, SemanticKind

_TOKEN = object()
CoordinateRef: TypeAlias = DimensionSymbol | TimeDimensionSymbol
CoordinateInput: TypeAlias = CoordinateRef | Deferred


def _coordinates(values: tuple[CoordinateInput, ...]) -> tuple[str, ...]:
    from marivo._authoring.model import AuthoringRepair
    from marivo.introspection.live.model import LiveHelpTarget
    from marivo.semantic._authoring_context import _value_ref
    from marivo.semantic.constraints import ConstraintId
    from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise

    if not values:
        _raise(
            ErrorKind.INVALID_REF,
            "additive_over() and additive_except() require at least one Dimension or "
            "TimeDimension ref as a positional argument; use additive_all() for all axes "
            "or non_additive() for none.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
            expected="one or more Dimension or TimeDimension refs as positional arguments",
            received="no axes",
            hint="Pass one or more native axes; use additive_all() for all axes "
            "or non_additive() for none.",
            repair_value=AuthoringRepair(
                kind="reauthor",
                help_target=LiveHelpTarget(surface="semantic", canonical_id="measure"),
                action="Pass one or more native axes to additive_over() or additive_except(); "
                "use additive_all() for all axes or non_additive() for none.",
            ),
        )
    paths: list[str] = []
    for coordinate in values:
        value = _value_ref(coordinate)
        if type(value) is not Ref or value.kind not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            _raise(
                ErrorKind.INVALID_REF,
                "additivity coordinates require Dimension or TimeDimension refs.",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.REF_SHAPE,
                expected="a Dimension or TimeDimension ref for each positional axis",
                received=type(coordinate).__name__,
                hint="Pass fields individually, for example ms.additive_over(region, product) "
                "or ms.additive_except(snapshot_at); unpack a collection with *axes.",
                repair_value=AuthoringRepair(
                    kind="reauthor",
                    help_target=LiveHelpTarget(surface="semantic", canonical_id="measure"),
                    action="Pass Dimension or TimeDimension fields as positional arguments; "
                    "unpack an existing collection with *axes.",
                ),
            )
        paths.append(value.path)
    if len(set(paths)) != len(paths):
        _raise(
            ErrorKind.INVALID_REF,
            "additivity coordinates must be unique.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
            expected="unique Dimension or TimeDimension refs",
            received=tuple(paths),
            hint="Pass each native axis only once to additive_over() or additive_except().",
            repair_value=AuthoringRepair(
                kind="reauthor",
                help_target=LiveHelpTarget(surface="semantic", canonical_id="measure"),
                action="Remove repeated axes from the positional arguments.",
            ),
        )
    return tuple(paths)


@dataclass(frozen=True, slots=True)
class AdditiveOverV1:
    coordinates: tuple[str, ...]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.additive_over(...) to construct an additivity policy")


@dataclass(frozen=True, slots=True)
class AdditiveAllV1:
    exceptions: tuple[str, ...]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.additive_all() or ms.additive_except(...)")


@dataclass(frozen=True, slots=True)
class NonAdditiveV1:
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.non_additive() to construct an additivity policy")


AdditivityPolicy: TypeAlias = AdditiveOverV1 | AdditiveAllV1 | NonAdditiveV1


def additive_over(*axes: CoordinateInput) -> AdditiveOverV1:
    """Declare exactly which native coordinates may be merged.

    Args: axes: One or more native Dimension or TimeDimension refs, passed positionally.
    Returns: A versioned additive policy.
    Example: ``ms.additive_over(Region, Product)``.
    Constraints: Each coordinate must belong to the declared native support.
    """
    return AdditiveOverV1(_coordinates(axes), _token=_TOKEN)


def additive_all() -> AdditiveAllV1:
    """Declare additivity over all native coordinates.

    Returns: A versioned additive-all policy.
    Example: ``ms.additive_all()``.
    Constraints: Accepts no arguments and does not prove contribution partition
    or coverage. Use additive_except() to retain fixed axes.
    """
    return AdditiveAllV1((), _token=_TOKEN)


def additive_except(*axes: CoordinateInput) -> AdditiveAllV1:
    """Declare additivity over native coordinates except named fixed axes.

    Args: axes: One or more native Dimension or TimeDimension refs that must remain fixed.
    Returns: A versioned additive policy.
    Example: ``ms.additive_except(SnapshotAt)``.
    Constraints: It does not prove contribution partition or coverage. A
    physical Measure status-time axis must occur in ``axes`` and be declared
    as ``status_time_dimension``. Sampling stays on the time field; explicit
    internal sample reduction owns temporal aggregation. Fixed coordinates
    alone do not require a status declaration.
    """
    return AdditiveAllV1(_coordinates(axes), _token=_TOKEN)


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


def _nulls_ignore() -> NullInputPolicyV1:
    """Construct the ignore-Null policy for ``ms.nulls.ignore()``."""
    return NullInputPolicyV1("ignore", _token=_TOKEN)


def _empty_null() -> EmptyContributionPolicyV1:
    """Construct the empty-Null policy for ``ms.empty.null()``."""
    return EmptyContributionPolicyV1("null", _token=_TOKEN)


nulls = _Nulls()
empty = _Empty()
