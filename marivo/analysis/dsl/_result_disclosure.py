"""Native finite result operations and axis-free correspondence disclosure."""

from marivo._definition.correspondence import CorrespondenceAxis, DeclaredCorrespondence
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
from marivo.analysis.dsl.correspondence import corresponding, period_offset, previous_year
from marivo.analysis.dsl.metrics import member_count
from marivo.analysis.dsl.query import Query
from marivo.analysis.dsl.references import broadcast


def provider() -> DisclosureProvider:
    parameters: tuple[P, ...]
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    for target, value, parameters, output, summary, code, requires, group in (
        (
            "broadcast",
            broadcast,
            (
                P("value", "A Column on a source Query."),
                P(
                    "to",
                    "Same-Session target Query with a unique complete projection to the reference axes.",
                ),
            ),
            "Column",
            "Transport a reference by complete keys and require full target coverage.",
            "result = broadcast(reference['value'], to=query)",
            ("broadcast", "reference", "query"),
            "methods.metric.reference",
        ),
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
            "member_count",
            member_count,
            (),
            "Deferred",
            "Count the explicit observe(members=...) identity domain, including declared absent identities.",
            "result = member_count()",
            ("member_count",),
            "methods.metric",
        ),
        (
            "corresponding",
            corresponding,
            (P("via", "Nonempty directed tuple of current declared one-to-one Relationships."),),
            "DeclaredCorrespondence",
            "Bind an explicit identity correspondence when consumed by Query.align/compare.",
            "result = corresponding(via=(relationship,))",
            ("corresponding", "relationship"),
            "methods.compare",
        ),
        (
            "period_offset",
            period_offset,
            (P("by", "Non-boolean integer offset in the consuming complete calendar authority."),),
            "CalendarCorrespondence",
            "Pair calendar periods by a certified or builtin period offset.",
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
                    "Construction reads no business rows; the consuming Query binds and checks complete coordinates.",
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
    for cls, producers, consumers in (
        (
            Column,
            ("Query.__getitem__", "Query.coord"),
            ("Query.select", "Query.where", "Query.top", "Column.sum", "Column.mean"),
        ),
        (DeclaredCorrespondence, ("corresponding",), ("Query.align", "Query.compare")),
        (
            CorrespondenceAxis,
            ("Query.align", "Query.compare"),
            ("Query.coord", "Population.project"),
        ),
    ):
        descriptors.append(
            value_type(
                cls.__name__,
                cls,
                summary=f"Immutable {cls.__name__} bound value.",
                acquisition="Use its registered producer; direct construction is not the workflow.",
                producers=producers,
                consumers=consumers,
            )
        )
        exports.append(ExportInput(cls.__name__, cls, cls.__name__))
    for correspondence_type, summary in (
        (
            DeclaredCorrespondence,
            "Return bounded declared path identities without resolving sources.",
        ),
        (CorrespondenceAxis, "Return bounded projection paths over retained endpoint bindings."),
    ):
        descriptors.append(
            operation(
                f"{correspondence_type.__name__}.show",
                "correspondence.show",
                correspondence_type.show,
                bindings=(bind(correspondence_type.show, correspondence_type),),
                summary=summary,
                parameters=(),
                output="str",
                constraints=("Rendering reads only the immutable correspondence value.",),
                effects="Pure value rendering without I/O.",
                failures=(),
                example=ExampleInput(
                    "result = correspondence.show()", ("correspondence",), "result", "str"
                ),
            )
        )
    for owner, name, parameters, output, summary, code, requires, group in (
        (
            Query,
            "__getitem__",
            (P("name", "Exact existing value name."),),
            "Column",
            "Locate a named value on this exact Query input.",
            "result = query['value']",
            ("query",),
            "methods.rows",
        ),
        (
            Query,
            "coord",
            (
                P(
                    "axis",
                    "An exact retained single-component scalar axis; preparation rejects composite coordinate Columns.",
                ),
            ),
            "Column",
            "Locate an existing scalar coordinate without reading a new attribute; complete composite keys remain coordinates and Population predicates.",
            "result = query.coord(axis)",
            ("query", "axis"),
            "methods.rows",
        ),
        (
            Query,
            "select",
            (
                P(
                    "values",
                    "Nonempty named mapping of same-input Columns or immutable non-null literals.",
                ),
            ),
            "Query",
            "Select named values and retain full keys, presence, projections and required checks.",
            "result = query.select({'value': query['value']})",
            ("query",),
            "methods.rows",
        ),
        (
            Query,
            "where",
            (
                P(
                    "predicate",
                    "Same-input boolean Column or compatible PopulationPredicate; only True is selected.",
                ),
            ),
            "Query",
            "Filter current coordinates while preserving upstream checks and display order.",
            "result = query.where(query['value'] > 0)",
            ("query",),
            "methods.rows",
        ),
        (
            Query,
            "top",
            (
                P("n", "Nonnegative integer maximum per partition."),
                P("by", "Same-input score Column; NA sorts last."),
                P("descending", "Boolean; largest first by default."),
                P(
                    "partition_by",
                    "Tuple of exact retained bound axes; empty means global ranking.",
                ),
            ),
            "Query",
            "Rank one actual input, breaking ties with complete canonical keys.",
            "result = query.top(10, by=query['value'])",
            ("query",),
            "methods.rows",
        ),
        (
            Query,
            "reduce",
            (
                P(
                    "values",
                    "Named Column.sum/mean or row_count requests; each uses its independent NA mask.",
                ),
                P("by", "Existing retained grouping axes; empty produces one scalar coordinate."),
            ),
            "Query",
            "Compute current-value statistics without acquiring original Metric rollup authority.",
            "result = query.reduce({'mean': query['value'].mean()})",
            ("query",),
            "methods.metric.summary",
        ),
        (
            Query,
            "align",
            (
                P("other", "Another Query owned by this Session."),
                P("match", "Axis-free correspondence or None for exact semantic binding identity."),
                P("how", "exact or outer; presence is independent of value NA."),
            ),
            "Query",
            "Align complete coordinates and retain left/right values and projections.",
            "result = query.align(other)",
            ("query", "other"),
            "methods.compare",
        ),
        (
            Query,
            "compare",
            (
                P(
                    "baseline",
                    "Same-Session Query with the same nonempty numeric value names and units.",
                ),
                P("match", "Explicit axis-free correspondence or None."),
                P("how", "exact or outer."),
                P(
                    "time_axis",
                    "paired by default; current requires complete actual current projection.",
                ),
                P(
                    "zero_denominator",
                    "null or error for newly computed relative differences only.",
                ),
            ),
            "Query",
            "Compute current, baseline, absolute and relative difference divided by absolute baseline.",
            "result = query.compare(baseline)",
            ("query", "baseline"),
            "methods.compare",
        ),
        (
            Query,
            "rollup",
            (
                P(
                    "by",
                    "Retained bound axes or a lawful calendar coarsening; window time axes stay unchanged.",
                ),
            ),
            "Query",
            "Recompute original Metrics from selected complete original contributions.",
            "result = query.rollup()",
            ("query",),
            "methods.metric.reduce",
        ),
        (
            Query,
            "drill",
            (P("add", "Nonempty tuple of new single-valued Field classifications."),),
            "Query",
            "Read new attributes only for selected complete versioned contribution identities and recompute originals.",
            "result = query.drill(add=(field,))",
            ("query", "field"),
            "methods.metric.reduce",
        ),
        (
            Query,
            "share",
            (
                P("name", "One existing original additive quantity name."),
                P(
                    "partition_by",
                    "Exact retained axes forming the denominator partitions; retain window time.",
                ),
                P("zero_denominator", "null or error for the newly computed share only."),
            ),
            "Query",
            "Add a share using this actual selected input; later filtering retains its denominator.",
            "result = query.share('value')",
            ("query",),
            "methods.metric.reference",
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
            "Request ignore-NA sum; empty/all-NA is NA. Complete with Query.reduce.",
            "result = column.sum()",
            ("column",),
            "methods.metric.summary",
        ),
        (
            Column,
            "mean",
            (),
            "_Statistic",
            "Request ignore-NA mean; empty/all-NA is NA. Complete with Query.reduce.",
            "result = column.mean()",
            ("column",),
            "methods.metric.summary",
        ),
    ):
        value = getattr(owner, name)
        descriptors.append(
            operation(
                f"{owner.__name__}.{name}",
                f"{owner.__name__.lower()}.{name}",
                value,
                bindings=(bind(value, owner),),
                summary=summary,
                parameters=parameters,
                output=output,
                constraints=(
                    "Columns retain one exact lazy input; Artifact cannot enter computation.",
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
                summary="Construct a finite pointwise result expression on one exact Query input.",
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
                failures=("AnalysisError: use the exact Query input and compatible types/units.",),
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
