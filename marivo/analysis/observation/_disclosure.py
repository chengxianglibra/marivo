"""Observation-owned native disclosure; semantic admission stays in contracts."""

from __future__ import annotations

import inspect

from marivo import _temporal
from marivo._temporal import Grain, TimeScope
from marivo.analysis import grain
from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
    with_sealed_variants,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis.dsl.time_group import time_group


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
            "Choose a built-in unit/count; subday width must tile and remain shorter than a nominal day, with one spelling. 60second/120second/60minute/24hour reject; 90second/90minute are legal. Grains have no ordering.",
        ),
    )
    for target, value, output, call, guidance in constructors:
        parameters = tuple(P(name, guidance) for name in bind(value).signature.parameters)
        descriptors.append(
            operation(
                "grain.define",
                "mv." + target,
                value,
                summary=guidance,
                discovery_group="inputs.time",
                parameters=parameters,
                output=output,
                constraints=(guidance,),
                effects="Construct an immutable Grain; no query or Artifact publication.",
                failures=("ValueError: choose a supported built-in unit and count.",),
                example=ExampleInput("result = " + call, (target,), "result", output),
            )
        )
        exports.append(ExportInput(target, value, "grain.define"))
    descriptors.append(
        operation(
            "time_group",
            "mv.time_group",
            time_group,
            summary="Filter contributions by a time field, bucket that field, and complete the output grid.",
            discovery_group="inputs.time",
            parameters=(
                P(
                    "field",
                    "Use a resolved TimeDimension with a single-valued path from every source root.",
                ),
                P("axis", "Use scope.by(grain) for the finite range and the bucket timezone."),
            ),
            related=("session.observe", "time_scope.by", "grouping"),
            output="Grouping",
            constraints=(
                "Only accepted in Session.observe.by. The half-open field range applies before aggregation and intersects where and Metric period or window conditions. Null time fields contribute to no bucket.",
                "Use a plain field for raw time values or an authored business date. Pass a TimeScope or CalendarAxis directly in by to provide Metric window endpoints.",
                "Custom calendars require one complete certified period. Multiple distinct field calendars form a product of their finite bucket grids.",
            ),
            effects="Capture only; no source reads.",
            failures=("AnalysisError: resolve the field and use a scoped CalendarAxis.",),
            example=ExampleInput(
                "days = session.time_scope(start='2026-08-01', end='2026-08-04').by(mv.grain('day'))\n"
                "result = mv.time_group(created_at, axis=days)",
                ("session", "created_at"),
                "result",
                "Grouping",
            ),
        )
    )
    exports.append(ExportInput("time_group", time_group, "time_group"))
    for type_value, target in (
        (Grain, "Grain"),
        (TimeScope, "TimeScope"),
    ):
        descriptors.append(
            value_type(
                target,
                type_value,
                summary=f"Exact {target} input contract.",
                acquisition=(
                    "Obtain session.time_scope(...), calendar.period/period_on or periods(...).items, "
                    "temporal_set.occurrence or occurrences(...).items, or a non-None calendar_axis.scope. "
                    "Direct construction is unsupported; scope reads never rebuild a range."
                    if type_value is TimeScope
                    else "Use mv.grain for builtin units; ms.calendar_grain for source-free lookup; "
                    "calendar.grain(level) captures a current certified snapshot when available, otherwise returns an unbound lookup. Direct construction is unsupported."
                ),
                producers=(
                    "session.time_scope",
                    "catalog.period_calendars.period",
                    "catalog.period_calendars.period_on",
                    "catalog.period_calendars.periods",
                    "catalog.temporal_sets.occurrence",
                    "catalog.temporal_sets.occurrences",
                    "calendar_axis.scope",
                )
                if type_value is TimeScope
                else ("grain.define", "catalog.period_calendars.grain"),
                constraints=(
                    "Equality/hash include captured snapshot identity. scope.by requires a captured calendar Grain: certify the calendar before calling calendar.grain(level). An unbound lookup can instead resolve in a consuming Session's certified scope, such as to_date.",
                )
                if type_value is Grain
                else (
                    "Session scopes retain their frozen timezone and exact InstantAnchor start/end; Catalog periods can retain civil date bounds.",
                ),
            )
        )
        exports.append(ExportInput(type_value.__name__, type_value, target))
    for receiver_type, receiver_name, method_names in ((TimeScope, "window", ("render", "show")),):
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
            _temporal._SessionTimeScope,
            _temporal._CalendarPeriodTimeScope,
            _temporal._TemporalOccurrenceTimeScope,
        ),
    }
    return DisclosureProvider(
        "observation",
        with_sealed_variants(
            descriptors,
            sealed_variants,
            applicability={
                _temporal._BuiltinGrain: "grain.kind == 'builtin'",
                _temporal._SemanticGrain: "grain.kind == 'semantic'",
                _temporal._SessionTimeScope: "time_scope.kind == 'absolute' constructed by session.time_scope(...)",
                _temporal._CalendarPeriodTimeScope: "time_scope.kind == 'calendar_period'",
                _temporal._TemporalOccurrenceTimeScope: "time_scope.kind == 'temporal_occurrence'",
            },
        ),
        tuple(exports),
    )
