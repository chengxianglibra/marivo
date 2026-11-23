"""Preserve provider key equality, domains and outer-join nulls."""

from __future__ import annotations

import ibis.expr.types as ir

from marivo.analysis.compiler.source_predicates import (
    distinct_returned_keys,
)
from marivo.analysis.compiler.source_predicates import string_bytes as _string_bytes
from marivo.datasource.spi import query_dialect


def _distinct(expression: ir.Table, provider: str) -> ir.Table:
    """Deduplicate returned string tuples independently of source collations."""
    return distinct_returned_keys(expression, provider)


def _same_key(left: ir.Value, right: ir.Value, provider: str) -> ir.BooleanValue:
    if left.type().is_string() and right.type().is_string():
        left, right = _string_bytes(left, provider), _string_bytes(right, provider)
    result = left.identical_to(right)
    assert isinstance(result, ir.BooleanValue)
    return result


def _outer_join_value(value: ir.Value, provider: str) -> ir.Value:
    """Preserve null payloads for an absent native endpoint."""
    nullable_value = query_dialect(provider).join_value
    return nullable_value(value) if nullable_value is not None else value
