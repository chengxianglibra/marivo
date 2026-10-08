"""Typed Operator owners supply native static inputs linked to execution consumers."""

from __future__ import annotations

from marivo.analysis._capabilities.dataset_model import (
    CONSTRUCTION_EFFECT,
    CONSTRUCTION_FAILURES,
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    operation,
    value_type,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis._comparison import WindowBucketAlignment, window_bucket
from marivo.analysis.forecast_models import (
    ForecastHorizon,
    ForecastModel,
    drift,
    naive,
    periods,
    seasonal_naive,
)


def provider() -> DisclosureProvider:
    parameters: tuple[P, ...]
    value: object
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []

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
