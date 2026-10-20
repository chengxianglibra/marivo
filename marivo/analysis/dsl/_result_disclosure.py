"""Native finite result operations and axis-free correspondence disclosure."""

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
from marivo.analysis.dsl.column import Column, row_count
from marivo.analysis.dsl.correspondence import period_offset, previous_year
from marivo.analysis.dsl.frame import AnalysisFrame


def provider() -> DisclosureProvider:
    parameters: tuple[P, ...]
    requires: tuple[str, ...]
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    for target, value, parameters, output, summary, code, requires, group in (
        (
            "row_count",
            row_count,
            (),
            "_Statistic",
            "Count all current coordinates independently of value NA masks.",
            "result = row_count()",
            ("row_count",),
            "methods.metric.summary",
        ),
        (
            "period_offset",
            period_offset,
            (P("by", "Non-boolean integer offset in the consuming complete calendar authority."),),
            "CalendarCorrespondence",
            "Pair calendar periods by a builtin period offset.",
            "result = period_offset(-1)",
            ("period_offset",),
            "inputs.time",
        ),
        (
            "previous_year",
            previous_year,
            (),
            "CalendarCorrespondence",
            "Pair same-name builtin day/month/quarter/year periods; leap days have no counterpart.",
            "result = previous_year()",
            ("previous_year",),
            "inputs.time",
        ),
    ):
        descriptors.append(
            operation(
                target,
                "mv." + target,
                value,
                summary=summary,
                parameters=parameters,
                output=output,
                constraints=(
                    "Construction reads no rows. period_offset supports builtin day/week/month/quarter/year only; previous_year supports builtin day/month/quarter/year and leap days have no counterpart. Matching rejects partial buckets and incompatible frozen interpretations."
                    if target in ("period_offset", "previous_year")
                    else "row_count is input-independent and counts current rows, including value NA. Population.count() captures an explicit identity domain as a source Metric.",
                ),
                effects="Capture an immutable definition without executing it.",
                failures=(
                    "AnalysisError: follow the structured expected/received facts and repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
            )
        )
        exports.append(ExportInput(target, value, target))
    descriptors.append(
        value_type(
            "Column",
            Column,
            summary="Immutable current-data expression bound to one exact Frame input.",
            acquisition="Use frame[name] or frame.coord(axis).",
            producers=("analysis_frame.__getitem__", "analysis_frame.coord"),
            consumers=(
                "analysis_frame.select",
                "analysis_frame.where",
                "analysis_frame.top",
                "Column.sum",
                "Column.mean",
            ),
        )
    )
    exports.append(ExportInput("Column", Column, "Column"))
    for owner, name, parameters, output, summary, code, requires, group in (
        (
            AnalysisFrame,
            "__getitem__",
            (P("name", "Exact existing value name."),),
            "Column",
            "Locate a named value on this exact AnalysisFrame input.",
            "result = frame['value']",
            ("frame",),
            "methods.rows",
        ),
        (
            AnalysisFrame,
            "coord",
            (
                P(
                    "axis",
                    "An exact retained single-component scalar axis; preparation rejects composite coordinate Columns.",
                ),
            ),
            "Column",
            "Locate an existing scalar coordinate without reading a new attribute; complete composite keys remain coordinates and Population predicates.",
            "result = frame.coord(axis)",
            ("frame", "axis"),
            "methods.rows",
        ),
        (
            AnalysisFrame,
            "select",
            (
                P(
                    "values",
                    "Nonempty named mapping of same-input Columns or immutable non-null literals.",
                ),
            ),
            "AnalysisFrame",
            "Select named values, preserving complete coordinates and presence without hiding deleted values.",
            "result = frame.select({'value': frame['value']})",
            ("frame",),
            "methods.rows",
        ),
        (
            AnalysisFrame,
            "where",
            (
                P(
                    "predicate",
                    "Same-input boolean Column; only True is selected. PopulationPredicate belongs to source construction.",
                ),
            ),
            "AnalysisFrame",
            "Filter current coordinates while preserving checks and current row order.",
            "result = frame.where(frame['value'] > 0)",
            ("frame",),
            "methods.rows",
        ),
        (
            AnalysisFrame,
            "top",
            (
                P("n", "Nonnegative integer maximum for the whole current input, excluding bool."),
                P("by", "Same-input score Column; NA sorts last."),
                P("descending", "Boolean; largest first by default."),
            ),
            "AnalysisFrame",
            "Rank the whole current input, with NA last and stable input order for ties.",
            "result = frame.top(10, by=frame['value'])",
            ("frame",),
            "methods.rows",
        ),
        (
            AnalysisFrame,
            "reduce",
            (
                P(
                    "values",
                    "Named Column.sum/mean or row_count requests; each uses its independent NA mask.",
                ),
                P("by", "Existing retained grouping axes; empty produces one scalar coordinate."),
            ),
            "AnalysisFrame",
            "Compute current-value statistics with independent NA masks; remove presence and retain only the selected complete axes.",
            "result = frame.reduce({'mean': frame['value'].mean()})",
            ("frame",),
            "methods.metric.summary",
        ),
        (
            AnalysisFrame,
            "align",
            (
                P("other", "Another AnalysisFrame owned by this Session."),
                P(
                    "on",
                    "None only for zero axes or one calendar or interval axis per input; otherwise a complete tuple of exact FrameAxis pairs.",
                ),
                P(
                    "match",
                    "Explicit supported CalendarCorrespondence or None for frozen coordinate equality; never inferred.",
                ),
                P("how", "exact or outer; presence is independent of value NA."),
            ),
            "AnalysisFrame",
            "Align complete coordinates, retaining left.<name>/right.<name> values and independent non-nullable presence.",
            "result = frame.align(other)",
            ("frame", "other"),
            "methods.compare",
        ),
        (
            AnalysisFrame,
            "compare",
            (
                P(
                    "baseline",
                    "Same-Session AnalysisFrame with matching nonempty value names and units; numeric or matching-tick Duration endpoints.",
                ),
                P(
                    "on",
                    "None only for zero axes or one calendar or interval axis per input; explicit mapping pairs every complete axis exactly once.",
                ),
                P("match", "Explicit supported CalendarCorrespondence or None; never inferred."),
                P("how", "exact or outer."),
                P(
                    "zero_denominator",
                    "null or error for newly computed relative differences only.",
                ),
            ),
            "AnalysisFrame",
            "Emit left.<name>, right.<name>, absolute.<name> and relative.<name> in four ordered groups; relative divides the difference by the absolute baseline.",
            "result = frame.compare(baseline)",
            ("frame", "baseline"),
            "methods.compare",
        ),
        (
            Column,
            "show",
            (),
            "None",
            "Print captured input, role, name, unit and kind without source access.",
            "result = column.show()",
            ("column",),
            "methods.rows",
        ),
        (
            Column,
            "divide",
            (
                P("other", "Same-input Column or compatible numeric literal."),
                P(
                    "zero_denominator",
                    "null by default or error; controls this division node only.",
                ),
            ),
            "Column",
            "Divide pointwise; NA propagates and zero is masked before arithmetic.",
            "result = column.divide(2)",
            ("column",),
            "methods.rows",
        ),
        (
            Column,
            "is_null",
            (),
            "Column",
            "Test value NA independently of coordinate presence.",
            "result = column.is_null()",
            ("column",),
            "methods.rows",
        ),
        (
            Column,
            "sum",
            (),
            "_Statistic",
            "Request ignore-NA sum; empty/all-NA is NA. Complete with AnalysisFrame.reduce.",
            "result = column.sum()",
            ("column",),
            "methods.metric.summary",
        ),
        (
            Column,
            "mean",
            (),
            "_Statistic",
            "Request ignore-NA mean; empty/all-NA is NA. Complete with AnalysisFrame.reduce.",
            "result = column.mean()",
            ("column",),
            "methods.metric.summary",
        ),
    ):
        value = getattr(owner, name)
        descriptors.append(
            operation(
                f"{'analysis_frame' if owner is AnalysisFrame else 'column'}.{name}",
                f"{'frame' if owner is AnalysisFrame else 'column'}.{name}",
                value,
                bindings=(bind(value, owner),),
                summary=summary,
                parameters=parameters,
                output=output,
                constraints=(
                    "Validate parameter shape and Session ownership, reject paired inputs, then require equal axis counts before inferring or validating a complete on mapping. on/outer cannot repair ownership, paired axes, axis count or incompatible key/time types. exact checks actual complete row domains; outer keeps the ordered union. Calendar matching requires complete nominal/actual buckets. Interval ordinal requires one declared record per side with the same boundary timezone; equality uses exact start/end/timezone."
                    if name in ("align", "compare")
                    else "Columns and statistics retain one exact Frame input; Artifact cannot enter computation. Current-row statistics do not acquire original Metric authority.",
                ),
                effects="Capture a lazy result operation; no source or schema read.",
                failures=(
                    "AnalysisError: follow the concrete expected/received facts and repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
            )
        )
    for name, code in (
        ("__add__", "column + 1"),
        ("__radd__", "1 + column"),
        ("__sub__", "column - 1"),
        ("__rsub__", "1 - column"),
        ("__mul__", "column * 2"),
        ("__rmul__", "2 * column"),
        ("__truediv__", "column / 2"),
        ("__rtruediv__", "2 / column"),
        ("__eq__", "column == 1"),
        ("__ne__", "column != 1"),
        ("__lt__", "column < 1"),
        ("__le__", "column <= 1"),
        ("__gt__", "column > 1"),
        ("__ge__", "column >= 1"),
        ("__and__", "column & other"),
        ("__or__", "column | other"),
        ("__neg__", "-column"),
        ("__invert__", "~column"),
    ):
        function = getattr(Column, name)
        unary = name in ("__neg__", "__invert__")
        descriptors.append(
            operation(
                "Column." + name,
                "column." + name,
                function,
                bindings=(bind(function, Column),),
                summary="Construct a finite pointwise result expression on one exact AnalysisFrame input.",
                parameters=()
                if unary
                else (
                    P(
                        "other",
                        "Same-input Column or compatible immutable literal; boolean operators require Columns.",
                    ),
                ),
                output="Column",
                constraints=(
                    "Nullable values propagate; known units and literal kinds must agree. Division defaults to null at zero; divide(...,zero_denominator='error') is explicit.",
                ),
                effects="Capture a result expression without I/O.",
                failures=(
                    "AnalysisError: use the exact AnalysisFrame input and compatible types/units.",
                ),
                example=ExampleInput(
                    "result = " + code,
                    ("column", "other") if name in ("__and__", "__or__") else ("column",),
                    "result",
                    "Column",
                ),
                discovery_group="methods.rows.expressions",
            )
        )
    return DisclosureProvider("analysis.dsl.results", tuple(descriptors), tuple(exports))
