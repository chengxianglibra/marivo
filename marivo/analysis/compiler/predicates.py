"""Pure Ibis lowering of the complete bound AnalysisPredicate vocabulary."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

import ibis
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


def _literal(value: CanonicalValue, predicate: BoundPredicate) -> ir.Scalar:
    if not isinstance(value, tuple) or len(value) != 2:
        raise compilation_error("canonical typed predicate literal", "invalid literal shape")
    kind, payload = value
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
    if kind == "instant" and isinstance(payload, str):
        return ibis.literal(datetime.fromisoformat(payload))
    if kind == "floating" and type(payload) is float:
        logical = predicate.field.logical_type_id if predicate.field is not None else "float64"
        return ibis.literal(payload, type="float32" if logical == "float32" else "float64")
    if kind == "integer" and type(payload) is int:
        return ibis.literal(payload)
    if kind == "boolean" and type(payload) is bool:
        return ibis.literal(payload)
    if kind == "string" and type(payload) is str:
        return ibis.literal(payload)
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
        return column.isin([_literal(value, predicate) for value in predicate.literal])
    literal = _literal(predicate.literal, predicate)
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
