"""Tests for the bounded plain-text card formatter."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from marivo.render import Card


@pytest.mark.parametrize("budget", (180, 400, 8192))
def test_card_owner_recovery_survives_byte_marker_fitting(budget: int) -> None:
    card = Card(
        identity="Artifact id=saved",
        available=(".to_pandas()",),
        omission_recovery="use .to_pandas()",
    )
    card.field("long_section_" + "x" * 300, "y" * 10000)
    text = card.render(max_output_bytes=budget)
    assert len(text.encode()) <= budget
    with pytest.raises(ValueError):
        card.render(max_output_bytes=1)


def test_byte_truncation_preserves_head_tail_and_names_omitted_rows() -> None:
    cap = 220
    result = (
        Card(
            identity="MetricFrame ref=frame_1 rows=25",
            available=(".show()", ".render()", ".to_pandas()"),
        )
        .status("ready")
        .table(
            columns=["bucket_start", "revenue", "orders"],
            rows=[[f"2026-06-{day:02d}", str(day * 10), str(day)] for day in range(1, 26)],
            row_count=25,
            show_omission_counts=True,
        )
        .render(max_output_bytes=cap)
    )
    assert len(result.encode()) <= cap


def test_exact_fit_card_renders_without_truncation_marker() -> None:
    card = Card(identity="ExactFit id=1", available=(".show()",)).field("value", "small")
    full = card.render(max_output_bytes=None)

    result = card.render(max_output_bytes=len(full.encode()))

    assert result == full


def test_lazy_table_stops_at_budget_and_names_omitted_rows() -> None:
    yielded = 0

    def rows_provider() -> Iterator[list[str]]:
        nonlocal yielded
        for index in range(100):
            yielded += 1
            yield [f"row-{index}", "x" * 20]

    cap = 260
    result = (
        Card(identity="Lazy rows=100", available=(".show()",))
        .lazy_table(
            columns=["name", "payload"],
            rows_provider=rows_provider,
            row_count=100,
        )
        .render(max_output_bytes=cap)
    )

    assert len(result.encode()) <= cap
    assert yielded < 100


def test_bounded_table_wide_rows_hit_byte_budget_before_row_limit() -> None:
    rows = [[f"row-{index}", "x" * 400] for index in range(60)]
    result = (
        Card(identity="Wide", available=(".show()",))
        .lazy_table(
            columns=["value", "payload"],
            rows_provider=lambda: iter(rows),
            row_count=len(rows),
            bounded_row_limit=50,
            show_omission_counts=True,
            recovery="session.persisted_frame('frame_1').to_pandas()",
        )
        .render(max_output_bytes=1_000)
    )

    assert len(result.encode()) <= 1_000
