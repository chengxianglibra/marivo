"""Statistical Help reachability, typing and executable documentation."""

import subprocess
from pathlib import Path

import pytest

from marivo._help.render import help as help_api
from marivo.analysis._capabilities.dataset_model import NavigationInput
from marivo.analysis._capabilities.dataset_registry import prepare
from tests.shared_fixtures import DslCaseFactory
from tests.support.paths import PROJECT_ROOT
from tests.support.typing import check

ROOT = PROJECT_ROOT
TARGETS = (
    "dsl.NumericComparison.deviation",
    "dsl.NumericComparison.runs",
    "dsl.NumericComparison.correlate",
    "dsl.NumericComparison.forecast",
    "LogicalDeviationResult",
    "MaterializedDeviationResult",
    "LogicalTimeRunResult",
    "MaterializedTimeRunResult",
    "LogicalAssociationResult",
    "MaterializedAssociationResult",
    "LogicalForecastResult",
    "MaterializedForecastResult",
    "dsl.LogicalDeviationResult.where",
    "dsl.MaterializedDeviationResult.where",
    "dsl.LogicalTimeRunResult.where",
    "dsl.MaterializedTimeRunResult.where",
    "dsl.LogicalAssociationResult.where",
    "dsl.MaterializedAssociationResult.where",
    "dsl.LogicalForecastResult.where",
    "dsl.MaterializedForecastResult.where",
)


@pytest.mark.parametrize("target", TARGETS)
def test_focused_help_is_independent_bounded_and_owned(
    target: str, capsys: pytest.CaptureFixture[str]
) -> None:
    help_api("analysis." + target)
    text = capsys.readouterr().out
    assert 0 < len(text.encode()) <= 8192
    assert "Example" in text or "example" in text or "Acquire:" in text


def test_nine_methods_are_reachable_through_existing_groups() -> None:
    registry = prepare()
    groups = {
        "methods.rows": ("dsl.NumericComparison.deviation", "dsl.NumericComparison.runs"),
        "methods.association": ("dsl.NumericComparison.correlate",),
        "methods.forecast": ("dsl.NumericComparison.forecast",),
    }
    for group, targets in groups.items():
        owner = registry.by_canonical_id(group)
        assert isinstance(owner, NavigationInput)
        assert set(targets) <= set(owner.members)


@pytest.mark.slow
def test_result_types_and_negative_calls_have_concrete_static_contracts(tmp_path: Path) -> None:
    source = tmp_path / "statistics_contracts.py"
    calls = (
        'v.correlate(b, method="invalid")',
        "v.forecast(horizon=1)",
        "r.rollup()",
        "r.attribute()",
        "c.rollup()",
        "f.rollup()",
        "f.attribute()",
    )
    source_text = """import marivo.analysis as mv
from typing_extensions import assert_type
def methods(v: mv.LogicalNumericRelation, b: mv.LogicalNumericRelation) -> None:
    assert_type(v.deviation(method="zscore"), mv.LogicalDeviationResult)
    assert_type(v.deviation(method="mad"), mv.LogicalDeviationResult)
    assert_type(v.runs(where=v.value.gt(0)), mv.LogicalTimeRunResult)
    assert_type(v.correlate(b, method="pearson"), mv.LogicalAssociationResult)
    assert_type(v.correlate(b, method="spearman"), mv.LogicalAssociationResult)
    assert_type(v.correlate(b, method="kendall"), mv.LogicalAssociationResult)
    assert_type(v.forecast(horizon=mv.periods(1), model=mv.naive()), mv.LogicalForecastResult)
    assert_type(v.forecast(horizon=mv.periods(1), model=mv.drift()), mv.LogicalForecastResult)
    assert_type(v.forecast(horizon=mv.periods(1), model=mv.seasonal_naive(periods=2)), mv.LogicalForecastResult)
def invalid(v: mv.LogicalNumericRelation, b: mv.LogicalNumericRelation, r: mv.LogicalDeviationResult, c: mv.LogicalCoefficientRelation, f: mv.LogicalForecastResult) -> None:
""" + "".join("    " + call + "\n" for call in calls)
    source.write_text(source_text)
    call_lines = [source_text.splitlines().index("    " + call) + 1 for call in calls]
    result = check(source)
    assert result.returncode != 0
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    error_locations = {line.split(": error:", maxsplit=1)[0] for line in errors}
    assert error_locations == {f"{source}:{line}" for line in call_lines}, (
        result.stdout + result.stderr
    )
    assert len(errors) == len(calls), result.stdout + result.stderr


def test_cli_help_uses_public_entry() -> None:
    result = subprocess.run(
        [str(ROOT / ".venv/bin/marivo"), "help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    text = result.stdout
    assert "marivo.help" in text


@pytest.mark.slow
def test_bilingual_nine_method_example_executes_with_current_continuations(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import marivo.analysis as mv
    import marivo.semantic as ms
    from tests.analysis.statistics.deviation_fixture import prepare_profiles
    from tests.support.documentation import _example

    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KC", "table", "us", "UTC", False, followup=True)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    daily = (
        members.observe(ms.ref.metric("sales.total_0"), during=grid, by=(mv.member(),))
        .group_by(grid)
        .rollup()
    )
    namespace: dict[str, object] = {
        "mv": mv,
        "revenue": members.read(ms.ref.measure("sales.order.profile_0")),
        "order_count": members.read(ms.ref.measure("sales.order.profile_1")),
        "daily": daily,
    }
    english = _example("en", "nine-statistical-methods")
    exec(compile(english, "bilingual-nine-method-example", "exec"), namespace)
    assert isinstance(namespace["chosen"], mv.MaterializedDeviationResult)
    assert isinstance(namespace["segments"], mv.MaterializedTimeRunResult)
    assert isinstance(namespace["selected"], mv.MaterializedForecastResult)
