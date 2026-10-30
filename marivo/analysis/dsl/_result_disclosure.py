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
from marivo.analysis.dsl.baseline import ComparisonBaseline, baseline
from marivo.analysis.dsl.column import Column, _Statistic, row_count
from marivo.analysis.dsl.correspondence import offset
from marivo.analysis.dsl.frame import AnalysisFrame


def provider() -> DisclosureProvider:
    parameters: tuple[P, ...]
    requires: tuple[str, ...]
    group: str | None
    constraints: tuple[str, ...]
    descriptors: list[Descriptor] = [
        operation(
            "baseline",
            "mv.baseline",
            baseline,
            summary="Capture one named baseline Frame and its correspondence rules.",
            parameters=(
                P(
                    "frame",
                    "Baseline Frame; compare binds same-Session ownership.",
                    ("session.observe", "session.persisted_frame"),
                ),
                P(
                    "on",
                    "Complete (current axis, baseline axis) tuples, or None for inference.",
                    ("analysis_frame.axis", "analysis_frame.axes"),
                ),
                P(
                    "match",
                    "Closed calendar rule, or None for frozen direct relation/equality inference.",
                    ("inputs.comparison",),
                ),
            ),
            output="ComparisonBaseline",
            constraints=(
                "Shape validation only. compare binds full axes, units and time compatibility. No alias is stored in the declaration; name it in compare's Mapping.",
            ),
            effects="Capture an immutable declaration without data reads or matching.",
            failures=("AnalysisError: pass a Frame, complete axis pairs and a closed rule.",),
            example=ExampleInput(
                "result = mv.baseline(frame)", ("frame",), "result", "ComparisonBaseline"
            ),
            discovery_group="inputs.comparison",
        ),
        value_type(
            "comparison_baseline",
            ComparisonBaseline,
            summary="Immutable named comparison input with read-only frame, on and match.",
            acquisition="Use mv.baseline(frame, on=..., match=...). Direct construction and extension are unsupported.",
            producers=("baseline",),
            consumers=("analysis_frame.compare",),
            constraints=(
                "Pass inside an alias-to-declaration Mapping. Bare declarations are not compare inputs. Metadata access does not read data.",
            ),
        ),
        operation(
            "comparison_baseline.show",
            "declaration.show",
            ComparisonBaseline.show,
            bindings=(bind(ComparisonBaseline.show, ComparisonBaseline),),
            summary="Print bounded captured Frame identity, axis pairs and calendar rule.",
            parameters=(),
            output="None",
            constraints=("Does not show Frame rows, execute inputs or bind a current Frame.",),
            effects="Print captured metadata only.",
            failures=(),
            example=ExampleInput("result = declaration.show()", ("declaration",), "result", "None"),
        ),
    ]
    exports: list[ExportInput] = [
        ExportInput("baseline", baseline, "baseline"),
        ExportInput("ComparisonBaseline", ComparisonBaseline, "comparison_baseline"),
    ]
    for target, value, parameters, output, summary, code, requires, group in (
        (
            "row_count",
            row_count,
            (),
            "_Statistic",
            "Count all current coordinates independently of value NA masks.",
            "result = row_count()",
            ("row_count",),
            "operators",
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
                    "Non-boolean integer steps of the consuming axis grain, including its step: day -7 is seven days; week -1 is one week, week -7 seven weeks; month -1 one month. Negative moves from current to earlier baseline. Not row positions or fixed days. Supply no years keyword.",
                ),
            ),
            "CalendarCorrespondence",
            "Construct an axis-free civil-year or grain-period offset.",
            "result = offset(years=-1)",
            ("offset",),
            "inputs.comparison",
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
                    "Supply exactly one of years or periods. Construction reads no rows. Builtin calendar grains currently require count=1; subday offset is unsupported. Missing civil dates are not clamped; week years use ISO week-year. Source shift supports unit week/month/year complete buckets or prefixes; fixed-data complete day/quarter matching remains supported."
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
            consumers=("analysis_frame.reduce", "analysis_frame.over"),
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
            producers=("analysis_frame.__getitem__", "analysis_frame.coord", "analysis_frame.over"),
            consumers=(
                "analysis_frame.select",
                "analysis_frame.where",
                "analysis_frame.top",
                "analysis_frame.correlate",
                "Column.corr",
                "Column.pair_count",
                "Column.sum",
                "Column.mean",
                "Column.share",
            ),
        )
    )
    exports.append(ExportInput("Column", Column, "Column"))
    descriptors.append(
        operation(
            "analysis_frame.attribute",
            "frame.attribute",
            AnalysisFrame.attribute,
            bindings=(bind(AnalysisFrame.attribute, AnalysisFrame),),
            summary="Explain retained source targets by new business classifications.",
            parameters=(
                P(
                    "by",
                    "Nonempty classifications or complete Population with joint keys/allocation.",
                    ("inputs.catalog", "inputs.population"),
                ),
                P(
                    "permutations",
                    "Complete permutations >=2; default1024. Int, not bool; Shapley only.",
                ),
                P(
                    "seed",
                    "Nonnegative int, not bool; default0. Fixed parent/player seed stream.",
                ),
            ),
            output="AnalysisFrame",
            constraints=(
                "Bind each target before reads: exact additive/rate-mix/linear, or paired whole-Metric group-replacement Shapley. No method override/n threshold/runtime fallback. Level keeps share rules. Retained Metric/compare/align only; define before observe. Column arithmetic has no source declaration. Division/rescale retain typed order/units.",
                "compute/persist retain declarations in process; independent reopen does not. Parent keys/old values stay fixed. Reads are independent. Game endpoints are cached; observed_total retains history.",
                "Qualified physical tables only. Integer/string/date32 keys; ClickHouse decoded Enum8/16 equality/filters and Distributed GLOBAL IN/JOIN across shards, retaining target View filters. Query Enum sorting uses decoded strings; Attribute excludes FixedString. Trust semantic identity/cardinality/version grain and nullable keys; no NULL/uniqueness scan. Check coverage, overlap, distinct partition and frozen time. Rate/mix needs nonnegative weights/group denominators, positive parent denominators and consistent components/original N/D. State games need domain proof for all coalitions; native-query games validate visited values. Failures never switch rules; any failed target rejects all.",
                "contribution/total/observed_total/contribution_share retain diagnostics/presence. Rate/mix adds effect.within/mix/entry_exit; mixed linear adds effect.additive. Shapley contribution_std_error: state permutation SE excludes summary bias; query observed marginal SE cannot isolate input changes. Relative outputs use abs(fresh baseline); zero baseline errors identify target/parent/value; zero totals give NA shares. Filters retain domain/facts; Float64 facts preserve values without arithmetic recomputation.",
                "Float SUM/AVG use summary reconstruction without a bias bound. State capture reads do not grow with permutations. Frequencies transfer O(N); coalitions <=min(2**n,2+permutations*(n-1)). Native queries grow with coalitions; no snapshot, materialization or drift audit. No row sampling/top-k/adaptive count; unchanged additive/level Float64 keeps max(1% of abs(total), numeric rounding budget).",
            ),
            effects="Lazy capture. compute evaluates fixed keys; persist publishes results without declarations.",
            failures=(
                "AnalysisError: follow repairs; rebuild missing declarations or investigate with population_from/in_population and observe.",
            ),
            example=ExampleInput(
                "result = source_frame.attribute(by=(region,))",
                ("source_frame", "region"),
                "result",
                "AnalysisFrame",
            ),
            discovery_group="operators",
            related=(
                "analysis_frame.compute",
                "analysis_frame.method_facts",
                "session.population_from",
            ),
        )
    )
    descriptors.append(
        operation(
            "analysis_frame.fix",
            "frame.fix",
            AnalysisFrame.fix,
            bindings=(bind(AnalysisFrame.fix, AnalysisFrame),),
            summary="Fix one complete ordinary coordinate and remove its axis without aggregating values.",
            parameters=(
                P(
                    "axis",
                    "A field or identity FrameAxis from this exact input; time and paired axes reject.",
                    ("analysis_frame.axis", "analysis_frame.axes"),
                ),
                P(
                    "key",
                    "One typed scalar for a single component, or a complete tuple in axis.key_names order. None matches a nullable NULL coordinate.",
                ),
            ),
            output="AnalysisFrame",
            constraints=(
                "Return a new lazy Frame. Preserve selected values, physical types, units, NA, presence, remaining coordinates and relative row order. An absent member produces an empty typed result; removing the final axis produces zero or one row, including with no value columns.",
                "Reacquire handles from the returned Frame. Retained source declarations preserve fixed parent keys for attribute; cold recovery does not restore declarations. Remaining axes use compare's default matching or a complete explicit on.",
            ),
            effects="Capture only; no schema or business read. Evaluation consumes the complete input and retains its required checks.",
            failures=(
                "AnalysisError: supply a complete key matching the current axis types and nullability; acquire the axis from this exact Frame.",
            ),
            example=ExampleInput(
                "result = frame.fix(frame.axes[0], 'east')", ("frame",), "result", "AnalysisFrame"
            ),
            discovery_group="operators",
            related=("analysis_frame.where", "analysis_frame.compare", "analysis_frame.reduce"),
        )
    )
    parameter_targets: dict[str, tuple[str, ...]] = {
        "name": ("analysis_frame.output_names",),
        "axis": ("analysis_frame.axis", "analysis_frame.axes"),
        "along": ("analysis_frame.axis", "analysis_frame.axes"),
        "by": ("analysis_frame.axis", "analysis_frame.axes"),
        "on": ("analysis_frame.axis", "analysis_frame.axes"),
        "x": ("analysis_frame.__getitem__",),
        "y": ("analysis_frame.__getitem__",),
        "predicate": ("Column",),
        "other": ("session.observe", "session.persisted_frame"),
        "baselines": ("baseline", "session.observe", "session.persisted_frame"),
        "match": ("inputs.comparison",),
        "statistic": ("value_statistic",),
    }
    for owner, name, parameters, output, summary, code, requires, group in (
        (
            AnalysisFrame,
            "__getitem__",
            (P("name", "Exact existing value name."),),
            "Column",
            "Locate a named value on this exact AnalysisFrame input.",
            "result = frame['value']",
            ("frame",),
            "inputs.frame",
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
            "inputs.frame",
        ),
        (
            AnalysisFrame,
            "select",
            (
                P(
                    "values",
                    "Named mapping of same-input Columns or immutable non-null literals; {} removes all values.",
                ),
            ),
            "AnalysisFrame",
            "Project values while preserving rows, order, complete coordinates and presence.",
            "result = frame.select({'value': frame['value']})",
            ("frame",),
            "operators",
        ),
        (
            AnalysisFrame,
            "where",
            (
                P(
                    "predicate",
                    "Same-input boolean Column; only True is selected. Semantic row predicates belong to source consumers.",
                ),
            ),
            "AnalysisFrame",
            "Filter current coordinates, preserving checks and row order.",
            "result = frame.where(frame['value'] > 0)",
            ("frame",),
            "operators",
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
            "Rank the whole current input, with NA last in either direction and canonical complete-key order for ties.",
            "result = frame.top(10, by=frame['value'])",
            ("frame",),
            "operators",
        ),
        (
            AnalysisFrame,
            "reduce",
            (
                P(
                    "values",
                    "Statistic, nonempty tuple or nonempty named Mapping. Direct single-segment sum/mean use <name>_sum/<name>_mean; row_count uses row_count. Expressions, multi-segment paths and corr/pair_count need Mapping.",
                ),
                P("by", "Retained complete axes; () gives one scalar coordinate."),
            ),
            "AnalysisFrame",
            "Reduce current-value rows; keep by axes and drop presence.",
            "selected = frame.where(frame['value'] > 0)\n"
            "result = selected.reduce(selected['value'].sum()).compute()",
            ("frame",),
            "operators",
        ),
        (
            AnalysisFrame,
            "over",
            (
                P(
                    "statistic",
                    "One same-input sum/mean/corr/pair_count, or reusable unbound row_count request.",
                ),
                P(
                    "by",
                    "Tuple of existing complete same-input partition axes; () uses the whole input.",
                ),
            ),
            "Column",
            "Reduce each current-data partition and broadcast its statistic to the original rows as a same-input Column.",
            "statistic = frame['value'].sum()\n"
            "result = frame.over(statistic)\n"
            "selected = frame.select({'value': frame['value'], 'total': result}).compute()\n"
            "selected.show()",
            ("frame",),
            "operators",
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
            "operators",
        ),
        (
            AnalysisFrame,
            "align",
            (
                P("other", "Another AnalysisFrame owned by this Session."),
                P(
                    "on",
                    "Default: unique same-name ordinary axes with equal roles and ordered key names, plus at most one same-kind time axis per side. Otherwise pass complete pairs declaring caller business correspondence, without checking original Entity/Field equality.",
                ),
                P(
                    "match",
                    "Explicit supported CalendarCorrespondence or None for frozen coordinate equality; never inferred.",
                ),
            ),
            "AnalysisFrame",
            "Align complete coordinates, retaining left.<name>/right.<name> values and independent non-nullable presence.",
            "result = frame.align(other)",
            ("frame", "other"),
            "operators",
        ),
        (
            AnalysisFrame,
            "compare",
            (
                P(
                    "baselines",
                    "Same-Session Frame (alias reference), or nonempty ordered alias-to-ComparisonBaseline Mapping via mv.baseline(...). Require equal nonempty value paths/units: numeric or matching-tick Duration. Names certify no Metric identity/business comparability; current is reserved.",
                ),
                P(
                    "on",
                    "Bare Frame pairs; named inputs put on in mv.baseline(...). Default: unique same-name ordinary axes with equal roles/ordered key names and at most one same-kind time axis per side. Caller owns business correspondence; Entity/Field identity is not checked. Compare supports at most one time pair.",
                ),
                P(
                    "match",
                    "Bare Frame only: CalendarCorrespondence. Named inputs put match in mv.baseline(...). Omission uses compatible frozen direct relations or equality. Do not mix shapes.",
                ),
            ),
            "AnalysisFrame",
            "Keep current axes/values; add <alias>.baseline.<name>, <alias>.absolute.<name>, <alias>.relative.<name>. Relative = difference / absolute baseline; zero gives NA.",
            "result = frame.compare(previous)",
            ("frame", "previous"),
            "operators",
        ),
        (
            Column,
            "show",
            (),
            "None",
            "Print captured input, role, name, unit and kind without source access.",
            "result = column.show()",
            ("column",),
            None,
        ),
        (
            Column,
            "share",
            (
                P(
                    "along",
                    "Nonempty tuple of distinct complete axes from this exact input; the remaining axes define partitions.",
                ),
            ),
            "Column",
            "Normalize current values by their partition sum; return signed ratios with the same rows and input.",
            "result = frame['value'].share(along=frame.axes)",
            ("frame",),
            "operators",
        ),
        (
            Column,
            "is_null",
            (),
            "Column",
            "Test value NA independently of coordinate presence.",
            "result = column.is_null()",
            ("column",),
            "inputs.frame",
        ),
        (
            Column,
            "sum",
            (),
            "_Statistic",
            "Request ignore-NA current-row sum: local int64 stays int64 and Decimal uses its native accumulator; source fusion follows the actual backend. Native overflow/nonfinite behavior is retained without prechecks or widening. Count and Duration retain their types. Empty input is zero and nonempty all-NA is NA. Retain the expression's unit without certifying original Metric additivity. Complete with AnalysisFrame.reduce or AnalysisFrame.over.",
            "result = column.sum()",
            ("column",),
            "inputs.frame",
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
            "Capture one coefficient statistic; finish with AnalysisFrame.reduce or AnalysisFrame.over. Use AnalysisFrame.correlate for the matching coefficient and pair count.",
            "result = frame.reduce({'coefficient': frame['value'].corr(frame['other'], method='spearman'), 'n_pairs': frame['value'].pair_count(frame['other'])}).compute()",
            ("frame",),
            "inputs.frame",
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
            "inputs.frame",
        ),
        (
            Column,
            "mean",
            (),
            "_Statistic",
            "Request ignore-NA current-row mean; empty/all-NA is NA. Retain the expression's unit without certifying an original overall Metric. Complete with AnalysisFrame.reduce or AnalysisFrame.over.",
            "result = column.mean()",
            ("column",),
            "inputs.frame",
        ),
    ):
        parameters = tuple(
            P(
                parameter.name,
                parameter.acquisition,
                ("value_statistic", "column.sum", "column.mean", "row_count")
                if name == "reduce" and parameter.name == "values"
                else ("analysis_frame.__getitem__", "Column")
                if name == "select" and parameter.name == "values"
                else ("analysis_frame.__getitem__",)
                if (name == "top" and parameter.name == "by")
                or (owner is Column and parameter.name == "other")
                else parameter_targets.get(parameter.name, ()),
            )
            for parameter in parameters
        )
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
                    "Validate parameter shape and Session ownership, reject paired inputs, then require equal axis counts before inferring or validating a complete on mapping. on/outer cannot repair ownership, paired axes, axis count or incompatible key/time types. Outer correspondence keeps the ordered union; presence is independent of value NA. Calendar ordinal requires complete nominal/actual buckets; offset also supports saved week/month/year bucket-start prefixes with exactly equal progress. Interval ordinal requires one declared record per side with the same boundary timezone; equality uses exact start/end/timezone."
                    if name == "align"
                    else "reference.present. Alias omission infers no time relationship; collisions need alias/select, never numbering. on/outer cannot repair ownership or incompatible axes. Outer order: current, then mapped baseline-only keys by alias. Baseline times must map into current domain; use align for unions. Direct shift may supply match; wrong anchors/conflicts reject. Calendar ordinal needs complete buckets; week/month/year bucket-start offset prefixes need exactly equal saved progress. Nonnullable current.present/<alias>.present differ from NA and inherited *.presence.*. Duration keeps ticks; relative is dimensionless (unit=None). Missing and NA propagate."
                    if name == "compare"
                    else "Numeric int64/float64/Decimal only, not Duration even when resolved during preparation. Use an explicitly defined numeric source expression or external computation for Duration. Both endpoints must share the exact Frame/Session input; align independent Frames first. Different units are allowed without conversion. Each group uses its own common non-NA pair mask after checking both expressions; zero and duplicate values remain. Zero-pair groups and Null categories remain; by=() on empty input returns one row. coefficient is nullable float64 with unit '1'; n_pairs is nonnullable int64 with no unit. Fewer than two pairs or a constant endpoint gives coefficient NA and retains n_pairs; empty grouped input has no rows. Spearman ranks paired values within each group with average ties. Presence is removed, never an implicit sample filter. Use analysis.analysis_frame.reduce with analysis.column.corr and analysis.column.pair_count for named compositions; output names do not infer pairing. Group coefficients/counts cannot reconstruct pooled correlation."
                    if name == "correlate"
                    else "Only same-input numeric Columns (int64/float64/Decimal), including self-pairs; Duration rejects during capture or physical preparation. Use a defined numeric source expression or external computation for Duration. Different units are allowed without conversion. Check both expressions, then use a common non-NA mask per group; zero and repeated values count. Self pair_count counts non-NA values. Retain zero-pair groups and counts for constant inputs; coefficient is NA for n<2 or a constant endpoint. corr returns only nullable float64/unit '1'; pair_count returns only nonnullable int64/no unit. No direct execution, arithmetic, automatic count expansion or inferred pair relationship. Use analysis.analysis_frame.reduce or analysis.analysis_frame.over to finish; analysis.analysis_frame.correlate supplies the matched two-column result."
                    if name in ("corr", "pair_count")
                    else "Example input: frame has a summable numeric value column named 'value'. Use reduce's statistic types, independent NA masks, units and numeric policies. Preserve exact input identity, complete keys, row count and row order; empty input stays empty. row_count counts every coordinate and binds without mutating its reusable request. Existing ordinary, interval, calendar and paired axes use frozen keys without rematching. Nested over evaluates dependencies in order; repeated broadcast totals remain repeated samples. In where/top, partitions are evaluated before selection. No source reread, Metric decomposition or new method facts."
                    if name == "over"
                    else "Equivalent to this Column divided by over(this Column.sum(), by=the complementary axes), with zero totals masked before division. Axis order normalizes to input order; empty along and zero-axis inputs reject. Numeric and legal Duration sum/divide types follow their existing kernels; boolean/string/date reject. NA propagates; zero totals give NA, including cancelling signed values. Ratios are not percentages and may lie outside [0,1]. Unit is '1' when known equal units cancel, otherwise None for unknown units. Filtering before share changes the denominator; filtering afterward preserves it. Current-value normalization does not certify source Metric additivity or contribution; it creates no method facts."
                    if name == "share"
                    else "Example needs numeric frame['value']. Names use bound value slots/schema, never variables/dependencies. Reject duplicate/invalid names at capture; use distinct legal Mapping keys. Names/order stay fixed through compute/persist. Use exact-input statistics after derivation. Independent NA masks. Sum/mean retain expression units, not original Metric additivity or rollup."
                    if name == "reduce"
                    else "Use a same-input score. Proven complete input keys omit duplicate scans, including n=0; unknown keys are checked before cropping. Required NULL, numeric and domain checks remain. Use explicit source_check to audit source declarations."
                    if name == "top"
                    else "Columns and statistics retain one exact Frame input; Artifact cannot enter computation. Current-row statistics do not acquire original Metric authority.",
                ),
                effects=(
                    "Lazy; no source/schema reads."
                    if name in ("reduce", "compare")
                    else "Capture a lazy result operation; no source or schema read."
                ),
                failures=(
                    "AnalysisError: follow its repair."
                    if name == "reduce"
                    else "AnalysisError: follow its repair."
                    if name == "compare"
                    else "AnalysisError: follow the concrete expected/received facts and repair.",
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
        boolean = name in ("__and__", "__or__", "__invert__")
        comparison = name in ("__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__")
        if boolean:
            constraints = (
                {
                    "__and__": "Three-valued boolean logic: False & NULL is False; True & NULL is NULL.",
                    "__or__": "Three-valued boolean logic: True | NULL is True; False | NULL is NULL.",
                    "__invert__": "Boolean negation preserves NULL: ~NULL is NULL.",
                }[name],
            )
        elif comparison:
            constraints = (
                "Compatible value kinds and matching Column units are required. Return a unitless boolean Column; NULL comparisons give NULL. Use is_null() to test missing values.",
            )
        else:
            constraints = (
                "Numeric and legal Duration operands use their native kernels, including rounding, errors, wrapping and nonfinite values. NULL propagates; no extra overflow/finite scans, widening or retry.",
                "Numeric division is float64; Duration / numeric retains the original Duration tick unit with half-even rounding, and Duration / Duration is float64. Numeric / Duration rejects. Known units divide or cancel; zero denominators give NA before arithmetic."
                if name in ("__truediv__", "__rtruediv__")
                else "Known Column units multiply; a numeric literal preserves the Column unit. Duration scaling retains its tick unit with half-even rounding."
                if name in ("__mul__", "__rmul__")
                else "Preserve the operand's kind and unit."
                if unary
                else "Column units must match; compatible literals retain the Column unit. Duration addition/subtraction requires compatible tick units.",
            )
        descriptors.append(
            operation(
                "Column." + name,
                "column." + name,
                function,
                bindings=(bind(function, Column),),
                summary=(function.__doc__ or "").splitlines()[0].strip(),
                parameters=()
                if unary
                else (
                    P(
                        "other",
                        "Boolean Column from this exact input."
                        if boolean
                        else "Column from this exact input or a compatible non-null immutable literal.",
                    ),
                ),
                output="Column",
                constraints=constraints,
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
            )
        )
    return DisclosureProvider("analysis.dsl.results", tuple(descriptors), tuple(exports))
