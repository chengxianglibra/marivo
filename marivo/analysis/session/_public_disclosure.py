"""Native contracts for logical receiver guidance and bound identity location."""

from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis._contract import AnalysisAction, AnalysisContract
from marivo.analysis.dsl.population import MemberAxis, member


def inputs() -> tuple[tuple[Descriptor, ...], tuple[ExportInput, ...]]:
    descriptors: list[Descriptor] = [
        value_type(
            "AnalysisContract",
            AnalysisContract,
            summary="Logical kind, domain, quantity and valid next calls for a Query or Population.",
            acquisition="Call query.contract() or population.contract().",
            producers=("Query.contract", "Population.contract"),
            consumers=("AnalysisContract.show",),
        ),
        value_type(
            "AnalysisAction",
            AnalysisAction,
            summary="One mechanically valid logical receiver call and its canonical Help target.",
            acquisition="Read contract.actions.",
            producers=("AnalysisContract",),
            consumers=("AnalysisAction.show",),
        ),
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
    for owner, receiver in ((AnalysisAction, "action"), (AnalysisContract, "contract")):
        function = owner.show
        descriptors.append(
            operation(
                f"{owner.__name__}.show",
                f"{receiver}.show",
                function,
                bindings=(bind(function, owner),),
                summary="Print bounded source-free logical guidance.",
                parameters=(),
                output="None",
                constraints=("No source or schema read; pending execution checks remain pending.",),
                effects="Print metadata only.",
                failures=("AnalysisError: inspect the exact receiver.",),
                example=ExampleInput(f"result = {receiver}.show()", (receiver,), "result", "None"),
            )
        )
    return tuple(descriptors), (
        *(
            ExportInput(value.__name__, value, value.__name__)
            for value in (AnalysisAction, AnalysisContract, MemberAxis)
        ),
        ExportInput("member", member, "member"),
    )
