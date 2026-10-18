"""Native contracts for logical receiver guidance and bound identity location."""

from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    ExampleInput,
    ExportInput,
    operation,
    value_type,
)
from marivo.analysis.dsl.population import MemberAxis, member


def inputs() -> tuple[tuple[Descriptor, ...], tuple[ExportInput, ...]]:
    descriptors: list[Descriptor] = [
        value_type(
            "MemberAxis",
            MemberAxis,
            summary="Locate one compatible complete identity on the receiving domain.",
            acquisition="Call mv.member().",
            producers=("member",),
            consumers=("session.observe", "Population.project", "Population.classify"),
        ),
        operation(
            "member",
            "mv.member",
            member,
            summary="Locate the unique compatible identity already bound by the receiver.",
            parameters=(),
            output="MemberAxis",
            constraints=(
                "observe requires explicit members=P; additional identity classifications must be single-valued on P. No Entity is guessed.",
            ),
            effects="Pure marker construction.",
            failures=("AnalysisError: select an explicit compatible identity binding.",),
            example=ExampleInput("result = mv.member()", (), "result", "MemberAxis"),
            discovery_group="inputs.population",
        ),
    ]
    return tuple(descriptors), (
        *(ExportInput(value.__name__, value, value.__name__) for value in (MemberAxis,)),
        ExportInput("member", member, "member"),
    )
