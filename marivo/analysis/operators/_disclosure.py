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
from marivo.analysis._comparison import (
    HolidayThenWeekdayTimeKey,
    HolidayTimeKey,
    OrdinalTimeKey,
    WeekdayTimeKey,
    exact,
    holiday,
    holiday_then_weekday,
    ordinal,
    outer,
    weekday,
)
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
            "exact",
            "exact",
            exact,
            (
                P(
                    "time",
                    "Optional closed original-grid time-key mapping.",
                    ("ordinal", "weekday", "holiday", "holiday_then_weekday"),
                ),
            ),
            "exact()",
            "ExactAlignment",
            "Checked complete keys; no trust/verification option.",
        ),
        (
            "outer",
            "outer",
            outer,
            (
                P("missing", "Keep by default, or explicit original empty_value finish."),
                P(
                    "time",
                    "Optional closed original-grid time-key mapping.",
                    ("ordinal", "weekday", "holiday", "holiday_then_weekday"),
                ),
            ),
            "outer()",
            "OuterAlignment",
            "Compare only; keep permits unequal complete grids; empty_value rejects unobserved time keys.",
        ),
        (
            "ordinal",
            "ordinal",
            ordinal,
            (),
            "ordinal()",
            "OrdinalTimeKey",
            "Map each complete original grid to aligned string keys ordinal:1..ordinal:N; retain both original time coordinates.",
        ),
        (
            "weekday",
            "weekday",
            weekday,
            (),
            "weekday()",
            "WeekdayTimeKey",
            "Single-day grids in their frozen boundary timezone: ISO weekday and same-weekday occurrence within each complete original grid; aligned string keys.",
        ),
        (
            "holiday",
            "holiday",
            holiday,
            (
                P(
                    "current",
                    "Certified whole-day current occurrence TimeScope from catalog.temporal_sets.get(...).occurrence(key).",
                    ("catalog.temporal_sets",),
                ),
                P(
                    "baseline",
                    "Certified whole-day baseline occurrence TimeScope from catalog.temporal_sets.get(...).occurrence(key).",
                    ("catalog.temporal_sets",),
                ),
            ),
            "holiday(current=current_occurrence, baseline=baseline_occurrence)",
            "HolidayTimeKey",
            "Whole-day certified occurrences and single-day grids in each occurrence's boundary timezone; signed civil-day offsets; exact snapshot identities stay frozen.",
        ),
        (
            "holiday_then_weekday",
            "holiday_then_weekday",
            holiday_then_weekday,
            (
                P(
                    "current",
                    "Certified whole-day current occurrence TimeScope from catalog.temporal_sets.get(...).occurrence(key).",
                    ("catalog.temporal_sets",),
                ),
                P(
                    "baseline",
                    "Certified whole-day baseline occurrence TimeScope from catalog.temporal_sets.get(...).occurrence(key).",
                    ("catalog.temporal_sets",),
                ),
            ),
            "holiday_then_weekday(current=current_occurrence, baseline=baseline_occurrence)",
            "HolidayThenWeekdayTimeKey",
            "Whole-day certified occurrences and single-day grids in each occurrence's boundary timezone; offsets inside, before/after ISO weekdays counted outward from the occurrence outside.",
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
                if name in ("ordinal", "weekday", "holiday", "holiday_then_weekday")
                else "inputs.forecast"
                if name == "periods"
                else "forecast_models",
                parameters=parameters,
                output=output,
                constraints=(constraint,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(
                    "result = " + code,
                    (name, "current_occurrence", "baseline_occurrence")
                    if name in ("holiday", "holiday_then_weekday")
                    else (name,),
                    "result",
                    output,
                    name in ("holiday", "holiday_then_weekday"),
                ),
            )
        )
        exports.append(ExportInput(name, value, target))
    for type_value, producer in (
        (OrdinalTimeKey, "ordinal"),
        (WeekdayTimeKey, "weekday"),
        (HolidayTimeKey, "holiday"),
        (HolidayThenWeekdayTimeKey, "holiday_then_weekday"),
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
