"""Tests for the bounded plain-text card formatter."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import derive_summaries
from marivo.render import (
    _DEFAULT_MAX_OUTPUT_BYTES,
    Card,
    RenderableResult,
    format_bounded_card,
)


def test_format_bounded_card_identity_only_card() -> None:
    result = format_bounded_card(identity="Foo bar=1", available=(".render()",))
    assert result.startswith("Foo bar=1")
    assert "available:" in result
    assert "- .render()" in result
    assert not result.endswith("\n")


def test_format_bounded_card_status_line_included_when_provided() -> None:
    result = format_bounded_card(
        identity="X",
        status="evidence=partial",
        available=(".render()",),
    )
    assert "status: evidence=partial" in result


def test_format_bounded_card_status_line_omitted_when_none() -> None:
    result = format_bounded_card(identity="X", status=None, available=(".render()",))
    assert "status:" not in result


def test_format_bounded_card_table_columns_and_preview_rows() -> None:
    result = format_bounded_card(
        identity="X",
        columns=["bucket_start", "revenue"],
        rows=[["2026-06-01", "10"], ["2026-06-02", "12"]],
        row_count=2,
        available=(".render()",),
    )
    assert "columns: bucket_start | revenue" in result
    assert "preview:" in result
    assert "2026-06-01 | 10" in result
    assert "2026-06-02 | 12" in result


def test_format_bounded_card_does_not_cap_columns_when_unbounded() -> None:
    columns = [f"c{i}" for i in range(12)]
    result = format_bounded_card(
        identity="X",
        columns=columns,
        rows=[columns],
        row_count=1,
        available=(".render()",),
        max_output_bytes=None,
    )
    assert f"columns: {' | '.join(columns)}" in result


def test_format_bounded_card_does_not_cap_rows_when_unbounded() -> None:
    rows = [[f"r{i}"] for i in range(12)]
    result = format_bounded_card(
        identity="X",
        columns=["value"],
        rows=rows,
        row_count=len(rows),
        available=(".render()",),
        max_output_bytes=None,
    )
    for row in rows:
        assert row[0] in result.splitlines()
    assert "output truncated" not in result


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


def test_cap_below_minimum_raises_value_error_with_minimum() -> None:
    with pytest.raises(ValueError, match=r"minimum.*pass max_output_bytes=None for full output"):
        format_bounded_card(identity="X", available=(".render()",), max_output_bytes=1)


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


def test_default_truncation_does_not_teach_preview() -> None:
    rendered = format_bounded_card(
        identity="MetricFrame ref=frame_1 rows=10",
        columns=["bucket_start", "value"],
        rows=[["2026-06-01", "1"]],
        row_count=10,
        available=(".show()", ".contract()", ".to_pandas()"),
        max_output_bytes=_DEFAULT_MAX_OUTPUT_BYTES,
    )
    assert ".preview(" not in rendered


def test_derive_summaries_uses_docstring_topic_and_override() -> None:
    def resolve(name: str) -> object | None:
        def fn() -> None:
            """First line is the summary.

            Second paragraph ignored.
            """

        return {"fn": fn, "novalue": None}.get(name)

    topics = {
        "topic_a": Descriptor(surface="x", kind="topic", symbol="topic_a", summary="topic summary"),
    }
    out = derive_summaries(
        ("fn", "topic_a", "novalue", "aliased"),
        resolve,
        topics,
        overrides={"aliased": "alias summary"},
    )
    assert out["fn"] == "First line is the summary."
    assert out["topic_a"] == "topic summary"
    assert out["aliased"] == "alias summary"
    assert out["novalue"] == ""
