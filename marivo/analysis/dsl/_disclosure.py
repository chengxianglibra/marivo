"""Native disclosure for the distinct Query and terminal Artifact protocols."""

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
                "R2.1 captures definitions; binding and execution are pending R2.2-R2.5.",
            ),
        ),
        value_type(
            "Artifact",
            Artifact,
            summary="Read-only committed output identity and manifest, without DSL or contract.",
            acquisition="Use Query.execute() or session.artifact(id) once R2.5/R2.6 are delivered.",
            producers=("Query.execute", "session.artifact"),
            consumers=(
                "Artifact.show",
                "Artifact.to_pandas",
                "Artifact.findings",
                "Artifact.evidence_digest",
            ),
            constraints=(
                "R2.1 rejects old graph output; terminal storage and reading are not yet available.",
            ),
        ),
    ]
    operations = (
        (
            Query,
            "contract",
            (),
            "AnalysisContract",
            "Describe known definitions and pending binding without reading a schema.",
            "result = query.contract()",
            ("query",),
            "entry",
        ),
        (
            Query,
            "execute",
            (),
            "Artifact",
            "Request explicit execution; R2.1 fails before data reads or Run admission.",
            "result = query.execute()",
            ("query",),
            "entry",
        ),
        (
            Artifact,
            "show",
            (ParameterInput("n", "Choose a nonnegative preview row count; default 20."),),
            "None",
            "Read a bounded preview from committed output only.",
            "result = artifact.show(n=20)",
            ("artifact",),
            "artifacts.reads",
        ),
        (
            Artifact,
            "to_pandas",
            (),
            "pandas.DataFrame",
            "Export complete isolated output; no DSL continuation or source lookup.",
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
            "Read committed output Findings without graph restoration.",
            "result = artifact.findings(limit=20)",
            ("artifact",),
            "artifacts.reads",
        ),
        (
            Artifact,
            "evidence_digest",
            (),
            "ArtifactDigest",
            "Read saved output facts; no current-source freshness claim.",
            "result = artifact.evidence_digest()",
            ("artifact",),
            "artifacts.reads",
        ),
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
                    "AnalysisError: follow the structured expected/received facts and repair; unavailable stages never fall back to graph execution.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
            )
        )
    return DisclosureProvider(
        "analysis.dsl",
        tuple(descriptors),
        (ExportInput("Query", Query, "Query"), ExportInput("Artifact", Artifact, "Artifact")),
    )
