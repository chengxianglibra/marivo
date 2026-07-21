"""Dependency-neutral descriptors for controlled runtime metric composition."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, cast

from marivo.refs import (
    FieldKind,
    MeasureKind,
    MetricKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    SemanticKindTag,
    _decode_ref_payload,
)
from marivo.semantic._authoring_validation import _normalize_time_fold
from marivo.semantic.ir import AggKind, AggregateFoldInput


class SlicePredicate(TypedDict):
    op: Literal["==", "!=", "in", ">", ">=", "<", "<=", "between"]
    value: Any


type SliceScalar = str | int | float | bool | None
type SliceValue = (
    SliceScalar | list[SliceScalar] | tuple[SliceScalar, ...] | set[SliceScalar] | SlicePredicate
)

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
class FrozenSliceMap(Mapping[Ref[FieldKind], SliceValue]):
    """Immutable normalized copy of a public typed slice mapping."""

    _items: tuple[tuple[Ref[FieldKind], FrozenSliceValue], ...]

    def __iter__(self) -> Iterator[Ref[FieldKind]]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, key: Ref[FieldKind]) -> SliceValue:
        for candidate, value in self._items:
            if candidate == key:
                return _thaw_slice_value(value)
        raise KeyError(key)

    def frozen_items(self) -> tuple[tuple[Ref[FieldKind], FrozenSliceValue], ...]:
        return self._items


def _freeze_slice_map(
    value: Mapping[Ref[FieldKind], SliceValue] | None,
    *,
    required: bool,
) -> FrozenSliceMap:
    if value is None:
        if required:
            raise TypeError("runtime metric slice mapping is required")
        return FrozenSliceMap(())
    if not isinstance(value, Mapping):
        raise TypeError(f"runtime metric slice requires a Mapping, got {type(value).__name__}")
    items: list[tuple[Ref[FieldKind], FrozenSliceValue]] = []
    seen: set[tuple[SemanticKind, str]] = set()
    for key, item in value.items():
        if type(key) is not Ref or key.kind not in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            raise TypeError(
                "runtime metric slice keys require exact Ref[dimension | time_dimension]"
            )
        identity = (key.kind, key.path)
        if identity in seen:
            raise ValueError(f"duplicate runtime metric slice dimension {key.path!r}")
        seen.add(identity)
        items.append((key, _freeze_slice_value(item)))
    if required and not items:
        raise ValueError("runtime metric slice mapping must not be empty")
    items.sort(key=lambda pair: pair[0].key)
    return FrozenSliceMap(tuple(items))


def _normalize_label(label: str) -> str:
    if not isinstance(label, str):
        raise TypeError(f"runtime metric label must be str, got {type(label).__name__}")
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
    measure: Ref[MeasureKind]
    agg: AggKind
    fold: AggregateFoldInput
    slice_by: FrozenSliceMap
    label: str = field(compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _normalize_label(self.label))


@dataclass(frozen=True)
class RuntimeSliceExpr:
    kind: Literal["slice"]
    metric: Ref[MetricKind] | RuntimeMetricExpr
    by: FrozenSliceMap
    label: str = field(compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _normalize_label(self.label))


@dataclass(frozen=True)
class RuntimeRatioExpr:
    kind: Literal["ratio"]
    numerator: Ref[MetricKind] | RuntimeMetricExpr
    denominator: Ref[MetricKind] | RuntimeMetricExpr
    zero_division: Literal["null", "error"]
    label: str = field(compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _normalize_label(self.label))


@dataclass(frozen=True)
class RuntimeWeightedMeanExpr:
    kind: Literal["weighted_mean"]
    value: Ref[MeasureKind]
    weight: Ref[MeasureKind]
    slice_by: FrozenSliceMap
    label: str = field(compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _normalize_label(self.label))


type RuntimeMetricExpr = (
    RuntimeAggregateExpr | RuntimeSliceExpr | RuntimeRatioExpr | RuntimeWeightedMeanExpr
)


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
            "dimension_ref": RefPayloadV1.from_ref(dimension).to_dict(),
            "value": _slice_value_payload(item),
        }
        for dimension, item in value.frozen_items()
    ]


def _slice_map_from_payload(payload: object) -> dict[Ref[FieldKind], SliceValue]:
    if not isinstance(payload, list):
        raise ValueError("runtime replay slice map must be an array")
    result: dict[Ref[FieldKind], SliceValue] = {}
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"dimension_ref", "value"}:
            raise ValueError("runtime replay slice entry must be an object")
        decoded = _decode_ref_payload(item["dimension_ref"])
        if decoded.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            raise ValueError("runtime replay slice entry requires a dimension ref")
        ref = cast("Ref[FieldKind]", decoded)
        result[ref] = _slice_value_from_payload(item.get("value"))
    return result


def replay_payload(expression: Ref[MetricKind] | RuntimeMetricExpr) -> dict[str, object]:
    """Encode one public metric descriptor for exact internal replay."""

    if type(expression) is Ref:
        if expression.kind is not SemanticKind.METRIC:
            raise TypeError("runtime replay expression ref must be Ref[metric]")
        return {
            "schema": "marivo.runtime_metric_expr/v1",
            "kind": "metric_ref",
            "metric_ref": RefPayloadV1.from_ref(expression).to_dict(),
        }
    if isinstance(expression, RuntimeAggregateExpr):
        return {
            "schema": "marivo.runtime_metric_expr/v1",
            "kind": "aggregate",
            "measure_ref": RefPayloadV1.from_ref(expression.measure).to_dict(),
            "agg": list(expression.agg) if isinstance(expression.agg, tuple) else expression.agg,
            "fold": (
                list(expression.fold) if isinstance(expression.fold, tuple) else expression.fold
            ),
            "slice_by": _slice_map_payload(expression.slice_by),
            "label": expression.label,
        }
    if isinstance(expression, RuntimeSliceExpr):
        return {
            "schema": "marivo.runtime_metric_expr/v1",
            "kind": "slice",
            "metric": replay_payload(expression.metric),
            "by": _slice_map_payload(expression.by),
            "label": expression.label,
        }
    if isinstance(expression, RuntimeRatioExpr):
        return {
            "schema": "marivo.runtime_metric_expr/v1",
            "kind": "ratio",
            "numerator": replay_payload(expression.numerator),
            "denominator": replay_payload(expression.denominator),
            "zero_division": expression.zero_division,
            "label": expression.label,
        }
    if isinstance(expression, RuntimeWeightedMeanExpr):
        return {
            "schema": "marivo.runtime_metric_expr/v1",
            "kind": "weighted_mean",
            "value_ref": RefPayloadV1.from_ref(expression.value).to_dict(),
            "weight_ref": RefPayloadV1.from_ref(expression.weight).to_dict(),
            "slice_by": _slice_map_payload(expression.slice_by),
            "label": expression.label,
        }
    raise TypeError(f"unsupported runtime replay expression {type(expression).__name__}")


def runtime_metric_leaf_refs(expression: RuntimeMetricExpr) -> tuple[Ref[SemanticKindTag], ...]:
    """Return ordered unique governed refs after bounded iterative traversal."""

    from marivo.semantic.metric_graph import (
        MAX_EXPRESSION_DEPTH,
        MAX_EXPRESSION_OCCURRENCES,
    )
    from marivo.semantic.metric_graph_canonical import MetricGraphContractError

    leaves: list[Ref[SemanticKindTag]] = []

    def add(ref: Ref[SemanticKindTag]) -> None:
        if ref not in leaves:
            leaves.append(ref)

    occurrences = 0
    stack: list[
        tuple[
            Ref[MetricKind] | RuntimeMetricExpr | None,
            int,
            str,
            FrozenSliceMap | None,
        ]
    ] = [(expression, 1, "root[0]", None)]
    while stack:
        value, depth, path, deferred_dimensions = stack.pop()
        if deferred_dimensions is not None:
            for dimension, _item in deferred_dimensions.frozen_items():
                add(cast("Ref[SemanticKindTag]", dimension))
            continue
        assert value is not None
        if depth > MAX_EXPRESSION_DEPTH:
            raise MetricGraphContractError(
                f"expression depth limit exceeded at 'root[0]': {depth} > {MAX_EXPRESSION_DEPTH}",
                kind="depth_limit_exceeded",
                observed_count=depth,
                limit=MAX_EXPRESSION_DEPTH,
                path=path,
            )
        node_occurrences = 1
        if isinstance(value, (RuntimeAggregateExpr, RuntimeWeightedMeanExpr)) and value.slice_by:
            node_occurrences = 2
            if depth == MAX_EXPRESSION_DEPTH:
                raise MetricGraphContractError(
                    f"expression depth limit exceeded at 'root[0]': "
                    f"{depth + 1} > {MAX_EXPRESSION_DEPTH}",
                    kind="depth_limit_exceeded",
                    observed_count=depth + 1,
                    limit=MAX_EXPRESSION_DEPTH,
                    path=f"{path}.child",
                )
        occurrences += node_occurrences
        if occurrences > MAX_EXPRESSION_OCCURRENCES:
            raise MetricGraphContractError(
                f"expression occurrence limit exceeded: {occurrences} > "
                f"{MAX_EXPRESSION_OCCURRENCES}",
                kind="occurrence_limit_exceeded",
                observed_count=occurrences,
                limit=MAX_EXPRESSION_OCCURRENCES,
                path=path,
            )
        if type(value) is Ref:
            add(cast("Ref[SemanticKindTag]", value))
            continue
        if isinstance(value, RuntimeAggregateExpr):
            add(cast("Ref[SemanticKindTag]", value.measure))
            for dimension, _item in value.slice_by.frozen_items():
                add(cast("Ref[SemanticKindTag]", dimension))
            continue
        if isinstance(value, RuntimeWeightedMeanExpr):
            add(cast("Ref[SemanticKindTag]", value.value))
            add(cast("Ref[SemanticKindTag]", value.weight))
            for dimension, _item in value.slice_by.frozen_items():
                add(cast("Ref[SemanticKindTag]", dimension))
            continue
        if isinstance(value, RuntimeSliceExpr):
            stack.append((None, depth, path, value.by))
            stack.append((value.metric, depth + 1, f"{path}.child", None))
            continue
        if isinstance(value, RuntimeRatioExpr):
            stack.append((value.denominator, depth + 1, f"{path}.denominator", None))
            stack.append((value.numerator, depth + 1, f"{path}.numerator", None))
            continue
        raise TypeError(f"unsupported runtime metric expression {type(value).__name__}")

    return tuple(leaves)


def from_replay_payload(payload: object) -> Ref[MetricKind] | RuntimeMetricExpr:
    """Decode a current-schema replay descriptor through public constructors."""

    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "marivo.runtime_metric_expr/v1"
        or not isinstance(payload.get("kind"), str)
    ):
        raise ValueError("runtime replay expression must be a typed object")
    kind = payload["kind"]
    if kind == "metric_ref":
        if set(payload) != {"schema", "kind", "metric_ref"}:
            raise ValueError("runtime replay metric_ref fields are invalid")
        metric_ref = _decode_ref_payload(payload["metric_ref"])
        if metric_ref.kind is not SemanticKind.METRIC:
            raise ValueError("runtime replay metric_ref requires metric kind")
        return cast("Ref[MetricKind]", metric_ref)
    label = payload.get("label")
    if not isinstance(label, str):
        raise ValueError("runtime replay label must be a non-empty string")
    if kind == "aggregate":
        if set(payload) != {
            "schema",
            "kind",
            "measure_ref",
            "agg",
            "fold",
            "slice_by",
            "label",
        }:
            raise ValueError("runtime replay aggregate fields are invalid")
        measure_ref = _decode_ref_payload(payload["measure_ref"])
        if measure_ref.kind is not SemanticKind.MEASURE:
            raise ValueError("runtime replay aggregate requires measure ref")
        raw_agg = payload.get("agg")
        agg = tuple(raw_agg) if isinstance(raw_agg, list) else raw_agg
        raw_fold = payload.get("fold")
        fold = tuple(raw_fold) if isinstance(raw_fold, list) else raw_fold
        return aggregate(
            cast("Ref[MeasureKind]", measure_ref),
            agg=cast("AggKind", agg),
            fold=cast("AggregateFoldInput", fold),
            slice_by=_slice_map_from_payload(payload.get("slice_by")),
            label=label,
        )
    if kind == "slice":
        if set(payload) != {"schema", "kind", "metric", "by", "label"}:
            raise ValueError("runtime replay slice fields are invalid")
        return slice(
            from_replay_payload(payload.get("metric")),
            by=_slice_map_from_payload(payload.get("by")),
            label=label,
        )
    if kind == "ratio":
        if set(payload) != {
            "schema",
            "kind",
            "numerator",
            "denominator",
            "zero_division",
            "label",
        }:
            raise ValueError("runtime replay ratio fields are invalid")
        zero_division = payload.get("zero_division")
        if zero_division not in {"null", "error"}:
            raise ValueError("runtime replay ratio has invalid zero_division")
        return ratio(
            from_replay_payload(payload.get("numerator")),
            from_replay_payload(payload.get("denominator")),
            zero_division=cast("Literal['null', 'error']", zero_division),
            label=label,
        )
    if kind == "weighted_mean":
        if set(payload) != {
            "schema",
            "kind",
            "value_ref",
            "weight_ref",
            "slice_by",
            "label",
        }:
            raise ValueError("runtime replay weighted_mean fields are invalid")
        value_ref = _decode_ref_payload(payload["value_ref"])
        weight_ref = _decode_ref_payload(payload["weight_ref"])
        if value_ref.kind is not SemanticKind.MEASURE:
            raise ValueError("runtime replay weighted_mean requires a value measure ref")
        if weight_ref.kind is not SemanticKind.MEASURE:
            raise ValueError("runtime replay weighted_mean requires a weight measure ref")
        return weighted_mean(
            cast("Ref[MeasureKind]", value_ref),
            cast("Ref[MeasureKind]", weight_ref),
            slice_by=_slice_map_from_payload(payload.get("slice_by")),
            label=label,
        )
    raise ValueError(f"unknown runtime replay expression kind {kind!r}")


def _require_metric_expr(
    value: object,
    *,
    parameter: str,
) -> Ref[MetricKind] | RuntimeMetricExpr:
    if type(value) is Ref:
        if value.kind is SemanticKind.METRIC:
            return cast("Ref[MetricKind] | RuntimeMetricExpr", value)
        raise TypeError(
            f"runtime metric {parameter} requires exact Ref[metric] or RuntimeMetricExpr, "
            f"got Ref[{value.kind.value}]"
        )
    if isinstance(
        value,
        (RuntimeAggregateExpr, RuntimeSliceExpr, RuntimeRatioExpr, RuntimeWeightedMeanExpr),
    ):
        return value
    raise TypeError(
        f"runtime metric {parameter} requires exact Ref[metric] or RuntimeMetricExpr, "
        f"got {type(value).__name__}"
    )


def aggregate(
    measure: Ref[MeasureKind],
    *,
    agg: AggKind,
    label: str,
    fold: AggregateFoldInput = None,
    slice_by: Mapping[Ref[FieldKind], SliceValue] | None = None,
) -> RuntimeAggregateExpr:
    """Construct one frozen aggregate over a governed measure.

    Args:
        measure: Exact loaded ``Ref[measure]`` to aggregate.
        agg: Registered aggregate kind, including ``("percentile", q)``.
        fold: Optional authoring-aligned time fold.
        slice_by: Optional branch-local typed slice copied into the descriptor.
        label: Required presentation-only label and stable public value-column handle.

    Returns:
        A frozen ``RuntimeAggregateExpr`` accepted by ``session.observe`` or by
        another runtime metric constructor.

    Example:
        >>> total = mv.runtime_metric.aggregate(
        ...     session.catalog.require(ms.ref.measure("sales.orders.amount")).ref,
        ...     agg="sum",
        ...     label="Observed revenue",
        ... )

    Constraints:
        Only governed measure and dimension refs are accepted. The constructor
        does not execute data, create catalog authority, or accept custom code.
    """

    if type(measure) is not Ref or measure.kind is not SemanticKind.MEASURE:
        raise TypeError(
            "runtime metric aggregate measure requires exact Ref[measure], "
            f"got {type(measure).__name__}"
        )
    return RuntimeAggregateExpr(
        kind="aggregate",
        measure=measure,
        agg=_normalize_agg(agg),
        fold=_normalize_fold(fold),
        slice_by=_freeze_slice_map(slice_by, required=False),
        label=_normalize_label(label),
    )


def weighted_mean(
    value: Ref[MeasureKind],
    weight: Ref[MeasureKind],
    *,
    label: str,
    slice_by: Mapping[Ref[FieldKind], SliceValue] | None = None,
) -> RuntimeWeightedMeanExpr:
    """Construct one exact weighted mean over two governed measures.

    Args:
        value: Exact loaded ``Ref[measure]`` containing row-level values.
        weight: Exact loaded additive ``Ref[measure]`` containing row-level weights.
        slice_by: Optional branch-local typed slice copied into the descriptor.
        label: Required presentation-only label and stable public value-column handle.

    Returns:
        A frozen ``RuntimeWeightedMeanExpr`` accepted by ``session.observe`` or
        by another runtime metric constructor.

    Example:
        >>> latency = session.catalog.require(ms.ref.measure("api.requests.latency_ms")).ref
        >>> requests = session.catalog.require(ms.ref.measure("api.requests.count")).ref
        >>> observed_latency = mv.runtime_metric.weighted_mean(
        ...     latency,
        ...     requests,
        ...     label="Observed latency",
        ... )

    Constraints:
        Both refs must resolve to measures on the same entity and physical row
        grain, and ``weight`` must be additive. Null value/weight pairs are
        excluded together and a zero paired weight sum produces null.
    """

    if type(value) is not Ref or value.kind is not SemanticKind.MEASURE:
        raise TypeError(
            "runtime metric weighted_mean value requires exact Ref[measure], "
            f"got {type(value).__name__}"
        )
    if type(weight) is not Ref or weight.kind is not SemanticKind.MEASURE:
        raise TypeError(
            "runtime metric weighted_mean weight requires exact Ref[measure], "
            f"got {type(weight).__name__}"
        )
    return RuntimeWeightedMeanExpr(
        kind="weighted_mean",
        value=value,
        weight=weight,
        slice_by=_freeze_slice_map(slice_by, required=False),
        label=_normalize_label(label),
    )


def slice(
    metric: Ref[MetricKind] | RuntimeMetricExpr,
    *,
    by: Mapping[Ref[FieldKind], SliceValue],
    label: str,
) -> RuntimeSliceExpr:
    """Construct one frozen branch-local slice over a metric expression.

    Args:
        metric: Exact ``Ref[metric]`` or closed runtime metric expression.
        by: Non-empty typed dimension-to-slice mapping copied into the descriptor.
        label: Required presentation-only label and stable public value-column handle.

    Returns:
        A frozen ``RuntimeSliceExpr`` that can be nested recursively or observed.

    Example:
        >>> failed = mv.runtime_metric.slice(
        ...     session.catalog.require(ms.ref.metric("sales.requests")).ref,
        ...     by={session.catalog.require(ms.ref.dimension("sales.requests.state")).ref: "FAILED"},
        ...     label="Observed failed requests",
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
    numerator: Ref[MetricKind] | RuntimeMetricExpr,
    denominator: Ref[MetricKind] | RuntimeMetricExpr,
    *,
    label: str,
    zero_division: Literal["null", "error"] = "null",
) -> RuntimeRatioExpr:
    """Construct one frozen recursive ratio from two metric expressions.

    Args:
        numerator: Exact ``Ref[metric]`` or closed runtime metric expression.
        denominator: Exact ``Ref[metric]`` or closed runtime metric expression.
        zero_division: ``"null"`` to retain a null result or ``"error"`` to fail.
        label: Required presentation-only label and stable public value-column handle.

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
        Only the closed aggregate, weighted_mean, slice, ratio, and catalog
        metric-ref algebra is admitted. SQL, callbacks, literals, and
        user-authored units are rejected.
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
    "FrozenSliceMap",
    "RuntimeAggregateExpr",
    "RuntimeMetricExpr",
    "RuntimeRatioExpr",
    "RuntimeSliceExpr",
    "RuntimeWeightedMeanExpr",
    "aggregate",
    "ratio",
    "slice",
    "weighted_mean",
]
