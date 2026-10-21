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
from marivo.analysis.dsl.column import Column, _Statistic, row_count
from marivo.analysis.dsl.correspondence import offset
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
            "offset",
            offset,
            (
                P(
                    "years",
                    "Call offset(years=n): non-boolean integer; ISO week-year for weeks, civil year otherwise. Supply no periods keyword.",
                ),
                P(
                    "periods",
                    "Call offset(periods=n): non-boolean integer nominal grain-period offset. Supply no years keyword.",
                ),
            ),
            "CalendarCorrespondence",
            "Construct an axis-free civil-year or grain-period offset.",
            "result = offset(years=-1)",
            ("offset",),
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
                    "Supply exactly one of years or periods. Construction reads no rows. Missing civil dates are not clamped; week years use ISO week-year. Source shift supports unit week/month/year complete buckets or prefixes; fixed-data complete day/quarter matching remains supported."
                    if target == "offset"
                    else "row_count is input-independent and counts current rows, including value NA. Population.count() captures an explicit identity domain as a source Metric.",
                ),
                effects="Capture an immutable definition without executing it.",
                failures=(
                    "AnalysisError: follow the structured expected/received facts and repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                related=("value_statistic",) if output == "_Statistic" else (),
                discovery_group=group,
            )
        )
        exports.append(ExportInput(target, value, target))
    descriptors.append(
        value_type(
            "value_statistic",
            _Statistic,
            summary="An immutable unfinished current-row statistic with read-only unit.",
            acquisition="Use mv.row_count() or Column.sum/mean/corr/pair_count; direct construction is unsupported.",
            producers=(
                "row_count",
                "column.sum",
                "column.mean",
                "column.corr",
                "column.pair_count",
            ),
            consumers=("analysis_frame.reduce",),
            constraints=(
                "row_count() is unbound and reusable across Frames without mutation. Column statistics retain their exact Frame/Session input and cannot be rebound, even to equal data.",
            ),
        )
    )
    descriptors.append(
        value_type(
            "Column",
            Column,
            summary="Immutable current-data expression bound to one exact Frame input.",
            acquisition="Use frame[name] or frame.coord(axis). Direct construction and extension are unsupported.",
            producers=("analysis_frame.__getitem__", "analysis_frame.coord"),
            consumers=(
                "analysis_frame.select",
                "analysis_frame.where",
                "analysis_frame.top",
                "analysis_frame.correlate",
                "Column.corr",
                "Column.pair_count",
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
                    "Same-input boolean Column; only True is selected. Native Deferred predicates belong to source consumers.",
                ),
            ),
            "AnalysisFrame",
            "Filter current coordinates, preserving checks and row order.",
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
                    "Named Column sum/mean/corr/pair_count or row_count requests; each outputs one column using its independent NA mask. Names never infer pair relationships; use correlate for a matching coefficient/count.",
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
            "correlate",
            (
                P("x", "Numeric Column from this exact Frame/Session input."),
                P("y", "Numeric Column from the same exact input; units may differ."),
                P("method", "Required pearson or spearman; no default or inference."),
                P("by", "Tuple of existing complete same-input axes; () produces one scalar row."),
            ),
            "AnalysisFrame",
            "Correlate one numeric pair on current rows, returning coefficient and n_pairs together from one input evaluation.",
            "result = frame.correlate(frame['value'], frame['other'], method='pearson').compute()",
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
                    "None only for zero axes or one calendar or interval axis per input; otherwise complete FrameAxis pairs declaring caller business correspondence, without checking original Entity/Field equality.",
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
                    "baselines",
                    "Nonempty ordered named same-Session Frame mapping with matching nonempty value paths and units: compatible numeric or matching-tick exact Duration endpoints. Equal names select pairs without proving Metric identity or business comparability. current is reserved.",
                ),
                P(
                    "on",
                    "Per-alias complete axis bijections declaring caller business correspondence, without checking original Entity/Field equality; omitted aliases infer only zero axes or one time axis.",
                ),
                P(
                    "match",
                    "Per-alias explicit rules; omitted rules use compatible immediate frozen direct relations or equality.",
                ),
                P("how", "exact or outer."),
                P(
                    "zero_denominator",
                    "null or error for newly computed relative differences only; divide by the absolute baseline.",
                ),
            ),
            "AnalysisFrame",
            "Retain current values and add <alias>.baseline.<name>, <alias>.absolute.<name> and <alias>.relative.<name>; relative divides the difference by the absolute baseline.",
            "result = frame.compare({'reference': baseline})",
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
            "Request ignore-NA current-row sum: int64 returns Decimal(38,0), Decimal returns Decimal(38,input_scale); count and Duration retain their types. Empty input is zero and nonempty all-NA is NA. Retain the expression's unit without certifying original Metric additivity. Complete with AnalysisFrame.reduce.",
            "result = column.sum()",
            ("column",),
            "methods.metric.summary",
        ),
        (
            Column,
            "corr",
            (
                P("other", "Numeric Column from this exact Frame/Session input; units may differ."),
                P(
                    "method",
                    "Required pearson or spearman; Spearman uses group-local paired average ranks.",
                ),
            ),
            "_Statistic",
            "Capture one coefficient statistic; finish with AnalysisFrame.reduce. Use AnalysisFrame.correlate for the matching coefficient and pair count.",
            "result = frame.reduce({'coefficient': frame['value'].corr(frame['other'], method='spearman'), 'n_pairs': frame['value'].pair_count(frame['other'])}).compute()",
            ("frame",),
            "methods.metric.summary",
        ),
        (
            Column,
            "pair_count",
            (
                P(
                    "other",
                    "Numeric Column from this exact Frame/Session input; self-pairing counts non-NA values.",
                ),
            ),
            "_Statistic",
            "Capture an independent valid-pair count without ranking or computing a coefficient. AnalysisFrame.correlate already includes n_pairs.",
            "result = frame.reduce({'n_pairs': frame['value'].pair_count(frame['other']), 'n_values': frame['value'].pair_count(frame['value'])}).compute()",
            ("frame",),
            "methods.metric.summary",
        ),
        (
            Column,
            "mean",
            (),
            "_Statistic",
            "Request ignore-NA current-row mean; empty/all-NA is NA. Retain the expression's unit without certifying an original overall Metric. Complete with AnalysisFrame.reduce.",
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
                    "Validate parameter shape and Session ownership, reject paired inputs, then require equal axis counts before inferring or validating a complete on mapping. on/outer cannot repair ownership, paired axes, axis count or incompatible key/time types. exact checks actual complete row domains; outer keeps the ordered union. Calendar ordinal requires complete nominal/actual buckets; offset also supports saved week/month/year bucket-start prefixes with exactly equal progress. Interval ordinal requires one declared record per side with the same boundary timezone; equality uses exact start/end/timezone."
                    if name == "align"
                    else "Validate each named same-Session endpoint and complete on binding; on/outer cannot repair ownership, paired axes, axis count or incompatible key/time types. Current axes and value paths stay unchanged. exact checks every actual complete joint key; outer preserves current order then mapped baseline-only keys in alias order. Reject baseline time rows outside the declared current time domain; use align for a general time union. Compatible direct shift relations may infer match; mismatched anchors or conflicting explicit rules reject. Prefix offset matching supports week/month/year with exact saved progress; ordinal requires complete buckets. current.present and <alias>.present are independent nonnullable row flags, distinct from value NA and inherited current.presence.* / <alias>.presence.*. Duration baseline/absolute preserve the original tick unit; relative is dimensionless. No missing value becomes zero; strict child errors remain required."
                    if name == "compare"
                    else "Numeric int64/finite float64/Decimal only, not Duration even when resolved during preparation. Use an explicitly defined numeric source expression or external computation for Duration. Both endpoints must share the exact Frame/Session input; align independent Frames first. Different units are allowed without conversion. Each group uses its own common non-NA pair mask after checking both expressions; zero and duplicate values remain. Zero-pair groups and Null categories remain; by=() on empty input returns one row. coefficient is nullable float64 with unit '1'; n_pairs is nonnullable int64 with no unit. Fewer than two pairs or a constant endpoint gives coefficient NA and retains n_pairs; empty grouped input has no rows. Spearman ranks paired values within each group with average ties. Presence is removed, never an implicit sample filter. Use analysis.analysis_frame.reduce with analysis.column.corr and analysis.column.pair_count for named compositions; output names do not infer pairing. Group coefficients/counts cannot reconstruct pooled correlation."
                    if name == "correlate"
                    else "Only same-input numeric Columns (int64/finite float64/Decimal), including self-pairs; Duration rejects during capture or physical preparation. Use a defined numeric source expression or external computation for Duration. Different units are allowed without conversion. Check both expressions, then use a common non-NA mask per group; zero and repeated values count. Self pair_count counts non-NA values. Retain zero-pair groups and counts for constant inputs; coefficient is NA for n<2 or a constant endpoint. corr returns only nullable float64/unit '1'; pair_count returns only nonnullable int64/no unit. No direct execution, arithmetic, automatic count expansion or inferred pair relationship. Use analysis.analysis_frame.reduce to finish; analysis.analysis_frame.correlate supplies the matched two-column result."
                    if name in ("corr", "pair_count")
                    else "Columns and statistics retain one exact Frame input; Artifact cannot enter computation. Sum/mean retain the current expression's unit; this does not certify original Metric additivity or permit rollup over removed axes. Summed inventory or ratios remain current-row statistics, not monthly inventory or an overall ratio."
                    if name == "reduce"
                    else "Columns and statistics retain one exact Frame input; Artifact cannot enter computation. Current-row statistics do not acquire original Metric authority.",
                ),
                effects="Capture a lazy result operation; no source or schema read.",
                failures=(
                    "AnalysisError: follow the concrete expected/received facts and repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                related=("value_statistic",) if output == "_Statistic" else (),
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
                summary="Construct a pointwise result expression on one exact Frame input.",
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
                    "Decimal add/subtract/multiply bind capped p/s with ties-away convergence; division is float64. Local integers are checked; source overflow follows its backend, including ClickHouse wrapping. Float64 may lose low bits beyond 2**53; identity/count/time/Duration stay exact.",
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
