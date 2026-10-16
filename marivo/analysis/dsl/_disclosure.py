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
            summary="Immutable named Metric definition; only Query owns computation.",
            acquisition="Call session.observe(metrics); no direct constructor.",
            producers=("session.observe",),
            consumers=("Query.contract", "Query.execute"),
            constraints=(
                "Ordinary context binds without reads; execute evaluates numeric stages and atomically publishes Store 10 terminal output. Committed output is opened and read without restoring computation.",
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
            "contract",
            (),
            "AnalysisContract",
            "Describe bound definitions and unresolved physical types without reading a schema.",
            "result = query.contract()",
            ("query",),
            "entry",
        ),
        (
            Query,
            "execute",
            (),
            "Artifact",
            "After preparation, each execute admits a new Run, reads sources when needed, evaluates numeric stages and atomically publishes Store 10 output. Read the committed Artifact without current-source or Catalog access.",
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
                producers=(producer,),
                consumers=(consumer,),
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
    return DisclosureProvider(
        "analysis.dsl",
        tuple(descriptors),
        (
            ExportInput("Query", Query, "Query"),
            ExportInput("Artifact", Artifact, "Artifact"),
            ExportInput("InstantAnchor", InstantAnchor, "InstantAnchor"),
            ExportInput("CalendarAxis", CalendarAxis, "CalendarAxis"),
        ),
    )
