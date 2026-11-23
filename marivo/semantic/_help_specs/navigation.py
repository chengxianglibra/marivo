"""Semantic task, check and expression discovery pages."""

from __future__ import annotations

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.targets import canonical_target
from marivo.semantic._help_specs.builders import _target
from marivo.semantic._help_specs.model import (
    SemanticBuilderTopic,
    SemanticCheckRoute,
    SemanticCheckTopic,
    SemanticHelpDescriptor,
    SemanticNavigationRoute,
    SemanticNavigationTopic,
    SemanticObjectContract,
    SemanticObjectIndexEntry,
    SemanticRootSection,
)


def _navigation_route(
    label: str,
    target: LiveHelpTarget,
    *,
    summary: str | None = None,
    owns_discovery: bool = True,
) -> SemanticNavigationRoute:
    return SemanticNavigationRoute(
        label=label,
        target=target,
        summary=summary,
        owns_discovery=owns_discovery,
    )


def _check_topic() -> SemanticCheckTopic:
    """Build proof/non-proof routing for semantic inspection and checks."""

    def route(
        question: str,
        targets: tuple[LiveHelpTarget, ...],
        proves: str,
        does_not_prove: str,
    ) -> SemanticCheckRoute:
        return SemanticCheckRoute(
            question=question,
            targets=targets,
            proves=proves,
            does_not_prove=does_not_prove,
        )

    def datasource(canonical_id: str) -> LiveHelpTarget:
        return LiveHelpTarget(surface="datasource", canonical_id=canonical_target(canonical_id))

    return SemanticCheckTopic(
        canonical_id="validation",
        summary="Choose offline loading/readiness, online validation, preview or explicit data checks by proof need.",
        routes=(
            route(
                "What physical source, schema, columns, and types exist?",
                (datasource("inspect"),),
                "Current authoritative physical metadata to the backend's supported extent.",
                "Reusable business meaning.",
            ),
            route(
                "Do I need bounded sampled rows or source-specific SQL evidence?",
                (datasource("inspection"), datasource("raw_sql")),
                "Explicitly scoped or governed physical observations.",
                "Semantic validity or typed-analysis authority.",
            ),
            route(
                "Do project sources execute, resolve refs, and compile as one project?",
                (_target("load"),),
                "Static project assembly and structural validation.",
                "Current external health or operation-shaped executability.",
            ),
            route(
                "Is this exact requested dependency closure statically ready for analysis?",
                (_target("readiness"),),
                "Governed semantic closure and analysis_ready_inputs.",
                "Successful execution of every future analysis shape.",
            ),
            route(
                "What does this entry produce under one explicit authoring scope?",
                (_target("preview"), _target("preview_many")),
                "Preview reads bounded current-source values and reports acquisition coverage and retained rows. Ordinary preview does not change readiness or certify business completeness.",
                "Preview reads bounded current-source values and reports acquisition coverage and retained rows. Ordinary preview does not change readiness or certify business completeness.",
            ),
            route(
                "How do I declare an exact source expectation?",
                (_target("source_check"),),
                "The expectation is explicit, typed, and closed.",
                "That the current source satisfies it.",
            ),
            route(
                "Do the definitions agree with the current physical source types?",
                (_target("validate"),),
                "Current Entity output, field bindings, numeric and temporal type contracts; "
                "bounded data expectations only when explicitly requested.",
                "Business approval or readiness mutation.",
            ),
        ),
    )


def _navigation_descriptors(
    object_contracts: tuple[SemanticObjectContract, ...],
) -> tuple[SemanticHelpDescriptor, ...]:
    """Build the need-directed model, time, catalog, and validation tree."""
    objects = {contract.semantic_kind.value: contract for contract in object_contracts}

    def object_entry(kind: str) -> SemanticObjectIndexEntry:
        return SemanticObjectIndexEntry(
            objects[kind],
            {
                "domain": "Choose the accountable business namespace before declaring its objects.",
                "entity": "Choose record grain and identity before mapping the physical source.",
                "dimension": "Choose categorical or scalar field meaning and its declaration mode.",
                "time_dimension": "Choose business time meaning and its physical representation.",
                "measure": "Choose a numeric row quantity and its aggregation permissions.",
                "metric": "Choose existing Metric composition or a reusable Metric definition.",
                "relationship": "Choose directed endpoints, cardinality, and complete join keys.",
            }[kind],
        )

    def route(
        label: str, target: str, summary: str, *, owns: bool = True
    ) -> SemanticNavigationRoute:
        return _navigation_route(label, _target(target), summary=summary, owns_discovery=owns)

    return (
        SemanticNavigationTopic(
            "model",
            "Choose the missing reusable definition. Single-mode definitions link directly to construction; fields offer column or expression modes.",
            (
                *(
                    object_entry(kind)
                    for kind in (
                        "domain",
                        "entity",
                        "dimension",
                        "time_dimension",
                        "measure",
                        "metric",
                        "relationship",
                    )
                ),
            ),
        ),
        SemanticNavigationTopic(
            "time",
            "Declare physical time parsing and source timezone semantics.",
            (
                route(
                    "physical time encoding",
                    "time.parsing",
                    "Declare native dates, timestamps, formatted values, or hour prefixes.",
                ),
            ),
        ),
        SemanticNavigationTopic(
            "time.parsing",
            "Choose the physical time encoding used by a TimeDimension declaration.",
            tuple(
                route(label, target, summary)
                for label, target, summary in (
                    ("date value", "date", "Declare the business timezone of a native DATE."),
                    (
                        "timestamp value",
                        "timestamp",
                        "Interpret a physical timestamp with explicit temporal meaning.",
                    ),
                    (
                        "formatted value",
                        "strptime",
                        "Parse a physical value using an explicit format.",
                    ),
                    ("hour prefix", "hour_prefix", "Parse supported hour-prefix source values."),
                )
            ),
        ),
        SemanticNavigationTopic(
            "catalog",
            "Use closed Catalog values for analysis. Declaration lookup symbols capture a recipe and remain distinct from closed analysis inputs.",
            (
                route(
                    "discover or list business objects",
                    "SemanticCatalog.search",
                    "Search business meaning or list loaded objects by kind/domain/entity with an empty query.",
                ),
                route(
                    "retrieve and explain a definition",
                    "SemanticCatalog.get",
                    "Retrieve a canonical identity; inspect its complete entry and native value.",
                ),
                route(
                    "declaration lookup kind",
                    "ref",
                    "Choose a typed lookup symbol when authoring a declaration or parameter.",
                ),
                route(
                    "current catalog contract",
                    "SemanticCatalog",
                    "Inspect search, get and explicit validation methods.",
                    owns=False,
                ),
            ),
        ),
        _check_topic(),
        *object_contracts,
    )


def _root_sections() -> tuple[SemanticRootSection, ...]:
    """Keep the semantic root to five explicit information needs."""
    return (
        SemanticRootSection(
            section_id="tasks",
            label="Choose by current semantic question",
            members=tuple(
                _target(target)
                for target in (
                    "model",
                    "expressions",
                    "time",
                    "catalog",
                    "validation",
                )
            ),
            route_summaries=(
                "Define reusable identity, fields, Metrics, and relationships.",
                "Compose closed inputs for this question or expressions for a declaration.",
                "Parse physical time or define business calendars and events.",
                "Retrieve closed values, inspect definitions, or capture declaration lookups.",
                "Choose offline loading/readiness, online validation, preview or a declared data expectation.",
            ),
        ),
    )


SCALAR_METHODS = {
    "abs",
    "sqrt",
    "exp",
    "round",
    "cast",
    "log",
    "clip",
    "coalesce",
    "least",
    "greatest",
    "nullif",
    "ifelse",
    "cases",
    "isnull",
    "notnull",
}


def expression_topics(rows: tuple[AuthoringCapability, ...]) -> tuple[SemanticBuilderTopic, ...]:
    row_summary = (
        "Use row arithmetic within one contribution root, then reduce before Metric arithmetic. Numeric Dimensions do not gain aggregation permission."
        "\n  Decimal add/subtract uses p=max(p1-s1,p2-s2)+max(s1,s2)+1, s=max(s1,s2); multiply uses p=p1+p2, s=s1+s2. "
        "Cap p at 38 and reduce s by the excess, down to zero. Mixed integers supply their bound digit width (int64: 19); "
        "untyped Decimal literals bind their own digits/exponent. Source output casts bind p/s; rounding follows the kernel. "
        "\n  Integer SUM uses native accumulation (Ibis int64); Decimal SUM returns Decimal(38,input_scale). Count stays int64; division converts numeric operands to float64 before dividing; mean keeps its declared type."
        "\n  Source and local overflow/rounding follow the actual kernel, including errors, wrapping and NaN/Infinity. No extra scans or protective widening. Saved values retain committed types."
        "\n  UInt64 inputs use exact Decimal(20,0) before semantic arithmetic. Explicit Entity casts can use .cast('decimal(20,0)') or .cast(ibis.dtype('decimal(38,0)')); .cast('int64') requires the signed range."
        "\n  Ordinary scalar division retains its source unit; zero denominators yield NA before arithmetic."
    )
    return (
        SemanticBuilderTopic(
            "row",
            "row",
            row_summary,
            (
                *expression_members(rows, "row"),
                _target("expressions.row_scalar"),
                _target("Entity.count"),
            ),
        ),
        SemanticBuilderTopic(
            "metric_samples",
            "MetricSamples",
            "Choose an operation on the constructed receiver. Internal Metric samples must finish with a reducer before observation.",
            expression_members(rows, "metric_samples"),
        ),
        *(
            SemanticBuilderTopic(
                f"{prefix}_scalar",
                f"{label} scalar operations",
                "Scalar methods preserve the expression stage; numeric and boolean roles retain their method constraints.",
                expression_members(rows, prefix, scalar=True),
            )
            for prefix, label in (("row", "row"), ("metric", "Metric"))
        ),
    )


def expression_members(
    rows: tuple[AuthoringCapability, ...], prefix: str, *, scalar: bool = False
) -> tuple[LiveHelpTarget, ...]:
    return tuple(
        LiveHelpTarget(surface="semantic", canonical_id=row.canonical_id)
        for row in rows
        if row.canonical_id.startswith(prefix + ".")
        and row.canonical_id != "metric.define"
        and (row.canonical_id.rsplit(".", 1)[-1] in SCALAR_METHODS) == scalar
    )


NAVIGATION: tuple[SemanticHelpDescriptor, ...] = (
    SemanticNavigationTopic(
        canonical_id="field",
        summary="A loaded Dimension, TimeDimension or Measure retains its semantic role and project. Compose objects directly. Declaration symbols require loader binding before analysis. Compare with ==, !=, <, <=, >, >=; for example (region == 'US') & (amount > 0). Combine predicates with &, | and ~ (not Python and/or/not). Deferred values have no .show(); inspect catalog.get('kind:path').show().",
        members=(
            _navigation_route(
                "source boolean conditions", _target("predicate"), owns_discovery=False
            ),
            _navigation_route(
                "Dimension definition", _target("objects.dimension"), owns_discovery=False
            ),
            _navigation_route(
                "TimeDimension definition",
                _target("objects.time_dimension"),
                owns_discovery=False,
            ),
            _navigation_route(
                "Measure definition", _target("objects.measure"), owns_discovery=False
            ),
            _navigation_route("single-valued relationship path", _target("Field.via")),
            _navigation_route("attribute version anchor", _target("Field.at")),
            _navigation_route("literal membership predicate", _target("Field.isin")),
            _navigation_route("missing-value predicate", _target("Dimension.isnull")),
        ),
    ),
    SemanticNavigationTopic(
        canonical_id="predicate",
        summary=(
            "Source conditions are row-stage boolean expressions. Compare loaded fields, use "
            "isnull/isin, and combine with mv.in_population(P) using &, | and ~; use "
            "parentheses, never Python and/or/not or bool. Example with resolved status, "
            "revenue and a compatible Population P: session.observe({'revenue': revenue}, "
            "where=(status == 'paid') | mv.in_population(P)). "
            "Boolean operators accept same-stage predicates and bool literals; other "
            "operand types reject at expression construction. TimeScope is a report "
            "selector: use session.observe(revenue, where=(scope, predicate)) to combine "
            "it with a row predicate. "
            "Membership tests complete correlated tuples with null-safe field matching; "
            "they return True or False. Field NA retains three-valued boolean semantics; "
            "only a final True selects a contribution. Both boolean sides retain static "
            "validation, numeric checks and producer dependencies, even under True/False. "
            "Observe, single-source Metric.where and members accept mixed conditions on "
            "their own contribution/identity domain. Population.where evaluates field and membership "
            "leaves over existing axes. Classify missing fields before filtering. Source reduction where= accepts field leaves "
            "only. TimeField comparisons freeze ISO strings and dates in the receiving "
            "Session report timezone, as session.time_scope does; aware inputs keep their "
            "instant. Naive datetimes and ambiguous/nonexistent civil boundaries reject. "
            "DATE compares its source business-day start; == selects an exact instant. "
            "Use session.instant(value, timezone=...) for explicit boundary timezones. Fixed "
            "field predicates remain in force through window history and supply no report endpoint. "
            "Ordinary string fields "
            "and fixed Frame Column comparisons keep their own types. "
            "Member leaves support boolean operators/constants, not comparisons, "
            "arithmetic, ifelse, cast or methods. Bare Python bool is not a condition. "
            "Metric comparisons produce metric-stage booleans for same-stage conditional expressions; they cannot filter source rows. "
            "AnalysisFrame.where requires that exact Frame's boolean Column. Capture and "
            "show perform no business reads. Inspect captured "
            "stage/dependencies with expression.show(), or conditions on the receiving Population or AnalysisFrame."
        ),
        members=(
            _navigation_route("field expressions", _target("field"), owns_discovery=False),
            _navigation_route(
                "source Metric filtering", _target("Metric.where"), owns_discovery=False
            ),
            _navigation_route(
                "membership leaf",
                LiveHelpTarget(surface="analysis", canonical_id="in_population"),
                owns_discovery=False,
            ),
            _navigation_route(
                "observation contributions",
                LiveHelpTarget(surface="analysis", canonical_id="session.observe"),
                owns_discovery=False,
            ),
            _navigation_route(
                "source identities",
                LiveHelpTarget(surface="analysis", canonical_id="session.members"),
                owns_discovery=False,
            ),
            _navigation_route(
                "existing Population coordinates",
                LiveHelpTarget(surface="analysis", canonical_id="population.where"),
                owns_discovery=False,
            ),
            _navigation_route(
                "current Frame rows",
                LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.where"),
                owns_discovery=False,
            ),
        ),
    ),
    SemanticNavigationTopic(
        canonical_id="expressions",
        summary=(
            "For analysis, compose closed Catalog fields and Metrics using their governed meaning and aggregation permissions; "
            "a question-scoped expression does not require authoring or publishing a Metric. For example, with closed compatible "
            "revenue and order_count, session.observe({'average_order_value': revenue / order_count}) captures the question. "
            "For reusable declarations, native lookup symbols compose before loading and the loader closes the complete dependency scope; "
            "bare lookup symbols are not analysis inputs. Choose the expression role before an operation. "
            "Row arithmetic stays within one contribution root; reduce before Metric arithmetic. per creates internal samples that must be reduced. "
            "where, per, and window keep their receiver constraints; a compound Metric is not automatically filterable."
        ),
        members=(
            _navigation_route(
                "scalar fields",
                _target("field"),
                summary="Compare or navigate a governed Dimension, TimeField, or Measure.",
            ),
            _navigation_route(
                "source conditions",
                _target("predicate"),
                summary="Combine field and Population membership conditions on their receiving domain.",
            ),
            _navigation_route(
                "row quantities",
                _target("expressions.row"),
                summary="Compute within one contribution root and reduce source contributions.",
            ),
            _navigation_route(
                "Metric values",
                _target("expressions.Metric"),
                summary="Compose existing reduced values; publishing a new Metric is optional.",
                owns_discovery=False,
            ),
            _navigation_route(
                "explicit scale and unit",
                _target("rescale"),
                summary="Divide a numeric Row or Metric by an authored constant and declare its target unit.",
            ),
            _navigation_route(
                "Metric scalar operations",
                _target("expressions.Metric_scalar"),
                summary="Use stage-checked numeric and conditional methods.",
            ),
            _navigation_route(
                "internal samples",
                _target("expressions.MetricSamples"),
                summary="Reduce a Metric's per-sample values to a Metric.",
            ),
            _navigation_route(
                "filter contributions",
                _target("Metric.where"),
                summary="Filter an eligible source Metric before reduction.",
                owns_discovery=False,
            ),
            _navigation_route(
                "choose internal samples",
                _target("Metric.per"),
                summary="Choose the internal sample domain before a reducer.",
                owns_discovery=False,
            ),
            _navigation_route(
                "choose a window",
                _target("Metric.window"),
                summary="Attach an explicit Metric observation window.",
                owns_discovery=False,
            ),
            _navigation_route("inspect captured object metadata", _target("SemanticObject.show")),
            _navigation_route(
                "inspect captured definitions",
                _target("SemanticObject.show"),
                owns_discovery=False,
            ),
        ),
    ),
)
