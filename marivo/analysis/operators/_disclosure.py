"""Typed Operator owners supply native static inputs linked to execution consumers."""

from __future__ import annotations

from marivo.analysis._capabilities.dataset_model import (
    CONSTRUCTION_EFFECT,
    CONSTRUCTION_FAILURES,
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis._comparison import WindowBucketAlignment, window_bucket
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.forecast_models import (
    ForecastHorizon,
    ForecastModel,
    drift,
    naive,
    periods,
    seasonal_naive,
)


def provider(registry: DatasetFamilyRegistry) -> DisclosureProvider:
    parameters: tuple[P, ...]
    requires: tuple[str, ...]
    value: object
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []

    for name, parameters, code, requires, summary in (
        (
            "where",
            (
                P(
                    "predicates",
                    "Construct typed predicates using fields admitted by the receiver's family.",
                    ("filters",),
                ),
            ),
            "result = dimensioned.where(eq(region, 'north'))",
            ("dimensioned", "eq", "region"),
            "Filter rows; Population filters membership, while result filters do not redefine source membership.",
        ),
        (
            "rank",
            (
                P("by", "Select a current sortable field.", ("datasets.fields",)),
                P("order", "Choose ascending or descending."),
                P(
                    "ties",
                    "Choose ordinal, dense, min or max; ordinal uses a registered unique tie-breaker.",
                ),
                P("partition_by", "Select an ordered tuple of current grouping fields."),
            ),
            "result = dimensioned.aggregate().rank(dimensioned.aggregate().fields.metric(revenue))",
            ("dimensioned", "revenue"),
            "Add stable rank metadata without dropping rows; limit is a separate operation.",
        ),
        (
            "limit",
            (P("count", "Choose a positive row count after establishing total ordering."),),
            "ranked = dimensioned.aggregate().rank(dimensioned.aggregate().fields.metric(revenue))\nresult = ranked.limit(1)",
            ("dimensioned", "revenue"),
            "Keep a bounded prefix of an explicitly totally ordered Dataset.",
        ),
    ):
        admitted = tuple(
            f
            for f in registry.registrations
            if any(c.id == f.family_id + "." + name for c in f.consumers)
        )
        bindings = tuple(
            bind(getattr(t, name), t)
            for f in admitted
            for t in (f.logical_type, f.materialized_type)
        )
        descriptors.append(
            operation(
                "datasets." + name,
                "dataset." + name,
                bindings[0].implementation,
                bindings=bindings,
                summary=summary,
                discovery_group="methods.rows",
                parameters=parameters,
                output="Logical Dataset of the receiver family",
                constraints=(summary,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(code, requires, "result", "Logical Dataset"),
                registration_ids=tuple(f.family_id + "." + name for f in admitted),
            )
        )

    for name, target, value, parameters, code, output, constraint in (
        (
            "window_bucket",
            "window_bucket",
            window_bucket,
            (),
            "window_bucket()",
            "WindowBucketAlignment",
            "Pair complete equally sized bucket sequences by ordinal.",
        ),
        (
            "periods",
            "periods",
            periods,
            (P("count", "Choose a positive integer forecast horizon."),),
            "periods(2)",
            "ForecastHorizon",
            "The horizon counts forecast buckets, not elapsed seconds.",
        ),
        (
            "naive",
            "forecast_models.naive",
            naive,
            (),
            "naive()",
            "ForecastModel",
            "Repeat the last observed value.",
        ),
        (
            "drift",
            "forecast_models.drift",
            drift,
            (),
            "drift()",
            "ForecastModel",
            "Extrapolate the training endpoint drift.",
        ),
        (
            "seasonal_naive",
            "forecast_models.seasonal_naive",
            seasonal_naive,
            (
                P(
                    "periods",
                    "Choose a positive integer season length supported by the training history.",
                ),
            ),
            "seasonal_naive(periods=2)",
            "ForecastModel",
            "Repeat the latest complete seasonal pattern.",
        ),
    ):
        descriptors.append(
            operation(
                target,
                "mv." + name,
                value,
                summary=constraint,
                discovery_group="inputs.time"
                if name == "window_bucket"
                else "inputs.forecast"
                if name == "periods"
                else "forecast_models",
                parameters=parameters,
                output=output,
                constraints=(constraint,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput("result = " + code, (name,), "result", output),
            )
        )
        exports.append(ExportInput(name, value, target))
    for type_value, producer in (
        (WindowBucketAlignment, "window_bucket"),
        (ForecastHorizon, "periods"),
        (ForecastModel, "forecast_models.naive"),
    ):
        descriptors.append(
            value_type(
                type_value.__name__,
                type_value,
                summary=f"Closed {type_value.__name__} type_value contract.",
                acquisition="Use its registered policy/model constructor.",
                producers=(producer,),
            )
        )
        exports.append(ExportInput(type_value.__name__, type_value, type_value.__name__))
    return DisclosureProvider("operators", tuple(descriptors), tuple(exports))
