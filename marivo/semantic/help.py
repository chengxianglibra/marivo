"""ms.help - agent-facing introspection of the semantic surface."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

from marivo.introspection.render import format_family_block
from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import Surface, render, top_level_families
from marivo.semantic.constraints import iter_constraints

_SUMMARIES: dict[str, str] = {
    "AiContext": "agent-facing semantic metadata schema",
    "AiContextView": "read-only view of ai_context fields: business_definition, guardrails, synonyms",
    "AssessmentIssue": "a single rule-based authoring assessment issue",
    "AuthoringAssessment": "issues, questions, and status for authoring readiness",
    "AuthoringQuestion": "an unresolved business decision raised by a check",
    "BriefStatus": "stepwise brief preparation status enum",
    "ComponentFact": "metric component fact used in derived-metric composition",
    "CrossEntityMetricBrief": "brief for a cross-entity metric authoring step",
    "DatasetSource": "type alias: TableSource | FileSource",
    "DatasourceDetails": "kind-specific details for a datasource including backend type",
    "DerivedMetricBrief": "brief for a derived metric authoring step",
    "DimensionBrief": "brief for a dimension authoring step",
    "DimensionDetails": "kind-specific details for a categorical dimension",
    "DimensionRef": "stable reference to a declared dimension",
    "DimensionValueFact": "dimension value fact from evidence sampling",
    "DomainBrief": "brief for a domain authoring step",
    "DomainDetails": "kind-specific details for a domain including child entities and metrics",
    "DomainRef": "stable reference to a declared domain",
    "EntityBrief": "brief for an entity authoring step",
    "EntityDetails": "kind-specific details for an entity object",
    "EntityRef": "stable reference to a declared entity",
    "FileSource": "physical file source (ParquetSourceIR | CsvSourceIR)",
    "FormatCandidate": "parse-variant format candidate from time-dimension inspection",
    "JoinPathFact": "join path evidence fact from relationship probing",
    "MeasureBrief": "brief for a single-column measure authoring step",
    "MetricBrief": "brief for a single-entity metric authoring step",
    "MeasureDetails": "kind-specific details for a row-level quantitative measure",
    "MetricDetails": "kind-specific details for a metric object including aggregation, composition, provenance, and parity",
    "MetricRef": "stable reference to a declared metric",
    "ParitySummary": "semantic parity evidence summary",
    "PreviewSummary": "raw preview evidence summary",
    "PrimaryKeyCandidate": "candidate primary key from entity inspection",
    "ReadinessInputSummary": "semantic readiness closeout input summary",
    "ReadinessIssue": "semantic readiness issue",
    "ReadinessReport": "semantic readiness report",
    "RegisteredMatch": "explainable registered-object reuse fact",
    "RelationshipBrief": "brief for a relationship authoring step",
    "RelationshipDetails": "kind-specific details for a relationship between entities",
    "RelationshipRef": "stable reference to a declared relationship",
    "RichnessSummary": "semantic readiness richness gap summary",
    "SemanticCatalog": "read-only object graph over a loaded semantic project — returned by ms.load()",
    "SemanticKind": "semantic kind enum: domain, datasource, entity, dimension, measure, time_dimension, metric, relationship",
    "SemanticKindInput": "input type accepted where a SemanticKind value is expected",
    "SemanticObject": "unified read shape for all loaded semantic objects",
    "SemanticObjectDetails": "union of kind-specific detail shapes for a SemanticObject",
    "SemanticObjectList": "browsing result from catalog.list() — has .show(), .refs(), .objects",
    "SemanticRef": "stable semantic identifier passable directly to analysis APIs",
    "SemanticRefInput": "input type accepted where a SemanticRef value is expected",
    "SnapshotVersioning": "snapshot versioning declaration for an entity",
    "TableSource": "physical table source (table name, optional database)",
    "TimeDimensionBrief": "brief for a time-dimension authoring step",
    "TimeDimensionDetails": "kind-specific details for a time dimension including parse variant, granularity, timezone, and sampling",
    "TimeDimensionRef": "stable reference to a declared time dimension",
    "ValidityVersioning": "validity-window versioning declaration for an entity",
    "VerifyResult": "per-object verification result",
    "VersioningHints": "versioning strategy hints from entity inspection",
    "additivity": "metric summability: additive / non_additive / semi_additive(over, fold)",
    "composition": "derived-metric composition kinds (ratio/weighted_average/linear); distinct from the decompose analysis op",
    "constraints": "authoring and validation constraints",
    "derived_metric": "declare a body-free canonical ratio or weighted-average metric",
    "dimension": "declare a non-aggregated dimension on an entity",
    "from_sql": "declare SQL parity provenance for a metric body",
    "join_on": "build a relationship key pair for ms.relationship(keys=[...])",
    "measure": "declare a row-level quantitative measure on an entity for later aggregation",
    "domain": "open a domain namespace for decorator registration",
    "entity": "declare an entity over a structured source",
    "errors": "SemanticError hierarchy and ErrorKind enum",
    "help": "this introspection entry point",
    "help_text": "return semantic help text without printing",
    "load": "load a semantic project and return a SemanticCatalog — accepts models to filter domains",
    "metric": "declare an aggregate metric from a measure or an ibis body",
    "parquet": "parquet file source for ms.entity(source=ms.parquet(...))",
    "csv": "csv file source for ms.entity(source=ms.csv(...))",
    "prepare_cross_entity_metric": "prepare a cross-entity metric brief",
    "prepare_derived_metric": "prepare a derived metric brief from component metrics",
    "prepare_dimension": "prepare a dimension brief for one entity column",
    "prepare_domain": "prepare a domain authoring brief",
    "prepare_entity": "prepare an entity authoring brief from a datasource source",
    "prepare_metric": "prepare a single-entity metric brief",
    "prepare_relationship": "prepare a relationship brief with join-key evidence",
    "prepare_time_dimension": "prepare a time-dimension brief",
    "ratio": "derived metric helper (a/b)",
    "readiness": "run structural readiness check for semantic refs",
    "ref": "refer to another metric by qualified name",
    "relationship": "declare a relationship between entities",
    "snapshot": "declare snapshot versioning for an entity",
    "sum": "sum aggregation marker",
    "table": "table source for ms.entity(source=...)",
    "time_dimension": "declare a time-aware dimension used as the calendar axis",
    "typing": "IbisBackend Protocol and AiContext TypedDict",
    "validity": "declare validity-window versioning for an entity",
    "verify_object": "verify a single authored semantic object is reachable and valid",
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


def _composition_content() -> dict[str, object]:
    return {
        "summary": (
            "Derived-metric composition declares how a metric value is built from "
            "other metrics. Distinct from the decompose analysis op that attributes "
            "a delta."
        ),
        "examples": [
            {
                "metric_shape": "ratio",
                "constructor": "ms.ratio(name=..., numerator=..., denominator=...)",
            },
            {
                "metric_shape": "weighted average",
                "constructor": "ms.weighted_average(name=..., value=..., weight=...)",
            },
            {
                "metric_shape": "linear (a +/- b)",
                "constructor": "ms.linear(name=..., add=[...], subtract=[...])",
            },
        ],
        "boundary": "composition = how a metric is built; decompose = an analysis op that attributes a delta.",
        "related_help": [
            "ms.help('metric')",
            "ms.help('derived_metric')",
            "ms.help('additivity')",
            "ms.help('constraints')",
        ],
    }


def _composition_text(content: dict[str, object]) -> str:
    examples = cast("list[dict[str, object]]", content["examples"])
    lines = [
        "marivo.semantic composition",
        "",
        str(content["summary"]),
        "",
        "Composition kinds:",
    ]
    for ex in examples:
        lines.append(f"  - {ex['metric_shape']}: {ex['constructor']}")
    lines.extend(("", "Boundary:"))
    lines.append(f"  {content['boundary']}")
    lines.append("")
    lines.append('Call ms.help("composition") for agent-readable data.')
    return "\n".join(lines)


def _composition_topic() -> Descriptor:
    content = _composition_content()
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="composition",
        summary=cast("str", content["summary"]),
        content=content,
        doc=_composition_text(content),
        see_also=(
            "ms.help('metric')",
            "ms.help('derived_metric')",
            "ms.help('additivity')",
            "ms.help('constraints')",
        ),
    )


def _metric_content() -> dict[str, object]:
    return {
        "summary": (
            "declare an aggregate metric either with ms.aggregate(name=..., measure=..., agg=...) "
            "or with @ms.metric(entities=..., additivity=..., provenance=ms.from_sql(...) optional)"
        ),
        "tier1": "ms.aggregate(name=..., measure=<measure_ref>, agg='sum'|'count'|'mean'|'min'|'max')",
        "tier2": "@ms.metric(entities=[...], additivity='additive'|'non_additive'|ms.semi_additive(over, fold))",
        "body_rule": "No body for tier-1 (call-form); body required for tier-2 (decorator-form).",
        "related_help": [
            "ms.help('composition')",
            "ms.help('additivity')",
            "ms.help('derived_metric')",
            "ms.help('measure')",
            "ms.help('from_sql')",
        ],
    }


def _metric_text(content: dict[str, object]) -> str:
    lines = [
        "marivo.semantic metric",
        "",
        str(content["summary"]),
        "",
        "Tier-1 (call-form, no body):",
        f"  {content['tier1']}",
        "",
        "Tier-2 (decorator-form, body required):",
        f"  {content['tier2']}",
        "",
        str(content["body_rule"]),
    ]
    return "\n".join(lines)


def _metric_topic() -> Descriptor:
    content = _metric_content()
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="metric",
        summary=cast("str", content["summary"]),
        content=content,
        doc=_metric_text(content),
        see_also=(
            "ms.help('composition')",
            "ms.help('additivity')",
            "ms.help('derived_metric')",
            "ms.help('measure')",
            "ms.help('from_sql')",
        ),
    )


def _additivity_content() -> dict[str, object]:
    return {
        "summary": (
            "Metric summability: additive, non_additive, or semi_additive. "
            "Semi-additive metrics fold along a time axis via ms.semi_additive(over, fold)."
        ),
        "buckets": [
            {
                "kind": "additive",
                "use": "Fully summable across all dimensions (e.g. revenue).",
            },
            {
                "kind": "non_additive",
                "use": "Not summable (e.g. ratio, rate). Derived metrics are typically non_additive.",
            },
            {
                "kind": "semi_additive",
                "use": "Summable except along a status time axis; requires fold and over.",
            },
        ],
        "semi_additive_form": "ms.semi_additive(over=<time_dimension_ref>, fold='last'|'first'|'mean'|'min'|'max')",
        "rules": [
            "semi_additive requires over to be a declared time dimension",
            "fold is a metric definition choice, not an observe parameter",
            "non-sampled semi_additive metrics omit fold but still declare over",
        ],
        "related_help": [
            "ms.help('metric')",
            "ms.help('composition')",
            "ms.help('constraints')",
        ],
    }


def _additivity_text(content: dict[str, object]) -> str:
    buckets = cast("list[dict[str, object]]", content["buckets"])
    rules = cast("list[str]", content["rules"])
    lines = [
        "marivo.semantic additivity",
        "",
        str(content["summary"]),
        "",
        "Buckets:",
    ]
    for bucket in buckets:
        lines.append(f"  - {bucket['kind']}: {bucket['use']}")
    lines.extend(("", "Semi-additive form:"))
    lines.append(f"  {content['semi_additive_form']}")
    lines.extend(("", "Rules:"))
    for rule in rules:
        lines.append(f"  - {rule}")
    return "\n".join(lines)


def _additivity_topic() -> Descriptor:
    content = _additivity_content()
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="additivity",
        summary=cast("str", content["summary"]),
        content=content,
        doc=_additivity_text(content),
        see_also=(
            "ms.help('metric')",
            "ms.help('composition')",
            "ms.help('constraints')",
        ),
    )


def _measure_topic() -> Descriptor:
    summary = "declare a row-level quantitative measure on an entity"
    content = {
        "summary": summary,
        "authoring": "@ms.measure(entity=<entity_ref>, additivity='additive'|'non_additive'|ms.semi_additive(...), unit=None)",
        "aggregation": "Use ms.aggregate(name=..., measure=<measure_ref>, agg='sum'|'count'|'mean'|'min'|'max') to turn a measure into a metric.",
        "boundary": "Measures are not group-by axes or filters. Slice by dimensions; aggregate measures into metrics.",
        "related_help": [
            "ms.help('metric')",
            "ms.help('additivity')",
            "ms.help('dimension')",
        ],
    }
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="measure",
        summary=summary,
        content=content,
        doc="\n".join(
            (
                "marivo.semantic measure",
                "",
                summary,
                "",
                f"Authoring: {content['authoring']}",
                f"Aggregation: {content['aggregation']}",
                "",
                f"Boundary: {content['boundary']}",
            )
        ),
        see_also=tuple(content["related_help"]),
    )


def _from_sql_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="from_sql",
        summary="declare SQL parity provenance for a metric body",
        content={
            "form": "ms.from_sql(sql='SELECT ...', dialect='duckdb')",
            "usage": "Pass as provenance= kwarg to @ms.metric for SQL parity verification.",
            "related_help": [
                "ms.help('metric')",
            ],
        },
        doc=(
            "marivo.semantic from_sql\n"
            "\n"
            "declare SQL parity provenance for a metric body\n"
            "\n"
            "Form:\n"
            "  ms.from_sql(sql='SELECT ...', dialect='duckdb')\n"
            "\n"
            "Pass as provenance= kwarg to @ms.metric for SQL parity verification."
        ),
        see_also=("ms.help('metric')",),
    )


def _join_on_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="join_on",
        summary="build a relationship key pair for ms.relationship(keys=[...])",
        content={
            "form": "ms.join_on(<from_dimension_ref>, <to_dimension_ref>)",
            "usage": "Each join_on call creates a (from_key, to_key) pair for relationship keys.",
            "related_help": [
                "ms.help('relationship')",
            ],
        },
        doc=(
            "marivo.semantic join_on\n"
            "\n"
            "build a relationship key pair for ms.relationship(keys=[...])\n"
            "\n"
            "Form:\n"
            "  ms.join_on(<from_dimension_ref>, <to_dimension_ref>)\n"
            "\n"
            "Each join_on call creates a (from_key, to_key) pair for relationship keys."
        ),
        see_also=("ms.help('relationship')",),
    )


def _parquet_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="parquet",
        summary="parquet file source for ms.entity(source=ms.parquet(...))",
        content={
            "form": "ms.parquet(path, hive_partitioning=False, columns=None)",
            "usage": "Declares a Parquet file source for an entity.",
            "related_help": [
                "ms.help('entity')",
                "ms.help('csv')",
            ],
        },
        doc=(
            "marivo.semantic parquet\n"
            "\n"
            "parquet file source for ms.entity(source=ms.parquet(...))\n"
            "\n"
            "Form:\n"
            "  ms.parquet(path, hive_partitioning=False, columns=None)"
        ),
        see_also=("ms.help('entity')", "ms.help('csv')"),
    )


def _csv_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="csv",
        summary="csv file source for ms.entity(source=ms.csv(...))",
        content={
            "form": "ms.csv(path, header=True, delimiter=',', columns=None)",
            "usage": "Declares a CSV file source for an entity.",
            "related_help": [
                "ms.help('entity')",
                "ms.help('parquet')",
            ],
        },
        doc=(
            "marivo.semantic csv\n"
            "\n"
            "csv file source for ms.entity(source=ms.csv(...))\n"
            "\n"
            "Form:\n"
            "  ms.csv(path, header=True, delimiter=',', columns=None)"
        ),
        see_also=("ms.help('entity')", "ms.help('parquet')"),
    )


def _date_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="date",
        summary="date-only parse variant for ms.time_dimension(parse=ms.date())",
        content={
            "form": "ms.date()",
            "usage": "No timezone or format arguments — date-only columns.",
            "related_help": [
                "ms.help('time_dimension')",
            ],
        },
        doc=(
            "marivo.semantic date\n"
            "\n"
            "date-only parse variant for ms.time_dimension(parse=ms.date())\n"
            "\n"
            "Form:\n"
            "  ms.date()"
        ),
        see_also=("ms.help('time_dimension')",),
    )


def _datetime_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="datetime",
        summary="datetime parse variant with required timezone for ms.time_dimension(parse=ms.datetime(tz))",
        content={
            "form": "ms.datetime(timezone='UTC')",
            "usage": "Requires timezone argument for datetime columns with zone info.",
            "related_help": [
                "ms.help('time_dimension')",
            ],
        },
        doc=(
            "marivo.semantic datetime\n"
            "\n"
            "datetime parse variant with required timezone for ms.time_dimension(parse=ms.datetime(tz))\n"
            "\n"
            "Form:\n"
            "  ms.datetime(timezone='UTC')"
        ),
        see_also=("ms.help('time_dimension')",),
    )


def _timestamp_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="timestamp",
        summary="timestamp parse variant with timezone and sample interval",
        content={
            "form": "ms.timestamp(timezone='UTC', sample_interval=None)",
            "usage": "For sub-day timestamp columns. Requires timezone; sample_interval is optional.",
            "related_help": [
                "ms.help('time_dimension')",
            ],
        },
        doc=(
            "marivo.semantic timestamp\n"
            "\n"
            "timestamp parse variant with timezone and sample interval\n"
            "\n"
            "Form:\n"
            "  ms.timestamp(timezone='UTC', sample_interval=None)"
        ),
        see_also=("ms.help('time_dimension')",),
    )


def _strptime_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="strptime",
        summary="strptime parse variant with format, data_type, and optional sample interval",
        content={
            "form": "ms.strptime(format='%Y%m%d', data_type='string', sample_interval=None)",
            "usage": "For string/integer columns needing explicit format parsing. sample_interval is optional for sampled time dimensions.",
            "related_help": [
                "ms.help('time_dimension')",
            ],
        },
        doc=(
            "marivo.semantic strptime\n"
            "\n"
            "strptime parse variant with format, data_type, and optional sample interval\n"
            "\n"
            "Form:\n"
            "  ms.strptime(format='%Y%m%d', data_type='string', sample_interval=None)"
        ),
        see_also=("ms.help('time_dimension')",),
    )


def _hour_prefix_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="hour_prefix",
        summary="hour-prefix parse variant for partitioned hourly time dimensions",
        content={
            "form": "ms.hour_prefix(prefix='dt', data_type='string')",
            "usage": "For hour-granularity partitioned columns. Requires hour granularity on the time dimension.",
            "related_help": [
                "ms.help('time_dimension')",
            ],
        },
        doc=(
            "marivo.semantic hour_prefix\n"
            "\n"
            "hour-prefix parse variant for partitioned hourly time dimensions\n"
            "\n"
            "Form:\n"
            "  ms.hour_prefix(prefix='dt', data_type='string')"
        ),
        see_also=("ms.help('time_dimension')",),
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

    all_names = tuple(
        dict.fromkeys(
            (
                *ms.__all__,
                "constraints",
                "composition",
                "metric",
                "measure",
                "from_sql",
                "join_on",
                "parquet",
                "csv",
                "date",
                "datetime",
                "timestamp",
                "strptime",
                "hour_prefix",
                "additivity",
            )
        )
    )
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
            "composition": _composition_topic(),
            "metric": _metric_topic(),
            "measure": _measure_topic(),
            "from_sql": _from_sql_topic(),
            "join_on": _join_on_topic(),
            "parquet": _parquet_topic(),
            "csv": _csv_topic(),
            "date": _date_topic(),
            "datetime": _datetime_topic(),
            "timestamp": _timestamp_topic(),
            "strptime": _strptime_topic(),
            "hour_prefix": _hour_prefix_topic(),
            "additivity": _additivity_topic(),
        },
        pinned_entries=("SemanticCatalog", "SemanticObject", "SemanticObjectList"),
    )


def _format_top_level_text() -> str:
    data = cast("dict[str, object]", render(_surface(), None, "json"))
    entries = cast("list[dict[str, str]]", data["entries"])
    lines = ["marivo.semantic - top-level entries:", ""]
    for entry in entries:
        lines.append(f"  ms.{entry['name']:<18} [{entry['kind']}]  {entry['summary']}")
    lines.extend(format_family_block(top_level_families(_surface()), help_call="ms.help"))
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
            "derived_metric", "composition", "constraints"). None prints
            the top-level surface listing.

    Returns:
        None

    Raises:
        TypeError: When called with ``format=``, ``json=``, or other
            unsupported keyword arguments.

    Example:
        >>> ms.help()
        >>> ms.help("metric")
        >>> ms.help("composition")
    """

    normalized = None if symbol == "" else symbol
    print(help_text(normalized))
