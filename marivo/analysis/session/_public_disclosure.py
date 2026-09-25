"""First-round Analysis DSL native Help bindings owned by the Session surface."""

from __future__ import annotations

from inspect import Parameter, isfunction, signature

from marivo.analysis import public_dsl as dsl
from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    ExampleInput,
    ExportInput,
    ParameterInput,
    bind,
    operation,
    value_type,
)


def inputs() -> tuple[tuple[Descriptor, ...], tuple[ExportInput, ...]]:
    """Bind first-round public values and callables to one native Help owner."""
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    types = (
        dsl.AnalysisContract,
        dsl.GroupedAnalysisDomain,
        dsl.GroupedNumericRelation,
        dsl.GroupedRatioRelation,
        dsl.LogicalAnalysisDomain,
        dsl.LogicalAssociationResult,
        dsl.LogicalCategoryRelation,
        dsl.LogicalNumericRelation,
        dsl.MaterializedAnalysisDomain,
        dsl.MaterializedAssociationResult,
        dsl.MaterializedCategoryRelation,
        dsl.MaterializedNumericRelation,
        dsl.MaterializedGroupedNumericRelation,
        dsl.LogicalRolledNumericRelation,
        dsl.MaterializedRolledNumericRelation,
        dsl.LogicalRolledRatioRelation,
        dsl.MaterializedRolledRatioRelation,
        dsl.LogicalRatioRelation,
        dsl.MaterializedRatioRelation,
        dsl.LogicalDifferenceRelation,
        dsl.MaterializedDifferenceRelation,
        dsl.LogicalSelectedDifferenceRelation,
        dsl.MaterializedSelectedDifferenceRelation,
        dsl.LogicalStatisticRelation,
        dsl.MaterializedStatisticRelation,
        dsl.MaterializedCoefficientRelation,
        dsl.LogicalCoefficientSelectionRelation,
        dsl.MaterializedCoefficientSelectionRelation,
        dsl.LogicalFixedAnalysisDomain,
        dsl.LogicalSelectedCategoryRelation,
        dsl.MaterializedSelectedCategoryRelation,
        dsl.RowMethod,
        dsl.RootRoute,
        dsl.RootRoutes,
    )
    for value in types:
        name = (
            value.__name__
            if value not in (dsl.RootRoute, dsl.RootRoutes)
            else ("RootRoute" if value is dsl.RootRoute else "RootRoutes")
        )
        descriptors.append(
            value_type(
                name,
                value,
                summary=f"First-round governed Analysis {name} value.",
                acquisition="Construct through session.members() or the returned typed relation.",
                producers=("session.members",),
                consumers=("session.artifact",),
                constraints=("Exact member, semantic and Artifact bindings govern continuations.",),
            )
        )
        exports.append(ExportInput(name, value, name))

    functions = (dsl.route, dsl.routes, dsl.sum, dsl.count, dsl.mean)
    for function in functions:
        name = function.__name__
        params = tuple(
            ParameterInput(
                key, "Supply the exact governed input for this closed first-round shape."
            )
            for key in signature(function).parameters
        )
        descriptors.append(
            operation(
                "dsl." + name,
                "mv." + name,
                function,
                summary=f"Construct the admitted {name} argument for the Analysis DSL.",
                parameters=params,
                output="RootRoute"
                if name == "route"
                else "RootRoutes"
                if name == "routes"
                else "RowMethod",
                constraints=("Only declared J1–J4 input shapes are admitted.",),
                effects="Pure argument construction; no source read or Run.",
                failures=("AnalysisError: use the structured expected and received repair.",),
                example=ExampleInput(
                    f"result = mv.{name}()"
                    if name in ("sum", "count", "mean")
                    else f"result = mv.{name}(root, through=(relationship,))"
                    if name == "route"
                    else "result = mv.routes(first_route, second_route)",
                    ()
                    if name in ("sum", "count", "mean")
                    else ("root", "relationship")
                    if name == "route"
                    else ("first_route", "second_route"),
                    "result",
                    "A closed Analysis DSL argument.",
                    True,
                ),
            )
        )
        exports.append(ExportInput(name, function, "dsl." + name))

    owner_methods: tuple[type[object], ...] = (
        dsl._Value,
        dsl._MaterializedValue,
        *(value for value in types if value not in (dsl.RowMethod, dsl.RootRoute, dsl.RootRoutes)),
    )
    for owner in owner_methods:
        for name, value in vars(owner).items():
            if name.startswith("_") or not isfunction(value):
                continue
            target = "dsl." + owner.__name__.lstrip("_") + "." + name
            arguments = tuple(
                f"{key}={key}" if parameter.kind is Parameter.KEYWORD_ONLY else key
                for key, parameter in signature(value).parameters.items()
                if key != "self"
            )
            params = tuple(
                ParameterInput(key, "Use the bound relation and exact governed input.")
                for key in signature(value).parameters
                if key != "self"
            )
            descriptors.append(
                operation(
                    target,
                    "relation." + name,
                    value,
                    bindings=(bind(value, owner),),
                    summary=f"{owner.__name__}.{name} for an admitted first-round relation.",
                    parameters=params,
                    output=str(signature(value).return_annotation),
                    constraints=(
                        "The exact receiver and its current contract gate this operation.",
                    ),
                    effects="Construct a logical continuation, publish on execute, or read exact retained state.",
                    failures=(
                        "AnalysisError: inspect the structured repair for the current shape.",
                    ),
                    example=ExampleInput(
                        f"result = relation.{name}()"
                        if not params
                        else f"result = relation.{name}({', '.join(arguments)})",
                        ("relation", *(parameter.name for parameter in params)),
                        "result",
                        "The receiver-bound result.",
                        True,
                    ),
                )
            )
    return tuple(descriptors), tuple(exports)
