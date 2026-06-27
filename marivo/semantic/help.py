"""ms.help - agent-facing introspection of the semantic surface."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any, cast

from marivo.introspection.constraints import Constraint
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
    lines.extend(
        (
            "",
            "Valid string values: 'additive', 'non_additive', 'semi_additive' (use underscores, not hyphens).",
        )
    )
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


_GRANULARITY_VALUES = ["year", "quarter", "month", "week", "day", "hour", "minute", "second"]


def _param(
    type_: str,
    meaning: str,
    *,
    default: str | None = None,
    allowed_values: list[str] | None = None,
) -> dict[str, object]:
    out: dict[str, object] = {"type": type_, "meaning": meaning}
    if default is not None:
        out["default"] = default
    if allowed_values is not None:
        out["allowed_values"] = allowed_values
    return out


def _time_parse_contract() -> dict[str, object]:
    return {
        "native_date": {
            "form": "omit parse",
            "when": "physical column is a native date type",
            "notes": "Use no parse metadata unless project policy requires explicit metadata.",
        },
        "native_datetime": {
            "form": "ms.datetime(timezone=None, sample_interval=None)",
            "when": "physical column is a native datetime type",
            "parameters": {
                "timezone": "optional IANA timezone string; omit when engine/project timezone policy applies",
                "sample_interval": "optional (count, unit) for sampled time axes",
            },
        },
        "native_timestamp": {
            "form": "ms.timestamp(timezone=None, sample_interval=None)",
            "when": "physical column is a native timestamp type",
            "parameters": {
                "timezone": "optional IANA timezone string; omit when engine/project timezone policy applies",
                "sample_interval": "optional (count, unit) for sampled time axes",
            },
        },
        "string_or_integer_date_like": {
            "form": "ms.strptime(format, timezone=None, sample_interval=None)",
            "when": "physical column is string or integer encoded time",
            "parameters": {
                "format": "required strptime format observed or confirmed for the physical encoding",
                "timezone": "optional IANA timezone string for sub-day parsed values",
                "sample_interval": "optional (count, unit) for sampled time axes",
            },
        },
        "hour_only": {
            "form": "ms.hour_prefix(prefix, sample_interval=None)",
            "when": "physical column stores an hour bucket with a stable date prefix",
            "parameters": {
                "prefix": "required date prefix that gives hour values date context",
                "sample_interval": "optional (count, unit) for sampled time axes",
            },
        },
    }


def _additivity_contract() -> dict[str, object]:
    return {
        "allowed_values": ["additive", "non_additive", "ms.semi_additive(...)"],
        "semi_additive": {
            "form": "ms.semi_additive(over=<TimeDimensionRef>, fold='last'|'first'|'mean'|'min'|'max')",
            "when": "measure or metric is additive except along one time axis",
        },
    }


def _parse_constructor_contracts() -> dict[str, dict[str, object]]:
    sample_interval = _param(
        "tuple[int, str] | None",
        "optional sampling cadence for sampled time axes",
        default="None",
    )
    timezone = _param(
        "str | None",
        "optional IANA timezone string; omit when engine/project timezone policy applies",
        default="None",
    )
    return {
        "datetime": {
            "summary": "Declare parse metadata for a native datetime time column.",
            "constructor": "ms.datetime",
            "required": [],
            "optional": ["timezone", "sample_interval"],
            "discover": "md.discover_time_dimensions",
            "parameters": {"timezone": timezone, "sample_interval": sample_interval},
            "static_constraints": [
                "only use as the parse value of a time dimension",
                "omit timezone when engine/project timezone policy applies",
            ],
        },
        "timestamp": {
            "summary": "Declare parse metadata for a native timestamp time column.",
            "constructor": "ms.timestamp",
            "required": [],
            "optional": ["timezone", "sample_interval"],
            "discover": "md.discover_time_dimensions",
            "parameters": {"timezone": timezone, "sample_interval": sample_interval},
            "static_constraints": [
                "only use as the parse value of a time dimension",
                "omit timezone when engine/project timezone policy applies",
            ],
        },
        "strptime": {
            "summary": "Declare parse metadata for string or integer encoded time columns.",
            "constructor": "ms.strptime",
            "required": ["format"],
            "optional": ["timezone", "sample_interval"],
            "discover": "md.discover_time_dimensions",
            "parameters": {
                "format": _param("str", "Python strptime-compatible physical encoding format"),
                "timezone": timezone,
                "sample_interval": sample_interval,
            },
            "static_constraints": [
                "format is required",
                "format must be accepted by ms.strptime(...) validation",
                "date-only parsed values must omit timezone",
                "only use as the parse value of a time dimension",
            ],
        },
        "hour_prefix": {
            "summary": "Declare parse metadata for hour-only buckets with a stable date prefix.",
            "constructor": "ms.hour_prefix",
            "required": ["prefix"],
            "optional": ["sample_interval"],
            "discover": "md.discover_time_dimensions",
            "parameters": {
                "prefix": _param("str", "date prefix that gives hour values date context"),
                "sample_interval": sample_interval,
            },
            "static_constraints": [
                "only valid with time_dimension granularity='hour'",
                "only use as the parse value of a time dimension",
            ],
        },
    }


def _authoring_contracts() -> dict[str, dict[str, object]]:
    name = _param("str", "semantic object name")
    domain = _param("DomainRef | None", "override the active authoring domain", default="None")
    ai_context = _param(
        "AiContextValue | None", "business meaning and agent-facing guidance", default="None"
    )
    entity_ref = _param("EntityRef", "entity ref returned by ms.entity(...)")
    entity_ref_or_str = _param("EntityRef | str", "owning entity")
    column = _param("str", "physical source column name")
    function_body = _param("Callable", "decorated function body returning one supported expression")
    unit = _param("str | None", "UCUM unit token such as 'USD', 'CNY', '%', or '1'", default="None")
    additivity = _param(
        "Additivity",
        "additive, non_additive, or ms.semi_additive(...) policy",
        allowed_values=["additive", "non_additive", "ms.semi_additive(...)"],
    )

    return {
        "domain": {
            "summary": "Declare a semantic domain namespace.",
            "constructor": "ms.domain",
            "required": ["name"],
            "optional": ["ai_context"],
            "discover": None,
            "parameters": {"name": name, "ai_context": ai_context},
            "static_constraints": ["name must be unique within the project"],
        },
        "entity": {
            "summary": "Declare an entity over one structured physical source.",
            "constructor": "ms.entity",
            "required": ["name", "datasource", "source"],
            "optional": ["primary_key", "versioning", "domain", "ai_context"],
            "discover": "md.discover_entity",
            "parameters": {
                "name": name,
                "datasource": _param(
                    "DatasourceRef | str", "datasource ref or declared datasource name"
                ),
                "source": _param(
                    "TableSourceIR | ParquetSourceIR | CsvSourceIR", "structured physical source"
                ),
                "primary_key": _param(
                    "list[str] | None", "authoritative primary-key columns", default="None"
                ),
                "versioning": _param(
                    "SnapshotVersioningIR | ValidityVersioningIR | None",
                    "optional versioning policy",
                    default="None",
                ),
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": ["source must be ms.table(...), ms.parquet(...), or ms.csv(...)"],
        },
        "dimension_column": {
            "summary": "Declare a categorical dimension directly from one physical column.",
            "constructor": "ms.dimension_column",
            "required": ["name", "entity", "column"],
            "optional": ["domain", "ai_context"],
            "discover": "md.discover_dimensions",
            "parameters": {
                "name": name,
                "entity": entity_ref,
                "column": column,
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": [
                "entity must be an EntityRef",
                "domain must match the entity domain",
            ],
        },
        "dimension": {
            "summary": "Declare a categorical dimension with an expression body.",
            "constructor": "@ms.dimension",
            "required": ["entity", "function_body"],
            "optional": ["name", "domain", "ai_context"],
            "discover": "md.discover_dimensions",
            "parameters": {
                "entity": entity_ref_or_str,
                "function_body": function_body,
                "name": _param(
                    "str | None", "defaults to the decorated function name", default="None"
                ),
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": ["body must return one supported dimension expression"],
        },
        "time_dimension_column": {
            "summary": "Declare a time dimension directly from one physical column.",
            "constructor": "ms.time_dimension_column",
            "required": ["name", "entity", "column", "granularity"],
            "optional": ["parse", "is_default", "domain", "ai_context"],
            "discover": "md.discover_time_dimensions",
            "parameters": {
                "name": name,
                "entity": entity_ref,
                "column": column,
                "granularity": _param(
                    "Literal",
                    "finest grain at which queries are meaningful",
                    allowed_values=_GRANULARITY_VALUES,
                ),
                "parse": _param(
                    "SemanticParse | None", "optional physical encoding metadata", default="None"
                ),
                "is_default": _param(
                    "bool", "whether this is the default time axis for the entity", default="False"
                ),
                "domain": domain,
                "ai_context": ai_context,
            },
            "parse": _time_parse_contract(),
            "static_constraints": [
                "ms.hour_prefix(...) requires granularity='hour'",
                "sub-day date-only parses are invalid",
                "sample_interval unit cannot be coarser than the declared granularity",
                "entity must be an EntityRef",
                "domain must match the entity domain",
            ],
        },
        "time_dimension": {
            "summary": "Declare a time dimension with an expression body.",
            "constructor": "@ms.time_dimension",
            "required": ["entity", "granularity", "function_body"],
            "optional": ["name", "parse", "is_default", "domain", "ai_context"],
            "discover": "md.discover_time_dimensions",
            "parameters": {
                "entity": entity_ref_or_str,
                "granularity": _param(
                    "Literal",
                    "finest grain at which queries are meaningful",
                    allowed_values=_GRANULARITY_VALUES,
                ),
                "function_body": function_body,
                "name": _param(
                    "str | None", "defaults to the decorated function name", default="None"
                ),
                "parse": _param(
                    "SemanticParse | None", "optional physical encoding metadata", default="None"
                ),
                "is_default": _param(
                    "bool", "whether this is the default time axis for the entity", default="False"
                ),
                "domain": domain,
                "ai_context": ai_context,
            },
            "parse": _time_parse_contract(),
            "static_constraints": [
                "body must return one supported time expression",
                "ms.hour_prefix(...) requires granularity='hour'",
                "sub-day date-only parses are invalid",
            ],
        },
        "measure_column": {
            "summary": "Declare a row-level quantitative measure directly from one physical column.",
            "constructor": "ms.measure_column",
            "required": ["name", "entity", "column", "additivity"],
            "optional": ["unit", "domain", "ai_context"],
            "discover": "md.discover_measures",
            "parameters": {
                "name": name,
                "entity": entity_ref,
                "column": column,
                "additivity": additivity,
                "unit": unit,
                "domain": domain,
                "ai_context": ai_context,
            },
            "additivity": _additivity_contract(),
            "static_constraints": [
                "entity must be an EntityRef",
                "domain must match the entity domain",
                "unit must be a valid UCUM token when provided",
            ],
        },
        "measure": {
            "summary": "Declare a row-level quantitative measure with an expression body.",
            "constructor": "@ms.measure",
            "required": ["entity", "additivity", "function_body"],
            "optional": ["name", "unit", "domain", "ai_context"],
            "discover": "md.discover_measures",
            "parameters": {
                "entity": entity_ref_or_str,
                "additivity": additivity,
                "function_body": function_body,
                "name": _param(
                    "str | None", "defaults to the decorated function name", default="None"
                ),
                "unit": unit,
                "domain": domain,
                "ai_context": ai_context,
            },
            "additivity": _additivity_contract(),
            "static_constraints": [
                "body must return one supported measure expression",
                "unit must be a valid UCUM token when provided",
            ],
            "authoring_guidance": (
                "Default to ms.measure_column(name=..., entity=<entity_ref>, column='...', "
                "additivity='additive'|'non_additive'|ms.semi_additive(over, fold), unit=...) "
                "for direct physical columns; @ms.measure(...) is the escape hatch for "
                "expression bodies. Aggregate a measure into a metric with ms.aggregate(...); "
                "use ms.count(...) for entity row counts."
            ),
        },
        "aggregate": {
            "summary": "Declare a tier-1 aggregate metric over a verified measure.",
            "constructor": "ms.aggregate",
            "required": ["name", "measure", "agg"],
            "optional": ["domain", "ai_context"],
            "discover": None,
            "parameters": {
                "name": name,
                "measure": _param("MeasureRef", "verified measure ref"),
                "agg": _param(
                    "str", "aggregation function", allowed_values=["sum", "mean", "min", "max"]
                ),
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": ["measure must be a MeasureRef"],
        },
        "count": {
            "summary": "Declare a tier-1 row-count metric over an entity.",
            "constructor": "ms.count",
            "required": ["name", "entity"],
            "optional": ["domain", "ai_context"],
            "discover": None,
            "parameters": {
                "name": name,
                "entity": entity_ref,
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": ["entity must be an EntityRef"],
        },
        "metric": {
            "summary": "Choose the correct metric constructor before authoring a metric.",
            "constructor": "metric family",
            "required": [],
            "optional": [],
            "discover": "md.discover_relationship for cross-entity viability when multiple entities are involved",
            "parameters": {},
            "decision_order": [
                "count",
                "aggregate",
                "ratio",
                "weighted_average",
                "linear",
                "expression",
            ],
            "variants": {
                "count": {
                    "when": "metric is row count over one entity",
                    "constructor": "ms.count",
                    "required": ["name", "entity"],
                    "optional": ["domain", "ai_context"],
                },
                "aggregate": {
                    "when": "metric is a simple aggregation over one verified measure",
                    "constructor": "ms.aggregate",
                    "required": ["name", "measure", "agg"],
                    "optional": ["domain", "ai_context"],
                },
                "ratio": {
                    "when": "metric divides one existing metric by another",
                    "constructor": "ms.ratio",
                    "required": ["name", "numerator", "denominator"],
                    "optional": ["unit", "domain", "ai_context"],
                },
                "weighted_average": {
                    "when": "metric is weighted average from an existing value metric and weight metric",
                    "constructor": "ms.weighted_average",
                    "required": ["name", "value", "weight"],
                    "optional": ["unit", "domain", "ai_context"],
                },
                "linear": {
                    "when": "metric adds and/or subtracts existing metrics",
                    "constructor": "ms.linear",
                    "required": ["name"],
                    "optional": ["add", "subtract", "unit", "domain", "ai_context"],
                },
                "expression": {
                    "when": "metric needs an expression body over one or more entities, measures, or metrics",
                    "constructor": "@ms.metric",
                    "required": ["entities", "additivity", "function_body"],
                    "optional": [
                        "name",
                        "root_entity",
                        "fanout_policy",
                        "unit",
                        "domain",
                        "provenance",
                        "ai_context",
                    ],
                    "parameters": {
                        "entities": _param(
                            "list[EntityRef | str]", "entities used by the metric body"
                        ),
                        "additivity": additivity,
                        "function_body": function_body,
                        "name": _param(
                            "str | None", "defaults to the decorated function name", default="None"
                        ),
                        "root_entity": _param(
                            "EntityRef | str | None",
                            "required when more than one entity is provided",
                            default="None",
                        ),
                        "fanout_policy": _param(
                            "Literal['block', 'aggregate_then_join']",
                            "cross-entity fanout handling policy",
                            default="block",
                            allowed_values=["block", "aggregate_then_join"],
                        ),
                        "unit": unit,
                        "domain": domain,
                        "provenance": _param(
                            "MetricProvenance | None",
                            "optional ms.from_sql(...) parity provenance",
                            default="None",
                        ),
                        "ai_context": ai_context,
                    },
                    "additivity": _additivity_contract(),
                    "static_constraints": [
                        "body must return one supported metric expression",
                        "entities must be non-empty",
                        "root_entity is required when more than one entity is provided",
                    ],
                },
            },
            "static_constraints": [
                "read ms.help('metric') before choosing any metric constructor",
                "after selecting a variant, read the specific constructor help for full parameter details",
                "use md.discover_relationship only to evaluate cross-entity viability when multiple entities are involved",
            ],
        },
        "relationship": {
            "summary": "Declare a semantic relationship between two verified entities.",
            "constructor": "ms.relationship",
            "required": ["name", "from_entity", "to_entity", "keys"],
            "optional": ["domain", "ai_context"],
            "discover": "md.discover_relationship",
            "parameters": {
                "name": name,
                "from_entity": entity_ref,
                "to_entity": entity_ref,
                "keys": _param("list[RelationshipKey]", "join keys built with ms.join_on(...)"),
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": [
                "from_entity and to_entity must be EntityRef values",
                "keys must be built with ms.join_on(...)",
            ],
        },
        "ratio": {
            "summary": "Declare a derived ratio metric.",
            "constructor": "ms.ratio",
            "required": ["name", "numerator", "denominator"],
            "optional": ["unit", "domain", "ai_context"],
            "discover": None,
            "parameters": {
                "name": name,
                "numerator": _param("MetricRef", "numerator metric"),
                "denominator": _param("MetricRef", "denominator metric"),
                "unit": unit,
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": ["numerator and denominator must be MetricRef values"],
        },
        "weighted_average": {
            "summary": "Declare a derived weighted-average metric.",
            "constructor": "ms.weighted_average",
            "required": ["name", "value", "weight"],
            "optional": ["unit", "domain", "ai_context"],
            "discover": None,
            "parameters": {
                "name": name,
                "value": _param("MetricRef", "value metric"),
                "weight": _param("MetricRef", "weight metric"),
                "unit": unit,
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": ["value and weight must be MetricRef values"],
        },
        "linear": {
            "summary": "Declare a derived linear-combination metric.",
            "constructor": "ms.linear",
            "required": ["name"],
            "optional": ["add", "subtract", "unit", "domain", "ai_context"],
            "discover": None,
            "parameters": {
                "name": name,
                "add": _param("list[MetricRef] | None", "metrics to add", default="None"),
                "subtract": _param("list[MetricRef] | None", "metrics to subtract", default="None"),
                "unit": unit,
                "domain": domain,
                "ai_context": ai_context,
            },
            "static_constraints": ["at least one metric must appear in add or subtract"],
        },
    }


_ENRICHMENT_KEYS = ("default_path", "tier1", "tier2", "body_rule", "authoring_guidance")


def _contract_text(symbol: str, content: dict[str, object]) -> str:
    contract = cast("dict[str, object]", content["authoring_contract"])
    lines = [
        f"marivo.semantic {symbol}",
        "",
        str(content["summary"]),
        "",
        f"Constructor: {contract['constructor']}",
        f"Required: {', '.join(cast('list[str]', contract['required']))}",
        f"Optional: {', '.join(cast('list[str]', contract['optional']))}",
        f"Discover: {contract['discover']}",
    ]
    if "parse" in contract:
        lines.extend(("", "Parse decision:"))
        parse = cast("dict[str, dict[str, object]]", contract["parse"])
        for key in (
            "native_date",
            "native_datetime",
            "native_timestamp",
            "string_or_integer_date_like",
            "hour_only",
        ):
            lines.append(f"  - {key}: {parse[key]['form']}")
    if "additivity" in contract:
        lines.extend(("", "Additivity:"))
        additivity = cast("dict[str, object]", contract["additivity"])
        lines.append(f"  - allowed: {', '.join(cast('list[str]', additivity['allowed_values']))}")
        lines.append(
            f"  - semi_additive: {cast('dict[str, object]', additivity['semi_additive'])['form']}"
        )
    if "variants" in contract:
        lines.extend(("", "Metric constructor decision order:"))
        variants = cast("dict[str, dict[str, object]]", contract["variants"])
        for key in cast("list[str]", contract["decision_order"]):
            variant = variants[key]
            lines.append(f"  - {key}: {variant['constructor']} when {variant['when']}")
    if "default_path" in content:
        lines.extend(("", "Default path:"))
        lines.append(f"  {content['default_path']}")
    if "tier1" in content:
        lines.extend(("", "Tier-1 (call-form, no body):"))
        lines.append(f"  {content['tier1']}")
    if "tier2" in content:
        lines.extend(("", "Tier-2 (decorator-form, body required):"))
        lines.append(f"  {content['tier2']}")
    if "body_rule" in content:
        lines.append("")
        lines.append(str(content["body_rule"]))
    if "authoring_guidance" in content:
        lines.extend(("", "Authoring guidance:"))
        lines.append(f"  {content['authoring_guidance']}")
    lines.extend(("", "Static constraints:"))
    for constraint in cast("list[str]", contract["static_constraints"]):
        lines.append(f"  - {constraint}")
    lines.extend(("", "Workflow:"))
    for step in cast("list[str]", content["workflow"]):
        lines.append(f"  - {step}")
    return "\n".join(lines)


_CONSTRAINT_TOPICS = frozenset({"time_dimension"})


def _contract_topic(
    symbol: str,
    contract: dict[str, object],
    catalog: Mapping[str, Constraint],
) -> Descriptor:
    summary = cast("str", contract["summary"])
    content: dict[str, object] = {
        "summary": summary,
        "authoring_contract": contract,
        "workflow": [
            "Read this contract to identify required and optional parameters.",
            "Run the matching md.discover_* call when the object depends on datasource evidence.",
            "Use discovery evidence, registry facts, project docs, prior decisions, and user answers to choose values.",
            "Author one object and run ms.verify_object(...).",
        ],
    }
    for key in _ENRICHMENT_KEYS:
        if key in contract:
            content[key] = contract[key]
    constraints: tuple[Constraint, ...] = ()
    if symbol in _CONSTRAINT_TOPICS:
        constraints = tuple(
            constraint for constraint in catalog.values() if symbol in constraint.applies_to
        )
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol=symbol,
        summary=summary,
        content=content,
        constraints=constraints,
        doc=_contract_text(symbol, content),
        see_also=("ms.help('constraints')",),
    )


def _parse_contract_topic(symbol: str, contract: dict[str, object]) -> Descriptor:
    summary = cast("str", contract["summary"])
    content: dict[str, object] = {
        "summary": summary,
        "authoring_contract": contract,
        "workflow": [
            "Use this constructor only as a time dimension parse value.",
            "Read ms.help('time_dimension_column') or ms.help('time_dimension') for the owning time dimension contract.",
            "Use md.discover_time_dimensions(...) evidence and user/project context to decide whether this parse constructor is needed.",
            "Author the time dimension and run ms.verify_object(...).",
        ],
    }
    return Descriptor(
        surface="marivo.semantic",
        kind="topic",
        symbol=symbol,
        summary=summary,
        content=content,
        doc=_contract_text(symbol, content),
        see_also=("ms.help('time_dimension_column')", "ms.help('time_dimension')"),
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

    catalog = {constraint.id: constraint for constraint in iter_constraints()}
    contract_topics = {
        symbol: _contract_topic(symbol, contract, catalog)
        for symbol, contract in _authoring_contracts().items()
    }
    parse_contract_topics = {
        symbol: _parse_contract_topic(symbol, contract)
        for symbol, contract in _parse_constructor_contracts().items()
    }
    all_names = tuple(
        dict.fromkeys(
            (
                *ms.__all__,
                "constraints",
                "composition",
                *contract_topics,
                *parse_contract_topics,
                "from_sql",
                "join_on",
                "parquet",
                "csv",
                "additivity",
            )
        )
    )
    topics = {
        **contract_topics,
        **parse_contract_topics,
        "constraints": _constraint_topic(),
        "composition": _composition_topic(),
        "from_sql": _from_sql_topic(),
        "join_on": _join_on_topic(),
        "parquet": _parquet_topic(),
        "csv": _csv_topic(),
        "additivity": _additivity_topic(),
    }
    summaries = derive_summaries(
        all_names,
        _resolve,
        topics,
        overrides={},
    )
    return Surface(
        name="marivo.semantic",
        all_names=all_names,
        summaries=summaries,
        resolve=_resolve,
        catalog=catalog,
        topics=topics,
        pinned_entries=("SemanticCatalog", "SemanticObject", "SemanticObjectList"),
        hidden_names=frozenset(
            {
                "SemanticKindInput",
                "SemanticRefInput",
                "datetime",
                "timestamp",
                "strptime",
                "hour_prefix",
            }
        ),
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
