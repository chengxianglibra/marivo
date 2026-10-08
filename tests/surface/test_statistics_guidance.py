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


def test_result_types_and_negative_calls_have_concrete_static_contracts(tmp_path: Path) -> None:
    source = tmp_path / "statistics_positive.py"
    source.write_text("""import marivo.analysis as mv
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
""")
    positive = check(source)
    assert positive.returncode == 0, positive.stdout + positive.stderr
    calls = (
        'v.correlate(b, method="invalid")',
        "v.forecast(horizon=1)",
        "r.rollup()",
        "r.attribute()",
        "c.rollup()",
        "f.rollup()",
        "f.attribute()",
    )
    source = tmp_path / "statistics_negative.py"
    source.write_text(
        "import marivo.analysis as mv\ndef invalid(v: mv.LogicalNumericRelation, b: mv.LogicalNumericRelation, r: mv.LogicalDeviationResult, c: mv.LogicalCoefficientRelation, f: mv.LogicalForecastResult) -> None:\n"
        + "".join("    " + c + "\n" for c in calls)
    )
    negative = check(source)
    assert negative.returncode != 0
    for line in range(3, 3 + len(calls)):
        assert f"{source}:{line}: error:" in negative.stdout, negative.stdout + negative.stderr


def test_cli_and_api_documentation_use_current_entry() -> None:
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
    api = (ROOT / "docs/api/analysis.rst").read_text()
    for result_type in ("DeviationResult", "TimeRunResult", "AssociationResult", "ForecastResult"):
        for prefix in ("Logical", "Materialized"):
            assert ".. autoclass:: " + prefix + result_type in api


def test_latest_examples_cover_nine_methods_and_continuations() -> None:
    for edition in ("docs", "zh-cn/docs"):
        source = (
            ROOT / "site/src/content/docs" / edition / "latest/guides/statistics.mdx"
        ).read_text()
        for token in (
            'method="zscore"',
            'method="mad"',
            ".runs(where=",
            'method="pearson"',
            'method="spearman"',
            'method="kendall"',
            "model=mv.naive()",
            "model=mv.drift()",
            "model=mv.seasonal_naive(periods=2)",
            ".where(",
            ".rank(",
            "mv.table(",
            ".contract()",
            ".show()",
        ):
            assert token in source, (edition, token)


@pytest.mark.runtime
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
        members.observe(
            ms.ref.metric("sales.total_0"), during=grid, by=(ms.ref.entity("sales.order"),)
        )
        .group_by(grid)
        .rollup()
    )
    namespace: dict[str, object] = {
        "mv": mv,
        "revenue": members.read(ms.ref.measure("sales.order.profile_0")),
        "order_count": members.read(ms.ref.measure("sales.order.profile_1")),
        "daily": daily,
    }
    english, chinese = (
        _example("en", "nine-statistical-methods"),
        _example("zh", "nine-statistical-methods"),
    )
    assert english == chinese and english.startswith('zscore = revenue.deviation(method="zscore")')
    exec(compile(english, "bilingual-nine-method-example", "exec"), namespace)
    assert isinstance(namespace["chosen"], mv.MaterializedDeviationResult)
    assert isinstance(namespace["segments"], mv.MaterializedTimeRunResult)
    assert isinstance(namespace["selected"], mv.MaterializedForecastResult)
