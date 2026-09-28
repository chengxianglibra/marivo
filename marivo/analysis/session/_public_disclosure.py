"""First-round Analysis DSL native Help bindings owned by the Session surface."""

from __future__ import annotations

from inspect import Parameter, getdoc, isfunction, signature

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

_METHOD_GROUPS = {
    ("GroupedRatioRelation", "rollup"): "methods.metric",
    ("LogicalAnalysisDomain", "read"): "inputs.population",
    ("LogicalAnalysisDomain", "group_by"): "methods.metric",
    ("LogicalAnalysisDomain", "observe"): "methods.metric",
    ("LogicalNumericRelation", "group_by"): "methods.metric",
    ("LogicalNumericRelation", "rollup"): "methods.metric",
    ("LogicalNumericRelation", "summarize"): "methods.metric",
    ("LogicalNumericRelation", "compare"): "methods.compare",
    ("LogicalNumericRelation", "correlate"): "methods.association",
    ("MaterializedNumericRelation", "summarize"): "methods.metric",
    ("MaterializedNumericRelation", "compare"): "methods.compare",
    ("LogicalRatioRelation", "group_by"): "methods.metric",
    ("LogicalRatioRelation", "rollup"): "methods.metric",
    ("LogicalRatioRelation", "summarize"): "methods.metric",
    ("MaterializedRatioRelation", "rollup"): "methods.metric",
    ("LogicalCategoryRelation", "where"): "methods.rows",
    ("LogicalDifferenceRelation", "where"): "methods.rows",
    ("LogicalDifferenceRelation", "summarize"): "methods.compare",
    ("MaterializedSelectedDifferenceRelation", "members"): "methods.rows",
    ("MaterializedCoefficientRelation", "where"): "methods.association",
}

_INPUT_GUIDANCE = {
    "metric": "Use one exact Metric Ref from the current Semantic catalog.",
    "dimension": "Use a declared categorical Dimension Ref on this receiver's domain.",
    "during": "Use mv.time_scope(start=..., end=...) with absolute bounds.",
    "via": "Use the exact relationship Ref or mv.routes(...) required by this Metric.",
    "coordinates": "Up to two distinct string contribution Dimension Refs; omit when none are needed.",
    "baseline": "Use a distinct observation of the same members and Metric.",
    "other": "Use another Metric observation on the same members and time scope.",
    "method": "Use one closed mv.sum/count/mean() value or the stated method literal.",
    "predicate": "Build the predicate from this receiver's own value handle.",
    "max_output_bytes": "Keep the default bound or request a smaller positive byte limit.",
}


def _doc_section(value: object, heading: str) -> str:
    lines = (getdoc(value) or "").splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(heading + ":"):
            continue
        result = [line.partition(":")[2].strip()]
        for following in lines[index + 1 :]:
            if not following.strip() or following.endswith(":"):
                break
            result.append(following.strip())
        return " ".join(part for part in result if part)
    return ""


def _effects(name: str) -> str:
    if name == "execute":
        return "Execute one qualified graph in Store 7; publish atomically or reuse an exact fixed key."
    if name in ("show", "to_pandas"):
        return "Read the exact committed Artifact under bounded or isolated-read guards."
    if name in ("read", "group_by", "observe"):
        return "Bind a typed graph; live inputs may use schema-only R1 preflight, without business rows or Run."
    if name == "contract":
        return "Read bound definition and retained metadata without source I/O."
    return "Construct a typed continuation without business-source I/O."


def inputs() -> tuple[tuple[Descriptor, ...], tuple[ExportInput, ...]]:
    """Bind first-round public values and callables to one native Help owner."""
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    types = (
        dsl.AnalysisAction,
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
        acquisition = (
            "Read one action from relation.contract().actions."
            if value is dsl.AnalysisAction
            else "Call relation.contract()."
            if value is dsl.AnalysisContract
            else "Call mv.sum(), mv.count(), or mv.mean()."
            if value is dsl.RowMethod
            else "Call mv.route(root, through=(...))."
            if value is dsl.RootRoute
            else "Call mv.routes(first_route, second_route)."
            if value is dsl.RootRoutes
            else "Construct through session.members() or the returned typed relation."
        )
        producers = (
            ("dsl.Value.contract",)
            if value is dsl.AnalysisContract
            else ("AnalysisContract",)
            if value is dsl.AnalysisAction
            else ("dsl.sum", "dsl.count", "dsl.mean")
            if value is dsl.RowMethod
            else ("dsl.route",)
            if value is dsl.RootRoute
            else ("dsl.routes",)
            if value is dsl.RootRoutes
            else ("session.members",)
        )
        descriptors.append(
            value_type(
                name,
                value,
                summary=f"First-round governed Analysis {name} value.",
                acquisition=acquisition,
                producers=producers,
                consumers=("AnalysisAction",)
                if value is dsl.AnalysisContract
                else ("dsl.routes",)
                if value is dsl.RootRoute
                else ("dsl.LogicalAnalysisDomain.observe",)
                if value is dsl.RootRoutes
                else ("methods.metric",)
                if value is dsl.RowMethod
                else ("session.artifact",)
                if name.startswith("Materialized")
                else (),
                constraints=("Exact member, semantic and Artifact bindings govern continuations.",),
            )
        )
        exports.append(ExportInput(name, value, name))

    functions = (dsl.route, dsl.routes, dsl.sum, dsl.count, dsl.mean)
    for function in functions:
        name = function.__name__
        params = tuple(
            ParameterInput(key, _INPUT_GUIDANCE.get(key, "Use the exact governed input."))
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
                constraints=("Only qualified typed graph input shapes are admitted.",),
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
                if key != "self" and parameter.default is Parameter.empty
            )
            params = tuple(
                ParameterInput(
                    key,
                    _INPUT_GUIDANCE.get(key, "Use the exact bound relation or governed input."),
                    ("dsl.route", "dsl.routes")
                    if key == "via"
                    else ("dsl.sum", "dsl.count", "dsl.mean")
                    if key == "method" and name == "summarize"
                    else (),
                )
                for key in signature(value).parameters
                if key != "self"
            )
            descriptors.append(
                operation(
                    target,
                    "relation." + name,
                    value,
                    bindings=(bind(value, owner),),
                    summary=(getdoc(value) or f"{owner.__name__}.{name}.").splitlines()[0],
                    discovery_group=_METHOD_GROUPS.get((owner.__name__, name)),
                    parameters=params,
                    output=str(signature(value).return_annotation),
                    constraints=(
                        _doc_section(value, "Constraints")
                        or "The exact receiver and current contract gate this operation.",
                    ),
                    effects=_effects(name),
                    failures=(
                        "AnalysisError: inspect the structured repair for the current shape.",
                    ),
                    example=ExampleInput(
                        f"result = relation.{name}()"
                        if not params
                        else f"result = relation.{name}({', '.join(arguments)})",
                        (
                            "relation",
                            *(
                                parameter.name
                                for parameter in signature(value).parameters.values()
                                if parameter.name != "self" and parameter.default is Parameter.empty
                            ),
                        ),
                        "result",
                        "The receiver-bound result.",
                        True,
                    ),
                )
            )
    return tuple(descriptors), tuple(exports)
