"""Independent public receiver and rejected call-shape typing obligations."""

from pathlib import Path

import pytest

from tests.support.typing import check

pytestmark = pytest.mark.slow

RECEIVERS = (
    "LogicalNumericRelation",
    "LogicalRatioRelation",
    "LogicalRolledNumericRelation",
    "LogicalRolledRatioRelation",
    "LogicalDifferenceRelation",
    "LogicalSelectedNumericRelation",
    "LogicalSelectedDifferenceRelation",
    "LogicalStatisticRelation",
)


def test_deviation_and_statistics_public_contracts(tmp_path: Path) -> None:
    source = tmp_path / "statistics_contracts.py"
    body = "import marivo.analysis as mv\nfrom typing_extensions import assert_type\n"
    for receiver in RECEIVERS:
        body += (
            f"def accepts_{receiver}(value: mv.{receiver}, category: mv.LogicalCategoryRelation, fixed: mv.MaterializedCategoryRelation) -> None:\n"
            "    assert_type(value.deviation(method='zscore'), mv.LogicalDeviationResult)\n"
            "    assert_type(value.deviation(method='mad', partition_by=(category,)), mv.LogicalDeviationResult)\n"
        )
    body += (
        "def fields(result: mv.LogicalDeviationResult, fixed: mv.MaterializedDeviationResult) -> None:\n"
        "    assert_type(result.execute(), mv.MaterializedDeviationResult)\n"
        "    assert_type(result.where(result.score.value.gt(0)), mv.LogicalDeviationResult)\n"
    )
    for field in ("observed", "reference", "deviation", "score"):
        body += f"    assert_type(result.{field}, mv.LogicalNumericRelation)\n"
    body += """def methods(v: mv.LogicalNumericRelation, b: mv.LogicalNumericRelation) -> None:
    assert_type(v.deviation(method="zscore"), mv.LogicalDeviationResult)
    assert_type(v.deviation(method="mad"), mv.LogicalDeviationResult)
    assert_type(v.runs(where=v.value.gt(0)), mv.LogicalTimeRunResult)
    assert_type(v.correlate(b, method="pearson"), mv.LogicalAssociationResult)
    assert_type(v.correlate(b, method="spearman"), mv.LogicalAssociationResult)
    assert_type(v.correlate(b, method="kendall"), mv.LogicalAssociationResult)
    assert_type(v.forecast(horizon=mv.periods(1), model=mv.naive()), mv.LogicalForecastResult)
    assert_type(v.forecast(horizon=mv.periods(1), model=mv.drift()), mv.LogicalForecastResult)
    assert_type(v.forecast(horizon=mv.periods(1), model=mv.seasonal_naive(periods=2)), mv.LogicalForecastResult)
def invalid_deviation(value: mv.LogicalNumericRelation, category: mv.LogicalCategoryRelation, fixed: mv.MaterializedDeviationResult) -> None:
"""
    deviation_calls = (
        "value.deviation()",
        "value.deviation(method='stddev')",
        "value.deviation('zscore')",
        "value.deviation(method='mad', partition_by=[category])",
        "value.deviation(method='mad', partition_by=(value,))",
        "value.deviation(method='mad', partition_by=('region',))",
        "value.deviation(method='mad', threshold=2)",
        "category.deviation(method='mad')",
        "fixed.execute()",
        "fixed.where(True)",
        "value.deviation(method='mad').where(True)",
    )
    body += "".join(f"    {call}\n" for call in deviation_calls)
    body += """def invalid_statistics(v: mv.LogicalNumericRelation, b: mv.LogicalNumericRelation, r: mv.LogicalDeviationResult, c: mv.LogicalCoefficientRelation, f: mv.LogicalForecastResult) -> None:
"""
    statistics_calls = (
        'v.correlate(b, method="invalid")',
        "v.forecast(horizon=1)",
        "r.rollup()",
        "r.attribute()",
        "c.rollup()",
        "f.rollup()",
        "f.attribute()",
    )
    calls = (*deviation_calls, *statistics_calls)
    body += "".join(f"    {call}\n" for call in statistics_calls)
    source.write_text(body)

    call_lines = [body.splitlines().index("    " + call) + 1 for call in calls]
    result = check(source)
    assert result.returncode != 0
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    error_locations = {line.split(": error:", maxsplit=1)[0] for line in errors}
    assert error_locations == {f"{source}:{line}" for line in call_lines}, (
        result.stdout + result.stderr
    )
    assert len(errors) == len(calls), result.stdout + result.stderr
