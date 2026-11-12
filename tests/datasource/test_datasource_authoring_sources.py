from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

import marivo.datasource as md
from marivo.datasource.errors import DatasourceFieldInvalidError


def test_table_csv_and_json_sources_use_optional_projection_mappings() -> None:
    table_source = md.table("orders", columns={"order_id": "Order ID"})
    csv_source = md.csv("orders.csv", columns={"order_id": "Order ID"})
    json_source = md.json(
        "events.json",
        columns={"event_id": "event.id", "app_name": "apps[].name"},
        records_path="$.result.items",
    )

    assert table_source.columns == (("order_id", "Order ID"),)
    assert csv_source.columns == (("order_id", "Order ID"),)
    assert json_source.columns == (("app_name", "apps[].name"), ("event_id", "event.id"))
    assert json_source.to_dict()["columns"] == {
        "app_name": "apps[].name",
        "event_id": "event.id",
    }
    assert md.csv("orders.csv").columns == ()
    assert md.json("events.json").columns == ()


def test_json_projection_paths_support_nested_fields_and_one_shared_traversal() -> None:
    source = md.json(
        "events.json",
        columns={"event_id": "event.id", "app_name": "apps[].name"},
        records_path="$.result.items",
    )

    assert source.records_path == "$.result.items"


def test_json_source_parameters_remain_independent_of_column_projection() -> None:
    start = md.source_param("start")
    source = md.json(
        "https://api.example/query",
        columns={"value": "data.value"},
        query_params={"start": start, "step": "60s"},
    )

    assert start.name == "start"
    assert source.query_params == (("start", start), ("step", "60s"))
    assert source.to_dict()["query_params"] == {
        "start": {"kind": "source_param", "name": "start"},
        "step": "60s",
    }


def test_json_accepts_fixed_and_runtime_post_json_body_values() -> None:
    source = md.json(
        "https://api.example/graphql",
        columns={"name": "data.name"},
        method="POST",
        body={
            "query": "{ items { name } }",
            "variables": {
                "app_ids": [md.source_param("app_id")],
                "page_num": md.source_param("page_num"),
                "limit": 10,
            },
        },
    )

    assert source.method == "POST"
    assert source.columns == (("name", "data.name"),)
    assert source.body_params == (
        (("variables", "app_ids", 0), md.source_param("app_id")),
        (("variables", "page_num"), md.source_param("page_num")),
    )


def test_physical_types_cannot_be_declared_in_source_interfaces() -> None:
    with pytest.raises(TypeError):
        md.csv("orders.csv", schema={"order_id": "string"})  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        md.json("events.json", schema={"event_id": "string"})  # type: ignore[call-arg]


@pytest.mark.parametrize("target", ["table", "csv", "json"])
@pytest.mark.parametrize(
    "columns",
    [{}, {"": "source"}, {"output": ""}, {"output": 42}, ["output"]],
)
def test_source_projection_errors_identify_the_owning_constructor(
    target: str, columns: object
) -> None:
    projected = cast("Mapping[str, str]", columns)
    with pytest.raises(DatasourceFieldInvalidError) as caught:
        if target == "table":
            md.table("orders", columns=projected)
        elif target == "csv":
            md.csv("orders.csv", columns=projected)
        else:
            md.json("events.json", columns=projected)
    error = caught.value
    assert error.expected and error.received
    assert error.location == f"md.{target}(columns=...)"
    assert error.effect_observed is not None and not error.effect_observed.query_executed
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == target
    assert error.repair.kind == "reauthor"
    assert "non-empty mapping" in error.repair.action
    assert "omit" not in error.repair.action.lower()


@pytest.mark.parametrize("name", ["", "1start", "start-time", "开始"])
def test_source_param_requires_a_stable_ascii_identifier(name: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        md.source_param(name)


def test_json_query_parameters_reject_nested_list_and_nonfinite_values() -> None:
    with pytest.raises(TypeError):
        md.json("https://api.example/query", query_params={"start": [1, [2]]})  # type: ignore[list-item]
    with pytest.raises(ValueError):
        md.json("https://api.example/query", query_params={"start": float("nan")})
