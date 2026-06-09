"""ms.help - agent-facing introspection of the semantic surface."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal, cast

from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import Surface, render
from marivo.semantic.constraints import iter_constraints

_SUMMARIES: dict[str, str] = {
    "AiContext": "agent-facing semantic metadata schema",
    "AiContextInput": "agent-authored ai_context fields for semantic object handoff",
    "AssessmentIssue": "a single rule-based authoring assessment issue",
    "AssessmentResult": "facts, issues, and questions from an authoring check",
    "AuthoringAssessment": "facts, issues, questions, and status for authoring readiness",
    "AuthoringEvidenceInput": "source SQL / knowledge / confirmation evidence input",
    "AuthoringQuestion": "an unresolved business decision raised by a check",
    "AuthoringSourceInput": "physical source, role, datasource, and columns for authoring",
    "AuthoringSourceRole": "type alias: primary/from/to/component source role",
    "BoundedProfilePolicy": "bounded profiling policy that reads rows with a limit",
    "ColumnEvidence": "deep-dive evidence for one source column",
    "ColumnProfile": "bounded-sample profile facts for one column",
    "DatasetSource": "type alias: TableSource | FileSource",
    "DecisionKind": "semantic decision category enum",
    "DecisionRecord": "stored semantic authoring decision",
    "DemandSignal": "signal used to score semantic richness gaps",
    "EvidenceFact": "a single observed authoring fact with evidence refs",
    "EvidenceRef": "reference to collected authoring evidence",
    "FileSource": "physical file source (path + format: parquet/csv/json)",
    "MetadataOnlyPolicy": "metadata-only profiling policy (no row reads)",
    "ParitySummary": "semantic parity evidence summary",
    "PreviewSummary": "raw preview evidence summary",
    "ReadinessInputSummary": "semantic readiness closeout input summary",
    "ReadinessIssue": "semantic readiness issue",
    "ReadinessReport": "semantic readiness report",
    "RejectedCandidate": "candidate rejected during semantic authoring",
    "RichnessGap": "missing semantic detail identified by richness checks",
    "RichnessReport": "semantic richness report",
    "RichnessSummary": "semantic readiness richness gap summary",
    "SamplePolicy": "type alias: MetadataOnlyPolicy | BoundedProfilePolicy | SelectedColumnsPolicy",
    "SelectedColumnsPolicy": "profiling policy that reads selected columns with a limit",
    "SemanticProject": "primary reader for a loaded semantic project",
    "SourceEvidencePack": "collected facts and bounded profiles for a source",
    "SchemaColumn": "named column with name and data_type attributes, from a pack's schema",
    "TableSource": "physical table source (table name, optional database)",
    "dataset": "declare a dataset over a structured source",
    "derived_metric": "declare a body-free canonical ratio or weighted-average metric",
    "errors": "SemanticError hierarchy and ErrorKind enum",
    "field": "declare a non-aggregated field on a dataset",
    "file": "file source for ms.dataset(source=...)",
    "find_project": "discover a semantic project by walking up from a directory",
    "help": "this introspection entry point",
    "metric": "declare a dataset-backed aggregate metric",
    "model": "open a model namespace for decorator registration",
    "ratio": "derived metric helper (a/b)",
    "ref": "refer to another metric by qualified name",
    "relationship": "declare a relationship between datasets",
    "snapshot": "declare snapshot versioning for a dataset",
    "sum": "sum aggregation marker",
    "table": "table source for ms.dataset(source=...)",
    "time_field": "declare a time-aware field used as the calendar axis",
    "typing": "IbisBackend Protocol and AiContext TypedDict",
    "validity": "declare validity-window versioning for a dataset",
    "weighted_average": "weighted-average aggregation marker",
    "constraints": "authoring and validation constraints",
    "decomposition": "metric decomposition builders and aggregation boundary",
}


def _constraint_topic() -> Descriptor:
    constraints = [
        {
            "id": constraint.id,
            "title": constraint.title,
        }
        for constraint in iter_constraints()
    ]
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="constraints",
        summary="Semantic authoring and validation constraints. Drill into an id for full rule details.",
        content={"constraints": constraints},
        doc="\n".join(
            (
                "marivo.semantic constraints:",
                "",
                *(f"  {constraint['id']:<34} {constraint['title']}" for constraint in constraints),
                "",
                'Call ms.help("<constraint_id>", format="json") for full rule details.',
            )
        ),
    )


def _decomposition_content() -> dict[str, object]:
    return {
        "summary": (
            "Metric decomposition is not SQL aggregation. Decomposition declares how "
            "metric values compose during drilldown, derived calculations, and "
            "component-aware analysis."
        ),
        "builders": [
            {
                "name": "sum",
                "call": "ms.sum()",
                "use": "Aggregate metric over its dataset row set.",
                "components": [],
            },
            {
                "name": "ratio",
                "call": "ms.ratio(numerator=..., denominator=...)",
                "use": "Derived metric expressed as numerator / denominator.",
                "components": ["numerator", "denominator"],
            },
            {
                "name": "weighted_average",
                "call": "ms.weighted_average(value=..., weight=...)",
                "use": "Derived metric whose value is explained by additive value and weight components.",
                "components": ["numerator", "weight"],
            },
        ],
        "guidance": [
            {
                "metric_shape": "additive_amount",
                "body": ".sum() or another dataset-backed reduction",
                "decomposition": "ms.sum()",
            },
            {
                "metric_shape": "count",
                "body": ".count() in the metric body",
                "decomposition": "ms.sum()",
            },
            {
                "metric_shape": "mean_or_average",
                "body": "ms.derived_metric(..., decomposition=ms.ratio(...))",
                "decomposition": "ms.ratio(...)",
            },
            {
                "metric_shape": "weighted_average",
                "body": "ms.derived_metric(..., decomposition=ms.weighted_average(...))",
                "decomposition": "ms.weighted_average(...)",
            },
        ],
        "anti_patterns": [
            "Do not call ms.count(); count metrics use .count() in the metric body and ms.sum() decomposition.",
            "Do not call ms.mean(); mean metrics should be modeled as ratio or weighted_average components.",
            "Do not infer decomposition builders from common SQL aggregate names.",
        ],
        "related_help": [
            "ms.help('metric', format='json')",
            "ms.help('derived_metric', format='json')",
            "ms.help('constraints', format='json')",
        ],
    }


def _decomposition_text(content: dict[str, object]) -> str:
    builders = cast("list[dict[str, object]]", content["builders"])
    guidance = cast("list[dict[str, object]]", content["guidance"])
    anti_patterns = cast("list[str]", content["anti_patterns"])
    lines = [
        "marivo.semantic decomposition",
        "",
        str(content["summary"]),
        "",
        "Supported builders:",
    ]
    for builder in builders:
        lines.append(f"  - {builder['call']}: {builder['use']}")
    lines.extend(("", "Guidance:"))
    for guidance_item in guidance:
        lines.append(
            f"  - {guidance_item['metric_shape']}: body {guidance_item['body']}; decomposition {guidance_item['decomposition']}"
        )
    lines.extend(("", "Anti-patterns:"))
    for anti_pattern in anti_patterns:
        lines.append(f"  - {anti_pattern}")
    lines.append("")
    lines.append('Call ms.help("decomposition", format="json") for agent-readable data.')
    return "\n".join(lines)


def _decomposition_topic() -> Descriptor:
    content = _decomposition_content()
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="decomposition",
        summary=cast("str", content["summary"]),
        content=content,
        doc=_decomposition_text(content),
        see_also=(
            "ms.help('metric', format='json')",
            "ms.help('derived_metric', format='json')",
            "ms.help('constraints', format='json')",
        ),
    )


def _resolve(symbol: str) -> Any | None:
    import marivo.semantic as ms
    from marivo.semantic import errors as errors_mod
    from marivo.semantic import typing as typing_mod

    if hasattr(ms, symbol):
        return getattr(ms, symbol)
    if hasattr(errors_mod, symbol):
        return getattr(errors_mod, symbol)
    if hasattr(typing_mod, symbol):
        return getattr(typing_mod, symbol)
    return None


@lru_cache(maxsize=1)
def _surface() -> Surface:
    import marivo.semantic as ms

    all_names = tuple(dict.fromkeys((*ms.__all__, "constraints", "decomposition")))
    summaries = {name: _SUMMARIES.get(name, "") for name in all_names}
    catalog = {constraint.id: constraint for constraint in iter_constraints()}
    return Surface(
        name="marivo.semantic",
        all_names=all_names,
        summaries=summaries,
        resolve=_resolve,
        catalog=catalog,
        topics={
            "constraints": _constraint_topic(),
            "decomposition": _decomposition_topic(),
        },
    )


def _format_top_level_text() -> str:
    data = cast("dict[str, object]", render(_surface(), None, "json"))
    entries = cast("list[dict[str, str]]", data["entries"])
    lines = ["marivo.semantic - top-level entries:", ""]
    for entry in entries:
        lines.append(f"  ms.{entry['name']:<18} [{entry['kind']}]  {entry['summary']}")
    lines.append("")
    lines.append('Call ms.help("<name>") for detail on any entry.')
    lines.append('Call ms.help("<name>", format="json") for agent-readable data.')
    return "\n".join(lines)


def help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    normalized = None if symbol == "" else symbol
    if normalized is None:
        return _format_top_level_text()
    return cast("str", render(_surface(), normalized, "text"))


def help(  # noqa: A001, RUF100
    symbol: str | None = None,
    *,
    format: Literal["text", "json"] = "text",
) -> dict[str, object] | None:
    """Print or return agent-facing help for the semantic surface.

    Without arguments, lists top-level entries. With a symbol name (decorator,
    builder, function, exception class, topic, or constraint id) prints its
    signature, docstring, and bounded constraint summaries. With
    ``format="json"``, prints the structured JSON descriptor and returns the dict.
    """

    normalized = None if symbol == "" else symbol
    if format == "json":
        data = cast("dict[str, object]", render(_surface(), normalized, "json"))
        print(json.dumps(data, indent=2, sort_keys=True))
        return data
    if format != "text":
        raise ValueError("format must be 'text' or 'json'")
    print(help_text(normalized))
    return None
