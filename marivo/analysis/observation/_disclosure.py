"""Observation-owned native disclosure; semantic admission stays in contracts."""

from __future__ import annotations

import inspect

from marivo import _temporal
from marivo._temporal import BeforeEndBoundary, Grain, TimeScope
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
                effects="Construct an immutable Grain; no query, Run, or Artifact publication.",
                failures=("ValueError: choose a supported built-in unit and count.",),
                example=ExampleInput("result = " + call, (target,), "result", output),
            )
        )
        exports.append(ExportInput(target, value, target))
    for type_value, target, producer in (
        (Grain, "Grain", "grain"),
        (TimeScope, "TimeScope", "session.time_scope"),
        (BeforeEndBoundary, "BeforeEndBoundary", "TimeScope"),
    ):
        descriptors.append(
            value_type(
                target,
                type_value,
                summary=f"Exact {target} input contract.",
                acquisition="Read scope.before_end from an existing TimeScope."
                if type_value is BeforeEndBoundary
                else "Construct with session.time_scope(...); its frozen timezone supports by(Grain). "
                "Session.instant(...) constructs one exact boundary; no module-level time constructors are public."
                if type_value is TimeScope
                else f"Construct with mv.{producer}(...).",
                producers=(producer,),
            )
        )
        exports.append(ExportInput(type_value.__name__, type_value, target))
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
            _temporal._SessionTimeScope,
            _temporal._CalendarPeriodTimeScope,
            _temporal._TemporalOccurrenceTimeScope,
        ),
    }
    return DisclosureProvider(
        "observation", with_sealed_variants(descriptors, sealed_variants), tuple(exports)
    )
