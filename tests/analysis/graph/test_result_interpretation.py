"""Interpretation boundaries preserve values and source-free recovery."""

from __future__ import annotations

from marivo.analysis.materialization.graph_dataset import _shared_column_boundaries, duration_facts


def test_duration_disclosure_uses_actual_carrier() -> None:
    for unit, divisor in (("s", 1), ("ms", 1000), ("us", 1000000), ("ns", 1000000000)):
        assert dict(duration_facts(unit)) == {
            "duration_unit": unit,
            "duration_seconds": f"seconds = ticks / {divisor}",
        }


def test_shared_column_facts_keep_distinct_units_and_column_order() -> None:
    facts = dict(
        _shared_column_boundaries(
            (
                ("first", duration_facts("us")),
                ("second", duration_facts("ms")),
                ("third", duration_facts("us")),
            )
        )
    )
    assert facts["[first, third].duration_unit"] == "us"
    assert facts["[first, third].duration_seconds"] == "seconds = ticks / 1000000"
    assert facts["second.duration_unit"] == "ms"
    assert facts["second.duration_seconds"] == "seconds = ticks / 1000"
