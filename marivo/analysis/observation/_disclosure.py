"""Observation-owned native disclosure; semantic admission stays in contracts."""

from __future__ import annotations

import inspect

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
    for type_value, target in (
        (Grain, "Grain"),
        (TimeScope, "TimeScope"),
    ):
        descriptors.append(
            value_type(
                target,
                type_value,
                summary=f"Exact {target} input contract.",
                acquisition="Obtain session.time_scope(...) or a non-None calendar_axis.scope. Direct construction is unsupported."
                if type_value is TimeScope
                else "Use mv.grain(unit, count=...). Direct construction is unsupported.",
                producers=("session.time_scope", "calendar_axis.scope")
                if type_value is TimeScope
                else ("grain.define",),
                constraints=(
                    "Use scope.by(grain) in observe.by; bare Grain rejects. Natural grains have unit/count value identity.",
                )
                if type_value is Grain
                else (
                    "Use one TimeScope in observe.where. Exact anchors retain a frozen timezone.",
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
    return DisclosureProvider("observation", tuple(descriptors), tuple(exports))
