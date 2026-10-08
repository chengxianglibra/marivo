"""Independent row completeness, precision and context-budget contracts."""

from collections.abc import Iterator, Sequence
from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

import marivo.semantic as ms
from marivo._data_render import _DataCard
from marivo.datasource.manage import RawSqlResult
from marivo.preview import PreviewCoverage, PreviewResult


@pytest.fixture(params=("preview", "raw_sql"))
def result(request: pytest.FixtureRequest) -> PreviewResult | RawSqlResult:
    rows = tuple({"amount": Decimal(f"9007199254740993.{i:06d}")} for i in range(12))
    if request.param == "preview":
        return PreviewResult(
            kind="semantic_metric",
            ref="sales.amount",
            columns=("amount",),
            types={"amount": "decimal(30,6)"},
            rows=rows,
            requested_limit=12,
            returned_row_count=12,
            is_truncated=True,
            status="passed",
            coverage=PreviewCoverage((), 13, "truncated", "sample_only"),
        )
    return RawSqlResult(
        datasource=ms.ref.datasource("warehouse"),
        backend_type="duckdb",
        sql="SELECT amount",
        reason="inspect amounts",
        columns=("amount",),
        types={"amount": "decimal(30,6)"},
        rows=rows,
        returned_row_count=12,
        timeout_seconds=30,
        duration_ms=1,
        warnings=(),
    )


def test_all_small_result_rows_and_query_limits_are_independent(
    result: PreviewResult | RawSqlResult, capsys: pytest.CaptureFixture[str]
) -> None:
    result.show()
    text = capsys.readouterr().out
    assert "12 total; 12 shown" in text
    assert "9007199254740993.000011" in text
    if isinstance(result, PreviewResult):
        assert "query_truncated: true" in text
    else:
        assert "query_truncated" not in text
    assert "not full-source cardinality" in text
    assert "Omitted:" not in text
    assert len(text.encode()) <= 8192
    assert text == result.render() + "\n"


@pytest.mark.parametrize("n", (0, 1, 7, 12, 100))
def test_row_count_controls_only_display(result: PreviewResult | RawSqlResult, n: int) -> None:
    text = result.render(n=n, max_output_bytes=None)
    assert f"12 total; {min(n, 12)} shown" in text
    assert len(result.rows) == 12
    if n < 12:
        assert f"Omitted: {12 - n} rows; reason=row_limit" in text
        assert ".show(n=None, max_output_bytes=None)" in text
    else:
        assert "Omitted:" not in text


def test_exact_fit_and_one_byte_less_keep_whole_rows(
    result: PreviewResult | RawSqlResult, capsys: pytest.CaptureFixture[str]
) -> None:
    full = result.render(max_output_bytes=None)
    budget = len(full.encode()) + 1
    result.show(max_output_bytes=budget)
    assert capsys.readouterr().out == full + "\n"
    result.show(max_output_bytes=budget - 1)
    shorter = capsys.readouterr().out
    assert len(shorter.encode()) < budget
    assert "reason=output_budget" in shorter
    for line in shorter.splitlines():
        if line.startswith("9007199254740993"):
            assert line in full.splitlines()


def test_empty_result_retains_columns_and_source_boundary(
    result: PreviewResult | RawSqlResult,
) -> None:
    empty = replace(result, rows=(), returned_row_count=0)
    text = empty.render(n=0)
    assert "0 total; 0 shown" in text
    assert "columns: amount" in text
    if isinstance(result, PreviewResult):
        assert "query_truncated: true" in text
    else:
        assert "query_truncated" not in text
    assert "Omitted:" not in text


@pytest.mark.parametrize("argument", ("n", "max_output_bytes"))
@pytest.mark.parametrize("value", (-1, True, False, 1.5, "2"))
def test_invalid_controls_reject_before_reading(
    result: PreviewResult | RawSqlResult, argument: str, value: object
) -> None:
    with pytest.raises(ValueError, match=argument):
        # Runtime invalid inputs deliberately bypass static signatures.
        result.show(**{argument: value})


def test_byte_budget_must_be_positive(result: PreviewResult | RawSqlResult) -> None:
    with pytest.raises(ValueError, match="max_output_bytes"):
        result.show(max_output_bytes=0)
    with pytest.raises(ValueError, match="minimum is"):
        result.show(max_output_bytes=1)


def test_utf8_multiline_precision_and_row_boundary() -> None:
    values = (
        "é\nline\r\t|\\",
        Decimal("123456789012345678.123456"),
        9007199254740993,
        pd.Timedelta(1, unit="ns"),
    )
    card = _DataCard("Data", ("label", "decimal", "integer", "duration"), lambda: (values,), 1)
    text = card.render(n=None, max_output_bytes=None)
    assert "é\\nline\\r\\t\\|\\\\" in text
    assert "123456789012345678.123456 | 9007199254740993" in text
    assert "0 days 00:00:00.000000001" in text
    assert card.render(n=None, max_output_bytes=len(text.encode()) + 1) == text


def test_wide_first_row_does_not_erase_boundaries_or_skip_ahead() -> None:
    card = _DataCard(
        "Data",
        ("value",),
        lambda: (("x" * 10000,), ("small",)),
        2,
        facts=(("unit", "CNY"),),
        boundaries=(("coverage", "incomplete"),),
    )
    text = card.render(n=None, max_output_bytes=500)
    assert "2 total; 0 shown" in text
    assert "unit: CNY" in text and "coverage: incomplete" in text
    assert "small" not in text
    assert "reason=output_budget" in text
    assert ".show(max_output_bytes=None)" in text
    assert "x" * 10000 in card.render(n=None, max_output_bytes=None)


def test_byte_and_row_limits_both_explain_recovery() -> None:
    card = _DataCard("Data", ("value",), lambda: (("x" * 10000,) for _ in range(10)), 10)
    text = card.render(n=2, max_output_bytes=500)
    assert "reason=row_limit+output_budget" in text
    assert ".show(n=None, max_output_bytes=None)" in text


def test_bounded_render_does_not_format_entire_result() -> None:
    visits = 0

    def rows() -> Iterator[Sequence[object]]:
        nonlocal visits
        for i in range(100000):
            visits += 1
            yield (i, "a" * 50)

    card = _DataCard("Data", ("id", "value"), rows, 100000)
    text = card.render(n=None, max_output_bytes=1000)
    assert len(text.encode()) + 1 <= 1000
    assert visits < 20
    visits = 0
    card.render(n=0, max_output_bytes=1000)
    assert visits == 0


@pytest.mark.parametrize(
    "target",
    (
        "analysis.actions.show",
        "analysis.dsl.MaterializedTable.show",
        "semantic.PreviewResult",
        "datasource.RawSqlResult",
    ),
)
def test_native_help_discloses_row_and_byte_controls(target: str) -> None:
    from marivo._help.render import render_help_text

    text = render_help_text(target)[0]
    assert "n:" in text and "max_output_bytes:" in text and "8192" in text
    assert "None removes the budget" in text
    assert "zero means metadata only" in text
    assert "n=20" in text
    assert "table = mv.table\n" not in text
    assert len(text.encode()) <= 8192
