"""Tests for the bounded plain-text card formatter."""

from __future__ import annotations

from marivo.introspection.render import format_bounded_card


def test_identity_only_card():
    result = format_bounded_card(identity="Foo bar=1", available=(".render()",))
    assert result.startswith("Foo bar=1")
    assert "available:" in result
    assert "- .render()" in result
    assert not result.endswith("\n")


def test_status_line_included():
    result = format_bounded_card(identity="X", status="evidence=partial", available=(".render()",))
    lines = result.splitlines()
    assert lines[1] == "status: evidence=partial"


def test_columns_line_included():
    result = format_bounded_card(
        identity="X", columns=["bucket_start", "revenue"], available=(".render()",)
    )
    assert "columns: bucket_start | revenue" in result


def test_preview_rows_included():
    result = format_bounded_card(
        identity="X",
        columns=["a", "b"],
        rows=[["1", "2"], ["3", "4"]],
        row_count=2,
        available=(".render()",),
    )
    assert "preview:" in result
    assert "1 | 2" in result
    assert "3 | 4" in result


def test_truncation_line_shows_remaining():
    result = format_bounded_card(
        identity="X",
        columns=["a"],
        rows=[["1"], ["2"], ["3"], ["4"], ["5"]],
        row_count=10,
        preview_truncation_hint="call .preview(limit=...) or .to_pandas()",
        available=(".render()",),
    )
    assert "5 more rows; call .preview(limit=...) or .to_pandas()" in result


def test_column_list_capped_at_eight():
    result = format_bounded_card(
        identity="X",
        columns=["c" + str(i) for i in range(12)],
        available=(".render()",),
    )
    cols_line = next(ln for ln in result.splitlines() if ln.startswith("columns:"))
    assert cols_line.count("|") == 7  # 8 columns -> 7 separators


def test_preview_rows_capped_at_five():
    result = format_bounded_card(
        identity="X",
        columns=["a"],
        rows=[["r" + str(i)] for i in range(10)],
        row_count=10,
        preview_truncation_hint="call .preview(limit=...)",
        available=(".render()",),
    )
    shown = [ln for ln in result.splitlines() if ln.startswith("r")]
    assert len(shown) == 5


def test_no_status_when_none():
    result = format_bounded_card(identity="X", available=(".render()",))
    assert "status:" not in result


def test_multiple_available_entries():
    result = format_bounded_card(
        identity="X",
        available=(".summary()", ".preview(limit=...)", ".to_pandas()", ".render()"),
    )
    assert "- .summary()" in result
    assert "- .preview(limit=...)" in result
    assert "- .to_pandas()" in result
    assert "- .render()" in result


def test_truncation_shown_when_row_count_is_none():
    result = format_bounded_card(
        identity="X",
        columns=["a"],
        rows=[["r" + str(i)] for i in range(10)],
        row_count=None,
        preview_truncation_hint="call .preview(limit=...)",
        available=(".render()",),
    )
    assert "5 more rows; call .preview(limit=...)" in result


def test_truncation_singular_row():
    result = format_bounded_card(
        identity="X",
        columns=["a"],
        rows=[["1"], ["2"], ["3"], ["4"], ["5"], ["6"]],
        row_count=6,
        preview_truncation_hint="call .to_pandas()",
        available=(".render()",),
    )
    assert "1 more row; call .to_pandas()" in result
