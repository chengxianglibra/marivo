"""Sealed authoring predicates and exact, local field binding."""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, SupportsIndex, TypeAlias

from marivo._compat import Never
from marivo.analysis.datasets.descriptors import (
    DatasetField,
    _bool_tuple_arity,
    _bool_tuple_value,
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
PredicateLiteral: TypeAlias = (
    str | bool | int | float | Decimal | date | datetime | tuple[bool, ...]
)
ComparisonKind: TypeAlias = Literal["eq", "not_eq", "lt", "lte", "gt", "gte"]
PredicateKind: TypeAlias = Literal[
    "eq",
    "not_eq",
    "lt",
    "lte",
    "gt",
    "gte",
    "is_in",
    "is_null",
    "is_not_null",
    "all_of",
    "any_of",
    "not_",
]
_TOKEN = object()
_EQUALITY_KINDS = ("eq", "not_eq", "is_in")
_ORDER_KINDS = ("lt", "lte", "gt", "gte")


def _error(
    expected: str,
    received: str,
    location: str = "observation.where",
    *,
    repair: str = "Use a focused predicate helper with an exact current field and compatible typed literals.",
) -> Never:
    raise ObservationPredicateError(
        expected=expected, received=received, repair=repair, location=location
    )


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class AnalysisPredicate:
    """Immutable unbound predicate produced by the focused private helpers."""

    _token: object = field(repr=False)
    kind: PredicateKind
    operand: PredicateField | None = field(default=None, repr=False)
    value: PredicateLiteral | None = field(default=None, repr=False)
    children: tuple[AnalysisPredicate, ...] = ()
    values: tuple[PredicateLiteral, ...] = field(default=(), repr=False)
    child_paths: tuple[tuple[int, ...], ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self._token is not _TOKEN or type(self) is not AnalysisPredicate:
            _error("helper-produced predicate", "direct predicate construction")

    def __bool__(self) -> Never:
        _error("predicate passed to where", "Python truth-value conversion")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        _error("in-process predicate authoring", "predicate serialization")

    def __repr__(self) -> str:
        return f"<AnalysisPredicate kind={self.kind}; literals redacted>"


def _operand(operand: PredicateField) -> None:
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


def _scalar(value: PredicateLiteral, *, kind: PredicateKind) -> None:
    if type(value) is tuple:
        if (
            not value
            or any(type(item) is not bool for item in value)
            or kind not in _EQUALITY_KINDS
        ):
            _error("non-empty bool tuple for mask equality or membership", "invalid mask literal")
        return
    if value is None:
        repair = (
            "Use is_not_null(field) for a non-null condition."
            if kind == "not_eq"
            else "Use is_null(field), or any_of(is_in(field, values), is_null(field)) for nullable membership."
        )
        _error("non-null typed literal", "None", repair=repair)
    if type(value) not in (str, bool, int, float, Decimal, date, datetime):
        _error("closed non-null scalar literal", type(value).__name__)
    if isinstance(value, float) and not math.isfinite(value):
        _error("finite floating literal", "non-finite float")
    if isinstance(value, Decimal) and not value.is_finite():
        _error("finite decimal literal", "non-finite decimal")
    if type(value) is str and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        _error("Unicode scalar sequence", "unpaired surrogate")


def _comparison(
    kind: ComparisonKind, operand: PredicateField, value: PredicateLiteral
) -> AnalysisPredicate:
    _operand(operand)
    _scalar(value, kind=kind)
    return AnalysisPredicate(_TOKEN, kind, operand, value)


def eq(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate:
    """Describe equality of a field and value; binding occurs at where().

    Args: field: Exact selector or semantic field. value: Non-null typed scalar.
    Returns: Immutable predicate. Example: ``eq(region, 'EU')``.
    Constraints: No coercion, field lookup, or data work occurs here.
    """
    return _comparison("eq", field, value)


def not_eq(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate:
    """Describe inequality under SQL three-valued logic.

    Args: field: Exact selector or semantic field. value: Non-null typed scalar.
    Returns: Immutable predicate. Example: ``not_eq(region, 'EU')``.
    Constraints: Null rows do not pass; use is_not_null for a null check.
    """
    return _comparison("not_eq", field, value)


def lt(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate:
    """Describe a strict upper bound on a field.

    Args: field: Exact selector or semantic field. value: Non-null typed scalar.
    Returns: Immutable predicate. Example: ``lt(revenue, 10)``.
    Constraints: where() must establish the field's governed value order.
    """
    return _comparison("lt", field, value)


def lte(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate:
    """Describe an inclusive upper bound on a field.

    Args: field: Exact selector or semantic field. value: Non-null typed scalar.
    Returns: Immutable predicate. Example: ``lte(revenue, 10)``.
    Constraints: Logical type compatibility is checked by where().
    """
    return _comparison("lte", field, value)


def gt(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate:
    """Describe a strict lower bound on a field.

    Args: field: Exact selector or semantic field. value: Non-null typed scalar.
    Returns: Immutable predicate. Example: ``gt(revenue, 10)``.
    Constraints: Logical type compatibility is checked by where().
    """
    return _comparison("gt", field, value)


def gte(field: PredicateField, value: PredicateLiteral) -> AnalysisPredicate:
    """Describe an inclusive lower bound on a field.

    Args: field: Exact selector or semantic field. value: Non-null typed scalar.
    Returns: Immutable predicate. Example: ``gte(revenue, 10)``.
    Constraints: Logical type compatibility is checked by where().
    """
    return _comparison("gte", field, value)


def is_in(
    field: PredicateField, values: list[PredicateLiteral] | tuple[PredicateLiteral, ...]
) -> AnalysisPredicate:
    """Describe membership in a non-empty typed literal list.

    Args: field: Exact selector or semantic field. values: Non-null literal list or tuple.
    Returns: Immutable predicate. Example: ``is_in(region, ['EU', 'APAC'])``.
    Constraints: Sets and arbitrary iterables are rejected; where() normalizes values.
    """
    _operand(field)
    if type(values) not in (list, tuple) or not values:
        _error("non-empty list or tuple of typed literals", "invalid membership container")
    for value in values:
        _scalar(value, kind="is_in")
    return AnalysisPredicate(_TOKEN, "is_in", field, values=tuple(values))


def is_null(field: PredicateField) -> AnalysisPredicate:
    """Describe a null check on a nullable current field.

    Args: field: Exact selector or semantic field.
    Returns: Immutable predicate. Example: ``is_null(region)``.
    Constraints: A non-nullable field rejects this constant condition at where().
    """
    _operand(field)
    return AnalysisPredicate(_TOKEN, "is_null", field)


def is_not_null(field: PredicateField) -> AnalysisPredicate:
    """Describe a non-null check on a nullable current field.

    Args: field: Exact selector or semantic field.
    Returns: Immutable predicate. Example: ``is_not_null(region)``.
    Constraints: A non-nullable field rejects this constant condition at where().
    """
    _operand(field)
    return AnalysisPredicate(_TOKEN, "is_not_null", field)


def _combinator(
    kind: Literal["all_of", "any_of"], predicates: tuple[AnalysisPredicate, ...]
) -> AnalysisPredicate:
    if len(predicates) < 2 or any(type(item) is not AnalysisPredicate for item in predicates):
        _error("at least two helper-produced predicates", "invalid boolean combinator")
    children: list[AnalysisPredicate] = []
    paths: list[tuple[int, ...]] = []
    for index, child in enumerate(predicates):
        if child.kind == kind:
            children.extend(child.children)
            paths.extend((index, *path) for path in child.child_paths)
        else:
            children.append(child)
            paths.append((index,))
    return AnalysisPredicate(_TOKEN, kind, children=tuple(children), child_paths=tuple(paths))


def all_of(*predicates: AnalysisPredicate) -> AnalysisPredicate:
    """Combine at least two predicates with SQL three-valued AND.

    Args: predicates: Helper-produced predicates in authored order.
    Returns: Immutable conjunction. Example: ``all_of(eq(region, 'EU'), gt(revenue, 0))``.
    Constraints: Python boolean operators are not predicate constructors.
    """
    return _combinator("all_of", predicates)


def any_of(*predicates: AnalysisPredicate) -> AnalysisPredicate:
    """Combine at least two predicates with SQL three-valued OR.

    Args: predicates: Helper-produced predicates in authored order.
    Returns: Immutable disjunction. Example: ``any_of(eq(region, 'EU'), is_null(region))``.
    Constraints: Binding preserves OR boundaries without distributing conjunctions.
    """
    return _combinator("any_of", predicates)


def not_(predicate: AnalysisPredicate) -> AnalysisPredicate:
    """Negate exactly one predicate with SQL three-valued NOT.

    Args: predicate: One helper-produced predicate.
    Returns: Immutable negation. Example: ``not_(eq(region, 'EU'))``.
    Constraints: An unknown predicate remains unknown and does not pass a filter.
    """
    if type(predicate) is not AnalysisPredicate:
        _error("one helper-produced predicate", "invalid negation")
    return AnalysisPredicate(_TOKEN, "not_", children=(predicate,), child_paths=((0,),))


@dataclass(frozen=True, slots=True, repr=False)
class BoundPredicate:
    """One immutable tree; raw canonical literals remain private arguments."""

    kind: PredicateKind
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


def _decimal_text(value: Decimal) -> str:
    # Decimal.normalize() uses the ambient arithmetic context and can round literals.
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if not value else text


def _literal(field: DatasetField, value: PredicateLiteral, kind: PredicateKind) -> CanonicalValue:
    logical = field.logical_type_id
    width = _bool_tuple_arity(logical)
    if width is not None and type(value) is tuple and kind in _EQUALITY_KINDS:
        mask = _bool_tuple_value(value, arity=width)
        if mask is None:
            _error("exact authored-axis bool mask length", "incompatible mask literal")
        return ("bool_tuple", mask)
    if (
        logical
        in ("integer", "int64", "int32", "int16", "int8", "uint8", "uint16", "uint32", "uint64")
        and type(value) is int
    ):
        if logical.startswith("uint") and not 0 <= value < 1 << int(logical[4:]):
            _error(f"a {logical} literal in its unsigned range", "out-of-range integer")
        return ("integer", value)
    if logical in ("floating", "float64", "float32", "numeric") and (
        type(value) is int or type(value) is float
    ):
        try:
            number = float(value)
            if logical == "float32":
                number = struct.unpack("!f", struct.pack("!f", number))[0]
        except (OverflowError, struct.error):
            _error("exact finite floating literal", "literal outside floating range")
        if not math.isfinite(number) or number != value:
            _error("exact finite floating literal", "lossy floating conversion")
        return ("floating", number if number else 0.0)
    if logical.startswith("decimal") and (type(value) is int or type(value) is Decimal):
        return ("decimal", _decimal_text(Decimal(value)))
    if logical in ("string", "str") and type(value) is str and kind in _EQUALITY_KINDS:
        return ("string", value)
    if logical in ("boolean", "bool") and type(value) is bool and kind in _EQUALITY_KINDS:
        return ("boolean", value)
    if logical in ("date", "civil_date") and type(value) is date:
        return ("date", value.isoformat())
    if type(value) is datetime and (
        logical == "instant"
        or re.fullmatch(r"timestamp\('[^']+'(?:, [0-9]+)?\)", logical)
        or (
            logical == "timestamp"
            and field.role_id == "time_coordinate"
            and field.name in ("from_time", "to_time", "followup_until")
            and field.field_id.value == f"generated.events.time_to_event.{field.name}@v1"
        )
    ):
        if value.tzinfo is None:
            _error("a timezone-aware instant literal", "naive timestamp")
        return ("instant", value.astimezone(timezone.utc).isoformat())
    if (
        (
            logical in ("timestamp", "datetime", "localizable_datetime")
            or re.fullmatch(r"timestamp\([0-6]\)", logical)
        )
        and type(value) is datetime
        and value.tzinfo is None
    ):
        return ("civil_timestamp", value.isoformat(timespec="microseconds"))
    if logical in ("timestamp", "datetime", "localizable_datetime"):
        _error(
            "complete read-timezone authority for a localizable datetime",
            "field has no bound read timezone",
            repair="Bind an exact governed instant or read-timezone field before filtering.",
        )
    if logical in ("string", "str") and kind in _ORDER_KINDS:
        _error("governed string ordering identity", "field has no bound collation")
    _error(f"{kind} literal compatible with {logical}", type(value).__name__)


def _merge_occurrences(left: BoundPredicate, right: BoundPredicate) -> BoundPredicate:
    return replace(
        left,
        occurrence_paths=tuple(sorted({*left.occurrence_paths, *right.occurrence_paths})),
        children=tuple(
            _merge_occurrences(first, second)
            for first, second in zip(left.children, right.children, strict=True)
        ),
    )


def _compare_literals(left: CanonicalValue, right: CanonicalValue) -> int | None:
    if not isinstance(left, tuple) or not isinstance(right, tuple):
        return None
    if len(left) != 2 or len(right) != 2 or left[0] != right[0]:
        return None
    first, second = left[1], right[1]
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return (first > second) - (first < second)
    if isinstance(first, str) and isinstance(second, str):
        if left[0] == "decimal":
            one, two = Decimal(first), Decimal(second)
            return (one > two) - (one < two)
        # Canonical UTC instants and ISO dates have the same order as their values.
        return (first > second) - (first < second)
    if left[0] == "bool_tuple" and isinstance(first, tuple) and isinstance(second, tuple):
        # Only canonical bool tuples reach this comparison; no ordering predicate is admitted.
        return 0 if first == second else None
    return None


def _allows(predicate: BoundPredicate, literal: CanonicalValue) -> bool:
    comparison = _compare_literals(literal, predicate.literal)
    if predicate.kind == "is_in" and isinstance(predicate.literal, tuple):
        return literal in predicate.literal
    if comparison is None:
        return True
    return {
        "eq": comparison == 0,
        "not_eq": comparison != 0,
        "lt": comparison < 0,
        "lte": comparison <= 0,
        "gt": comparison > 0,
        "gte": comparison >= 0,
    }.get(predicate.kind, True)


def _contradictions(children: tuple[BoundPredicate, ...]) -> None:
    """Reject only contradictions proved within one conjunction under SQL 3VL."""
    paths = tuple(sorted({path for child in children for path in child.occurrence_paths}))

    def reject(expected: str, received: str) -> Never:
        _error(expected, received, location=f"observation.where{paths}")

    groups: dict[str, list[BoundPredicate]] = {}
    identities = {_canonical_digest(child.identity_payload()) for child in children}
    for child in children:
        if (
            child.kind == "not_"
            and child.children
            and _canonical_digest(child.children[0].identity_payload()) in identities
        ):
            reject("consistent conjunction", "contradictory predicate and negation")
        if child.field is not None:
            groups.setdefault(_field_binding_fingerprint(child.field), []).append(child)
    for predicates in groups.values():
        if any(item.kind == "is_null" for item in predicates) and any(
            item.kind != "is_null" for item in predicates
        ):
            reject("consistent null constraints", "contradictory null conditions")
        candidates: tuple[CanonicalValue, ...] | None = None
        for item in predicates:
            if item.kind == "eq":
                candidates = (item.literal,)
                break
            if item.kind == "is_in" and isinstance(item.literal, tuple):
                candidates = item.literal
        if candidates is not None and not any(
            all(_allows(item, value) for item in predicates) for value in candidates
        ):
            reject("consistent equality, membership and bounds", "contradictory comparisons")
        for lower in predicates:
            if lower.kind not in ("gt", "gte"):
                continue
            for upper in predicates:
                if upper.kind not in ("lt", "lte"):
                    continue
                compared = _compare_literals(lower.literal, upper.literal)
                if compared is not None and (
                    compared > 0 or (compared == 0 and (lower.kind == "gt" or upper.kind == "lt"))
                ):
                    reject("consistent lower and upper bounds", "contradictory comparisons")


def _normalize_boolean(
    kind: Literal["all_of", "any_of"],
    children: tuple[BoundPredicate, ...],
    occurrence_paths: tuple[tuple[int, ...], ...] = (),
) -> BoundPredicate:
    flattened = tuple(
        item for child in children for item in (child.children if child.kind == kind else (child,))
    )
    deduplicated: dict[str, BoundPredicate] = {}
    for child in flattened:
        digest = _canonical_digest(child.identity_payload())
        previous = deduplicated.get(digest)
        deduplicated[digest] = child if previous is None else _merge_occurrences(previous, child)
    ordered = tuple(deduplicated[key] for key in sorted(deduplicated))
    if kind == "all_of":
        _contradictions(ordered)
    return (
        ordered[0]
        if len(ordered) == 1
        else BoundPredicate(kind, children=ordered, occurrence_paths=occurrence_paths)
    )


def bind_predicates(
    predicates: tuple[AnalysisPredicate, ...],
    resolver: Callable[[PredicateField], DatasetField],
) -> BoundPredicate:
    """Bind exact current fields and normalize a 3VL tree without changing boolean scopes."""
    if not predicates or any(type(item) is not AnalysisPredicate for item in predicates):
        _error("one or more helper-produced predicates", "empty or invalid where arguments")

    def visit(item: AnalysisPredicate, path: tuple[int, ...]) -> BoundPredicate:
        try:
            return visit_item(item, path)
        except ObservationPredicateError as error:
            if error.location != "observation.where":
                raise
            raise ObservationPredicateError(
                expected=error.expected or "admitted bound predicate",
                received=error.received or "invalid predicate",
                repair=error.hint or "Repair the predicate at the authored occurrence.",
                location=f"observation.where{path}",
            ) from None

    def visit_item(item: AnalysisPredicate, path: tuple[int, ...]) -> BoundPredicate:
        if item.kind in ("all_of", "any_of", "not_"):
            children = tuple(
                visit(child, (*path, *child_path))
                for child, child_path in zip(item.children, item.child_paths, strict=True)
            )
            if item.kind == "not_":
                return BoundPredicate("not_", children=children, occurrence_paths=(path,))
            return _normalize_boolean(item.kind, children, (path,))
        if item.operand is None:
            _error("complete field predicate", "corrupt predicate")
        resolved = resolver(item.operand)
        comparison_roles = {
            "comparison_ordinal": "comparison_coordinate",
            "current_time": "comparison_time",
            "baseline_time": "comparison_time",
            "coordinate_presence": "status",
            "current_value": "comparison_value",
            "baseline_value": "comparison_value",
            "delta": "comparison_value",
            "relative_delta": "effect_value",
            "calculation_status": "status",
            "relative_delta_status": "status",
        }
        comparison_field = (
            resolved.field_id.value == f"generated.compare.{resolved.name}@v1"
            and comparison_roles.get(resolved.name) == resolved.role_id
        )
        from marivo.analysis.domains.event_comparison import filterable_field as funnel_delta_field
        from marivo.analysis.domains.event_reducers import event_filterable_field
        from marivo.analysis.domains.lifecycle_reducers import (
            filterable_field as lifecycle_filterable_field,
        )
        from marivo.analysis.operators.attribution_contracts import attribution_filterable_field
        from marivo.analysis.operators.correlate import association_filterable_field
        from marivo.analysis.operators.discovery import candidate_filterable_field
        from marivo.analysis.operators.forecast import forecast_filterable_field

        if (
            resolved.role_id not in ("metric", "dimension", "time_dimension", "rank")
            and not comparison_field
            and not attribution_filterable_field(resolved)
            and not association_filterable_field(resolved)
            and not forecast_filterable_field(resolved)
            and not candidate_filterable_field(resolved)
            and not funnel_delta_field(resolved)
            and not event_filterable_field(resolved)
            and not lifecycle_filterable_field(resolved)
        ):
            _error("retained Metric, Dimension or exact generated row field", resolved.role_id)
        literal: CanonicalValue
        if item.kind in ("is_null", "is_not_null"):
            if not resolved.nullable:
                _error(
                    "nullable current field",
                    "constant null condition on non-nullable field",
                    repair="Remove the condition because this field is non-nullable.",
                )
            literal = None
        elif item.kind == "is_in":
            values = tuple(_literal(resolved, value, item.kind) for value in item.values)
            identities = {_canonical_digest(value): value for value in values}
            literal = tuple(identities[key] for key in sorted(identities))
        else:
            if item.value is None:
                _error("complete comparison predicate", "corrupt comparison")
            literal = _literal(resolved, item.value, item.kind)
        return BoundPredicate(item.kind, resolved, literal, occurrence_paths=(path,))

    return _normalize_boolean(
        "all_of", tuple(visit(predicate, (index,)) for index, predicate in enumerate(predicates))
    )
