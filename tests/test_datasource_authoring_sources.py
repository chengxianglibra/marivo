from __future__ import annotations

import pytest

import marivo.datasource as md


def test_csv_and_json_require_typed_schema() -> None:
    csv_source = md.csv("orders.csv", schema={"order_id": "string", "amount": "decimal(18,2)"})
    json_source = md.json("events.json", schema={"event_id": "string", "occurred_at": "timestamp"})
    assert csv_source.schema == (("order_id", "string"), ("amount", "decimal(18,2)"))
    assert json_source.schema == (("event_id", "string"), ("occurred_at", "timestamp"))
    with pytest.raises(TypeError, match="schema"):
        md.csv("orders.csv")
    with pytest.raises(TypeError, match="schema"):
        md.json("events.json")


def test_json_accepts_a_wrapped_records_path() -> None:
    source = md.json(
        "events.json",
        schema={"event_id": "string"},
        records_path="$.result.items",
    )

    assert source.records_path == "$.result.items"

    with pytest.raises(ValueError, match=r"JsonSourceIR\.records_path"):
        md.json("events.json", schema={"event_id": "string"}, records_path="$[*]")


def test_json_declares_fixed_and_runtime_query_parameters() -> None:
    start = md.source_param("start")
    source = md.json(
        "https://api.example/query",
        schema={"value": "float64"},
        query_params={"query": "sum(metric) by (cluster)", "start": start, "step": "60s"},
    )

    assert start.name == "start"
    assert source.query_params == (
        ("query", "sum(metric) by (cluster)"),
        ("start", start),
        ("step", "60s"),
    )
    assert source.to_dict()["query_params"] == {
        "query": "sum(metric) by (cluster)",
        "start": {"kind": "source_param", "name": "start"},
        "step": "60s",
    }


@pytest.mark.parametrize("name", ["", "1start", "start-time", "开始"])
def test_source_param_requires_a_stable_ascii_identifier(name: str) -> None:
    with pytest.raises((TypeError, ValueError), match=r"SourceParamIR\.name"):
        md.source_param(name)


def test_json_query_parameters_reject_non_scalar_and_nonfinite_values() -> None:
    with pytest.raises(TypeError, match="query_params values"):
        md.json(
            "https://api.example/query",
            schema={"value": "float64"},
            query_params={"start": [1]},  # type: ignore[dict-item]
        )
    with pytest.raises(ValueError, match="finite float"):
        md.json(
            "https://api.example/query",
            schema={"value": "float64"},
            query_params={"start": float("nan")},
        )


def test_json_accepts_fixed_and_runtime_post_json_body_values() -> None:
    source = md.json(
        "https://api.example/graphql",
        schema={"name": "string"},
        method="POST",
        body={
            "query": "{ items { name } }",
            "variables": {
                "app_ids": [md.source_param("app_id")],
                "page_num": md.source_param("page_num"),
                "limit": 10,
            },
        },
        records_path="$.data.items",
    )

    assert source.method == "POST"
    assert source.to_dict()["body"] == {
        "query": "{ items { name } }",
        "variables": {
            "app_ids": [None],
            "page_num": None,
            "limit": 10,
        },
    }
    assert source.to_dict()["body_params"] == [
        {"path": ["variables", "app_ids", 0], "name": "app_id"},
        {"path": ["variables", "page_num"], "name": "page_num"},
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"body": {"query": "x"}}, "requires method='POST'"),
        ({"method": "POST"}, "requires a JSON body"),
        ({"method": "PATCH", "body": {"query": "x"}}, "method must be"),
        ({"method": "POST", "body": ["x"]}, "JSON object mapping"),
        ({"method": "POST", "body": {"value": float("inf")}}, "finite"),
    ],
)
def test_json_rejects_invalid_post_body_contract(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        md.json(
            "https://api.example/graphql",
            schema={"name": "string"},
            **kwargs,  # type: ignore[arg-type]
        )


def test_authoring_scopes_require_explicit_positive_guards() -> None:
    scoped = md.partition({"log_date": "20260710"}, max_rows=1000, timeout_seconds=30)
    unpruned = md.unpruned(max_rows=1000, timeout_seconds=30)
    assert scoped.values == (("log_date", "20260710"),)
    assert unpruned.max_rows == 1000
    for factory in (
        lambda: md.partition({}, max_rows=1000, timeout_seconds=30),
        lambda: md.unpruned(max_rows=0, timeout_seconds=30),
        lambda: md.unpruned(max_rows=1000, timeout_seconds=0),
    ):
        with pytest.raises((TypeError, ValueError)):
            factory()


def test_source_module_owns_concrete_scope_types() -> None:
    from marivo.datasource.source import PartitionScope, UnprunedScope

    assert md.PartitionScope is PartitionScope
    assert md.UnprunedScope is UnprunedScope
    assert "AuthoringScope" not in md.__all__
