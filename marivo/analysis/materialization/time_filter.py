"""Backend-native clock algebra and implication-preserving scan predicates."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler._wall_order import wall_order_bound
from marivo.analysis.compiler.source_time import (
    _ORDERED_FORMATS,
    _complement,
    _duckdb_strptime,
    _localize_native,
    _raw_lineage,
    _render_native,
    _scalar_cutoff,
    ordered_time_literal,
)
from marivo.analysis.core.time_authority import time_zone
from marivo.semantic.ir import StrptimeParse

_PROBE = ibis.timestamp("2000-01-01")
_LOCALIZE = type(_localize_native("UTC", _PROBE).op())
_RENDER = type(_render_native("UTC", _PROBE).op())
_PARSE = type(_duckdb_strptime(ibis.literal("20000101"), "%Y%m%d").op())
_COMPARISONS = (ops.Equals, ops.NotEquals, ops.Greater, ops.GreaterEqual, ops.Less, ops.LessEqual)
_DAY = timedelta(days=1)


def _zone(node: ops.Node) -> str | None:
    if type(node) not in (_LOCALIZE, _RENDER):
        return None
    zone = node.args[0]
    return zone.value if isinstance(zone, ops.Literal) and isinstance(zone.value, str) else None


def cancel_utc(expression: ir.Expr) -> ir.Expr:
    """Cancel UTC inverses only; a civil fold is never an inverse identity."""

    def rewrite(
        node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object
    ) -> ops.Node:
        value = node.copy(**kwargs)
        if _zone(value) == "UTC":
            inner = value.args[1]
            if (
                isinstance(inner, ops.Node)
                and _zone(inner) == "UTC"
                and type(inner) is not type(value)
            ):
                original = inner.args[1]
                assert isinstance(original, ops.Value)
                return (
                    original
                    if original.dtype == value.dtype
                    else original.to_expr().cast(value.dtype).op()
                )
        return value

    return expression.op().map(rewrite)[expression.op()].to_expr()


def _resolved(node: ops.Value) -> ops.Value:
    while isinstance(node, ops.Field):
        if isinstance(node.rel, ops.Project):
            node = node.rel.values[node.name]
        elif isinstance(node.rel, (ops.Filter, ops.Reference)):
            node = ops.Field(node.rel.parent, node.name)
        else:
            break
    return node


def _raw_for(raw: ops.Value, parent: ops.Relation) -> ir.Value | None:
    """Rebind the raw value to the same projection as a prepared field."""
    resolved = _resolved(raw)
    for name in parent.schema.names:
        field = ops.Field(parent, name)
        if _resolved(field) == resolved:
            return field.to_expr()
    return None


def _clock(node: ops.Value) -> tuple[str, ops.Value, timedelta, bool] | None:
    value = _resolved(node)
    if type(value) is _RENDER:
        inner = value.args[1]
        if (
            isinstance(inner, ops.Value)
            and type(inner) is _LOCALIZE
            and _zone(inner) == _zone(value)
        ):
            raw, zone = inner.args[1], _zone(value)
            if isinstance(raw, ops.Value) and zone is not None:
                return zone, raw, timedelta(0), True
    if type(value) is _LOCALIZE:
        zone, raw = _zone(value), value.args[1]
        if zone is not None and isinstance(raw, ops.Value):
            shift = timedelta(0)
            if isinstance(raw, (ops.TimestampAdd, ops.TimestampSub)) and isinstance(
                raw.right, ops.Literal
            ):
                amount = raw.right
                if (
                    amount.dtype.is_interval()
                    and amount.dtype.unit.short == "s"
                    and isinstance(amount.value, int)
                ):
                    shift = timedelta(
                        seconds=amount.value * (-1 if isinstance(raw, ops.TimestampSub) else 1)
                    )
                    raw = raw.left
            return zone, raw, shift, False
    if _encoded(value) is not None:
        return "UTC", value, timedelta(0), True
    return None


def _bound(node: ops.Value) -> datetime | None:
    while isinstance(node, ops.Cast):
        node = node.arg
    if isinstance(node, ops.Literal) and isinstance(node.value, datetime):
        value = node.value
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )
    if isinstance(node, ops.Literal) and isinstance(node.value, date):
        return datetime.combine(node.value, time(), timezone.utc)
    return None


def _encoded(node: ops.Value) -> tuple[ops.Value, str] | None:
    node = _resolved(node)
    while isinstance(node, ops.Cast) and (node.dtype.is_timestamp() or node.dtype.is_date()):
        node = node.arg
    if type(node) is not _PARSE:
        return None
    raw, format_node = node.args
    if not isinstance(raw, ops.Value) or not isinstance(format_node, ops.Literal):
        return None
    fmt = format_node.value
    if not isinstance(fmt, str) or fmt not in _ORDERED_FORMATS:
        return None
    if isinstance(raw, ops.Cast) and raw.dtype.is_string() and raw.arg.dtype.is_integer():
        raw = raw.arg
    if not (raw.dtype.is_string() or raw.dtype.is_integer()):
        return None
    return raw, fmt


def _encoded_guard(
    raw: ir.Value, fmt: str, boundary: datetime, operation: type[ops.Value]
) -> ir.BooleanValue:
    text = ordered_time_literal(boundary, fmt, strict=operation in (ops.Greater, ops.LessEqual))
    if text is None:
        return ibis.literal(True)
    try:
        literal = (
            ibis.literal(int(text), type=raw.type())
            if raw.type().is_integer()
            else ibis.literal(text)
        )
    except (OverflowError, TypeError, ValueError):
        return ibis.literal(True)
    if operation in (ops.GreaterEqual, ops.Greater):
        return raw >= literal
    if operation in (ops.Less, ops.LessEqual):
        return raw < literal
    if datetime.strptime(text, fmt) != boundary:
        return ibis.literal(True)
    return raw == literal if operation is ops.Equals else raw != literal


def _exact(node: ops.Value, parent: ops.Relation) -> ir.BooleanValue | None:
    """Translate only compiler-owned clocks with a retained physical field."""
    if not isinstance(node, _COMPARISONS):
        return None
    clock, bound, reverse = _clock(node.left), _bound(node.right), False
    if clock is None or bound is None:
        clock, bound, reverse = _clock(node.right), _bound(node.left), True
    if clock is None or bound is None:
        return None
    zone, raw_node, shift, civil = clock
    if civil and zone != "UTC":
        return None
    encoded = _encoded(raw_node)
    raw = _raw_for(encoded[0] if encoded else raw_node, parent)
    if raw is None or not _raw_lineage(raw):
        return None
    operation = type(node)
    if reverse:
        operation = {
            ops.Greater: ops.Less,
            ops.GreaterEqual: ops.LessEqual,
            ops.Less: ops.Greater,
            ops.LessEqual: ops.GreaterEqual,
        }.get(operation, operation)

    def lower(strict: bool) -> ir.BooleanValue | None:
        cutoff, inclusive = wall_order_bound(bound, time_zone(zone), strict=strict)
        return _scalar_cutoff(
            raw,
            StrptimeParse(encoded[1]) if encoded else None,
            cutoff - shift,
            inclusive=inclusive,
        )

    try:
        if operation in (ops.GreaterEqual, ops.Less):
            result = lower(False)
            return (
                None
                if result is None
                else result
                if operation is ops.GreaterEqual
                else _complement(result)
            )
        if operation in (ops.Greater, ops.LessEqual):
            result = lower(True)
            return (
                None
                if result is None
                else result
                if operation is ops.Greater
                else _complement(result)
            )
        first, last = lower(False), lower(True)
        if first is None or last is None:
            return None
        equal = first & ~last
        return equal if operation is ops.Equals else ~equal
    except OverflowError:
        return None


def _scan(node: ops.Value, parent: ops.Relation) -> ir.BooleanValue:
    if isinstance(node, ops.Literal) and isinstance(node.value, bool):
        return ibis.literal(node.value)
    if isinstance(node, (ops.And, ops.Or)):
        left, right = (
            _scan(node.left, parent),
            _scan(node.right, parent),
        )
        for value, other in ((left, right), (right, left)):
            constant = value.op()
            if isinstance(constant, ops.Literal) and isinstance(constant.value, bool):
                if isinstance(node, ops.And):
                    return other if constant.value else value
                return value if constant.value else other
        return left & right if isinstance(node, ops.And) else left | right
    if not isinstance(node, _COMPARISONS):
        return ibis.literal(True)
    clock, bound, reverse = _clock(node.left), _bound(node.right), False
    if clock is None or bound is None:
        clock, bound, reverse = _clock(node.right), _bound(node.left), True
    if clock is None or bound is None:
        # Other source comparisons remain useful conjunctive scan conditions.
        return (
            node.to_expr()
            if isinstance(node.left, ops.Field) and isinstance(node.right, ops.Literal)
            else ibis.literal(True)
        )
    zone, raw_node, shift, civil = clock
    encoded = _encoded(raw_node)
    raw = _raw_for(encoded[0] if encoded else raw_node, parent)
    if raw is None:
        return ibis.literal(True)
    operation = type(node)
    if reverse:
        operation = {
            ops.Greater: ops.Less,
            ops.GreaterEqual: ops.LessEqual,
            ops.Less: ops.Greater,
            ops.LessEqual: ops.GreaterEqual,
        }.get(operation, operation)

    def compare(cutoff: datetime, op: type[ops.Value]) -> ir.BooleanValue:
        if encoded:
            return _encoded_guard(raw, encoded[1], cutoff, op)
        result = op(raw.op(), ibis.literal(cutoff).op()).to_expr()
        assert isinstance(result, ir.BooleanValue)
        return result

    try:
        cutoff = bound.replace(tzinfo=None) - shift
        if zone == "UTC":
            return compare(cutoff, operation)
        # Named-zone offsets are strictly inside +/-24h. This envelope does
        # not infer a transition or an inverse at a fold/gap. A civil round
        # trip contains two offsets, so it needs twice that margin. The exact
        # native predicate remains authoritative, including NULL/error behavior.
        margin = 2 * _DAY if civil else _DAY
        lower, upper = cutoff - margin, cutoff + margin
    except OverflowError:
        return ibis.literal(True)
    if operation in (ops.Greater, ops.GreaterEqual):
        return compare(lower, ops.GreaterEqual)
    if operation in (ops.Less, ops.LessEqual):
        return compare(upper, ops.Less)
    if operation is ops.Equals:
        return compare(lower, ops.GreaterEqual) & compare(upper, ops.Less)
    return ibis.literal(True)


def optimize_time(expression: ir.Expr) -> ir.Expr:
    """Lower qualified local clocks and add guards to remaining native filters."""
    existing_ranges = tuple(
        atom
        for filtered in expression.op().find(ops.Filter)
        for predicate in filtered.predicates
        for atom in predicate.find(_COMPARISONS)
    )

    def scan(node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
        value = node.copy(**kwargs)
        if isinstance(value, ops.Filter):

            def exact(
                item: ops.Node, _children: Mapping[ops.Node, ops.Node], **parts: object
            ) -> ops.Node:
                updated = item.copy(**parts)
                replacement = (
                    _exact(updated, value.parent) if isinstance(updated, ops.Value) else None
                )
                return replacement.op() if replacement is not None else updated

            predicates = tuple(p.map(exact, filter=ops.Value)[p] for p in value.predicates)
            value = ops.Filter(value.parent, predicates)
            guards = tuple(_scan(p, value.parent) for p in value.predicates)
            existing = (
                *existing_ranges,
                *tuple(
                    atom
                    for filtered in (value, *value.parent.find(ops.Filter))
                    for predicate in filtered.predicates
                    for atom in predicate.find(_COMPARISONS)
                ),
            )

            def present(guard: ops.Value) -> bool:
                if isinstance(guard, ops.And):
                    return present(guard.left) and present(guard.right)
                if not isinstance(guard, _COMPARISONS):
                    return False
                return any(
                    type(atom) is type(guard)
                    and _resolved(atom.left) == _resolved(guard.left)
                    and (
                        atom.right.value == guard.right.value
                        if isinstance(atom.right, ops.Literal)
                        and isinstance(guard.right, ops.Literal)
                        else atom.right == guard.right
                    )
                    for atom in existing
                )

            useful = tuple(
                g.op()
                for g in guards
                if not (isinstance(g.op(), ops.Literal) and g.op().value is True)
                and g.op() not in value.predicates
                and not present(g.op())
            )
            if useful:
                return ops.Filter(value.parent, (*useful, *value.predicates))
        return value

    return expression.op().map(scan)[expression.op()].to_expr()
