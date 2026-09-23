"""Projection-only source IR and authoring contract tests."""

from __future__ import annotations

import pytest

import marivo.datasource as md
from marivo.datasource.ir import TableSourceIR, source_to_dict
from marivo.semantic.ir import source_from_dict


def test_unprojected_table_keeps_source_identity_shape() -> None:
    source = md.table("orders", database=("warehouse", "sales"))

    assert source.columns == ()
    assert source.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": ["warehouse", "sales"],
    }
    assert source_to_dict(source) == source.to_dict()
    assert source_from_dict(source.to_dict()) == source


def test_projection_mapping_is_canonical_and_round_trips() -> None:
    direct = TableSourceIR(
        table="events",
        columns=(("score", "_generated_score"), ("event_time", "event.timestamp")),
    )
    built = md.table(
        "events",
        columns={
            "score": "_generated_score",
            "event_time": "event.timestamp",
        },
    )

    assert direct == built
    assert built.columns == (
        ("event_time", "event.timestamp"),
        ("score", "_generated_score"),
    )
    assert built.to_dict()["columns"] == {
        "event_time": "event.timestamp",
        "score": "_generated_score",
    }
    assert source_from_dict(built.to_dict()) == built


def test_legacy_typed_projection_payload_is_rejected() -> None:
    with pytest.raises((TypeError, ValueError)):
        source_from_dict(
            {
                "kind": "table",
                "table": "events",
                "database": None,
                "columns": {"event_time": {"source": "event.timestamp", "data_type": "timestamp"}},
            }
        )

    with pytest.raises(ValueError, match="typed CSV schema declarations"):
        source_from_dict({"kind": "csv", "path": "events.csv", "schema": {"id": "int64"}})
    with pytest.raises(ValueError, match="typed JSON schemas"):
        source_from_dict({"kind": "json", "path": "events.json", "schema": {"id": "int64"}})
    assert not hasattr(md, "source_column")


@pytest.mark.parametrize(
    "columns",
    [
        [],
        {"event_time": ""},
        {"": "event_time"},
        {"event_time": {"source": "event.timestamp"}},
    ],
)
def test_table_projection_rejects_invalid_or_ambiguous_shapes(columns: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        md.table("events", columns=columns)  # type: ignore[arg-type]


def test_table_projection_can_select_one_physical_column_under_two_aliases() -> None:
    source = md.table("events", columns={"event_time": "timestamp", "created_at": "timestamp"})

    assert source.columns == (("created_at", "timestamp"), ("event_time", "timestamp"))
