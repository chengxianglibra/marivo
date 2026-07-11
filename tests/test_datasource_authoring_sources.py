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
