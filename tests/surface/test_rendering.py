"""Tests for the bounded plain-text card formatter."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from marivo.render import Card, RenderableResult


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
    assert "output truncated" in text
    assert "use .to_pandas()" in text
    assert "max_output_bytes=None" not in text
    with pytest.raises(ValueError, match=r"use \.to_pandas\(\)"):
        card.render(max_output_bytes=1)


def test_card_owner_recovery_survives_row_limit() -> None:
    text = (
        Card(
            identity="Artifact id=saved",
            available=(".to_pandas()",),
            omission_recovery="use .to_pandas()",
        )
        .table(("value",), (("one",), ("two",)), row_count=2, bounded_row_limit=1)
        .render()
    )
    assert "preview limited to 1 rows" in text
    assert "use .to_pandas()" in text
    assert "max_output_bytes=None" not in text


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
    assert result.startswith("MetricFrame ref=frame_1 rows=25")
    assert "available:" in result
    assert "- .show()" in result
    assert f"output truncated at {cap} bytes" in result
    assert "omitted:" in result
    assert "displayed=" in result
    assert "total=25" in result
    assert "omitted=" in result
    assert "preview" in result
    assert "rows" in result
    assert "pass max_output_bytes=None for full output" in result


def test_max_output_bytes_none_returns_full_output_without_marker() -> None:
    rows = [[f"r{i}"] for i in range(30)]
    result = (
        Card(identity="X", available=(".render()",))
        .table(
            ["value"],
            rows,
            row_count=len(rows),
        )
        .render(max_output_bytes=None)
    )
    assert "r29" in result
    assert "output truncated" not in result


def test_exact_fit_card_renders_without_truncation_marker() -> None:
    card = Card(identity="ExactFit id=1", available=(".show()",)).field("value", "small")
    full = card.render(max_output_bytes=None)

    result = card.render(max_output_bytes=len(full.encode()))

    assert result == full
    assert "output truncated" not in result


@pytest.mark.parametrize("include_newline", (False, True))
@pytest.mark.parametrize("exact_counts", (False, True))
def test_reported_minimum_budget_can_be_used_without_another_error(
    include_newline: bool, exact_counts: bool
) -> None:
    card = Card(identity="Result", available=(".show()",)).field("metadata", "界" * 1000)
    card.table(
        ("value",),
        (("a",), ("b",)),
        row_count=26,
        bounded_row_limit=2,
        show_omission_counts=exact_counts,
        report_rows=exact_counts,
        recovery=".show(n=26, max_output_bytes=None)",
    )
    with pytest.raises(ValueError, match="minimum") as caught:
        card.render(max_output_bytes=1, _include_newline=include_newline)
    match = re.search(r"minimum is (\d+) bytes", str(caught.value))
    assert match is not None
    minimum = int(match[1])
    output = card.render(max_output_bytes=minimum, _include_newline=include_newline)
    assert len(output.encode("utf-8")) + int(include_newline) <= minimum
    assert "output truncated" in output
    if exact_counts:
        assert "total=26 displayed=0 omitted=26" in output
        assert ".show(n=26, max_output_bytes=None)" in output


def test_empty_list_section_renders_none() -> None:
    result = Card(identity="X", available=(".render()",)).listing("warnings", []).render()
    assert "warnings: none" in result


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
    assert result.startswith("Lazy rows=100")
    assert "available:" in result
    assert yielded < 100
    assert f"output truncated at {cap} bytes" in result
    assert "preview" in result
    assert "rows" in result


@pytest.mark.parametrize("row_count", [49, 50])
def test_bounded_table_at_or_below_row_limit_is_not_omitted(row_count: int) -> None:
    rows = [[f"row-{index}"] for index in range(row_count)]
    result = (
        Card(identity="Rows", available=(".show()",))
        .lazy_table(
            columns=["value"],
            rows_provider=lambda: iter(rows),
            row_count=row_count,
            bounded_row_limit=50,
            show_omission_counts=True,
        )
        .render()
    )

    assert f"row-{row_count - 1}" in result
    assert "preview limited" not in result


def test_bounded_table_row_limit_has_exact_counts_and_copyable_recovery() -> None:
    rows = [[f"row-{index}"] for index in range(53)]
    recovery = "session.persisted_frame('frame_1').to_pandas()"
    card = Card(identity="Rows", available=(".show()",)).lazy_table(
        columns=["value"],
        rows_provider=lambda: iter(rows),
        row_count=len(rows),
        bounded_row_limit=50,
        show_omission_counts=True,
        recovery=recovery,
    )

    result = card.render()

    assert "row-49" in result
    assert "row-50" not in result
    assert "preview limited to 50 rows" in result
    assert "preview (displayed=50 total=53 omitted=3)" in result
    assert recovery in result
    full = card.render(max_output_bytes=None)
    assert "row-52" in full
    assert "preview limited" not in full


def test_bounded_table_wide_rows_hit_byte_budget_before_row_limit() -> None:
    rows = [[f"row-{index}", "x" * 400] for index in range(60)]
    recovery = "session.persisted_frame('frame_1').to_pandas()"
    result = (
        Card(identity="Wide", available=(".show()",))
        .lazy_table(
            columns=["value", "payload"],
            rows_provider=lambda: iter(rows),
            row_count=len(rows),
            bounded_row_limit=50,
            show_omission_counts=True,
            recovery=recovery,
        )
        .render(max_output_bytes=1_000)
    )

    assert len(result.encode()) <= 1_000
    assert "output truncated at 1000 bytes" in result
    assert "total=60" in result
    assert "displayed=50" not in result
    assert recovery in result


def test_bounded_table_zero_rows_renders_none_without_omission() -> None:
    result = (
        Card(identity="Empty", available=(".show()",))
        .lazy_table(
            columns=["value"],
            rows_provider=lambda: iter(()),
            row_count=0,
            bounded_row_limit=50,
            show_omission_counts=True,
        )
        .render()
    )

    assert "preview: none" in result
    assert "omitted=" not in result


def test_renderable_result_mixin_provides_repr_render_and_show(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ExampleResult(RenderableResult):
        def _repr_identity(self) -> str:
            return "ExampleResult id=1"

        def _card(self) -> Card:
            return Card(identity=self._repr_identity(), available=(".show()",)).field("value", "42")

    result = ExampleResult()
    assert repr(result) == "<ExampleResult id=1; call .show() to inspect>"
    assert result.render() == "ExampleResult id=1\nvalue: 42\navailable:\n- .show()"
    result.show(max_output_bytes=None)
    assert capsys.readouterr().out == "ExampleResult id=1\nvalue: 42\navailable:\n- .show()\n"
