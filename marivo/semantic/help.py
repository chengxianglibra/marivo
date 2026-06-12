"""ms.help - agent-facing introspection of the semantic surface."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import Surface, render
from marivo.semantic.constraints import iter_constraints

_SUMMARIES: dict[str, str] = {
    "AiContext": "agent-facing semantic metadata schema",
    "AiContextView": "read-only view of ai_context fields: business_definition, guardrails, synonyms",
    "AssessmentIssue": "a single rule-based authoring assessment issue",
    "AuthoringAssessment": "facts, issues, questions, and status for authoring readiness",
    "AuthoringQuestion": "an unresolved business decision raised by a check",
    "BriefStatus": "stepwise brief preparation status enum",
    "ColumnProfile": "bounded-sample profile facts for one column",
    "ComponentFact": "metric component fact used in derived-metric decomposition",
    "CrossEntityMetricBrief": "brief for a cross-entity metric authoring step",
    "DatasetSource": "type alias: TableSource | FileSource",
    "DatasourceDetails": "kind-specific details for a datasource including backend type",
    "DerivedMetricBrief": "brief for a derived metric authoring step",
    "DimensionBrief": "brief for a dimension authoring step",
    "DimensionDetails": "kind-specific details for a dimension or measure field",
    "DimensionRef": "stable reference to a declared dimension",
    "DimensionValueFact": "dimension value fact from evidence sampling",
    "DomainBrief": "brief for a domain authoring step",
    "DomainDetails": "kind-specific details for a domain including child entities and metrics",
    "DomainRef": "stable reference to a declared domain",
    "EntityBrief": "brief for an entity authoring step",
    "EntityDetails": "kind-specific details for an entity object",
    "EntityRef": "stable reference to a declared entity",
    "FileSource": "physical file source (path + format: parquet/csv/json)",
    "FormatCandidate": "date format candidate from time-dimension inspection",
    "JoinPathFact": "join path evidence fact from relationship probing",
    "MetricBrief": "brief for a single-entity metric authoring step",
    "MetricDetails": "kind-specific details for a metric object including decomposition and parity",
    "MetricRef": "stable reference to a declared metric",
    "ParitySummary": "semantic parity evidence summary",
    "PreviewSummary": "raw preview evidence summary",
    "PrimaryKeyCandidate": "candidate primary key from entity inspection",
    "ReadinessInputSummary": "semantic readiness closeout input summary",
    "ReadinessIssue": "semantic readiness issue",
    "ReadinessReport": "semantic readiness report",
    "RegisteredMatch": "explainable registered-object reuse fact",
    "RelationshipBrief": "brief for a relationship authoring step",
    "RelationshipDetails": "kind-specific details for a relationship between datasets",
    "RelationshipRef": "stable reference to a declared relationship",
    "RichnessSummary": "semantic readiness richness gap summary",
    "SemanticCatalog": "read-only object graph over a loaded semantic project — returned by ms.load()",
    "SemanticKind": "semantic kind enum: domain, datasource, entity, dimension, time_dimension, metric, relationship",
    "SemanticKindInput": "input type accepted where a SemanticKind value is expected",
    "SemanticObject": "unified read shape for all loaded semantic objects",
    "SemanticObjectDetails": "union of kind-specific detail shapes for a SemanticObject",
    "SemanticObjectList": "browsing result from catalog.list() — has .show(), .refs(), .objects",
    "SemanticRef": "stable semantic identifier passable directly to analysis APIs",
    "SemanticRefInput": "input type accepted where a SemanticRef value is expected",
    "SnapshotVersioning": "snapshot versioning declaration for a dataset",
    "TableSource": "physical table source (table name, optional database)",
    "TimeDimensionBrief": "brief for a time-dimension authoring step",
    "TimeDimensionDetails": "kind-specific details for a time dimension including granularity and format",
    "TimeDimensionRef": "stable reference to a declared time dimension",
    "ValidityVersioning": "validity-window versioning declaration for a dataset",
    "VerifyResult": "per-object verification result",
    "VersioningHints": "versioning strategy hints from entity inspection",
    "constraints": "authoring and validation constraints",
    "decomposition": "metric decomposition builders and aggregation boundary",
    "derived_metric": "declare a body-free canonical ratio or weighted-average metric",
    "dimension": "declare a non-aggregated dimension on an entity",
    "domain": "open a domain namespace for decorator registration",
    "entity": "declare an entity over a structured source",
    "errors": "SemanticError hierarchy and ErrorKind enum",
    "file": "file source for ms.entity(source=...)",
    "find_project": "internal — use ms.load() instead",
    "help": "this introspection entry point",
    "help_text": "return semantic help text without printing",
    "load": "load a semantic project and return a SemanticCatalog — the normal agent entrypoint",
    "metric": "declare a dataset-backed aggregate metric",
    "prepare_cross_entity_metric": "prepare a cross-entity metric brief",
    "prepare_derived_metric": "prepare a derived metric brief from component metrics",
    "prepare_dimensions": "prepare dimension briefs for one entity",
    "prepare_domain": "prepare a domain authoring brief",
    "prepare_entity": "prepare an entity authoring brief from a datasource source",
    "prepare_metric": "prepare a single-entity metric brief",
    "prepare_relationship": "prepare a relationship brief with join-key evidence",
    "prepare_time_dimension": "prepare a time-dimension brief",
    "ratio": "derived metric helper (a/b)",
    "ref": "refer to another metric by qualified name",
    "relationship": "declare a relationship between datasets",
    "snapshot": "declare snapshot versioning for a dataset",
    "sum": "sum aggregation marker",
    "table": "table source for ms.entity(source=...)",
    "time_dimension": "declare a time-aware dimension used as the calendar axis",
    "time_fold": "sampled semi-additive time folding for bandwidth-style metrics",
    "typing": "IbisBackend Protocol and AiContext TypedDict",
    "validity": "declare validity-window versioning for a dataset",
    "weighted_average": "weighted-average aggregation marker",
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
                'Call ms.help("<constraint_id>") for full rule details.',
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
            "ms.help('metric')",
            "ms.help('derived_metric')",
            "ms.help('constraints')",
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
    lines.append('Call ms.help("decomposition") for agent-readable data.')
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
            "ms.help('metric')",
            "ms.help('derived_metric')",
            "ms.help('constraints')",
        ),
    )


def _time_fold_content() -> dict[str, object]:
    return {
        "summary": "sampled semi-additive time folding for bandwidth-style metrics",
        "folds": ["mean", "min", "max", "first", "last", "('quantile', q)"],
        "rules": [
            "time_fold requires additivity='semi_additive'",
            "fold_time_dimension binds the sampled axis used for filtering, sample points, and buckets",
            "fold is a metric definition choice, not an observe parameter",
        ],
    }


def _time_fold_text(content: dict[str, object]) -> str:
    folds = cast("list[str]", content["folds"])
    rules = cast("list[str]", content["rules"])
    lines = [
        "marivo.semantic time_fold",
        "",
        str(content["summary"]),
        "",
        "Supported folds:",
    ]
    for fold in folds:
        lines.append(f"  - {fold}")
    lines.extend(("", "Rules:"))
    for rule in rules:
        lines.append(f"  - {rule}")
    return "\n".join(lines)


def _time_fold_topic() -> Descriptor:
    content = _time_fold_content()
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="time_fold",
        summary=cast("str", content["summary"]),
        content=content,
        doc=_time_fold_text(content),
        see_also=("metric", "constraints"),
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

    all_names = tuple(dict.fromkeys((*ms.__all__, "constraints", "decomposition", "time_fold")))
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
            "time_fold": _time_fold_topic(),
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
    return "\n".join(lines)


def help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    normalized = None if symbol == "" else symbol
    if normalized is None:
        return _format_top_level_text()
    return cast("str", render(_surface(), normalized, "text"))


def help(
    symbol: str | None = None,
) -> None:
    """Print bounded agent-facing help for the semantic surface and return None.

    Args:
        symbol: Symbol name, constraint id, or topic (e.g. "metric",
            "derived_metric", "decomposition", "constraints"). None prints
            the top-level surface listing.

    Returns:
        None

    Raises:
        TypeError: When called with ``format=``, ``json=``, or other
            unsupported keyword arguments.

    Example:
        >>> ms.help()
        >>> ms.help("metric")
        >>> ms.help("decomposition")
    """

    normalized = None if symbol == "" else symbol
    print(help_text(normalized))
