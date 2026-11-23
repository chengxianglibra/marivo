"""Source and internal-sample expression facts."""

from __future__ import annotations

from marivo._authoring.model import AuthoringInputRequirement
from marivo.semantic._help_specs.builders import (
    _parameter_input,
)


def _reduction_inputs(
    prefix: str, method: str, family: str
) -> tuple[list[AuthoringInputRequirement], str]:
    inputs = [AuthoringInputRequirement(role="receiver", family=family)]
    if prefix == "row":
        inputs.append(_parameter_input("dependency", "TimeExpression", "time", optional=True))
        argument = (
            "0.95, "
            if method in {"quantile", "approx_quantile"}
            else "weight=quantity, "
            if method == "weighted_mean"
            else ""
        )
        example = f"amount.{method}({argument}time=paid_at)"
    elif prefix == "MetricSamples":
        argument = (
            "0.95"
            if method in {"quantile", "approx_quantile"}
            else "weight=order_count"
            if method == "weighted_mean"
            else ""
        )
        example = (
            f"samples = revenue.per(paid_at)\nsamples.{method}(order_by=paid_at, include_null=False)"
            if method in {"first", "last"}
            else f"samples = revenue.per(customer_id)\nsamples.{method}({argument})"
        )
    elif prefix == "Metric":
        parameter, input_family, example = {
            "where": ("predicate", "RowPredicate", "revenue.where(status == 'paid')"),
            "per": (
                "by",
                "Dimension | TimeField | Grouping | tuple[Dimension | TimeField | Grouping | Population, ...] | Population | Grain",
                'scope = session.time_scope(start="2026-10-05", end="2026-10-12")\n'
                'daily_average = revenue.per(mv.grain("day")).mean()\n'
                "folded = session.observe(\n"
                '    {"daily_average": daily_average}, by=(scope.by(mv.grain("week")),)\n'
                ").compute()",
            ),
            "window": ("window", "WindowRule", "revenue.window(mv.all_history())"),
        }[method]
        inputs.append(_parameter_input("dependency", input_family, parameter))
    else:
        inputs.append(_parameter_input("dependency", "TimeExpression", "time", optional=True))
        example = "orders.count(time=paid_at)"
    if method in {"quantile", "approx_quantile"}:
        inputs.append(_parameter_input("dependency", "Number", "q"))
    if method == "weighted_mean":
        inputs.append(
            _parameter_input(
                "dependency",
                "NumericMetric" if prefix == "MetricSamples" else "NumericRow",
                "weight",
            )
        )
    if method in {"first", "last"}:
        inputs.extend(
            (
                _parameter_input("dependency", "TimeField", "order_by", optional=True),
                _parameter_input("dependency", "bool", "include_null", optional=True),
            )
        )
    if prefix == "Metric" and method == "per":
        inputs.append(_parameter_input("dependency", "Grain", "grain", optional=True))
    if prefix in {"row", "MetricSamples"} and method not in {
        "count",
        "count_distinct",
    }:
        inputs.append(_parameter_input("dependency", "Text", "nulls", optional=True))
    return inputs, example


def _metric_scope_summary(method: str) -> str:
    summary = ""
    if method == "per":
        summary += (
            "per accepts a Dimension, TimeField, native field Grouping or membership, a nonempty tuple of these, "
            "or a complete Population; finish with a sample reducer. Field samples use observed "
            "joint keys. Population samples match every shared axis by complete key; "
            "axes must have compatible roles, paths and time bindings. Interval keys match exact bounds. Without shared axes, "
            "each parent key uses all Population tuples. Compatible samples with no facts "
            "use the child Metric's empty value. No compatible samples leave an empty fold. "
            "Place correlated mv.membership(...) rules directly in samples, or use (P, rule, ...). "
            "Membership fields expand in order; explicit non-first fields locate interleaved axes. "
            "Reject repeated first fields, overlapping rules and reordered member fields. "
            "Internal P may select a subset; empty or unmatched samples retain external coordinates. "
            "Each nested layer keeps its own domain and complete allocation rules. "
            "per(field) uses exact TimeField values. per(grain) buckets each contribution's declared time role. "
            "per(field, grain=grain) buckets one explicit native TimeField without changing contribution time. Only clocked branches follow report time. Grain-only sampling needs at least one clocked branch; explicit fields and Populations supply their own sample domains. "
            "Both builtin forms use the Session report timezone and actual time samples, including all-NULL quantities. "
            "They need no finite period, create no empty buckets, and reject NULL sample times and order_by. "
            "Same-field layers require matching timezones and nested grains. Day nests in month. Week in month rejects. "
            "Business periods use ordinary fields and explicit externally chosen ranges. "
            "Each nested layer retains complete external, parent and child keys. "
            "Natural grains use the Session reporting timezone. "
            "Builtin folds inside windows use report endpoints; windows inside builtin samples use the nearest sample endpoints. Actual samples are discovered before inner windows; no empty dates are added. Explicit fields retain their cohorts and contribution clocks. Nested windows reject. "
            "TimeField sample reducers use represented samples without scheduled coverage or implicit fill; "
            "sample_interval describes cadence and does not bucket timestamps or require a finite observation period. "
        )
    if method == "where":
        summary += (
            "Filter source contributions before reduction within one source reduction or common paired scope using a row-stage "
            "boolean condition, including (status == 'paid') | mv.in_population(population). "
            "Repeated where calls conjoin conditions on the same input check domain; neither "
            "boolean side shields the other's errors. See semantic.predicate for grammar. "
            "Composite or sampled Metrics must filter the eligible child reduction. "
            "This is distinct from AnalysisFrame.where on current rows."
        )
    if method == "window":
        summary += "Window clocked branches at report endpoints, or the nearest enclosing builtin calendar sample endpoints; independent branches retain their scope. per(...).reduce().window(W) folds window contributions; window(W).per(...).reduce() folds sample-endpoint window values. trailing_periods counts the endpoint layer's periods. Fixed predicates and different-field sample cohorts remain in force. Nested windows are rejected."
    return summary


def _reduction_summary(prefix: str, method: str) -> str:
    summary = ""
    if prefix == "Metric":
        return _metric_scope_summary(method)
    if method in {"first", "last"}:
        summary += (
            f"Select the {method} internal sample. "
            "order_by may name an internal TimeField; when omitted, exactly one internal "
            "TimeField must be inferable. Time keys must uniquely order the samples. "
            "include_null defaults to False (skip NA); True retains the selected sample's NA."
            " Calendar samples use the captured bucket's instant order. Omit order_by."
        )
    elif prefix == "row" and method in {
        "median",
        "quantile",
        "approx_median",
        "approx_quantile",
    }:
        summary += (
            "Exact median/quantile uses native linear interpolation h=(n-1)q on DuckDB, PostgreSQL "
            "and ClickHouse; MySQL/Trino reject exact source quantiles at compilation. "
            "ClickHouse exact inputs must be integer/float; source Duration quantiles are unavailable. Explicit approximate methods use DuckDB approx_quantile, "
            "Trino approx_percentile or ClickHouse TDigest; PostgreSQL/MySQL reject approximation. "
            "Median uses q=0.5; quantile requires q in [0, 1]. Empty/all-NULL inputs return NA; nulls='error' rejects NULL inputs. "
            "Native nonfinite values follow the backend. Integer/float results are float64; Decimal retains its declared "
            "precision and scale. Algorithms may differ by backend; no cross-backend error bound "
            "or automatic exact/approximate substitution is promised."
        )
    elif method == "sum":
        summary += (
            "Sum requires business permission. Empty input returns zero. With nulls='ignore', "
            "nonempty all-NA input returns NULL. SQL SUM uses the backend's native accumulator. "
            "Native errors and wrapping follow the selected kernel. Completed and saved Frames "
            "retain native NaN and Infinity."
        )
        if prefix == "MetricSamples":
            summary += " Local integer sample SUM uses Arrow's int64 accumulator."
        else:
            summary += " The source SUM expression declares int64 for signed integer input and Decimal(38,input_scale) for Decimal input."
    elif method == "mean":
        summary += (
            "Mean skips NULLs and returns NA without valid inputs; it retains the declared mean type. "
            "Qualified sample folds execute natively without failure fallback."
        )
    elif method == "weighted_mean":
        summary += (
            "Compute sum(value * weight) / sum(weight) over valid pairs. "
            "Zero or cancelling weight sums yield NA. "
            + (
                "weight must be a completed Metric over the same internal samples."
                if prefix == "MetricSamples"
                else "weight must be a numeric row expression on the same contribution root."
            )
        )
    elif method in {"min", "max"}:
        summary += (
            "Return the minimum numeric value. "
            if method == "min"
            else "Return the maximum numeric value. "
        ) + "Skip NULLs; empty/all-NULL inputs return NA."
    elif method == "count":
        summary += (
            "Count complete Entity contribution rows. "
            if prefix == "Entity"
            else "Count non-NULL values. "
        ) + "Empty input returns zero; the result is an int64 count with unit '1'."
    elif method == "count_distinct":
        summary += "Count distinct non-NULL values exactly; empty input returns zero. The result is an int64 count with unit '1'."
        if prefix == "row":
            summary += " Native source compilation uses COUNT(DISTINCT), or ClickHouse uniqExact."
    elif method == "approx_nunique":
        summary += "Estimate the distinct non-NULL count explicitly. The result is an int64 count with unit '1'. "
        summary += (
            "Native source compilation uses DuckDB approx_count_distinct, Trino approx_distinct or ClickHouse uniqHLL12; PostgreSQL/MySQL reject this operation."
            if prefix == "row"
            else "Internal samples have no qualified local approximate distinct kernel."
        )
    else:
        summary += (
            "Reduce internal sample values with "
            + ("an explicit approximate " if method.startswith("approx_") else "an exact ")
            + ("median (q=0.5). " if method.endswith("median") else "quantile; pass q in [0, 1]. ")
            + "Empty/all-NULL inputs return NA; no exact/approximate substitution is inferred."
        )
    if prefix == "MetricSamples":
        summary += (
            " Time samples use represented values without scheduled coverage or implicit fill, "
            "independently of formula shape or execution route. Explicit domains "
            "and NULL/weight policies still apply."
        )
    if prefix in {"row", "Entity"}:
        summary += (
            " time= fixes this source reduction's contribution clock. Omit it to inherit the "
            "single contribution Entity's default_time when declared; otherwise the reduction is report independent. Explicit time=None disables inheritance. "
            "A report TimeScope or CalendarAxis filters only branches with contribution clocks. A field predicate "
            "can still filter source rows independently."
        )
    if prefix in {"row", "MetricSamples"} and method not in {"count", "count_distinct"}:
        summary += (
            " nulls='ignore' adds no assertion; nulls='error' asserts no NULLs on the demanded input; "
            "empty input passes. Successful values use the same computation."
        )
    return summary
