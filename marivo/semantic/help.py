"""ms.help - agent-facing introspection of the semantic surface."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

from marivo.introspection.render import format_family_block
from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import Surface, derive_summaries, render, top_level_families
from marivo.semantic.constraints import iter_constraints


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
            "Declare how a derived metric value is built from other metrics (ratio, weighted_average, linear)."
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
        "summary": ("Declare metrics via ms.aggregate (tier-1) or @ms.metric decorator (tier-2)."),
        "default_path": (
            "Default to prepare_measure -> ms.measure_column -> verify_object(measure) "
            "-> ms.aggregate -> verify_object(metric)."
        ),
        "tier1": (
            "recommended default: ms.aggregate(name=..., measure=<verified_measure_ref>, "
            "agg='sum'|'mean'|'min'|'max'); use ms.count(name=..., entity=<entity_ref>) "
            "for entity row counts"
        ),
        "tier2": (
            "escape hatch: @ms.metric(entities=[...], "
            "additivity='additive'|'non_additive'|ms.semi_additive(over, fold), "
            "provenance=ms.from_sql(...) optional)"
        ),
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
        "Default path:",
        f"  {content['default_path']}",
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
            "Metric summability: additive, non_additive, or semi_additive "
            "(folded along a time axis via ms.semi_additive)."
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
        "semi_additive_form": "ms.semi_additive(over=<TimeDimensionRef>, fold='last'|'first'|'mean'|'min'|'max')",
        "rules": [
            "semi_additive requires over to be a declared @ms.time_dimension(...) ref",
            "fold is a metric definition choice, not an observe parameter",
            "non-sampled semi_additive metrics still declare over and fold, typically fold='last' or fold='first'",
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
    summary = "Declare a row-level quantitative measure on an entity."
    content = {
        "summary": summary,
        "authoring": (
            'Use ms.measure_column(name="amount", entity=orders, '
            'column="amount", additivity="additive", unit="CNY") for direct '
            "physical columns; escape hatch: @ms.measure(...) for expression bodies."
        ),
        "aggregation": "Use ms.aggregate(name='total_amount', measure=amount, agg='sum') to turn a measure into a metric; use ms.count(...) for entity row counts.",
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
        summary="Declare SQL parity provenance for a metric body.",
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
        summary="Build a relationship key pair for ms.relationship(keys=[...]).",
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
        summary="Parquet file source for ms.entity(source=ms.parquet(...)).",
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
        summary="CSV file source for ms.entity(source=ms.csv(...)).",
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


def _datetime_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="datetime",
        summary="Datetime parse variant with optional timezone for ms.time_dimension(parse=ms.datetime(...)).",
        content={
            "form": "ms.datetime(timezone=None, sample_interval=None)",
            "usage": "For datetime columns. timezone is optional; omitted means datasource engine timezone.",
            "related_help": [
                "ms.help('time_dimension')",
            ],
        },
        doc=(
            "marivo.semantic datetime\n"
            "\n"
            "datetime parse variant with optional timezone for ms.time_dimension(parse=ms.datetime(...))\n"
            "\n"
            "Form:\n"
            "  ms.datetime(timezone=None, sample_interval=None)"
        ),
        see_also=("ms.help('time_dimension')",),
    )


def _timestamp_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="timestamp",
        summary="Timestamp parse variant with optional timezone and sample interval.",
        content={
            "form": "ms.timestamp(timezone=None, sample_interval=None)",
            "usage": "For timestamp columns. timezone is optional; omitted means datasource engine timezone.",
            "related_help": [
                "ms.help('time_dimension')",
            ],
        },
        doc=(
            "marivo.semantic timestamp\n"
            "\n"
            "timestamp parse variant with optional timezone and sample interval\n"
            "\n"
            "Form:\n"
            "  ms.timestamp(timezone=None, sample_interval=None)"
        ),
        see_also=("ms.help('time_dimension')",),
    )


def _strptime_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="strptime",
        summary="Strptime parse variant with format and optional sample interval.",
        content={
            "form": "ms.strptime(format='%Y%m%d', sample_interval=None)",
            "usage": "For string/integer columns needing explicit format parsing. The physical column type (string or integer) is inferred at analysis time. sample_interval is optional for sampled time dimensions.",
            "related_help": [
                "ms.help('time_dimension')",
            ],
        },
        doc=(
            "marivo.semantic strptime\n"
            "\n"
            "strptime parse variant with format and optional sample interval\n"
            "\n"
            "Form:\n"
            "  ms.strptime(format='%Y%m%d', sample_interval=None)"
        ),
        see_also=("ms.help('time_dimension')",),
    )


def _hour_prefix_topic() -> Descriptor:
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol="hour_prefix",
        summary="Hour-prefix parse variant for partitioned hourly time dimensions.",
        content={
            "form": "ms.hour_prefix(prefix='dt')",
            "usage": "For hour-granularity partitioned columns. The physical column type (string or integer) is inferred at analysis time. Requires hour granularity on the time dimension. Optional sample_interval=(count, unit) enables sampled-fold axis.",
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
            "  ms.hour_prefix(prefix='dt')\n"
            "  ms.hour_prefix(prefix='dt', sample_interval=(1, 'hour'))"
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
                "datetime",
                "timestamp",
                "strptime",
                "hour_prefix",
                "additivity",
            )
        )
    )
    topics = {
        "constraints": _constraint_topic(),
        "composition": _composition_topic(),
        "metric": _metric_topic(),
        "measure": _measure_topic(),
        "from_sql": _from_sql_topic(),
        "join_on": _join_on_topic(),
        "parquet": _parquet_topic(),
        "csv": _csv_topic(),
        "datetime": _datetime_topic(),
        "timestamp": _timestamp_topic(),
        "strptime": _strptime_topic(),
        "hour_prefix": _hour_prefix_topic(),
        "additivity": _additivity_topic(),
    }
    summaries = derive_summaries(
        all_names,
        _resolve,
        topics,
        overrides={"BriefStatus": "brief status: sufficient | needs_input | blocked"},
    )
    catalog = {constraint.id: constraint for constraint in iter_constraints()}
    return Surface(
        name="marivo.semantic",
        all_names=all_names,
        summaries=summaries,
        resolve=_resolve,
        catalog=catalog,
        topics=topics,
        pinned_entries=("SemanticCatalog", "SemanticObject", "SemanticObjectList"),
        hidden_names=frozenset({"SemanticKindInput", "SemanticRefInput"}),
        family_suffixes=(("Report", "Reports"), ("Result", "Results")),
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
