"""Pure Ibis lowering of the complete bound AnalysisPredicate vocabulary."""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.datasets.handles import CanonicalValue
from marivo.analysis.observation.predicates import BoundPredicate


def predicate_leaves(predicate: BoundPredicate | None) -> Iterator[BoundPredicate]:
    """Visit every field leaf without turning OR or NOT into separate filters."""
    if predicate is None:
        return
    if predicate.kind in ("all_of", "any_of", "not_"):
        for child in predicate.children:
            yield from predicate_leaves(child)
    else:
        yield predicate


def _boolean(value: ir.Value) -> ir.BooleanValue:
    if not isinstance(value, ir.BooleanValue):
        raise compilation_error("Boolean Ibis predicate", "unexpected expression type")
    return value


def _literal(
    value: CanonicalValue,
    predicate: BoundPredicate,
    *,
    physical_type: dt.DataType | None = None,
) -> ir.Scalar:
    if not isinstance(value, tuple) or len(value) != 2:
        raise compilation_error("canonical typed predicate literal", "invalid literal shape")
    kind, payload = value
    logical = predicate.field.logical_type_id if predicate.field is not None else "float64"
    if logical == "unknown":
        if physical_type is None:
            raise compilation_error("an observed source type for the predicate field", "unknown")
        if kind == "integer" and type(payload) is int and isinstance(physical_type, dt.Integer):
            if not physical_type.bounds.lower <= payload <= physical_type.bounds.upper:
                raise compilation_error(
                    str(physical_type), f"integer literal {payload!r} is out of range"
                )
            return ibis.literal(payload, type=physical_type)
        if kind == "floating" and type(payload) is float and isinstance(physical_type, dt.Floating):
            try:
                floating_number = float(payload)
            except (OverflowError, ValueError):
                raise compilation_error(
                    str(physical_type), "floating literal is out of range"
                ) from None
            if not floating_number == payload:
                raise compilation_error(str(physical_type), "floating literal is not lossless")
            return ibis.literal(floating_number, type=physical_type)
        if kind == "integer" and type(payload) is int and isinstance(physical_type, dt.Floating):
            floating_number = float(payload)
            if not math.isfinite(floating_number) or int(floating_number) != payload:
                raise compilation_error(str(physical_type), "integer literal is not lossless")
            return ibis.literal(floating_number, type=physical_type)
        if kind == "decimal" and isinstance(payload, str) and isinstance(physical_type, dt.Decimal):
            decimal_number = Decimal(payload)
            exponent = decimal_number.as_tuple().exponent
            if not isinstance(exponent, int):
                raise compilation_error("finite canonical decimal", "invalid decimal")
            scale = max(-exponent, 0)
            integer_digits = max(decimal_number.adjusted() + 1, 0) if decimal_number else 0
            if scale > (physical_type.scale or 0) or integer_digits > (
                (physical_type.precision or 0) - (physical_type.scale or 0)
            ):
                raise compilation_error(str(physical_type), "decimal literal is out of range")
            return ibis.literal(decimal_number, type=physical_type)
        if kind == "string" and type(payload) is str and physical_type.is_string():
            if predicate.kind in ("lt", "lte", "gt", "gte"):
                raise compilation_error("a governed string ordering identity", "no bound collation")
            return ibis.literal(payload)
        if kind == "boolean" and type(payload) is bool and physical_type.is_boolean():
            if predicate.kind in ("lt", "lte", "gt", "gte"):
                raise compilation_error("Boolean equality", "Boolean ordering is unsupported")
            return ibis.literal(payload)
        if kind == "date" and isinstance(payload, str) and physical_type.is_date():
            return ibis.literal(date.fromisoformat(payload), type=physical_type)
        if (
            kind in ("instant", "civil_timestamp")
            and isinstance(payload, str)
            and physical_type.is_timestamp()
        ):
            moment = datetime.fromisoformat(payload)
            timezone = getattr(physical_type, "timezone", None)
            if (moment.tzinfo is None) != (timezone is None):
                raise compilation_error(str(physical_type), "timestamp literal timezone mismatch")
            return ibis.literal(moment, type=physical_type)
        raise compilation_error(
            f"a predicate literal compatible with observed physical type {physical_type}",
            f"{kind} literal",
        )
    if kind == "decimal" and isinstance(payload, str):
        number = Decimal(payload)
        _, digits, exponent = number.as_tuple()
        if not isinstance(exponent, int):
            raise compilation_error("finite canonical decimal", "invalid decimal")
        scale = max(-exponent, 0)
        precision = max(len(digits) + max(exponent, 0), scale, 1)
        # Ibis's unqualified Decimal literal otherwise lowers to backend defaults.
        return ibis.literal(number, type=f"decimal({precision}, {scale})")
    if kind == "date" and isinstance(payload, str):
        return ibis.literal(date.fromisoformat(payload))
    if kind in ("instant", "civil_timestamp") and isinstance(payload, str):
        moment = datetime.fromisoformat(payload)
        return ibis.literal(
            moment, type="timestamp(6)" if moment.tzinfo is None else "timestamp('UTC', 6)"
        )
    if kind == "floating" and type(payload) is float:
        logical = predicate.field.logical_type_id if predicate.field is not None else "float64"
        return ibis.literal(payload, type="float32" if logical == "float32" else "float64")
    if kind == "integer" and type(payload) is int:
        logical = predicate.field.logical_type_id if predicate.field is not None else "int64"
        return (
            ibis.literal(payload, type=logical)
            if logical.startswith("uint")
            else ibis.literal(payload)
        )
    if kind == "boolean" and type(payload) is bool:
        return ibis.literal(payload)
    if kind == "string" and type(payload) is str:
        return ibis.literal(payload)
    if (
        kind == "bool_tuple"
        and isinstance(payload, tuple)
        and all(type(item) is bool for item in payload)
    ):
        return ibis.literal(list(payload), type="array<boolean>")
    raise compilation_error("closed canonical typed predicate literal", "unsupported literal")


def lower_bound_predicate(table: ir.Table, predicate: BoundPredicate) -> ir.BooleanValue:
    """Lower one complete canonical tree, preserving SQL three-valued logic and nulls."""
    if predicate.kind in ("all_of", "any_of", "not_"):
        children = tuple(lower_bound_predicate(table, child) for child in predicate.children)
        if predicate.kind == "not_":
            if len(children) != 1:
                raise compilation_error("one negated predicate", "invalid negation")
            return ~children[0]
        if len(children) < 2:
            raise compilation_error("at least two boolean operands", "invalid combinator")
        result = children[0]
        for child in children[1:]:
            result = result & child if predicate.kind == "all_of" else result | child
        return result
    if predicate.field is None:
        raise compilation_error("exact bound predicate field", "missing field")
    column = table[predicate.field.name]
    if predicate.kind == "is_null":
        return column.isnull()
    if predicate.kind == "is_not_null":
        return column.notnull()
    if predicate.kind == "is_in":
        if not isinstance(predicate.literal, tuple) or not predicate.literal:
            raise compilation_error("non-empty canonical membership literals", "invalid membership")
        return column.isin(
            [
                _literal(
                    value,
                    predicate,
                    physical_type=column.type()
                    if predicate.field.logical_type_id == "unknown"
                    else None,
                )
                for value in predicate.literal
            ]
        )
    literal = _literal(
        predicate.literal,
        predicate,
        physical_type=column.type() if predicate.field.logical_type_id == "unknown" else None,
    )
    if predicate.kind == "eq":
        return _boolean(column == literal)
    if predicate.kind == "not_eq":
        return _boolean(column != literal)
    if predicate.kind == "lt":
        return _boolean(column < literal)
    if predicate.kind == "lte":
        return _boolean(column <= literal)
    if predicate.kind == "gt":
        return _boolean(column > literal)
    if predicate.kind == "gte":
        return _boolean(column >= literal)
    raise compilation_error("closed registered predicate kind", "unsupported predicate")
