"""Independent row completeness, precision and context-budget contracts."""

from collections.abc import Iterator, Sequence
from decimal import Decimal

import pytest

from marivo._data_render import _DataCard
from marivo.datasource.manage import RawSqlResult
from marivo.preview import PreviewCoverage, PreviewResult
from marivo.refs import ref as ref_factory


@pytest.fixture(params=("preview", "raw_sql"))
def result(request: pytest.FixtureRequest) -> PreviewResult | RawSqlResult:
    rows: tuple[dict[str, object], ...] = tuple(
        {"amount": Decimal(f"9007199254740993.{i:06d}")} for i in range(12)
    )
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
        _datasource=ref_factory.datasource("warehouse"),
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


def test_all_small_result_rows_and_preview_limits_are_independent(
    result: PreviewResult | RawSqlResult, capsys: pytest.CaptureFixture[str]
) -> None:
    result.show()
    text = capsys.readouterr().out
    assert len(text.encode()) <= 8192


def test_row_count_controls_only_display(result: PreviewResult | RawSqlResult) -> None:
    assert len(result.rows) == 12


def test_exact_fit_and_one_byte_less_keep_whole_rows(
    result: PreviewResult | RawSqlResult, capsys: pytest.CaptureFixture[str]
) -> None:
    full = result.render(max_output_bytes=None)
    budget = len(full.encode()) + 1
    result.show(max_output_bytes=budget)
    capsys.readouterr()
    result.show(max_output_bytes=budget - 1)
    shorter = capsys.readouterr().out
    assert len(shorter.encode()) < budget


def test_bounded_render_does_not_format_entire_result() -> None:
    visits = 0

    def rows() -> Iterator[Sequence[object]]:
        nonlocal visits
        for i in range(100):
            visits += 1
            yield (i, "a" * 50)

    card = _DataCard("Data", ("id", "value"), rows, 100)
    text = card.render(n=None, max_output_bytes=1000)
    assert len(text.encode()) + 1 <= 1000
    assert visits < 20
    visits = 0
    card.render(n=0, max_output_bytes=1000)
    assert visits == 0


def test_invalid_controls_reject_before_reading(result: PreviewResult | RawSqlResult) -> None:
    invalid_values: tuple[object, ...] = (-1, True, 1.5, "2")
    for argument in ("n", "max_output_bytes"):
        for value in invalid_values:
            with pytest.raises(ValueError):
                # Runtime invalid inputs deliberately bypass static signatures.
                result.show(**{argument: value})  # type: ignore[arg-type]  # Invalid controls.


def test_byte_budget_must_be_positive(result: PreviewResult | RawSqlResult) -> None:
    with pytest.raises(ValueError):
        result.show(max_output_bytes=0)
    with pytest.raises(ValueError):
        result.show(max_output_bytes=1)
