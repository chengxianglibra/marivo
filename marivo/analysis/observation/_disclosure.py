"""Observation-owned native disclosure; semantic admission stays in contracts."""

from __future__ import annotations

import inspect

from marivo import _temporal
from marivo._temporal import BeforeEndBoundary, Grain, TimeScope
from marivo.analysis import grain, runtime_metric, time_scope
from marivo.analysis._capabilities.dataset_model import (
    CONSTRUCTION_EFFECT,
    CONSTRUCTION_FAILURES,
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    NavigationInput,
    bind,
    operation,
    value_type,
    with_sealed_variants,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)


def provider() -> DisclosureProvider:
    parameters: tuple[P, ...]
    value: object
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    constructors = (
        (
            "grain",
            grain,
            "Grain",
            "grain('day')",
            "Choose a built-in unit/count or exact governed period calendar and level.",
        ),
        (
            "time_scope",
            time_scope,
            "TimeScope",
            "time_scope(start='2026-08-01', end='2026-09-01')",
            "Use a half-open window: start is included and end is excluded. Date-only bounds use the Session's report timezone; set report_timezone explicitly for the intended calendar. End a full month at the first day of the next month.",
        ),
    )
    for target, value, output, call, guidance in constructors:
        parameters = tuple(P(name, guidance) for name in bind(value).signature.parameters)
        descriptors.append(
            operation(
                target,
                "mv." + target,
                value,
                summary=guidance,
                discovery_group="inputs.time",
                parameters=parameters,
                output=output,
                constraints=(guidance,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput("result = " + call, (target,), "result", output),
            )
        )
        exports.append(ExportInput(target, value, target))
    for type_value, target, producer in (
        (Grain, "Grain", "grain"),
        (TimeScope, "TimeScope", "time_scope"),
        (BeforeEndBoundary, "BeforeEndBoundary", "TimeScope"),
    ):
        descriptors.append(
            value_type(
                target,
                type_value,
                summary=f"Exact {target} input contract.",
                acquisition="Read scope.before_end from an existing TimeScope."
                if type_value is BeforeEndBoundary
                else f"Construct with mv.{producer}(...).",
                producers=(producer,),
            )
        )
        exports.append(ExportInput(type_value.__name__, type_value, target))
    metric_parameter_inputs = {
        "measure": "Select an exact Measure ref from the semantic catalog.",
        "value": "Select the governed numeric value Measure ref.",
        "weight": "Select the governed additive weight Measure ref.",
        "agg": "count_distinct, median and percentile require exact operations; approx_* explicitly permits approximation. Scoped catalog.readiness() blocks known backend incompatibilities without connecting; execution never substitutes the definition.",
        "fold": "Use the Measure's governed temporal fold by default; supply only an admitted fold override.",
        "slice_by": "Optionally map exact Dimension refs to typed slice values; the mapping is copied into the value.",
        "label": "Choose a nonempty value-column label distinct from retained coordinate names.",
        "metric": "Select a Metric ref or an already constructed closed Runtime Metric expression.",
        "by": "Supply a nonempty typed Dimension-to-slice mapping.",
        "numerator": "Select an exact Metric ref or closed Runtime Metric numerator expression.",
        "denominator": "Select an exact Metric ref or closed Runtime Metric denominator expression.",
        "zero_division": "Choose null to retain undefined ratios or error to reject them.",
        "add": "Supply ordered Metric refs/expressions with coefficient +1; at least two total terms are required.",
        "subtract": "Supply ordered Metric refs/expressions with coefficient -1, or an empty tuple.",
    }
    members = []
    for name, code in (
        ("aggregate", "runtime_metric.aggregate(amount_measure, agg='sum', label='total')"),
        (
            "weighted_mean",
            "runtime_metric.weighted_mean(amount_measure, weight_measure, label='weighted')",
        ),
        ("slice", "runtime_metric.slice(revenue, by={region: 'north'}, label='north_revenue')"),
        ("ratio", "runtime_metric.ratio(revenue, count_metric, label='rate')"),
        ("linear", "runtime_metric.linear(add=(revenue, revenue), label='total')"),
    ):
        value = getattr(runtime_metric, name)
        target = "runtime_metric." + name
        members.append(target)
        guidance = (
            "Construct a closed expression from exact semantic refs and an explicit label. "
            "Graph observation currently qualifies DuckDB table/Parquet UTC instant-us "
            "sources: int64 sum/count leaves, independently reduced ratios, nested signed "
            "linear sum/count terms, and paired int64 value/weight means. Omitted during "
            "adds no time restriction; runtime leaves need an explicit default event axis. "
            "Other numeric types, nested nonlinear finishes and coordinate transport need "
            "their own method qualification. No SQL or arbitrary expressions."
        )
        descriptors.append(
            operation(
                target,
                "mv." + target,
                value,
                summary=guidance,
                parameters=tuple(
                    P(n, metric_parameter_inputs[n]) for n in bind(value).signature.parameters
                ),
                output="governed RuntimeMetricExpr",
                constraints=(guidance,),
                effects=CONSTRUCTION_EFFECT,
                failures=("ValueError: repair the exact constructor input before observing.",),
                example=ExampleInput(
                    "result = " + code,
                    ("runtime_metric", "amount_measure")
                    if name == "aggregate"
                    else ("runtime_metric", "amount_measure", "weight_measure")
                    if name == "weighted_mean"
                    else ("runtime_metric", "revenue", "region")
                    if name == "slice"
                    else ("runtime_metric", "revenue", "count_metric")
                    if name == "ratio"
                    else ("runtime_metric", "revenue"),
                    "result",
                    "RuntimeMetricExpr",
                ),
            )
        )
    descriptors.append(
        NavigationInput(
            "runtime_metric",
            "Closed governed Runtime Metric constructors.",
            tuple(members),
            discovery_group="inputs",
            related=("catalog.require",),
        )
    )
    exports.append(ExportInput("runtime_metric", runtime_metric, "runtime_metric"))
    for receiver_type, receiver_name, method_names in (
        (Grain, "daily_grain", ("to_token", "width_seconds")),
        (TimeScope, "window", ("contract", "model_dump", "render", "show")),
        (BeforeEndBoundary, "boundary", ("show",)),
    ):
        for method_name in method_names:
            method_value = inspect.getattr_static(receiver_type, method_name)
            binding = bind(method_value, receiver_type)
            descriptors.append(
                operation(
                    receiver_type.__name__ + "." + method_name,
                    receiver_name + "." + method_name,
                    method_value,
                    bindings=(binding,),
                    summary="Inspect the immutable temporal value.",
                    parameters=tuple(
                        P(
                            n,
                            "Use the reflected temporal serialization or rendering option; defaults preserve the bounded value contract.",
                        )
                        for n in binding.signature.parameters
                        if n != "self"
                    ),
                    output=str(binding.signature.return_annotation),
                    constraints=("Pure temporal metadata; no source read.",),
                    effects="Pure value projection.",
                    failures=(
                        "ValueError: select a grain or temporal option supported by this value.",
                    ),
                    example=ExampleInput(
                        f"result = {receiver_name}.{method_name}"
                        + ("" if isinstance(method_value, property) else "()"),
                        (receiver_name,),
                        "result",
                        str(binding.signature.return_annotation),
                    ),
                )
            )
    sealed_variants = {
        "Grain": (_temporal._BuiltinGrain, _temporal._SemanticGrain),
        "TimeScope": (
            _temporal._AbsoluteTimeScope,
            _temporal._CalendarPeriodTimeScope,
            _temporal._TemporalOccurrenceTimeScope,
        ),
    }
    return DisclosureProvider(
        "observation", with_sealed_variants(descriptors, sealed_variants), tuple(exports)
    )
