"""Regression tests for static semantic authoring contracts exposed by ms.help."""

from __future__ import annotations

from typing import Any, cast

import pytest

from marivo.introspection.surface import render as surface_render
from marivo.semantic import help_text as semantic_help_text
from marivo.semantic.help import _surface


def _help_json(symbol: str) -> dict[str, Any]:
    return cast("dict[str, Any]", surface_render(_surface(), symbol, "json"))


EXPECTED_CONTRACTS: dict[str, dict[str, object]] = {
    "domain": {
        "constructor": "ms.domain",
        "required": ["name", "owner"],
        "optional": ["ai_context"],
        "discover": None,
    },
    "entity": {
        "constructor": "ms.entity",
        "required": ["name", "datasource", "source"],
        "optional": ["primary_key", "versioning", "domain", "ai_context"],
        "discover": "md.discover_entity",
    },
    "dimension_column": {
        "constructor": "ms.dimension_column",
        "required": ["name", "entity", "column"],
        "optional": ["domain", "ai_context"],
        "discover": "md.discover_dimensions",
    },
    "dimension": {
        "constructor": "@ms.dimension",
        "required": ["entity", "function_body"],
        "optional": ["name", "domain", "ai_context"],
        "discover": "md.discover_dimensions",
    },
    "time_dimension_column": {
        "constructor": "ms.time_dimension_column",
        "required": ["name", "entity", "column", "granularity"],
        "optional": ["parse", "is_default", "domain", "ai_context"],
        "discover": "md.discover_time_dimensions",
    },
    "time_dimension": {
        "constructor": "@ms.time_dimension",
        "required": ["entity", "granularity", "function_body"],
        "optional": ["name", "parse", "is_default", "domain", "ai_context"],
        "discover": "md.discover_time_dimensions",
    },
    "measure_column": {
        "constructor": "ms.measure_column",
        "required": ["name", "entity", "column", "additivity"],
        "optional": ["unit", "domain", "ai_context"],
        "discover": "md.discover_measures",
    },
    "measure": {
        "constructor": "@ms.measure",
        "required": ["entity", "additivity", "function_body"],
        "optional": ["name", "unit", "domain", "ai_context"],
        "discover": "md.discover_measures",
    },
    "aggregate": {
        "constructor": "ms.aggregate",
        "required": ["name", "measure", "agg"],
        "optional": ["fold", "unit", "domain", "ai_context"],
        "discover": None,
    },
    "count": {
        "constructor": "ms.count",
        "required": ["name", "entity"],
        "optional": ["domain", "ai_context"],
        "discover": None,
    },
    "metric": {
        "constructor": "metric family",
        "required": [],
        "optional": [],
        "discover": "md.discover_relationship for cross-entity viability when multiple entities are involved",
    },
    "relationship": {
        "constructor": "ms.relationship",
        "required": ["name", "from_entity", "to_entity", "keys"],
        "optional": ["domain", "ai_context"],
        "discover": "md.discover_relationship",
    },
    "ratio": {
        "constructor": "ms.ratio",
        "required": ["name", "numerator", "denominator"],
        "optional": ["unit", "domain", "ai_context"],
        "discover": None,
    },
    "weighted_average": {
        "constructor": "ms.weighted_average",
        "required": ["name", "value", "weight"],
        "optional": ["unit", "domain", "ai_context"],
        "discover": None,
    },
    "linear": {
        "constructor": "ms.linear",
        "required": ["name"],
        "optional": ["add", "subtract", "unit", "domain", "ai_context"],
        "discover": None,
    },
}


@pytest.mark.parametrize("symbol, expected", EXPECTED_CONTRACTS.items())
def test_semantic_help_exposes_authoring_contract_for_each_object(
    symbol: str,
    expected: dict[str, object],
) -> None:
    data = _help_json(symbol)

    assert data["kind"] == "topic"
    content = cast("dict[str, Any]", data["content"])
    contract = cast("dict[str, Any]", content["authoring_contract"])

    assert contract["constructor"] == expected["constructor"]
    assert contract["required"] == expected["required"]
    assert contract["optional"] == expected["optional"]
    assert contract["discover"] == expected["discover"]

    params = cast("dict[str, dict[str, Any]]", contract["parameters"])
    for parameter in cast("list[str]", expected["required"]) + cast(
        "list[str]", expected["optional"]
    ):
        assert parameter in params
        assert "type" in params[parameter]
        assert "meaning" in params[parameter]
        assert "source" not in params[parameter]
    assert "static_constraints" in contract


def test_time_dimension_column_help_inlines_parse_decision() -> None:
    data = _help_json("time_dimension_column")
    content = cast("dict[str, Any]", data["content"])
    contract = cast("dict[str, Any]", content["authoring_contract"])
    parse = cast("dict[str, Any]", contract["parse"])

    assert parse["native_date"]["form"] == "omit parse"
    assert parse["native_datetime"]["form"] == "ms.datetime(timezone=None, sample_interval=None)"
    assert parse["native_timestamp"]["form"] == "ms.timestamp(timezone=None, sample_interval=None)"
    assert parse["string_or_integer_date_like"]["form"] == (
        "ms.strptime(format, timezone=None, sample_interval=None)"
    )
    assert parse["hour_only"]["form"] == "ms.hour_prefix(prefix, sample_interval=None)"

    constraints = cast("list[str]", contract["static_constraints"])
    assert "ms.hour_prefix(...) requires granularity='hour'" in constraints
    assert "sub-day date-only parses are invalid" in constraints


def test_measure_help_contract_inlines_additivity_shapes() -> None:
    data = _help_json("measure_column")
    content = cast("dict[str, Any]", data["content"])
    contract = cast("dict[str, Any]", content["authoring_contract"])
    additivity = cast("dict[str, Any]", contract["additivity"])

    assert additivity["allowed_values"] == ["additive", "non_additive", "ms.semi_additive(...)"]
    assert additivity["semi_additive"]["form"] == (
        "ms.semi_additive(over=<TimeDimensionRef>, fold='last'|'first'|'mean'|'min'|'max'|('percentile', q))"
    )
    assert additivity["semi_additive"]["fold_allowed_values"] == [
        "mean",
        "min",
        "max",
        "first",
        "last",
        "('percentile', q)",
    ]


def test_specific_metric_constructor_help_remains_available() -> None:
    aggregate = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", _help_json("aggregate")["content"])["authoring_contract"],
    )
    ratio = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", _help_json("ratio")["content"])["authoring_contract"],
    )

    assert aggregate["constructor"] == "ms.aggregate"
    assert ratio["constructor"] == "ms.ratio"
    assert "function_body" not in aggregate["required"]
    assert ratio["required"] == ["name", "numerator", "denominator"]


def test_metric_help_is_unified_family_entry() -> None:
    data = _help_json("metric")
    content = cast("dict[str, Any]", data["content"])
    contract = cast("dict[str, Any]", content["authoring_contract"])

    assert contract["constructor"] == "metric family"
    assert contract["decision_order"] == [
        "count",
        "aggregate",
        "cumulative",
        "ratio",
        "weighted_average",
        "linear",
        "expression",
    ]

    variants = cast("dict[str, dict[str, Any]]", contract["variants"])
    assert variants["count"]["constructor"] == "ms.count"
    assert variants["aggregate"]["constructor"] == "ms.aggregate"
    assert variants["cumulative"]["constructor"] == "ms.cumulative"
    assert variants["expression"]["constructor"] == "@ms.metric"
    assert variants["ratio"]["constructor"] == "ms.ratio"
    assert variants["weighted_average"]["constructor"] == "ms.weighted_average"
    assert variants["linear"]["constructor"] == "ms.linear"
    assert (
        variants["aggregate"]["when"] == "metric is a simple aggregation over one verified measure"
    )
    assert variants["expression"]["when"] == (
        "metric needs an expression body over one or more entities, measures, or metrics"
    )
    assert variants["expression"]["required"] == ["entities", "additivity", "function_body"]
    assert "parameters" in variants["expression"]


def test_parse_constructor_help_topics_are_public_contracts() -> None:
    for symbol in ("datetime", "timestamp", "strptime", "hour_prefix"):
        data = _help_json(symbol)
        assert data["kind"] == "topic"
        content = cast("dict[str, Any]", data["content"])
        contract = cast("dict[str, Any]", content["authoring_contract"])
        assert contract["constructor"] == f"ms.{symbol}"
        assert contract["discover"] == "md.discover_time_dimensions"
        assert "parameters" in contract
        assert "static_constraints" in contract


def test_hour_prefix_help_explains_prefix_semantics_and_pushdown_example() -> None:
    text = semantic_help_text("hour_prefix")

    assert "day-level time dimension" in text
    assert "same entity" in text
    assert "parse=ms.hour_prefix(dt)" in text
    assert "log_hour" in text
    assert "two-column partition pushdown" in text


def test_parse_constructor_help_points_back_to_time_dimension_contract() -> None:
    for symbol in ("datetime", "timestamp", "strptime", "hour_prefix"):
        data = _help_json(symbol)
        see_also = tuple(cast("tuple[str, ...]", data["see_also"]))
        assert "ms.help('time_dimension_column')" in see_also
        assert "ms.help('time_dimension')" in see_also


def test_help_lists_authoring_topic() -> None:
    import marivo.semantic as ms

    text = ms.help_text()
    assert "authoring" in text


def test_authoring_topic_renders_semantic_stages_and_handoff() -> None:
    import marivo.semantic as ms

    text = ms.help_text("authoring")
    assert "import marivo.semantic as ms" in text
    # catalog browse
    assert "ms.load(" in text
    assert "catalog.list(" in text
    # authoring order (spec §ms.help("authoring"))
    assert "domain" in text and "entity" in text and "measure" in text
    assert "metric" in text and "relationship" in text
    # one-object-then-verify loop
    assert "ms.verify_object(" in text
    # readiness closeout + preview + analysis handoff
    assert "ms.readiness(" in text
    assert "catalog.preview(" in text
    assert "marivo.analysis" in text
    # routes to constructor help, does not duplicate tables
    assert 'ms.help("entity")' in text or "ms.help('entity')" in text
    # no catalog.query guess; no prepare_ stage
    assert "catalog.query" not in text
    assert "prepare_" not in text
    assert "recommend" not in text.lower()
    assert text.count("\n") <= 80


def test_entity_source_parameter_includes_json_source_ir() -> None:
    data = _help_json("entity")
    content = cast("dict[str, Any]", data["content"])
    contract = cast("dict[str, Any]", content["authoring_contract"])
    params = cast("dict[str, dict[str, Any]]", contract["parameters"])
    source_param = params["source"]
    assert "JsonSourceIR" in cast("str", source_param["type"])
    constraints = cast("list[str]", contract["static_constraints"])
    assert any("ms.json(...)" in c for c in constraints)
