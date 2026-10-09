"""Independent public receiver and rejected call-shape typing obligations."""

from pathlib import Path

from tests.support.typing import check

RECEIVERS = (
    "LogicalNumericRelation",
    "LogicalRatioRelation",
    "LogicalRolledNumericRelation",
    "LogicalRolledRatioRelation",
    "LogicalDifferenceRelation",
    "LogicalSelectedNumericRelation",
    "LogicalSelectedDifferenceRelation",
    "LogicalStatisticRelation",
    "MaterializedNumericRelation",
    "MaterializedRatioRelation",
    "MaterializedRolledNumericRelation",
    "MaterializedRolledRatioRelation",
    "MaterializedDifferenceRelation",
    "MaterializedSelectedNumericRelation",
    "MaterializedSelectedDifferenceRelation",
    "MaterializedStatisticRelation",
    "MaterializedGroupedNumericRelation",
)


def test_all_frozen_concrete_receivers_and_owned_fields(tmp_path: Path) -> None:
    source = tmp_path / "deviation_positive.py"
    body = "import marivo.analysis as mv\nfrom typing_extensions import assert_type\n"
    for receiver in RECEIVERS:
        body += (
            f"def accepts_{receiver}(value: mv.{receiver}, category: mv.LogicalCategoryRelation, fixed: mv.MaterializedCategoryRelation) -> None:\n"
            "    assert_type(value.deviation(method='zscore'), mv.LogicalDeviationResult)\n"
            "    assert_type(value.deviation(method='mad', partition_by=(category, fixed)), mv.LogicalDeviationResult)\n"
        )
    body += (
        "def fields(result: mv.LogicalDeviationResult, fixed: mv.MaterializedDeviationResult) -> None:\n"
        "    assert_type(result.execute(), mv.MaterializedDeviationResult)\n"
        "    assert_type(result.where(result.score.value.gt(0)), mv.LogicalDeviationResult)\n"
        "    assert_type(fixed.where(fixed.score.value.is_defined()), mv.LogicalDeviationResult)\n"
    )
    for field in ("observed", "reference", "deviation", "score"):
        body += f"    assert_type(result.{field}, mv.LogicalNumericRelation)\n    assert_type(fixed.{field}, mv.MaterializedNumericRelation)\n"
    source.write_text(body)
    result = check(source)
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_statistical_calls_are_individually_rejected(tmp_path: Path) -> None:
    source = tmp_path / "deviation_negative.py"
    setup = (
        "import marivo.analysis as mv\n"
        "def invalid(value: mv.LogicalNumericRelation, category: mv.LogicalCategoryRelation, fixed: mv.MaterializedDeviationResult) -> None:\n"
    )
    calls = (
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
    source.write_text(setup + "".join(f"    {call}\n" for call in calls))
    result = check(source)
    assert result.returncode != 0
    for line in range(3, 3 + len(calls)):
        assert f"{source}:{line}: error:" in result.stdout, result.stdout + result.stderr
