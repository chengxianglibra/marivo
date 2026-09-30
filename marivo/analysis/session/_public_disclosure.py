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
    ("_NumericComparison", "compare"): "methods.compare",
    ("_NumericComparison", "ratio"): "methods.compare",
    ("GroupedRatioRelation", "rollup"): "methods.metric",
    ("LogicalAnalysisDomain", "each"): "methods.metric",
    ("LogicalAnalysisDomain", "read"): "inputs.population",
    ("LogicalAnalysisDomain", "group_by"): "methods.metric",
    ("LogicalAnalysisDomain", "observe"): "methods.metric",
    ("LogicalNumericRelation", "group_by"): "methods.metric",
    ("LogicalNumericRelation", "rollup"): "methods.metric",
    ("LogicalNumericRelation", "summarize"): "methods.metric",
    ("LogicalNumericRelation", "correlate"): "methods.association",
    ("MaterializedNumericRelation", "summarize"): "methods.metric",
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
    "field": "Use an exact Measure, Dimension or TimeDimension Ref from the current catalog.",
    "at": "Select the attribute version independently with datetime or TimeScope.before_end.",
    "metric": "Use one exact Metric Ref from the current Semantic catalog.",
    "keys": "Retain complete axes or explicit classifications; unkeyed categories group their values, other receivers use Singleton.",
    "dimensions": "Retain complete Entity/Dimension axes or explicit corresponding categories.",
    "groups": "Use an explicit matching typed target domain to retain empty groups.",
    "dimension": "Use a declared categorical Dimension Ref on this receiver's domain.",
    "during": "Use mv.time_scope(start=..., end=...) with absolute bounds.",
    "via": "Use the exact relationship Ref or mv.routes(...) required by this Metric.",
    "coordinates": "Distinct qualified contribution Dimension Refs; complete tuples remain bound together.",
    "baseline": "Use recursively compatible numeric endpoints under the selected design.",
    "design": "Use mv.TimeChange(), mv.CohortContrast(), or mv.PeriodChange(alignment=mv.window_bucket()).",
    "pairing": "Use mv.ExactKeys() or an exact-node-bound mv.one_to_one(...) for ratio; comparison designs also accept mv.UnionKeys(missing=...).",
    "value": "Choose difference or relative_change; zero baselines remain Undefined.",
    "other": "Use another Metric observation on the same members and time scope.",
    "method": "Use one closed mv.sum/count/count_defined/min/max/mean() value or the stated method literal.",
    "predicate": "Build a predicate from this receiver or an exactly corresponding numeric relation.",
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
        dsl.OneToOneCorrespondence,
        dsl.PeriodChange,
        dsl.UnionKeys,
        dsl.ExactKeys,
        dsl.TimeChange,
        dsl.CohortContrast,
        dsl.AnalysisAction,
        dsl.AnalysisContract,
        dsl.GroupedAnalysisDomain,
        dsl.GroupedNumericRelation,
        dsl.GroupedStatisticRelation,
        dsl.GroupedRatioRelation,
        dsl.LogicalAnalysisDomain,
        dsl.LogicalTimeAnalysisDomain,
        dsl.MaterializedTimeAnalysisDomain,
        dsl.TimeGrid,
        dsl.GridWindow,
        dsl.GridEndpoint,
        dsl.LogicalAssociationResult,
        dsl.LogicalCategoryRelation,
        dsl.LogicalBooleanRelation,
        dsl.MaterializedBooleanRelation,
        dsl.LogicalTemporalRelation,
        dsl.MaterializedTemporalRelation,
        dsl.LogicalSelectedBooleanRelation,
        dsl.MaterializedSelectedBooleanRelation,
        dsl.LogicalSelectedTemporalRelation,
        dsl.MaterializedSelectedTemporalRelation,
        dsl.LogicalSelectedNumericRelation,
        dsl.MaterializedSelectedNumericRelation,
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
        dsl.CountMethod,
        dsl.RootRoute,
        dsl.RootRoutes,
    )
    for value in types:
        name = (
            value.__name__
            if value not in (dsl.RootRoute, dsl.RootRoutes)
            else ("RootRoute" if value is dsl.RootRoute else "RootRoutes")
        )
        policy_examples = {
            dsl.ExactKeys: "mv.ExactKeys()",
            dsl.UnionKeys: 'mv.UnionKeys(missing="keep")',
            dsl.TimeChange: "mv.TimeChange()",
            dsl.CohortContrast: "mv.CohortContrast()",
            dsl.PeriodChange: "mv.PeriodChange(alignment=mv.window_bucket())",
            dsl.OneToOneCorrespondence: "mv.one_to_one(left=left, right=right, via=relationship)",
        }
        acquisition = (
            f"Call {policy_examples[value]}."
            if value in policy_examples
            else "Call mv.time_grid(during=scope, grain=mv.grain('day'))."
            if value is dsl.TimeGrid
            else "Read grid.window."
            if value is dsl.GridWindow
            else "Read grid.start, grid.end or grid.before_end."
            if value is dsl.GridEndpoint
            else "Read one action from relation.contract().actions."
            if value is dsl.AnalysisAction
            else "Call relation.contract()."
            if value is dsl.AnalysisContract
            else "Call mv.count() or mv.count_defined()."
            if value is dsl.CountMethod
            else "Call mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean()."
            if value is dsl.RowMethod
            else "Call mv.route(root, through=(...))."
            if value is dsl.RootRoute
            else "Call mv.routes(first_route, second_route)."
            if value is dsl.RootRoutes
            else "Construct through session.members() or the returned typed relation."
        )
        producers = (
            ("dsl.time_grid",)
            if value is dsl.TimeGrid
            else ("TimeGrid",)
            if value in (dsl.GridWindow, dsl.GridEndpoint)
            else ("dsl.LogicalAnalysisDomain.each",)
            if value is dsl.LogicalTimeAnalysisDomain
            else ("dsl.Value.contract",)
            if value is dsl.AnalysisContract
            else ("AnalysisContract",)
            if value is dsl.AnalysisAction
            else ("dsl.count", "dsl.count_defined")
            if value is dsl.CountMethod
            else ("dsl.sum", "dsl.count", "dsl.count_defined", "dsl.min", "dsl.max", "dsl.mean")
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
                producers=("session.artifact",) if name.startswith("Materialized") else producers,
                consumers=("GridWindow", "GridEndpoint", "dsl.TimeGrid.show")
                if value is dsl.TimeGrid
                else (
                    "dsl.LogicalAnalysisDomain.read",
                    "dsl.LogicalAnalysisDomain.observe",
                    "dsl.GridEndpoint.show",
                )
                if value is dsl.GridEndpoint
                else ("dsl.LogicalAnalysisDomain.observe", "dsl.GridWindow.show")
                if value is dsl.GridWindow
                else ("AnalysisAction",)
                if value is dsl.AnalysisContract
                else ("dsl.routes",)
                if value is dsl.RootRoute
                else ("dsl.LogicalAnalysisDomain.observe",)
                if value is dsl.RootRoutes
                else ("methods.metric",)
                if value in (dsl.RowMethod, dsl.CountMethod)
                else (
                    "dsl." + name + ".show",
                    "dsl.NumericComparison.compare",
                    "dsl.NumericComparison.ratio",
                )
                if value in policy_examples
                else (),
                constraints=("Exact member, semantic and Artifact bindings govern continuations.",),
            )
        )
        exports.append(ExportInput(name, value, name))

    descriptors.append(
        operation(
            "dsl.time_grid",
            "mv.time_grid",
            dsl.time_grid,
            summary="Construct a finite time grid with exact receiver-bound handles.",
            discovery_group="methods.metric",
            parameters=(
                ParameterInput("during", "Use one finite half-open TimeScope."),
                ParameterInput("grain", "Use a builtin or certified calendar Grain."),
                ParameterInput(
                    "timezone",
                    "Optional boundary authority; a calendar must match its certification.",
                ),
            ),
            output="TimeGrid",
            constraints=("The Session binds report timezone once; conflicting reuse rejects.",),
            effects="Pure construction; no business rows or Run.",
            failures=("AnalysisError: inspect the exact binding repair.",),
            example=ExampleInput(
                "grid = mv.time_grid(during=mv.time_scope(start='2026-08-01', end='2026-09-01'), grain=mv.grain('day'))",
                (),
                "grid",
                "A bounded time-grid specification.",
                True,
            ),
        )
    )
    exports.append(ExportInput("time_grid", dsl.time_grid, "dsl.time_grid"))

    functions = (
        dsl.route,
        dsl.routes,
        dsl.sum,
        dsl.count,
        dsl.count_defined,
        dsl.min,
        dsl.max,
        dsl.mean,
    )
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
                output="CountMethod"
                if name in ("count", "count_defined")
                else "RootRoute"
                if name == "route"
                else "RootRoutes"
                if name == "routes"
                else "RowMethod",
                constraints=("Only qualified typed graph input shapes are admitted.",),
                effects="Pure argument construction; no source read or Run.",
                failures=("AnalysisError: use the structured expected and received repair.",),
                example=ExampleInput(
                    f"result = mv.{name}()"
                    if name in ("sum", "count", "count_defined", "min", "max", "mean")
                    else f"result = mv.{name}(root, through=(relationship,))"
                    if name == "route"
                    else "result = mv.routes(first_route, second_route)",
                    ()
                    if name in ("sum", "count", "count_defined", "min", "max", "mean")
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

    descriptors.append(
        operation(
            "dsl.one_to_one",
            "mv.one_to_one",
            dsl.one_to_one,
            summary="Bind an explicit one-to-one relationship to exact ordered numeric endpoints.",
            discovery_group="methods.compare",
            parameters=tuple(
                ParameterInput(
                    name,
                    _INPUT_GUIDANCE.get(name, "Use exact ordered numeric endpoint bindings."),
                    (),
                )
                for name in ("left", "right", "via", "time")
            ),
            output="OneToOneCorrespondence",
            constraints=(
                "Requires complete retained relationship identity keys; no many-to-one, Union or node reuse.",
            ),
            effects="Construct a bound correspondence without business reads or Run.",
            failures=("AnalysisError: follow the exact binding or retained-parts repair.",),
            example=ExampleInput(
                "result = mv.one_to_one(left=left, right=right, via=relationship)",
                ("left", "right", "relationship"),
                "result",
                "An exact ordered correspondence.",
                True,
            ),
        )
    )
    exports.append(ExportInput("one_to_one", dsl.one_to_one, "dsl.one_to_one"))

    owner_methods: tuple[type[object], ...] = (
        dsl._Value,
        dsl._CountRelation,
        dsl._NumericComparison,
        dsl._OriginalContinuation,
        dsl._StatisticContinuation,
        dsl._MaterializedValue,
        *(
            value
            for value in types
            if value not in (dsl.RowMethod, dsl.CountMethod, dsl.RootRoute, dsl.RootRoutes)
        ),
    )
    for owner in owner_methods:
        for name, value in vars(owner).items():
            if name.startswith("_") or not isfunction(value):
                continue
            target = "dsl." + owner.__name__.lstrip("_") + "." + name
            arguments = tuple(
                f"*{key}"
                if parameter.kind is Parameter.VAR_POSITIONAL
                else f"{key}={key}"
                if parameter.kind is Parameter.KEYWORD_ONLY
                else key
                for key, parameter in signature(value).parameters.items()
                if key != "self" and parameter.default is Parameter.empty
            )
            params = tuple(
                ParameterInput(
                    key,
                    _INPUT_GUIDANCE.get(key, "Use the exact bound relation or governed input."),
                    ("TimeChange", "CohortContrast", "PeriodChange")
                    if key == "design"
                    else ("ExactKeys", "UnionKeys", "OneToOneCorrespondence")
                    if key == "pairing"
                    else ("dsl.route", "dsl.routes")
                    if key == "via"
                    else (
                        "dsl.sum",
                        "dsl.count",
                        "dsl.count_defined",
                        "dsl.min",
                        "dsl.max",
                        "dsl.mean",
                    )
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
