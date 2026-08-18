from __future__ import annotations

from datetime import date, datetime

import pytest

import marivo.datasource as md
from marivo._compat import UTC


def test_csv_and_json_require_typed_schema() -> None:
    csv_source = md.csv("orders.csv", schema={"order_id": "string", "amount": "decimal(18,2)"})
    json_source = md.json("events.json", schema={"event_id": "string", "occurred_at": "timestamp"})
    assert csv_source.schema == (("order_id", "string"), ("amount", "decimal(18,2)"))
    assert json_source.schema == (("event_id", "string"), ("occurred_at", "timestamp"))
    with pytest.raises(TypeError, match="schema"):
        md.csv("orders.csv")
    with pytest.raises(TypeError, match="schema"):
        md.json("events.json")


def test_json_schema_type_names_require_ibis_names() -> None:
    with pytest.raises(ValueError, match="Ibis type string"):
        md.json("events.json", schema={"event_id": "BIGINT"})


def test_csv_schema_keeps_backend_type_names() -> None:
    source = md.csv("events.csv", schema={"event_id": "BIGINT"})

    assert source.schema == (("event_id", "BIGINT"),)


def test_json_declares_stable_output_aliases_for_nested_field_paths() -> None:
    source = md.json(
        "events.json",
        schema={"event_id": "int64", "app_name": "string"},
        records_path="$.result.items",
        field_paths={"app_name": "specificsource[].name"},
    )

    assert source.schema == (("event_id", "int64"), ("app_name", "string"))
    assert source.field_paths == (("app_name", "specificsource[].name"),)
    assert source.to_dict()["field_paths"] == {"app_name": "specificsource[].name"}


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


def test_json_query_parameters_reject_nested_list_and_nonfinite_values() -> None:
    with pytest.raises(TypeError, match="list values"):
        md.json(
            "https://api.example/query",
            schema={"value": "float64"},
            query_params={"start": [1, [2]]},  # type: ignore[dict-item]
        )
    with pytest.raises(ValueError, match="finite float"):
        md.json(
            "https://api.example/query",
            schema={"value": "float64"},
            query_params={"start": float("nan")},
        )


def test_json_query_parameters_reject_fixed_empty_list() -> None:
    with pytest.raises(ValueError, match="empty list"):
        md.json(
            "https://api.example/query",
            schema={"value": "float64"},
            query_params={"apps": []},
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


def test_time_range_reuses_partition_scope_and_normalizes_iso_boundaries() -> None:
    date_scope = md.time_range(
        "event_date",
        start="2026-08-01",
        end="2026-08-02",
        max_rows=1000,
        timeout_seconds=30,
    )
    aware_scope = md.time_range(
        "occurred_at",
        start="2026-08-01T08:00:00+08:00",
        end="2026-08-02T08:00:00+08:00",
        max_rows=1000,
        timeout_seconds=30,
    )

    assert isinstance(date_scope, md.PartitionScope)
    assert date_scope.values == ()
    assert date_scope._time_range is not None
    assert date_scope._time_range.start == date(2026, 8, 1)
    assert aware_scope._time_range is not None
    assert aware_scope._time_range.start == datetime(2026, 8, 1, tzinfo=UTC)
    assert aware_scope._time_range.end == datetime(2026, 8, 2, tzinfo=UTC)
    assert "time_range" in repr(date_scope)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-08-02", "2026-08-01"),
        ("2026-08-01", "2026-08-01T01:00:00"),
        ("2026-08-01T00:00:00", "2026-08-02T00:00:00Z"),
    ],
)
def test_time_range_rejects_invalid_or_mixed_boundaries(start: str, end: str) -> None:
    with pytest.raises(ValueError):
        md.time_range(
            "timestamp",
            start=start,
            end=end,
            max_rows=1000,
            timeout_seconds=30,
        )


def test_source_module_owns_concrete_scope_types() -> None:
    from marivo.datasource.source import PartitionScope, UnprunedScope

    assert md.PartitionScope is PartitionScope
    assert md.UnprunedScope is UnprunedScope
    assert "AuthoringScope" not in md.__all__
