"""Semantic coherence tests for projected table sources."""

from __future__ import annotations

import pytest

from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind
from tests.shared_fixtures import load_inline_semantic

_PROJECTED_SOURCE = """\
import marivo.datasource as md
import marivo.semantic as ms

events = ms.entity(
    name="events",
    datasource=ms.ref.datasource("wh"),
    source=md.table(
        "raw_events",
        columns={
            "event_id": md.source_column("payload.id", data_type="string"),
            "event_time": md.source_column("event.timestamp", data_type="timestamp"),
            "score": md.source_column("generated.score", data_type="float64"),
        },
    ),
    primary_key=["event_id"],
)
event_id = ms.dimension_column(name="event_id", entity=events, column="event_id")
event_time = ms.time_dimension_column(
    name="event_time",
    entity=events,
    column="event_time",
    granularity="second",
)
score = ms.measure_column(
    name="score",
    entity=events,
    column="score",
    additivity="non_additive",
)
"""


def test_projected_source_accepts_all_direct_semantic_alias_consumers() -> None:
    with load_inline_semantic(_PROJECTED_SOURCE) as result:
        assert result.errors == ()
        assert result.registry is not None


def test_projected_source_does_not_infer_expression_decorator_columns() -> None:
    source = (
        _PROJECTED_SOURCE
        + """
@ms.dimension(entity=events)
def raw_event_id(table):
    return table["payload.id"]

@ms.measure(entity=events, additivity="non_additive")
def raw_score(table):
    return table["generated.score"]
"""
    )

    with load_inline_semantic(source) as result:
        assert result.errors == ()


def test_unprojected_table_keeps_physical_column_contract() -> None:
    source = """\
import marivo.datasource as md
import marivo.semantic as ms

events = ms.entity(
    name="events",
    datasource=ms.ref.datasource("wh"),
    source=md.table("raw_events"),
    primary_key=["payload.id"],
)
event_id = ms.dimension_column(name="event_id", entity=events, column="payload.id")
event_time = ms.time_dimension_column(
    name="event_time",
    entity=events,
    column="event.timestamp",
    granularity="second",
)
score = ms.measure_column(
    name="score",
    entity=events,
    column="generated.score",
    additivity="non_additive",
)
"""

    with load_inline_semantic(source) as result:
        assert result.errors == ()


@pytest.mark.parametrize(
    ("source", "object_id", "received"),
    [
        (
            _PROJECTED_SOURCE.replace('primary_key=["event_id"]', 'primary_key=["payload.id"]'),
            "test.events",
            "payload.id",
        ),
        (
            _PROJECTED_SOURCE.replace(
                'name="event_id", entity=events, column="event_id"',
                'name="event_id", entity=events, column="payload.id"',
            ),
            "test.events.event_id",
            "payload.id",
        ),
        (
            _PROJECTED_SOURCE.replace('column="event_time"', 'column="event.timestamp"'),
            "test.events.event_time",
            "event.timestamp",
        ),
        (
            _PROJECTED_SOURCE.replace('column="score"', 'column="generated.score"'),
            "test.events.score",
            "generated.score",
        ),
    ],
)
def test_projected_source_rejects_physical_names_at_semantic_column_boundaries(
    source: str,
    object_id: str,
    received: str,
) -> None:
    with load_inline_semantic(source) as result:
        matching = [error for error in result.errors if object_id in error.semantic_refs]

    assert len(matching) == 1
    error = matching[0]
    assert error.kind == ErrorKind.INVALID_REF
    assert error.expected == "stable output aliases declared by md.table(columns=...)"
    assert error.received == received
    assert error.details["available_output_aliases"] == ["event_id", "event_time", "score"]
    assert error.details["omitted_missing_reference_count"] == 0
    assert error.details["missing_references"][0]["received_column"] == received
    assert "change each semantic column=" in error.hint
    assert "md.source_column" in error.hint


def test_projected_source_alias_error_bounds_available_candidates() -> None:
    bindings = ",\n".join(
        f'            "alias_{index:02d}": md.source_column("physical_{index:02d}", data_type="string")'
        for index in range(12)
    )
    source = f"""\
import marivo.datasource as md
import marivo.semantic as ms

events = ms.entity(
    name="events",
    datasource=ms.ref.datasource("wh"),
    source=md.table("raw_events", columns={{
{bindings}
    }}),
    primary_key=["missing"],
)
"""

    with load_inline_semantic(source) as result:
        error = result.errors[0]

    assert error.details["available_output_aliases"] == [
        f"alias_{index:02d}" for index in range(12)
    ]
    assert error.details["omitted_missing_reference_count"] == 0


def test_projected_source_alias_errors_are_aggregated_once_per_entity() -> None:
    source = _PROJECTED_SOURCE.replace(
        'primary_key=["event_id"]', 'primary_key=["payload.id"]'
    ).replace('column="event_time"', 'column="event.timestamp"')

    with load_inline_semantic(source) as result:
        matching = [
            error
            for error in result.errors
            if error.details.get("entity") == "test.events"
            and error.constraint_id == ConstraintId.REF_SHAPE
        ]

    assert len(matching) == 1
    missing = matching[0].details["missing_references"]
    assert [item["received_column"] for item in missing] == [
        "payload.id",
        "event.timestamp",
    ]
    assert matching[0].semantic_refs == ("test.events", "test.events.event_time")


def test_projected_source_alias_error_bounds_message_but_keeps_complete_details() -> None:
    missing = [f"missing_{index:02d}" for index in range(12)]
    source = _PROJECTED_SOURCE.replace(
        'primary_key=["event_id"]',
        f"primary_key={missing!r}",
    )

    with load_inline_semantic(source) as result:
        error = next(
            item
            for item in result.errors
            if item.details.get("entity") == "test.events"
            and item.constraint_id == ConstraintId.REF_SHAPE
        )

    assert len(error.details["missing_references"]) == 12
    assert "missing_07" in error.message
    assert "missing_08" not in error.message
    assert "(+4 more)" in error.message
