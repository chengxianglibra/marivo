"""Sealed authoring predicates and exact, local field binding."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, SupportsIndex, TypeAlias

from marivo._compat import Never
from marivo.analysis.datasets.descriptors import (
    DatasetField,
    _canonical_digest,
    _field_binding_fingerprint,
)
from marivo.analysis.datasets.fields import DatasetFieldRef
from marivo.analysis.datasets.handles import CanonicalValue
from marivo.analysis.observation.errors import ObservationPredicateError
from marivo.refs import DimensionKind, MetricKind, Ref, SemanticKind, TimeDimensionKind
from marivo.semantic.catalog import DimensionEntry, MetricEntry, TimeDimensionEntry

PredicateField: TypeAlias = (
    DatasetFieldRef
    | Ref[MetricKind]
    | Ref[DimensionKind]
    | Ref[TimeDimensionKind]
    | MetricEntry
    | DimensionEntry
    | TimeDimensionEntry
)
PredicateLiteral: TypeAlias = str | bool | int | float | Decimal | date | datetime
_TOKEN = object()


def _error(expected: str, received: str, location: str = "observation.where") -> Never:
    raise ObservationPredicateError(
        expected=expected,
        received=received,
        repair="Use eq, gt, or all_of with an exact current field and a compatible non-null scalar.",
        location=location,
    )


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class AnalysisPredicate:
    """Immutable unbound predicate produced by the focused private helpers."""

    _token: object = field(repr=False)
    kind: Literal["eq", "gt", "all_of"]
    operand: PredicateField | None = field(default=None, repr=False)
    value: PredicateLiteral | None = field(default=None, repr=False)
    children: tuple[AnalysisPredicate, ...] = ()

    def __post_init__(self) -> None:
        if self._token is not _TOKEN or type(self) is not AnalysisPredicate:
            _error("helper-produced predicate", "direct predicate construction")

    def __bool__(self) -> Never:
        _error("predicate passed to where", "Python truth-value conversion")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        _error("in-process predicate authoring", "predicate serialization")

    def __repr__(self) -> str:
        return f"<AnalysisPredicate kind={self.kind}; literals redacted>"


def _comparison(
    kind: Literal["eq", "gt"], operand: PredicateField, value: PredicateLiteral
) -> AnalysisPredicate:
    if type(operand) not in (Ref, DatasetFieldRef, MetricEntry, DimensionEntry, TimeDimensionEntry):
        _error(
            "exact Metric/Dimension ref, current entry, or Dataset selector", type(operand).__name__
        )
    if isinstance(operand, Ref) and operand.kind not in (
        SemanticKind.METRIC,
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
    ):
        _error("Metric or Dimension operand", operand.kind.value)
    if type(value) not in (str, bool, int, float, Decimal, date, datetime):
        _error("closed non-null scalar literal", type(value).__name__)
    if isinstance(value, float) and not math.isfinite(value):
        _error("finite floating literal", "non-finite float")
    if isinstance(value, Decimal) and not value.is_finite():
        _error("finite decimal literal", "non-finite decimal")
    return AnalysisPredicate(_TOKEN, kind, operand, value)


def eq(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate:
    """Describe equality of field and value; binding occurs at where().

    Args: field: Exact selector or semantic field. value: Non-null typed scalar.
    Returns: Immutable predicate. Example: ``eq(region, 'EU')``.
    Constraints: No coercion, field lookup, or data work occurs here.
    """
    return _comparison("eq", field, value)


def gt(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate:
    """Describe a strict ordered comparison of field and value.

    Args: field: Exact selector or semantic field. value: Non-null typed scalar.
    Returns: Immutable predicate. Example: ``gt(revenue, 10)``.
    Constraints: Logical type compatibility is checked by where().
    """
    return _comparison("gt", field, value)


def all_of(*predicates: AnalysisPredicate) -> AnalysisPredicate:
    """Combine at least two predicates with SQL three-valued AND.

    Args: predicates: Helper-produced predicates in authored order.
    Returns: Immutable conjunction. Example: ``all_of(eq(region, 'EU'), gt(revenue, 0))``.
    Constraints: Python boolean operators are not predicate constructors.
    """
    if len(predicates) < 2 or any(type(item) is not AnalysisPredicate for item in predicates):
        _error("at least two helper-produced predicates", "invalid conjunction")
    return AnalysisPredicate(_TOKEN, "all_of", children=tuple(predicates))


@dataclass(frozen=True, slots=True, repr=False)
class BoundPredicate:
    """One immutable tree; raw canonical literals remain private arguments."""

    kind: Literal["eq", "gt", "all_of"]
    field: DatasetField | None = None
    literal: CanonicalValue = None
    children: tuple[BoundPredicate, ...] = ()
    occurrence_paths: tuple[tuple[int, ...], ...] = ()

    def identity_payload(self) -> CanonicalValue:
        return (
            self.kind,
            _field_binding_fingerprint(self.field) if self.field is not None else None,
            _canonical_digest(self.literal),
            tuple(item.identity_payload() for item in self.children),
        )

    def __repr__(self) -> str:
        return f"<private bound predicate kind={self.kind}>"


def _literal(field: DatasetField, value: PredicateLiteral, kind: str) -> CanonicalValue:
    logical = field.logical_type_id
    if logical in ("integer", "int64", "int32") and type(value) is int:
        return ("integer", value)
    if logical in ("floating", "float64", "float32", "numeric") and (
        type(value) is int or type(value) is float
    ):
        try:
            number = float(value)
        except OverflowError:
            _error("exact finite floating literal", "integer outside floating range")
        if not math.isfinite(number) or (type(value) is int and int(number) != value):
            _error("exact finite floating literal", "lossy floating conversion")
        return ("floating", number if number else 0.0)
    if logical.startswith("decimal") and (type(value) is int or type(value) is Decimal):
        decimal = Decimal(value)
        return ("decimal", format(decimal.normalize(), "f"))
    if logical in ("string", "str") and type(value) is str and kind == "eq":
        return ("string", value)
    if logical in ("boolean", "bool") and type(value) is bool and kind == "eq":
        return ("boolean", value)
    if logical in ("date", "civil_date") and type(value) is date:
        return ("date", value.isoformat())
    if logical in ("timestamp", "datetime", "instant") and isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            _error("timezone-aware datetime", "naive datetime")
        return ("instant", value.astimezone(timezone.utc).isoformat())
    _error(f"{kind} literal compatible with {logical}", type(value).__name__)


def _not_above(equality: CanonicalValue, threshold: CanonicalValue) -> bool:
    if not isinstance(equality, tuple) or not isinstance(threshold, tuple):
        return False
    if len(equality) != 2 or len(threshold) != 2 or equality[0] != threshold[0]:
        return False
    left, right = equality[1], threshold[1]
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left <= right
    if isinstance(left, str) and isinstance(right, str):
        if equality[0] == "decimal":
            return Decimal(left) <= Decimal(right)
        if equality[0] == "date":
            return date.fromisoformat(left) <= date.fromisoformat(right)
        if equality[0] == "instant":
            return datetime.fromisoformat(left) <= datetime.fromisoformat(right)
    return False


def bind_predicates(
    predicates: tuple[AnalysisPredicate, ...],
    resolver: Callable[[PredicateField], DatasetField],
) -> BoundPredicate:
    """Resolve, typecheck, deduplicate and canonically order the exact conjunction."""
    if not predicates or any(type(item) is not AnalysisPredicate for item in predicates):
        _error("one or more helper-produced predicates", "empty or invalid where arguments")
    leaves: list[BoundPredicate] = []

    def visit(item: AnalysisPredicate, path: tuple[int, ...]) -> None:
        if item.kind == "all_of":
            for index, child in enumerate(item.children):
                visit(child, (*path, index))
            return
        if item.operand is None or item.value is None:
            _error("complete comparison predicate", "corrupt comparison")
        field = resolver(item.operand)
        if field.role_id not in ("metric", "dimension", "time_dimension"):
            _error("retained Metric or Dimension field", field.role_id)
        leaves.append(
            BoundPredicate(
                item.kind, field, _literal(field, item.value, item.kind), occurrence_paths=(path,)
            )
        )

    for index, predicate in enumerate(predicates):
        visit(predicate, (index,))
    deduplicated: dict[str, BoundPredicate] = {}
    equalities: dict[str, str] = {}
    for item in leaves:
        if item.field is None:
            _error("bound comparison field", "corrupt comparison")
        key = _field_binding_fingerprint(item.field)
        literal_digest = _canonical_digest(item.literal)
        if item.kind == "eq":
            previous = equalities.setdefault(key, literal_digest)
            if previous != literal_digest:
                _error("consistent equality constraints", "contradictory equalities")
        digest = _canonical_digest(item.identity_payload())
        previous_item = deduplicated.get(digest)
        if previous_item is None:
            deduplicated[digest] = item
        else:
            deduplicated[digest] = BoundPredicate(
                item.kind,
                item.field,
                item.literal,
                occurrence_paths=previous_item.occurrence_paths + item.occurrence_paths,
            )
    for equality in leaves:
        if equality.kind != "eq" or equality.field is None:
            continue
        for threshold in leaves:
            if (
                threshold.kind == "gt"
                and threshold.field is not None
                and _field_binding_fingerprint(equality.field)
                == _field_binding_fingerprint(threshold.field)
                and _not_above(equality.literal, threshold.literal)
            ):
                _error(
                    "consistent equality and lower-bound constraints", "contradictory comparisons"
                )
    ordered = tuple(deduplicated[key] for key in sorted(deduplicated))
    return ordered[0] if len(ordered) == 1 else BoundPredicate("all_of", children=ordered)
