"""Native disclosure for the distinct Query and terminal Artifact protocols."""

from marivo._temporal import InstantAnchor, TimeScope
from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    ParameterInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.dsl.query import Query
from marivo.analysis.dsl.temporal import CalendarAxis


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = [
        value_type(
            "Query",
            Query,
            summary="Immutable lazy definition of complete coordinates and named values; only Query owns computation.",
            acquisition="Call session.observe(metrics), session.query(population), session.read(population, fields) or session.values(...).",
            producers=("session.observe", "session.query", "session.read", "session.values"),
            consumers=("Query.show", "Query.execute", "Query.population"),
            constraints=(
                "Context binds without reads; execute qualifies the selected backend and method before a Run. Native numeric precision is accepted; pandas effective types feed local computation and saved output. Read committed output without restoring computation.",
            ),
        ),
        value_type(
            "Artifact",
            Artifact,
            summary="Read-only committed output identity and manifest, without DSL or contract.",
            acquisition="Call Query.execute() or session.artifact(id); both read the same committed output.",
            producers=("Query.execute", "session.artifact"),
            consumers=(
                "Artifact.show",
                "Artifact.to_pandas",
                "Artifact.findings",
                "Artifact.evidence_digest",
            ),
            constraints=(
                "Opening reads metadata only. Preview decodes bounded rows; export is complete. Finding/Evidence reads need no result tables. Old graph formats are rejected unchanged.",
            ),
        ),
    ]
    operations = (
        (
            TimeScope,
            "by",
            (
                ParameterInput(
                    "grain", "Pass mv.grain(unit) or an already certified calendar.grain(level)."
                ),
            ),
            "CalendarAxis",
            "Construct complete nominal and actual calendar buckets using frozen authority.",
            "result = scope.by(grain)",
            ("scope", "grain"),
            "inputs.time",
        ),
        (
            InstantAnchor,
            "show",
            (),
            "None",
            "Display the exact frozen UTC instant.",
            "result = anchor.show()",
            ("anchor",),
            "inputs.time",
        ),
        (
            CalendarAxis,
            "coarsen",
            (ParameterInput("grain", "A builtin or certified Grain over this same frozen scope."),),
            "CalendarAxis",
            "Construct a candidate coarsening; each consumer checks complete whole-bucket mapping and Metric merge permission.",
            "result = axis.coarsen(grain)",
            ("axis", "grain"),
            "inputs.time",
        ),
        (
            CalendarAxis,
            "show",
            (),
            "None",
            "Display bounded frozen calendar metadata.",
            "result = axis.show()",
            ("axis",),
            "inputs.time",
        ),
        (
            Query,
            "population",
            (),
            "Population",
            "Capture the Query's output coordinate image, preserving its own period, conditions, declarations and Session owner.",
            "result = query.population()",
            ("query",),
            "population_tasks",
        ),
        (
            Query,
            "show",
            (),
            "None",
            "Describe bound definitions and unresolved physical types without reading a schema.",
            "result = query.show()",
            ("query",),
            "entry",
        ),
        (
            Query,
            "execute",
            (),
            "Artifact",
            "After preparation, each execute admits a new Run, reads sources when needed, evaluates numeric stages and atomically publishes Store 11 output. Read the committed Artifact without current-source or Catalog access.",
            "result = query.execute()",
            ("query",),
            "entry",
        ),
        (
            Artifact,
            "show",
            (ParameterInput("n", "Choose a nonnegative preview row count; default 20."),),
            "None",
            "Display a bounded committed-output preview; receipt hashing can read the entire consumed part.",
            "result = artifact.show(n=20)",
            ("artifact",),
            "artifacts.reads",
        ),
        (
            Artifact,
            "to_pandas",
            (),
            "pandas.DataFrame",
            "Export complete committed output as an isolated pandas DataFrame with typed coordinate/value/presence columns and pure data attrs; no source lookup.",
            "result = artifact.to_pandas()",
            ("artifact",),
            "artifacts.reads",
        ),
        (
            Artifact,
            "findings",
            (
                ParameterInput("limit", "Choose a page size from 1 through 100."),
                ParameterInput("cursor", "Use the preceding page cursor or None."),
            ),
            "FindingPage",
            "Read a bounded page of committed output Findings without result-table reads or graph restoration.",
            "result = artifact.findings(limit=20)",
            ("artifact",),
            "artifacts.reads",
        ),
        (
            Artifact,
            "evidence_digest",
            (),
            "ArtifactDigest",
            "Return all saved output Evidence facts and collection digests; no result-table reads or current-source freshness claim.",
            "result = artifact.evidence_digest()",
            ("artifact",),
            "artifacts.reads",
        ),
    )
    for value, producer, consumer in (
        (InstantAnchor, "session.instant", "InstantAnchor.show"),
        (CalendarAxis, "TimeScope.by", "CalendarAxis.show"),
    ):
        descriptors.append(
            value_type(
                value.__name__,
                value,
                summary=f"Immutable {value.__name__} temporal binding.",
                acquisition=f"Call {producer}(...).",
                producers=(producer, "CalendarAxis.coarsen")
                if value is CalendarAxis
                else (producer,),
                consumers=(
                    consumer,
                    "CalendarAxis.coarsen",
                    "Query.rollup",
                    "Query.reduce",
                    "Query.share",
                )
                if value is CalendarAxis
                else (consumer,),
                constraints=("Frozen metadata only; no source or schema reads.",),
            )
        )
    for owner, name, parameters, output, summary, code, requires, group in operations:
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
                    "Query and Artifact have separate protocols; Artifact objects and IDs never enter computation.",
                ),
                effects=summary,
                failures=(
                    "ValueError: read the temporal validation message and use a compatible scope, Grain and certified calendar.",
                )
                if owner is TimeScope
                else (
                    "AnalysisError: follow the structured expected/received facts and repair; unavailable stages never fall back to graph execution.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
            )
        )
    from marivo.analysis.dsl._population_disclosure import provider as population_provider

    populations = population_provider()
    from marivo.analysis.dsl._result_disclosure import provider as result_provider

    results = result_provider()
    return DisclosureProvider(
        "analysis.dsl",
        (*descriptors, *populations.descriptors, *results.descriptors),
        (
            ExportInput("Query", Query, "Query"),
            ExportInput("Artifact", Artifact, "Artifact"),
            ExportInput("InstantAnchor", InstantAnchor, "InstantAnchor"),
            ExportInput("CalendarAxis", CalendarAxis, "CalendarAxis"),
            *populations.exports,
            *results.exports,
        ),
    )
