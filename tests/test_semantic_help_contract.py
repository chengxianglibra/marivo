"""Regression tests for static semantic authoring contracts exposed by ms.help."""

from __future__ import annotations

from typing import Any, cast

import pytest

from marivo.introspection.surface import render as surface_render
from marivo.semantic.help import _surface


def _help_json(symbol: str) -> dict[str, Any]:
    return cast("dict[str, Any]", surface_render(_surface(), symbol, "json"))


EXPECTED_CONTRACTS: dict[str, dict[str, object]] = {
    "domain": {
        "constructor": "ms.domain",
        "required": ["name"],
        "optional": ["ai_context"],
        "prepare": "ms.prepare_domain",
        "discover": None,
    },
    "entity": {
        "constructor": "ms.entity",
        "required": ["name", "datasource", "source"],
        "optional": ["primary_key", "versioning", "domain", "ai_context"],
        "prepare": "ms.prepare_entity",
        "discover": "md.discover_entity",
    },
    "dimension_column": {
        "constructor": "ms.dimension_column",
        "required": ["name", "entity", "column"],
        "optional": ["domain", "ai_context"],
        "prepare": "ms.prepare_dimension",
        "discover": "md.discover_dimensions",
    },
    "dimension": {
        "constructor": "@ms.dimension",
        "required": ["entity", "function_body"],
        "optional": ["name", "domain", "ai_context"],
        "prepare": "ms.prepare_dimension",
        "discover": "md.discover_dimensions",
    },
    "time_dimension_column": {
        "constructor": "ms.time_dimension_column",
        "required": ["name", "entity", "column", "granularity"],
        "optional": ["parse", "is_default", "domain", "ai_context"],
        "prepare": "ms.prepare_time_dimension",
        "discover": "md.discover_time_dimensions",
    },
    "time_dimension": {
        "constructor": "@ms.time_dimension",
        "required": ["entity", "granularity", "function_body"],
        "optional": ["name", "parse", "is_default", "domain", "ai_context"],
        "prepare": "ms.prepare_time_dimension",
        "discover": "md.discover_time_dimensions",
    },
    "measure_column": {
        "constructor": "ms.measure_column",
        "required": ["name", "entity", "column", "additivity"],
        "optional": ["unit", "domain", "ai_context"],
        "prepare": "ms.prepare_measure",
        "discover": "md.discover_measures",
    },
    "measure": {
        "constructor": "@ms.measure",
        "required": ["entity", "additivity", "function_body"],
        "optional": ["name", "unit", "domain", "ai_context"],
        "prepare": "ms.prepare_measure",
        "discover": "md.discover_measures",
    },
    "aggregate": {
        "constructor": "ms.aggregate",
        "required": ["name", "measure", "agg"],
        "optional": ["domain", "ai_context"],
        "prepare": "ms.prepare_metric",
        "discover": None,
    },
    "count": {
        "constructor": "ms.count",
        "required": ["name", "entity"],
        "optional": ["domain", "ai_context"],
        "prepare": "ms.prepare_metric",
        "discover": None,
    },
    "metric": {
        "constructor": "@ms.metric",
        "required": ["entities", "additivity", "function_body"],
        "optional": ["name", "unit", "domain", "provenance", "ai_context"],
        "prepare": "ms.prepare_metric",
        "discover": "md.discover_relationship for cross-entity viability when multiple entities are involved",
    },
    "relationship": {
        "constructor": "ms.relationship",
        "required": ["name", "from_entity", "to_entity", "keys"],
        "optional": ["domain", "ai_context"],
        "prepare": "ms.prepare_relationship",
        "discover": "md.discover_relationship",
    },
    "ratio": {
        "constructor": "ms.ratio",
        "required": ["name", "numerator", "denominator"],
        "optional": ["unit", "domain", "ai_context"],
        "prepare": "ms.prepare_derived_metric",
        "discover": None,
    },
    "weighted_average": {
        "constructor": "ms.weighted_average",
        "required": ["name", "value", "weight"],
        "optional": ["unit", "domain", "ai_context"],
        "prepare": "ms.prepare_derived_metric",
        "discover": None,
    },
    "linear": {
        "constructor": "ms.linear",
        "required": ["name"],
        "optional": ["add", "subtract", "unit", "domain", "ai_context"],
        "prepare": "ms.prepare_derived_metric",
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
    assert contract["prepare"] == expected["prepare"]
    assert contract["discover"] == expected["discover"]

    params = cast("dict[str, dict[str, Any]]", contract["parameters"])
    for parameter in cast("list[str]", expected["required"]) + cast("list[str]", expected["optional"]):
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
        "ms.semi_additive(over=<TimeDimensionRef>, fold='last'|'first'|'mean'|'min'|'max')"
    )


def test_metric_help_contract_separates_tier1_and_tier2_paths() -> None:
    aggregate = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", _help_json("aggregate")["content"])["authoring_contract"],
    )
    metric = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", _help_json("metric")["content"])["authoring_contract"],
    )

    assert aggregate["constructor"] == "ms.aggregate"
    assert metric["constructor"] == "@ms.metric"
    assert "function_body" not in aggregate["required"]
    assert "function_body" in metric["required"]


def test_parse_constructor_help_topics_are_deleted() -> None:
    for symbol in ("datetime", "timestamp", "strptime", "hour_prefix"):
        data = _help_json(symbol)
        assert data["kind"] == "unknown"
        assert data["symbol"] == symbol
        assert "signature" not in data
