"""Tests for app.core.semantic.scope_resolution pure functions."""

from __future__ import annotations

import pytest

from marivo.core.semantic.scope_resolution import (
    compute_metric_scope_dimension_sources,
    constraints_dict_to_filter,
    dataset_source_to_authority_locator,
    merge_filters,
    predicate_expression_to_sql,
    resolve_predicate_target_column,
    resolve_scope_constraint_column,
    table_name_matches_locator,
)

# ── merge_filters ────────────────────────────────────────────────────


def test_merge_filters_none_only() -> None:
    assert merge_filters(None, None) is None


def test_merge_filters_single_filter() -> None:
    assert merge_filters("a = 1") == "(a = 1)"


def test_merge_filters_multiple_filters() -> None:
    result = merge_filters("a = 1", None, "b = 2")
    assert result == "(a = 1) AND (b = 2)"


def test_merge_filters_all_none() -> None:
    assert merge_filters() is None


def test_merge_filters_empty_string_treated_as_falsy() -> None:
    assert merge_filters("") is None


# ── resolve_scope_constraint_column ──────────────────────────────────


def test_resolve_scope_constraint_column_plain() -> None:
    assert resolve_scope_constraint_column("platform") == "platform"


def test_resolve_scope_constraint_column_dimension_ref() -> None:
    sources = {"dimension.cluster": {"cluster"}}
    assert (
        resolve_scope_constraint_column("dimension.cluster", dimension_sources=sources) == "cluster"
    )


def test_resolve_scope_constraint_column_ambiguous_raises() -> None:
    sources = {"dimension.cluster": {"cluster_a", "cluster_b"}}
    with pytest.raises(ValueError, match="does not resolve to a unique physical column"):
        resolve_scope_constraint_column("dimension.cluster", dimension_sources=sources)


def test_resolve_scope_constraint_column_not_found_raises() -> None:
    sources: dict[str, set[str]] = {}
    with pytest.raises(ValueError, match="not available in metric semantic scope"):
        resolve_scope_constraint_column("dimension.cluster", dimension_sources=sources)


def test_resolve_scope_constraint_column_no_sources_raises() -> None:
    with pytest.raises(ValueError, match="requires a semantic metric scope"):
        resolve_scope_constraint_column("dimension.cluster", dimension_sources=None)


def test_resolve_scope_constraint_column_invalid_dotted_key_raises() -> None:
    with pytest.raises(ValueError, match="must be a physical column"):
        resolve_scope_constraint_column("entity.user")


# ── constraints_dict_to_filter ───────────────────────────────────────


def test_constraints_dict_to_filter_simple() -> None:
    result = constraints_dict_to_filter({"platform": "ios", "region": "us"})
    assert result == "platform = 'ios' AND region = 'us'"


def test_constraints_dict_to_filter_empty() -> None:
    assert constraints_dict_to_filter({}) is None


def test_constraints_dict_to_filter_skips_dict_and_list() -> None:
    result = constraints_dict_to_filter({"platform": "ios", "nested": {"a": 1}, "items": [1, 2]})
    assert result == "platform = 'ios'"


def test_constraints_dict_to_filter_with_semantic_refs() -> None:
    sources = {"dimension.cluster": {"cluster"}}
    result = constraints_dict_to_filter(
        {"dimension.cluster": "prod"},
        resolve_semantic_refs=True,
        dimension_sources=sources,
    )
    assert result == "cluster = 'prod'"


# ── resolve_predicate_target_column ──────────────────────────────────


def test_resolve_predicate_target_column_plain() -> None:
    assert resolve_predicate_target_column("user_id") == "user_id"


def test_resolve_predicate_target_column_dimension_ref() -> None:
    sources = {"dimension.cluster": {"cluster"}}
    assert (
        resolve_predicate_target_column("dimension.cluster", dimension_sources=sources) == "cluster"
    )


def test_resolve_predicate_target_column_other_dotted_ref() -> None:
    assert resolve_predicate_target_column("entity.user") == "user"


def test_resolve_predicate_target_column_deeply_nested() -> None:
    assert resolve_predicate_target_column("some.deep.ref") == "deep_ref"


# ── predicate_expression_to_sql ──────────────────────────────────────


def test_predicate_expression_to_sql_eq() -> None:
    result = predicate_expression_to_sql({"op": "=", "target_ref": "status", "value": "active"})
    assert result == "status = 'active'"


def test_predicate_expression_to_sql_is_null() -> None:
    result = predicate_expression_to_sql({"op": "is_null", "target_ref": "status"})
    assert result == "status IS NULL"


def test_predicate_expression_to_sql_is_not_null() -> None:
    result = predicate_expression_to_sql({"op": "is_not_null", "target_ref": "status"})
    assert result == "status IS NOT NULL"


def test_predicate_expression_to_sql_between() -> None:
    result = predicate_expression_to_sql({"op": "between", "target_ref": "age", "value": [10, 20]})
    assert result == "age BETWEEN '10' AND '20'"


def test_predicate_expression_to_sql_in() -> None:
    result = predicate_expression_to_sql(
        {"op": "in", "target_ref": "region", "value": ["us", "eu"]}
    )
    assert result == "region IN ('us', 'eu')"


def test_predicate_expression_to_sql_not_in() -> None:
    result = predicate_expression_to_sql({"op": "not_in", "target_ref": "region", "value": ["us"]})
    assert result == "NOT region IN ('us')"


def test_predicate_expression_to_sql_and_combination() -> None:
    expr = {
        "op": "and",
        "items": [
            {"op": "=", "target_ref": "a", "value": "1"},
            {"op": "is_null", "target_ref": "b"},
        ],
    }
    result = predicate_expression_to_sql(expr)
    assert result == "a = '1' AND b IS NULL"


def test_predicate_expression_to_sql_with_dimension_ref() -> None:
    sources = {"dimension.cluster": {"cluster"}}
    result = predicate_expression_to_sql(
        {"op": "=", "target_ref": "dimension.cluster", "value": "prod"},
        dimension_sources=sources,
    )
    assert result == "cluster = 'prod'"


def test_predicate_expression_to_sql_op_without_value() -> None:
    result = predicate_expression_to_sql({"op": ">", "target_ref": "age"})
    assert result == "age >"


# ── table_name_matches_locator ───────────────────────────────────────


def test_table_name_matches_locator_exact() -> None:
    assert table_name_matches_locator(
        "my_table", {"catalog": None, "schema": "my_schema", "table": "my_table"}
    )


def test_table_name_matches_locator_qualified() -> None:
    assert table_name_matches_locator(
        "my_schema.my_table", {"catalog": None, "schema": "my_schema", "table": "my_table"}
    )


def test_table_name_matches_locator_no_match() -> None:
    assert not table_name_matches_locator(
        "other_table", {"catalog": None, "schema": "my_schema", "table": "my_table"}
    )


def test_table_name_matches_locator_string() -> None:
    assert table_name_matches_locator("my_table", "my_schema.my_table")


def test_table_name_matches_locator_none() -> None:
    assert not table_name_matches_locator("my_table", None)


def test_table_name_matches_locator_empty() -> None:
    assert not table_name_matches_locator("", "my_table")
    assert not table_name_matches_locator("my_table", "")


def test_table_name_matches_locator_three_part() -> None:
    assert table_name_matches_locator(
        "my_table",
        {"catalog": "my_catalog", "schema": "my_schema", "table": "my_table"},
    )


# ── dataset_source_to_authority_locator ──────────────────────────────


def test_dataset_source_to_authority_locator_three_parts() -> None:
    result = dataset_source_to_authority_locator("catalog.schema.table")
    assert result == {"catalog": "catalog", "schema": "schema", "table": "table"}


def test_dataset_source_to_authority_locator_two_parts() -> None:
    result = dataset_source_to_authority_locator("schema.table")
    assert result == {"catalog": None, "schema": "schema", "table": "table"}


def test_dataset_source_to_authority_locator_one_part() -> None:
    result = dataset_source_to_authority_locator("table")
    assert result == {"catalog": None, "schema": None, "table": "table"}


# ── compute_metric_scope_dimension_sources ───────────────────────────


def test_compute_metric_scope_dimension_sources_basic() -> None:
    payload = {
        "dimensions": ["dimension.platform", "dimension.region"],
        "dataset_fields": {"platform": "string", "region": "string"},
    }
    result = compute_metric_scope_dimension_sources(
        payload=payload,
        table_name="my_table",
        dataset_source="my_table",
    )
    assert result == {
        "dimension.platform": {"platform"},
        "dimension.region": {"region"},
    }


def test_compute_metric_scope_dimension_sources_event_date_excluded() -> None:
    payload = {
        "dimensions": ["event_date", "dimension.platform"],
        "dataset_fields": {"platform": "string"},
    }
    result = compute_metric_scope_dimension_sources(
        payload=payload,
        table_name="my_table",
        dataset_source="my_table",
    )
    assert "event_date" not in result
    assert "dimension.platform" in result


def test_compute_metric_scope_dimension_sources_table_mismatch() -> None:
    payload = {
        "dimensions": ["dimension.platform"],
        "dataset_fields": {"platform": "string"},
    }
    result = compute_metric_scope_dimension_sources(
        payload=payload,
        table_name="other_table",
        dataset_source="my_schema.my_table",
    )
    assert result == {}


def test_compute_metric_scope_dimension_sources_no_dataset_source() -> None:
    payload = {
        "dimensions": ["dimension.platform"],
        "dataset_fields": {"platform": "string"},
    }
    result = compute_metric_scope_dimension_sources(
        payload=payload,
        table_name="my_table",
        dataset_source=None,
    )
    assert "dimension.platform" in result


def test_compute_metric_scope_dimension_sources_empty() -> None:
    result = compute_metric_scope_dimension_sources(
        payload={},
        table_name="my_table",
        dataset_source=None,
    )
    assert result == {}


def test_compute_metric_scope_dimension_sources_field_not_in_available() -> None:
    payload = {
        "dimensions": ["dimension.platform", "dimension.missing"],
        "dataset_fields": {"platform": "string"},
    }
    result = compute_metric_scope_dimension_sources(
        payload=payload,
        table_name="my_table",
        dataset_source=None,
    )
    assert "dimension.platform" in result
    assert "dimension.missing" not in result
