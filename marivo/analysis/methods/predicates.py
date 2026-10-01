"""Shared predicate type and Cell consumption, independent of physical route."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from math import isfinite

from marivo.analysis.core.model import reject
from marivo.analysis.core.predicates import DurationLiteral, ValuePredicate
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType, ValueType


def invalid(received: str) -> None:
    reject(
        "compatible typed, finite Defined predicate operands",
        received,
        "Select is_defined first, or bind compatible complete inputs; cohort alone admits existing Unknown Cells.",
        "analysis.predicate",
    )


def validate_operand(predicate: ValuePredicate, left: ValueType, right: ValueType | None) -> None:
    """Reject incompatible literals and field pairs before execution."""
    if predicate.operator == "is_defined":
        return
    if right is not None:
        if left != right and not (
            isinstance(left, DecimalType)
            and isinstance(right, DecimalType)
            and left.scale == right.scale
        ):
            invalid("field physical types differ")
        if (
            isinstance(left, ScalarType)
            and left.name in ("string", "boolean")
            and predicate.operator != "eq"
        ):
            invalid("category and boolean fields support equality only")
        return
    value = predicate.literal
    accepted = False
    if isinstance(left, DurationType):
        accepted = (
            isinstance(predicate.value, DurationLiteral) and left.unit == predicate.value.unit
        )
    elif isinstance(left, DecimalType):
        if type(value) is Decimal and value.is_finite():
            exponent = value.as_tuple().exponent
            accepted = (
                isinstance(exponent, int)
                and max(-exponent, 0) <= left.scale
                and max(value.adjusted() + 1, 0) <= left.precision - left.scale
            )
    elif isinstance(left, ScalarType):
        accepted = (
            (left.name == "int64" and type(value) is int)
            or (
                left.name == "float64"
                and (type(value) is float or (type(value) is int and -(2**53) <= value <= 2**53))
            )
            or (left.name == "string" and type(value) is str and predicate.operator == "eq")
            or (left.name == "boolean" and type(value) is bool and predicate.operator == "eq")
            or (left.name == "date" and type(value) is date)
            or (
                left.name == "timestamp"
                and type(value) is datetime
                and value.utcoffset() is not None
            )
        )
    if not accepted:
        if left == ScalarType("date"):
            reject(
                "a date literal for a DATE field",
                repr(value),
                "Use a date literal; datetime is a different temporal kind.",
                "analysis.predicate",
            )
        if left == ScalarType("timestamp"):
            reject(
                "a timezone-aware datetime for an instant field",
                repr(value),
                "Use a timezone-aware datetime literal; date and naive datetime do not identify an instant.",
                "analysis.predicate",
            )
        invalid(f"{value!r} is incompatible with {left}")


def evaluate_leaf(
    predicate: ValuePredicate,
    left: dict[str, object],
    right: dict[str, object] | None,
    *,
    cohort: bool = False,
) -> bool | None:
    """Consume both Cells before calculating a leaf truth value."""
    if predicate.operator == "is_defined":
        return left["cell_tag"] == "defined"
    operands = (left,) if right is None else (left, right)
    for row in operands:
        tag, value = row["cell_tag"], row["value"]
        if tag != "defined" and not (cohort and tag == "unknown"):
            invalid(f"received Cell tag {tag}")
        if tag == "defined" and (
            (type(value) is float and not isfinite(value))
            or (isinstance(value, Decimal) and not value.is_finite())
        ):
            invalid("received nonfinite Defined value")
    if any(row["cell_tag"] == "unknown" for row in operands):
        return None
    value = left["value"]
    other = predicate.literal if right is None else right["value"]
    if not isinstance(value, (int, float, Decimal, str, date)) or not isinstance(
        other, (int, float, Decimal, str, date)
    ):
        invalid("malformed Defined payload")
        return False
    if predicate.operator == "eq":
        return value == other
    if predicate.operator == "ne":
        return value != other
    if isinstance(value, (int, float)) and isinstance(other, (int, float)):  # noqa: SIM114 - preserve type narrowing for homogeneous comparisons
        less, equal = value < other, value == other
    elif isinstance(value, Decimal) and isinstance(other, Decimal):  # noqa: SIM114 - keep the Decimal family narrowed
        less, equal = value < other, value == other
    elif isinstance(value, date) and isinstance(other, date):
        less, equal = value < other, value == other
    else:
        invalid("ordered operands have incompatible payloads")
        return False
    return (
        less
        if predicate.operator == "lt"
        else less or equal
        if predicate.operator == "le"
        else not less and not equal
        if predicate.operator == "gt"
        else not less
    )
