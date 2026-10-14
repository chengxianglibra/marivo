"""One declaration per connected method.

Every static per-method fact lives here exactly once. ``MethodSemantics``,
``key_for_parameters`` and registry arity validation read these records instead
of switching on method-name literals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.analysis.core import rules
from marivo.analysis.core.model import CheckId, PartRole

MethodName: TypeAlias = Literal[
    "association.pearson",
    "association.kendall",
    "association.read",
    "forecast.naive",
    "forecast.drift",
    "forecast.seasonal_naive",
    "forecast.read",
    "time.runs",
    "time.runs_read",
    "deviation.zscore",
    "deviation.mad",
    "deviation.read",
    "attribution.additive_difference",
    "attribution.component_mix",
    "time.product",
    "group.attach",
    "bind_project",
    "metric.distinct",
    "metric.approx_distinct",
    "metric.quantile",
    "metric.approx_quantile",
    "metric.min",
    "metric.max",
    "metric.observe",
    "metric.mean",
    "metric.fold",
    "metric.sum_zero",
    "metric.count",
    "metric.weighted_mean",
    "metric.ratio",
    "metric.linear",
    "map_correspond",
    "cell.compare",
    "comparison.read",
    "cell.difference",
    "cell.relative_change",
    "cell.ratio",
    "row.sum",
    "row.mean",
    "row.min",
    "row.max",
    "row.count",
    "row.count_defined",
    "row.weighted_mean",
    "state_rollup.min",
    "state_rollup.max",
    "state_rollup",
    "state_rollup.mean",
    "state_rollup.fold",
    "state_rollup.sum_zero",
    "state_rollup.count",
    "state_rollup.ratio",
    "state_rollup.weighted_mean",
    "state_rollup.linear",
    "parts_transport",
    "domain.cohort",
    "display.rank",
    "display.table",
    "reference.share",
    "reference.penetration",
    "reference.standardize",
    "association.spearman",
]

PersistentStateKind: TypeAlias = Literal[
    "attribution_additive",
    "attribution_component_mix",
    "ranking",
    "table",
    "none",
    "cohort",
    "share",
    "penetration",
    "standardized",
    "original_min",
    "original_max",
    "original_sum",
    "original_mean",
    "original_fold",
    "original_sum_zero",
    "original_count",
    "original_ratio",
    "original_weighted_mean",
    "original_linear",
    "difference",
    "relative_change",
    "relation_ratio",
    "row_sum",
    "row_count",
    "row_count_defined",
    "row_mean",
    "row_min",
    "row_max",
    "spearman",
]

CellPolicy: TypeAlias = Literal[
    "strict", "propagate", "count_all", "defined_only", "input_owned", "pair_null"
]
UnitPolicy: TypeAlias = Literal["preserve", "count", "mean", "difference", "ratio", "coefficient"]
EmptyCellReasons: TypeAlias = tuple[
    tuple[Literal["null", "undefined", "unknown"], tuple[str, ...]], ...
]


@dataclass(frozen=True, slots=True)
class Arity:
    """Inclusive ordered-input bounds; ``maximum=None`` means unbounded."""

    minimum: int
    maximum: int | None
    expected: str
    repair: str

    def admits(self, count: int) -> bool:
        return count >= self.minimum and (self.maximum is None or count <= self.maximum)


@dataclass(frozen=True, slots=True)
class Selector:
    """Exact parameter type plus an optional discriminating attribute value."""

    params: type[object]
    attribute: str | None = None
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MethodDescriptor:
    name: MethodName
    rule: rules.RuleId
    selectors: tuple[Selector, ...]
    state_kind: PersistentStateKind | None = "none"
    local_laws: tuple[str, ...] = ()
    cell_policy: CellPolicy = "input_owned"
    unit_policy: UnitPolicy = "preserve"
    empty_cell_reasons: EmptyCellReasons = ()
    required_parts: tuple[PartRole, ...] = ()
    required_checks: tuple[CheckId, ...] = ()
    output_parts: tuple[PartRole, ...] = ()
    state_components: tuple[str, ...] = ()
    original_state_method: str | None = None
    arity: Arity | None = None


_COVERAGE_CHECKS: tuple[CheckId, ...] = (
    "source.contribution_partition@v1",
    "source.complete_coverage@v1",
)
_PAIR_CHECKS: tuple[CheckId, ...] = ("source.exact_pairing@v1", "source.finite_numeric@v1")
_FINITE: tuple[CheckId, ...] = ("source.finite_numeric@v1",)
_STATE_PARTS: tuple[PartRole, ...] = ("original_state", "coverage")
_ENDPOINT_PARTS: tuple[PartRole, ...] = ("current_endpoint", "baseline_endpoint", "correspondence")
_NULL_EMPTY: EmptyCellReasons = (("null", ("empty_contribution",)),)
_ASSOCIATION_EMPTY: EmptyCellReasons = (
    ("undefined", ("insufficient_pairs", "constant_a", "constant_b", "constant_both")),
)
_COMPARE_EMPTY: EmptyCellReasons = (
    ("undefined", ("missing_side", "zero_baseline", "zero_denominator")),
    ("null", ("empty_contribution",)),
)
# Each ratio component retains its own empty rule. Null operands propagate;
# defined operands with a zero denominator produce Undefined.
_RATIO_EMPTY: EmptyCellReasons = (
    ("null", ("empty_contribution",)),
    ("undefined", ("zero_denominator",)),
)
_WEIGHTED_EMPTY: EmptyCellReasons = (("null", ("empty_contribution", "zero_weight_sum")),)
_REFERENCE_EMPTY: EmptyCellReasons = (("undefined", ("zero_denominator", "empty_reference")),)
_RATIO_COMPONENTS = (
    "numerator_sum",
    "numerator_non_null_count",
    "denominator_sum",
    "denominator_non_null_count",
)
_WEIGHTED_COMPONENTS = ("weighted_numerator", "weight_sum", "non_null_pair_count", "row_count")
_MEAN_COMPONENTS = ("sum", "non_null_count", "row_count")

_ONE = Arity(1, 1, "one ordered input for this method", "Declare the rule's exact input arity.")
_ONE_OR_TWO = Arity(
    1, 2, "one or two ordered inputs for this method", "Declare the rule's exact input arity."
)
_ONE_QUANTITY = Arity(
    1,
    1,
    "1 ordered single-quantity inputs",
    "Declare one precise value type and domain per input quantity.",
)
_TWO_QUANTITIES = Arity(
    2,
    2,
    "2 ordered single-quantity inputs",
    "Declare one precise value type and domain per input quantity.",
)
_CORRESPONDENCE = Arity(
    1,
    2,
    "one or two ordered correspondence inputs",
    "Declare a shape admitted by the closed correspondence rule.",
)
_ASSOCIATION_ARITY = Arity(
    1, 16, "exact typed association inputs", "Qualify every ordered endpoint and retained part."
)
_ATTRIBUTION_ARITY = Arity(
    2, 2, "two ordered attribution inputs", "Bind original target and expanded basis."
)


def _association(name: MethodName, selector: Selector) -> MethodDescriptor:
    return MethodDescriptor(
        name,
        "association_score@v1",
        (selector,),
        cell_policy="pair_null",
        unit_policy="coefficient",
        empty_cell_reasons=_ASSOCIATION_EMPTY,
        arity=_ASSOCIATION_ARITY,
    )


def _metric(
    name: MethodName,
    params: type[object],
    state_kind: PersistentStateKind,
    *,
    empty: EmptyCellReasons = _NULL_EMPTY,
    output_parts: tuple[PartRole, ...] = _STATE_PARTS,
    components: tuple[str, ...] = (),
    paired: bool = True,
) -> MethodDescriptor:
    return MethodDescriptor(
        name,
        "bind_project@v1",
        (Selector(params),),
        state_kind=state_kind,
        empty_cell_reasons=empty,
        required_parts=("subject",),
        required_checks=_COVERAGE_CHECKS,
        output_parts=output_parts,
        state_components=components,
        arity=_ONE_OR_TWO if paired else _ONE,
    )


def _rollup(
    name: MethodName,
    method: str,
    state_kind: PersistentStateKind,
    *,
    empty: EmptyCellReasons = _NULL_EMPTY,
    components: tuple[str, ...] = ("sum", "non_null_count"),
) -> MethodDescriptor:
    return MethodDescriptor(
        name,
        "original_reduce@v1",
        (Selector(rules.OriginalReduce, "method", (method,)),),
        state_kind=state_kind,
        local_laws=("L8", "L9"),
        empty_cell_reasons=empty,
        required_parts=_STATE_PARTS,
        required_checks=_COVERAGE_CHECKS,
        output_parts=_STATE_PARTS,
        state_components=components,
        original_state_method=f"{method}@v1",
        arity=_ONE_QUANTITY,
    )


def _cell(
    name: MethodName,
    method: str,
    state_kind: PersistentStateKind,
    unit: UnitPolicy,
    empty: EmptyCellReasons,
) -> MethodDescriptor:
    return MethodDescriptor(
        name,
        "cell_derive@v1",
        (Selector(rules.CellDerive, "method", (method,)),),
        state_kind=state_kind,
        cell_policy="propagate",
        unit_policy=unit,
        empty_cell_reasons=empty,
        required_checks=_PAIR_CHECKS,
        output_parts=_ENDPOINT_PARTS,
        arity=_TWO_QUANTITIES,
    )


def _row(
    name: MethodName,
    method: str,
    state_kind: PersistentStateKind | None,
    components: tuple[str, ...],
    *,
    cell: CellPolicy = "strict",
    unit: UnitPolicy = "preserve",
    empty: EmptyCellReasons = (),
    checks: tuple[CheckId, ...] = _FINITE,
    required: tuple[PartRole, ...] = (),
) -> MethodDescriptor:
    return MethodDescriptor(
        name,
        "row_state@v1",
        (Selector(rules.RowState, "method", (method,)),),
        state_kind=state_kind,
        cell_policy=cell,
        unit_policy=unit,
        empty_cell_reasons=empty,
        required_parts=required,
        required_checks=checks,
        output_parts=("row_state",),
        state_components=components,
        arity=_ONE_QUANTITY,
    )


def _fit(name: MethodName, rule: rules.RuleId, selector: Selector) -> MethodDescriptor:
    return MethodDescriptor(name, rule, (selector,))


DESCRIPTORS: tuple[MethodDescriptor, ...] = (
    _association("association.pearson", Selector(rules.AssociationFit, "method", ("pearson",))),
    _association("association.kendall", Selector(rules.AssociationFit, "method", ("kendall",))),
    _association("association.read", Selector(rules.AssociationRead)),
    _fit("forecast.naive", "forecast@v1", Selector(rules.ForecastFit, "model", ("naive",))),
    _fit("forecast.drift", "forecast@v1", Selector(rules.ForecastFit, "model", ("drift",))),
    _fit(
        "forecast.seasonal_naive",
        "forecast@v1",
        Selector(rules.ForecastFit, "model", ("seasonal_naive",)),
    ),
    _fit("forecast.read", "forecast@v1", Selector(rules.ForecastRead)),
    _fit("time.runs", "time_runs@v1", Selector(rules.TimeRuns)),
    _fit("time.runs_read", "time_runs@v1", Selector(rules.TimeRunRead)),
    _fit("deviation.zscore", "deviation@v1", Selector(rules.DeviationFit, "method", ("zscore",))),
    _fit("deviation.mad", "deviation@v1", Selector(rules.DeviationFit, "method", ("mad",))),
    _fit("deviation.read", "deviation@v1", Selector(rules.DeviationRead)),
    MethodDescriptor(
        "attribution.additive_difference",
        "attribution@v1",
        (Selector(rules.AttributionDerive, "method", ("additive_difference",)),),
        state_kind="attribution_additive",
        arity=_ATTRIBUTION_ARITY,
    ),
    MethodDescriptor(
        "attribution.component_mix",
        "attribution@v1",
        (Selector(rules.AttributionDerive, "method", ("component_mix",)),),
        state_kind="attribution_component_mix",
        arity=_ATTRIBUTION_ARITY,
    ),
    _metric(
        "metric.min", rules.ObserveMetric, "original_min", components=("min", "non_null_count")
    ),
    _metric(
        "metric.max", rules.ObserveMetric, "original_max", components=("max", "non_null_count")
    ),
    _rollup("state_rollup.min", "min", "original_min", components=("min", "non_null_count")),
    _rollup("state_rollup.max", "max", "original_max", components=("max", "non_null_count")),
    _metric("metric.distinct", rules.ObserveMetric, "none", empty=(), output_parts=("coverage",)),
    _metric(
        "metric.approx_distinct",
        rules.ObserveMetric,
        "none",
        empty=(),
        output_parts=("coverage",),
    ),
    _metric("metric.quantile", rules.ObserveMetric, "none", output_parts=("coverage",)),
    _metric(
        "metric.approx_quantile",
        rules.ObserveMetric,
        "none",
        output_parts=("coverage",),
        paired=False,
    ),
    _metric(
        "metric.fold",
        rules.ObserveMetric,
        "original_fold",
        components=("samples", "fold_kind"),
        paired=False,
    ),
    _rollup("state_rollup.fold", "fold", "original_fold", components=("samples", "fold_kind")),
    _metric("metric.mean", rules.ObserveMetric, "original_mean", components=_MEAN_COMPONENTS),
    _rollup("state_rollup.mean", "mean", "original_mean", components=_MEAN_COMPONENTS),
    MethodDescriptor(
        "time.product", "parts_transport@v1", (Selector(rules.TimeProduct),), arity=_ONE
    ),
    MethodDescriptor(
        "group.attach",
        "parts_transport@v1",
        (Selector(rules.AttachCategory),),
        arity=Arity(2, 2, "two ordered classification inputs", "Bind receiver and category."),
    ),
    _metric("metric.sum_zero", rules.ObserveMetric, "original_sum_zero", empty=()),
    MethodDescriptor(
        "metric.ratio",
        "original_reduce@v1",
        (Selector(rules.OriginalRatio),),
        state_kind="original_ratio",
        empty_cell_reasons=_RATIO_EMPTY,
        required_parts=_STATE_PARTS,
        required_checks=_COVERAGE_CHECKS,
        output_parts=_STATE_PARTS,
        state_components=_RATIO_COMPONENTS,
        arity=_TWO_QUANTITIES,
    ),
    MethodDescriptor(
        "metric.linear",
        "occurrence_combine@v1",
        (Selector(rules.OccurrenceCombine),),
        state_kind="original_linear",
        empty_cell_reasons=_NULL_EMPTY,
        required_parts=_STATE_PARTS,
        required_checks=_COVERAGE_CHECKS,
        output_parts=_STATE_PARTS,
        arity=Arity(
            2,
            None,
            "two or more ordered signed inputs",
            "Declare one precise value type and domain per occurrence.",
        ),
    ),
    _rollup("state_rollup.linear", "linear", "original_linear", components=()),
    _rollup(
        "state_rollup.ratio",
        "ratio",
        "original_ratio",
        empty=_RATIO_EMPTY,
        components=_RATIO_COMPONENTS,
    ),
    _rollup("state_rollup.sum_zero", "sum_zero", "original_sum_zero", empty=()),
    _metric("metric.count", rules.ObserveCount, "original_count", empty=()),
    _metric(
        "metric.weighted_mean",
        rules.ObserveWeightedMean,
        "original_weighted_mean",
        empty=_WEIGHTED_EMPTY,
        components=_WEIGHTED_COMPONENTS,
    ),
    _rollup(
        "state_rollup.weighted_mean",
        "weighted_mean",
        "original_weighted_mean",
        empty=_WEIGHTED_EMPTY,
        components=_WEIGHTED_COMPONENTS,
    ),
    _rollup("state_rollup.count", "count", "original_count", empty=(), components=("count",)),
    _metric("metric.observe", rules.ObserveMetric, "original_sum"),
    MethodDescriptor(
        "bind_project",
        "bind_project@v1",
        (Selector(rules.BindProject),),
        empty_cell_reasons=(("null", ("source_null",)),),
        arity=_ONE,
    ),
    MethodDescriptor(
        "map_correspond",
        "map_correspond@v1",
        (Selector(rules.MapCorrespond),),
        local_laws=("L7",),
        arity=_CORRESPONDENCE,
    ),
    _cell(
        "cell.difference",
        "difference",
        "difference",
        "difference",
        (("undefined", ("missing_side", "zero_denominator")), ("null", ("empty_contribution",))),
    ),
    MethodDescriptor(
        "cell.compare",
        "cell_compare@v1",
        (Selector(rules.CellCompare),),
        cell_policy="propagate",
        empty_cell_reasons=_COMPARE_EMPTY,
        required_checks=_PAIR_CHECKS,
        output_parts=(*_ENDPOINT_PARTS, "comparison"),
    ),
    MethodDescriptor(
        "comparison.read",
        "comparison_read@v1",
        (Selector(rules.ComparisonRead),),
        empty_cell_reasons=_COMPARE_EMPTY,
        required_parts=("comparison",),
    ),
    _cell("cell.relative_change", "relative_change", "relative_change", "ratio", _COMPARE_EMPTY),
    _cell(
        "cell.ratio",
        "ratio",
        "relation_ratio",
        "ratio",
        (("undefined", ("zero_denominator",)), ("unknown", ("insufficient_business_coverage",))),
    ),
    _row("row.sum", "sum", "row_sum", ("sum", "count")),
    _row(
        "row.mean",
        "mean",
        "row_mean",
        ("sum", "count"),
        unit="mean",
        empty=(("undefined", ("empty_mean",)),),
    ),
    _row("row.min", "min", "row_min", ("min", "count"), empty=(("undefined", ("empty_min",)),)),
    _row("row.max", "max", "row_max", ("max", "count"), empty=(("undefined", ("empty_max",)),)),
    _row("row.count", "count", "row_count", ("count",), cell="count_all", unit="count", checks=()),
    _row(
        "row.count_defined",
        "count_defined",
        "row_count_defined",
        ("count_defined",),
        cell="defined_only",
        unit="count",
        checks=("source.cell_policy@v1",),
    ),
    # Weighted row means have no publishable durable state kind.
    _row(
        "row.weighted_mean",
        "weighted_mean",
        None,
        ("weighted_sum", "weight_sum"),
        unit="mean",
        required=("statistical_weight",),
    ),
    _rollup("state_rollup", "sum", "original_sum"),
    MethodDescriptor(
        "parts_transport",
        "parts_transport@v1",
        (Selector(rules.PartsTransport),),
        local_laws=("L1",),
        arity=_CORRESPONDENCE,
    ),
    MethodDescriptor(
        "domain.cohort",
        "domain.cohort@v1",
        (Selector(rules.PartsTransport, "mode", ("cohort",)),),
        state_kind="cohort",
    ),
    MethodDescriptor(
        "display.rank", "display@v1", (Selector(rules.DisplayRank),), state_kind="ranking"
    ),
    MethodDescriptor(
        "display.table", "display@v1", (Selector(rules.DisplayTable),), state_kind="table"
    ),
    MethodDescriptor(
        "reference.share",
        "reference@v1",
        (Selector(rules.ReferenceDerive, "kind", ("share",)),),
        state_kind="share",
        empty_cell_reasons=_REFERENCE_EMPTY,
    ),
    MethodDescriptor(
        "reference.penetration",
        "reference@v1",
        (Selector(rules.ReferenceDerive, "kind", ("penetration",)),),
        state_kind="penetration",
        empty_cell_reasons=_REFERENCE_EMPTY,
    ),
    MethodDescriptor(
        "reference.standardize",
        "reference@v1",
        (Selector(rules.ReferenceDerive, "kind", ("standardize",)),),
        state_kind="standardized",
        empty_cell_reasons=_REFERENCE_EMPTY,
    ),
    MethodDescriptor(
        "association.spearman",
        "association_score@v1",
        (
            Selector(rules.AssociationFit, "method", ("spearman",)),
            Selector(rules.AssociationScore),
        ),
        state_kind="spearman",
        cell_policy="pair_null",
        unit_policy="coefficient",
        empty_cell_reasons=_ASSOCIATION_EMPTY,
        required_checks=_PAIR_CHECKS,
        output_parts=("pair_counts",),
        state_components=(
            "input_observation_count",
            "matched_observation_count",
            "null_pair_count",
            "complete_pair_count",
        ),
        arity=_ASSOCIATION_ARITY,
    ),
)

BY_NAME: dict[MethodName, MethodDescriptor] = {item.name: item for item in DESCRIPTORS}

# ObserveMetric selection precedence: explicit method, then fold, mean, empty rule.
_OBSERVE_BY_METHOD: dict[str, MethodName] = {
    "min": "metric.min",
    "max": "metric.max",
    "count_distinct": "metric.distinct",
    "approx_count_distinct": "metric.approx_distinct",
    "median": "metric.quantile",
    "percentile": "metric.quantile",
    "approx_median": "metric.approx_quantile",
    "approx_percentile": "metric.approx_quantile",
}


def _observe_metric_name(params: rules.ObserveMetric) -> MethodName:
    named = _OBSERVE_BY_METHOD.get(params.method)
    if named is not None:
        return named
    if params.fold is not None:
        return "metric.fold"
    if params.method == "mean":
        return "metric.mean"
    return "metric.sum_zero" if params.metric.empty_rule == "zero" else "metric.observe"


def _build_selection() -> dict[type[object], tuple[tuple[Selector, MethodName], ...]]:
    table: dict[type[object], list[tuple[Selector, MethodName]]] = {}
    for descriptor in DESCRIPTORS:
        for selector in descriptor.selectors:
            table.setdefault(selector.params, []).append((selector, descriptor.name))
    # Discriminated selectors win; an undiscriminated selector is the type's fallback.
    return {
        kind: tuple(sorted(entries, key=lambda entry: entry[0].attribute is None))
        for kind, entries in table.items()
    }


_SELECTION = _build_selection()


def method_name_for(params: rules.RuleParameters) -> MethodName | None:
    """Return the one method name selected by exact parameters, or ``None``."""
    if type(params) is rules.PreparedObservation:
        return method_name_for(params.observation)
    if type(params) is rules.ObserveMetric:
        return _observe_metric_name(params)
    for selector, name in _SELECTION.get(type(params), ()):
        if selector.attribute is None or getattr(params, selector.attribute) in selector.values:
            return name
    return None
