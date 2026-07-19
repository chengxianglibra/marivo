"""Frozen public descriptors for controlled runtime metric composition."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from marivo.analysis._semantic_types import AnalysisDimensionRef
from marivo.analysis.slice_types import SlicePredicate, SliceScalar, SliceValue
from marivo.semantic._authoring_validation import _normalize_time_fold
from marivo.semantic.ir import AggKind, AggregateFoldInput
from marivo.semantic.refs import DimensionRef, MeasureRef, MetricRef, TimeDimensionRef

type FrozenSliceScalar = SliceScalar
type FrozenSliceValue = FrozenSliceScalar | tuple[FrozenSliceScalar, ...] | FrozenSlicePredicateV1


@dataclass(frozen=True)
class FrozenSlicePredicateV1:
    op: Literal["==", "!=", "in", ">", ">=", "<", "<=", "between"]
    value: FrozenSliceScalar | tuple[FrozenSliceScalar, ...]


def _freeze_slice_value(value: SliceValue) -> FrozenSliceValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        if set(value) != {"op", "value"}:
            raise TypeError("slice predicate requires exactly 'op' and 'value'")
        op = value["op"]
        if op not in {"==", "!=", "in", ">", ">=", "<", "<=", "between"}:
            raise ValueError(f"unsupported slice predicate operator {op!r}")
        frozen_value = _freeze_slice_value(cast("SliceValue", value["value"]))
        if isinstance(frozen_value, FrozenSlicePredicateV1):
            raise TypeError("nested slice predicates are not supported")
        return FrozenSlicePredicateV1(op=op, value=frozen_value)
    if isinstance(value, set):
        return tuple(sorted(value, key=lambda item: (type(item).__qualname__, repr(item))))
    if isinstance(value, list | tuple):
        if not all(item is None or isinstance(item, str | int | float | bool) for item in value):
            raise TypeError("slice collections require scalar values")
        return tuple(value)
    raise TypeError(f"unsupported slice value type {type(value).__name__}")


def _thaw_slice_value(value: FrozenSliceValue) -> SliceValue:
    if isinstance(value, FrozenSlicePredicateV1):
        thawed = list(value.value) if isinstance(value.value, tuple) else value.value
        return cast("SlicePredicate", {"op": value.op, "value": thawed})
    if isinstance(value, tuple):
        return list(value)
    return value


@dataclass(frozen=True)
class FrozenSliceMap(Mapping[AnalysisDimensionRef, SliceValue]):
    """Immutable normalized copy of a public typed slice mapping."""

    _items: tuple[tuple[AnalysisDimensionRef, FrozenSliceValue], ...]

    def __iter__(self) -> Iterator[AnalysisDimensionRef]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, key: AnalysisDimensionRef) -> SliceValue:
        for candidate, value in self._items:
            if candidate == key:
                return _thaw_slice_value(value)
        raise KeyError(key)

    def frozen_items(self) -> tuple[tuple[AnalysisDimensionRef, FrozenSliceValue], ...]:
        return self._items


def _freeze_slice_map(
    value: Mapping[AnalysisDimensionRef, SliceValue] | None,
    *,
    required: bool,
) -> FrozenSliceMap:
    if value is None:
        if required:
            raise TypeError("runtime metric slice mapping is required")
        return FrozenSliceMap(())
    if not isinstance(value, Mapping):
        raise TypeError(f"runtime metric slice requires a Mapping, got {type(value).__name__}")
    items: list[tuple[AnalysisDimensionRef, FrozenSliceValue]] = []
    seen: set[tuple[type[Any], str]] = set()
    for key, item in value.items():
        if not isinstance(key, DimensionRef | TimeDimensionRef):
            raise TypeError(
                "runtime metric slice keys require exact DimensionRef or TimeDimensionRef"
            )
        identity = (type(key), key.id)
        if identity in seen:
            raise ValueError(f"duplicate runtime metric slice dimension {key.id!r}")
        seen.add(identity)
        items.append((key, _freeze_slice_value(item)))
    if required and not items:
        raise ValueError("runtime metric slice mapping must not be empty")
    items.sort(key=lambda pair: (pair[0].id, type(pair[0]).__qualname__))
    return FrozenSliceMap(tuple(items))


def _normalize_label(label: str | None) -> str | None:
    if label is None:
        return None
    if not isinstance(label, str):
        raise TypeError(f"runtime metric label must be str or None, got {type(label).__name__}")
    normalized = label.strip()
    if not normalized:
        raise ValueError("runtime metric label must not be empty")
    return normalized


def _normalize_agg(agg: AggKind) -> AggKind:
    if isinstance(agg, str):
        if agg not in {"sum", "count", "count_distinct", "min", "max", "mean", "median"}:
            raise ValueError(f"unsupported runtime aggregate kind {agg!r}")
        return cast("AggKind", agg)
    if (
        not isinstance(agg, tuple)
        or len(agg) != 2
        or agg[0] != "percentile"
        or isinstance(agg[1], bool)
        or not isinstance(agg[1], int | float)
        or not 0 < float(agg[1]) < 1
    ):
        raise ValueError("aggregate percentile must be ('percentile', q) with 0 < q < 1")
    return ("percentile", float(agg[1]))


def _normalize_fold(fold: AggregateFoldInput) -> AggregateFoldInput:
    normalized = _normalize_time_fold(fold, semantic_id="runtime_metric.aggregate")
    if normalized is None:
        return None
    if normalized.kind == "percentile":
        assert normalized.q is not None
        return ("percentile", normalized.q)
    return cast("AggregateFoldInput", normalized.kind)


@dataclass(frozen=True)
class RuntimeAggregateExpr:
    kind: Literal["aggregate"]
    measure: MeasureRef
    agg: AggKind
    fold: AggregateFoldInput
    slice_by: FrozenSliceMap
    label: str | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class RuntimeSliceExpr:
    kind: Literal["slice"]
    metric: MetricExprInput
    by: FrozenSliceMap
    label: str | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class RuntimeRatioExpr:
    kind: Literal["ratio"]
    numerator: MetricExprInput
    denominator: MetricExprInput
    zero_division: Literal["null", "error"]
    label: str | None = field(default=None, compare=False, hash=False)


type RuntimeMetricExpr = RuntimeAggregateExpr | RuntimeSliceExpr | RuntimeRatioExpr
type MetricExprInput = MetricRef | RuntimeMetricExpr


def _slice_value_payload(value: FrozenSliceValue) -> object:
    if isinstance(value, FrozenSlicePredicateV1):
        return {
            "kind": "predicate",
            "op": value.op,
            "value": _slice_value_payload(value.value),
        }
    if isinstance(value, tuple):
        return {"kind": "sequence", "items": [_slice_value_payload(item) for item in value]}
    return {"kind": "scalar", "value": value}


def _slice_value_from_payload(payload: object) -> SliceValue:
    if not isinstance(payload, dict) or not isinstance(payload.get("kind"), str):
        raise ValueError("runtime replay slice value must be a typed object")
    kind = payload["kind"]
    if kind == "scalar":
        value = payload.get("value")
        if value is None or isinstance(value, str | int | float | bool):
            return value
        raise ValueError("runtime replay scalar slice value is invalid")
    if kind == "sequence":
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("runtime replay slice sequence requires items")
        scalars: list[SliceScalar] = []
        for item in items:
            decoded = _slice_value_from_payload(item)
            if decoded is not None and not isinstance(decoded, str | int | float | bool):
                raise ValueError("runtime replay slice sequence requires scalar items")
            scalars.append(decoded)
        return scalars
    if kind == "predicate":
        op = payload.get("op")
        if op not in {"==", "!=", "in", ">", ">=", "<", "<=", "between"}:
            raise ValueError("runtime replay slice predicate operator is invalid")
        return cast(
            "SlicePredicate",
            {"op": op, "value": _slice_value_from_payload(payload.get("value"))},
        )
    raise ValueError(f"unknown runtime replay slice value kind {kind!r}")


def _slice_map_payload(value: FrozenSliceMap) -> list[dict[str, object]]:
    return [
        {
            "ref_kind": dimension.kind.value,
            "semantic_id": dimension.id,
            "value": _slice_value_payload(item),
        }
        for dimension, item in value.frozen_items()
    ]


def _slice_map_from_payload(payload: object) -> dict[AnalysisDimensionRef, SliceValue]:
    if not isinstance(payload, list):
        raise ValueError("runtime replay slice map must be an array")
    result: dict[AnalysisDimensionRef, SliceValue] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("runtime replay slice entry must be an object")
        semantic_id = item.get("semantic_id")
        ref_kind = item.get("ref_kind")
        if not isinstance(semantic_id, str) or not semantic_id:
            raise ValueError("runtime replay slice entry requires semantic_id")
        if ref_kind == "dimension":
            ref: AnalysisDimensionRef = DimensionRef(semantic_id)
        elif ref_kind == "time_dimension":
            ref = TimeDimensionRef(semantic_id)
        else:
            raise ValueError("runtime replay slice entry has invalid ref_kind")
        result[ref] = _slice_value_from_payload(item.get("value"))
    return result


def replay_payload(expression: MetricExprInput) -> dict[str, object]:
    """Encode one public metric descriptor for exact internal replay."""

    if isinstance(expression, MetricRef):
        return {"kind": "metric_ref", "metric_id": expression.id}
    if isinstance(expression, RuntimeAggregateExpr):
        return {
            "kind": "aggregate",
            "measure_id": expression.measure.id,
            "agg": list(expression.agg) if isinstance(expression.agg, tuple) else expression.agg,
            "fold": (
                list(expression.fold) if isinstance(expression.fold, tuple) else expression.fold
            ),
            "slice_by": _slice_map_payload(expression.slice_by),
            "label": expression.label,
        }
    if isinstance(expression, RuntimeSliceExpr):
        return {
            "kind": "slice",
            "metric": replay_payload(expression.metric),
            "by": _slice_map_payload(expression.by),
            "label": expression.label,
        }
    if isinstance(expression, RuntimeRatioExpr):
        return {
            "kind": "ratio",
            "numerator": replay_payload(expression.numerator),
            "denominator": replay_payload(expression.denominator),
            "zero_division": expression.zero_division,
            "label": expression.label,
        }
    raise TypeError(f"unsupported runtime replay expression {type(expression).__name__}")


def from_replay_payload(payload: object) -> MetricExprInput:
    """Decode a current-schema replay descriptor through public constructors."""

    if not isinstance(payload, dict) or not isinstance(payload.get("kind"), str):
        raise ValueError("runtime replay expression must be a typed object")
    kind = payload["kind"]
    if kind == "metric_ref":
        metric_id = payload.get("metric_id")
        if not isinstance(metric_id, str) or not metric_id:
            raise ValueError("runtime replay metric_ref requires metric_id")
        return MetricRef(metric_id)
    label = payload.get("label")
    if label is not None and not isinstance(label, str):
        raise ValueError("runtime replay label must be a string or null")
    if kind == "aggregate":
        measure_id = payload.get("measure_id")
        if not isinstance(measure_id, str) or not measure_id:
            raise ValueError("runtime replay aggregate requires measure_id")
        raw_agg = payload.get("agg")
        agg = tuple(raw_agg) if isinstance(raw_agg, list) else raw_agg
        raw_fold = payload.get("fold")
        fold = tuple(raw_fold) if isinstance(raw_fold, list) else raw_fold
        return aggregate(
            MeasureRef(measure_id),
            agg=cast("AggKind", agg),
            fold=cast("AggregateFoldInput", fold),
            slice_by=_slice_map_from_payload(payload.get("slice_by")),
            label=label,
        )
    if kind == "slice":
        return slice(
            from_replay_payload(payload.get("metric")),
            by=_slice_map_from_payload(payload.get("by")),
            label=label,
        )
    if kind == "ratio":
        zero_division = payload.get("zero_division")
        if zero_division not in {"null", "error"}:
            raise ValueError("runtime replay ratio has invalid zero_division")
        return ratio(
            from_replay_payload(payload.get("numerator")),
            from_replay_payload(payload.get("denominator")),
            zero_division=cast("Literal['null', 'error']", zero_division),
            label=label,
        )
    raise ValueError(f"unknown runtime replay expression kind {kind!r}")


def _require_metric_expr(value: object, *, parameter: str) -> MetricExprInput:
    if isinstance(value, (MetricRef, RuntimeAggregateExpr, RuntimeSliceExpr, RuntimeRatioExpr)):
        return value
    raise TypeError(
        f"runtime metric {parameter} requires exact MetricRef or RuntimeMetricExpr, "
        f"got {type(value).__name__}"
    )


def aggregate(
    measure: MeasureRef,
    *,
    agg: AggKind,
    fold: AggregateFoldInput = None,
    slice_by: Mapping[AnalysisDimensionRef, SliceValue] | None = None,
    label: str | None = None,
) -> RuntimeAggregateExpr:
    """Construct one frozen aggregate over a governed measure.

    Args:
        measure: Exact loaded ``MeasureRef`` to aggregate.
        agg: Registered aggregate kind, including ``("percentile", q)``.
        fold: Optional authoring-aligned time fold.
        slice_by: Optional branch-local typed slice copied into the descriptor.
        label: Optional presentation-only label.

    Returns:
        A frozen ``RuntimeAggregateExpr`` accepted by ``session.observe`` or by
        another runtime metric constructor.

    Example:
        >>> total = mv.runtime_metric.aggregate(
        ...     session.catalog.get("measure.sales.orders.amount").ref,
        ...     agg="sum",
        ...     label="Observed revenue",
        ... )

    Constraints:
        Only governed measure and dimension refs are accepted. The constructor
        does not execute data, create catalog authority, or accept custom code.
    """

    if not isinstance(measure, MeasureRef):
        raise TypeError(
            f"runtime metric aggregate measure requires exact MeasureRef, got {type(measure).__name__}"
        )
    return RuntimeAggregateExpr(
        kind="aggregate",
        measure=measure,
        agg=_normalize_agg(agg),
        fold=_normalize_fold(fold),
        slice_by=_freeze_slice_map(slice_by, required=False),
        label=_normalize_label(label),
    )


def slice(
    metric: MetricExprInput,
    *,
    by: Mapping[AnalysisDimensionRef, SliceValue],
    label: str | None = None,
) -> RuntimeSliceExpr:
    """Construct one frozen branch-local slice over a metric expression.

    Args:
        metric: Exact ``MetricRef`` or closed runtime metric expression.
        by: Non-empty typed dimension-to-slice mapping copied into the descriptor.
        label: Optional presentation-only label.

    Returns:
        A frozen ``RuntimeSliceExpr`` that can be nested recursively or observed.

    Example:
        >>> failed = mv.runtime_metric.slice(
        ...     session.catalog.get("metric.sales.requests").ref,
        ...     by={session.catalog.get("dimension.sales.requests.state").ref: "FAILED"},
        ... )

    Constraints:
        Slice keys must be exact dimension refs and the expression stays
        session-scoped after observation; it does not redefine catalog meaning.
    """

    return RuntimeSliceExpr(
        kind="slice",
        metric=_require_metric_expr(metric, parameter="metric"),
        by=_freeze_slice_map(by, required=True),
        label=_normalize_label(label),
    )


def ratio(
    numerator: MetricExprInput,
    denominator: MetricExprInput,
    *,
    zero_division: Literal["null", "error"] = "null",
    label: str | None = None,
) -> RuntimeRatioExpr:
    """Construct one frozen recursive ratio from two metric expressions.

    Args:
        numerator: Exact ``MetricRef`` or closed runtime metric expression.
        denominator: Exact ``MetricRef`` or closed runtime metric expression.
        zero_division: ``"null"`` to retain a null result or ``"error"`` to fail.
        label: Optional presentation-only label.

    Returns:
        A frozen ``RuntimeRatioExpr`` that can be nested recursively or observed.

    Example:
        >>> rate = mv.runtime_metric.ratio(
        ...     numerator=failed,
        ...     denominator=total,
        ...     zero_division="null",
        ...     label="Observed failure rate",
        ... )

    Constraints:
        Only the closed aggregate, slice, ratio, and catalog metric-ref algebra
        is admitted. SQL, callbacks, literals, and user-authored units are rejected.
    """

    if zero_division not in {"null", "error"}:
        raise ValueError("runtime metric ratio zero_division must be 'null' or 'error'")
    return RuntimeRatioExpr(
        kind="ratio",
        numerator=_require_metric_expr(numerator, parameter="numerator"),
        denominator=_require_metric_expr(denominator, parameter="denominator"),
        zero_division=zero_division,
        label=_normalize_label(label),
    )


__all__ = [
    "AnalysisDimensionRef",
    "FrozenSliceMap",
    "MetricExprInput",
    "RuntimeAggregateExpr",
    "RuntimeMetricExpr",
    "RuntimeRatioExpr",
    "RuntimeSliceExpr",
    "aggregate",
    "ratio",
    "slice",
]
