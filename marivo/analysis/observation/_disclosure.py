"""Observation-owned native disclosure; semantic admission stays in contracts."""

from __future__ import annotations

import inspect

from marivo import _temporal
from marivo._temporal import Grain, TimeScope
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
    family,
    operation,
    value_type,
    with_sealed_variants,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis.datasets.descriptors import _CompleteFromSchema
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.observation import predicates as p
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
)
from marivo.refs import SemanticKind

METRIC = P(
    "metric",
    "Select an exact Metric ref, catalog entry or governed runtime_metric expression.",
    ("runtime_metric",),
)
SCOPE = P(
    "time_scope",
    "Construct an explicit observation window independently of membership scope.",
    ("time_scope",),
)
TIME = P(
    "time_dimension", "Choose the exact source Entity's TimeDimension from the semantic catalog."
)
POPULATION = P(
    "population",
    "Use a same-Session Population or an admitted identity-bearing Dataset; sampled/censored inputs obey source admission.",
    ("population",),
)


def provider(
    registry: DatasetFamilyRegistry, *, source_receiver: type[object]
) -> DisclosureProvider:
    parameters: tuple[P, ...]
    requires: tuple[str, ...]
    variants: tuple[type[object], ...]
    value: object
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    for fid, target, summary, variants in (
        (
            "population",
            "population",
            "Exact Entity membership under selection scope and optional governed sampling.",
            (_CompleteFromSchema,),
        ),
        (
            "metric",
            "metric_dataset",
            "Ordered Metric values over exact membership and observation coordinates.",
            (EntityPresentMetricSemantics, EntityReducedMetricSemantics),
        ),
    ):
        registration = registry.get(fid)
        descriptors.append(
            family(
                target,
                registration,
                summary=summary,
                variants=variants,
                acquisition=(
                    "Construct with session.population(...)."
                    if fid == "population"
                    else "Construct with session.observe(...)."
                )
                + " execute() produces the paired Materialized state.",
                constraints=(
                    "Logical and Materialized inputs share family admission; downstream operators return Logical state.",
                    "Membership selection and observation windows are distinct. Materialized folds require retained coordinates and sufficient statistics.",
                ),
            )
        )
        exports.extend(
            ExportInput(t.__name__, t, target)
            for t in (registration.logical_type, registration.materialized_type)
        )

    sources = (
        (
            "population.create",
            "population",
            "Governed membership from one Entity.",
            (
                P("entity", "Select an identity-bearing Entity from the semantic catalog."),
                SCOPE,
                TIME,
            ),
            "LogicalPopulationDataset",
            "result = session.population(customer)",
            ("session", "customer"),
        ),
        (
            "observe",
            "observe",
            "Observe ordered Metrics with explicit population and source window.",
            (P("metrics", METRIC.acquisition, METRIC.targets), POPULATION, SCOPE, TIME),
            "LogicalMetricDataset",
            "result = session.observe(revenue)",
            ("session", "revenue"),
        ),
        (
            "Session.source_bindings",
            "source_bindings",
            "Capture non-secret source parameters while constructing logical sources.",
            (
                P(
                    "bindings",
                    "Map exact Entity refs to their declared non-secret parameter values; obtain names from the source declaration.",
                ),
            ),
            "restoring context manager",
            "with session.source_bindings({}):\n    result = session.observe(revenue)",
            ("session", "revenue"),
        ),
    )
    for target, name, summary, parameters, output, code, requires in sources:
        descriptors.append(
            operation(
                target,
                "session." + name,
                getattr(source_receiver, name),
                semantic_kinds=(SemanticKind.ENTITY,)
                if name == "population"
                else (SemanticKind.METRIC, SemanticKind.TIME_DIMENSION)
                if name == "observe"
                else (),
                summary=summary,
                discovery_group="inputs" if name == "source_bindings" else "entry",
                related=("session.get_or_create", "catalog.require", "catalog.readiness"),
                parameters=parameters,
                output=output,
                constraints=(
                    "Current scoped semantic authority and one Session; construction performs no I/O.",
                    "A source captures bindings at construction; execution never consults an ambient binding scope.",
                ),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(
                    code,
                    requires,
                    "result",
                    "LogicalMetricDataset with captured source bindings"
                    if name == "source_bindings"
                    else output,
                ),
                registration_ids=(
                    ("session." + name,) if name in ("observe", "population") else ()
                ),
            )
        )

    metric = registry.get("metric")
    methods = (
        (
            "with_dimensions",
            (
                P(
                    "dimensions",
                    "Choose ordered unique non-time Dimensions from the semantic catalog.",
                ),
            ),
            "result = metric.with_dimensions(region)",
            ("metric", "region"),
            "Add governed Dimension coordinates; logical source authority is required for missing axes.",
        ),
        (
            "with_time_axis",
            (
                P("time_dimension", TIME.acquisition),
                P("grain", "Construct the exact calendar grain.", ("grain",)),
            ),
            "result = metric.with_time_axis(day, grain=grain('day'))",
            ("metric", "day", "grain"),
            "Add the exact TimeDimension and grain before Entity reduction.",
        ),
        (
            "aggregate",
            (),
            "result = dimensioned.aggregate()",
            ("dimensioned",),
            "Reduce Entity identity using each Metric's governed aggregation.",
        ),
        (
            "rollup",
            (
                P("drop_dimensions", "Choose only current Dimension refs to drop."),
                P("grain", "Choose a compatible coarser grain or None.", ("grain",)),
                P("drop_time", "True removes the time coordinate; cannot also request a grain."),
            ),
            "result = dimensioned.aggregate().rollup(drop_dimensions=(region,))",
            ("dimensioned", "region"),
            "Fold already reduced coordinates; retained sufficient statistics must authorize the fold.",
        ),
        (
            "metric",
            (
                P(
                    "metric",
                    "Select an existing Metric input or its current DatasetFieldRef. After cold recovery, use fields.get(name).",
                    ("datasets.fields",),
                ),
            ),
            "result = metric.metric(metric.fields.get('revenue'))",
            ("metric",),
            "Select one retained Metric identity; field selectors require this Session and current binding.",
        ),
    )
    for name, parameters, code, requires, summary in methods:
        descriptors.append(
            operation(
                "metric_dataset." + name,
                "dataset." + name,
                getattr(metric.logical_type, name),
                bindings=tuple(
                    bind(getattr(t, name), t)
                    for t in (metric.logical_type, metric.materialized_type)
                ),
                semantic_kinds=(SemanticKind.DIMENSION,) if name == "with_dimensions" else (),
                summary=summary,
                discovery_group="methods.metric",
                parameters=parameters,
                output="LogicalMetricDataset",
                constraints=(summary,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(code, requires, "result", "LogicalMetricDataset"),
                registration_ids=("metric." + name,),
            )
        )
    for name in (
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
    ):
        field = P(
            "field",
            "Select a typed semantic ref or exact DatasetFieldRef; raw identity fields are forbidden.",
            ("datasets.fields",),
        )
        if name in ("all_of", "any_of"):
            parameters = (
                P(
                    "predicates",
                    "Construct one or more typed AnalysisPredicate values.",
                    ("eq", "gt"),
                ),
            )
            call = f"{name}(eq(region, 'north'), gt(revenue, 0))"
        elif name == "not_":
            parameters = (P("predicate", "Construct one typed predicate.", ("eq",)),)
            call = "not_(eq(region, 'north'))"
        elif name in ("is_null", "is_not_null"):
            parameters, call = (field,), f"{name}(revenue)"
        elif name == "is_in":
            parameters = (
                field,
                P("values", "Supply a nonempty homogeneous tuple of admitted typed literals."),
            )
            call = "is_in(region, ('north', 'south'))"
        else:
            parameters = (
                field,
                P(
                    "value",
                    "Supply a compatible scalar; match civil/aware timestamp kinds. Use is_null/is_not_null for nulls.",
                ),
            )
            call = f"{name}(revenue, 0)"
        value = getattr(p, name)
        descriptors.append(
            operation(
                name,
                "mv." + name,
                value,
                summary="Construct a closed typed predicate; binding to rows occurs in where().",
                discovery_group="filters",
                parameters=parameters,
                output="AnalysisPredicate",
                constraints=(
                    "No Python truth testing, arbitrary expression, or raw Entity identity literal.",
                ),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(
                    "result = " + call,
                    (name, "eq", "gt", "region", "revenue")
                    if name in ("all_of", "any_of")
                    else ("not_", "eq", "region")
                    if name == "not_"
                    else (name, "region")
                    if name == "is_in"
                    else (name, "revenue"),
                    "result",
                    "AnalysisPredicate",
                ),
            )
        )
        exports.append(ExportInput(name, value, name))

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
            "time_scope(start='2026-02-01', end='2026-02-20')",
            "Choose an explicit bounded window; endpoints follow the temporal contract.",
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
        (p.AnalysisPredicate, "AnalysisPredicate", "eq"),
        (Grain, "Grain", "grain"),
        (TimeScope, "TimeScope", "time_scope"),
    ):
        descriptors.append(
            value_type(
                target,
                type_value,
                summary=f"Exact {target} input contract.",
                acquisition=f"Construct with mv.{producer}(...).",
                producers=(producer,),
            )
        )
        exports.append(ExportInput(type_value.__name__, type_value, target))
    metric_parameter_inputs = {
        "measure": "Select an exact Measure ref from the semantic catalog.",
        "value": "Select the governed numeric value Measure ref.",
        "weight": "Select the governed additive weight Measure ref.",
        "agg": "Choose an admitted aggregation; percentile uses its exact quantile declaration.",
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
            "Use exact semantic refs or closed Runtime Metric expressions with ±1 "
            "coefficients and an explicit label; admitted linear shapes execute on all "
            "six backends and stay value-exact (integer and Decimal sum terms keep "
            "their types, mixed terms follow the engine's float promotion); no SQL or "
            "arbitrary expressions."
            if name == "linear"
            else "Use exact semantic refs or closed Runtime Metric expressions, typed slice mappings and an explicit label; no SQL or arbitrary expressions."
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
                        f"result = {receiver_name}.{method_name}()",
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
