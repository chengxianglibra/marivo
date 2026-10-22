"""Check the public calls in the current disclosure examples against live Help."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.datasource as md
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import CallableInput
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.core.frame_method_facts import FrameAdditiveMethod
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    ("filename", "receivers"),
    (
        (
            "docs/architecture/analysis/examples.md",
            {
                "parents": mv.Population,
                "attributes": mv.AnalysisFrame,
                "row_summary": mv.AnalysisFrame,
                "variance": mv.AnalysisFrame,
                "change.artifact": mv.Artifact,
            },
        ),
        ("docs/architecture/datasource/discovery.md", {"snapshot": md.DiscoverySnapshot}),
        ("docs/api/datasource.rst", {"snapshot": md.DiscoverySnapshot}),
    ),
)
def test_documented_disclosure_calls_resolve_to_live_members(
    filename: str, receivers: dict[str, type]
) -> None:
    source = (Path(__file__).resolve().parents[2] / filename).read_text()
    for receiver, owner in receivers.items():
        # These named receivers belong to the examples above. Check their display
        # calls, including a possible chained helper, without executing a source.
        calls = re.findall(rf"\b{re.escape(receiver)}((?:\.\w+\(\))*\.show\([^)]*\))", source)
        assert calls, (filename, receiver)
        for suffix in calls:
            expression = ast.parse(receiver + suffix, mode="eval").body
            assert isinstance(expression, ast.Call)
            while isinstance(expression.func, ast.Attribute) and isinstance(
                expression.func.value, ast.Call
            ):
                expression = expression.func.value
            assert isinstance(expression.func, ast.Attribute)
            member = getattr(owner, expression.func.attr)
            text, _, _ = render_help_text(member)
            assert "Kind: callable" in text


def test_public_attribute_example_and_explicit_refusal_use_real_source(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    revenue = session.catalog.metrics.get("sales.revenue")
    region = session.catalog.fields.get("sales.customer.region")
    august = session.time_scope(start="2026-08-01", end="2026-09-01")
    july = session.time_scope(start="2026-07-01", end="2026-08-01")
    source_frame = session.observe({"revenue": revenue})
    descriptor = REGISTRY.by_canonical_id("analysis_frame.attribute")
    assert isinstance(descriptor, CallableInput)
    namespace = {"source_frame": source_frame, "region": region}
    exec(compile(descriptor.example.code, "<attribute-help-example>", "exec"), namespace)
    pending = namespace["result"]
    assert isinstance(pending, mv.AnalysisFrame)
    level = pending.compute()
    assert level.to_pandas()[("value", "contribution.revenue")].tolist() == [610, 50, 0]
    current = session.observe({"revenue": revenue}, by=(august,))
    baseline = session.observe({"revenue": revenue}, by=(july,))
    comparison = current.compare(
        {"reference": baseline}, match={"reference": mv.ordinal()}
    ).compute()
    target = comparison.select({"revenue": comparison["reference.absolute.revenue"]})
    result = target.attribute(by=(region,)).compute()
    assert result.to_pandas()[("value", "contribution.revenue")].tolist() == [-20, -50, 0]
    facts = result.method_facts
    assert len(facts) == 1
    method = facts[0].parents[0].metrics[0].method
    assert isinstance(method, FrameAdditiveMethod)
    assert method.change.value == -70
    saved = result.where(result["contribution.revenue"] < 0).persist()
    reopened = session.persisted_frame(saved.artifact.id)
    assert reopened.method_facts == facts
    assert reopened.to_pandas()[("value", "contribution.revenue")].tolist() == [-20, -50]
    nonlinear = revenue * revenue
    source = session.observe({"squared": nonlinear})
    with pytest.raises(AnalysisError) as error:
        source.attribute(by=(region,))
    assert vars(error.value)["reason"] == "unsupported_expression"
    investigation = session.observe({"squared": nonlinear}, by=(region,)).compute()
    assert investigation.method_facts == ()


@pytest.mark.parametrize("language", ("docs", "zh-cn/docs"))
def test_latest_attribute_guide_runs_its_actual_tutorial_blocks(
    language: str,
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo

    root = Path(__file__).resolve().parents[2]
    assert Path(marivo.__file__).resolve() == root / "marivo/__init__.py"
    monkeypatch.setenv("PYTHONPATH", str(root))
    monkeypatch.chdir(tmp_path)
    guides = root / "site/src/content/docs" / language / "latest"
    tutorial = (guides / "start/python.mdx").read_text() + (guides / "analysis/dsl.mdx").read_text()
    guide = (guides / "analysis/dsl.mdx").read_text()

    def block(source: str, marker: str) -> str:
        match = re.search(
            r"\{/\* example: " + re.escape(marker) + r" \*/\}\s*```python\n(.*?)```",
            source,
            re.DOTALL,
        )
        assert match is not None, marker
        return match.group(1)

    namespace: dict[str, object] = {}
    exec(compile(block(tutorial, "demo-seed"), "<tutorial-seed>", "exec"), namespace)
    semantic_project_factory(
        {
            "datasources/warehouse.py": block(tutorial, "demo-datasource"),
            "sales/_domain.py": block(tutorial, "demo-model"),
        },
        load=False,
    )
    exec(compile(block(tutorial, "demo-analysis"), "<tutorial-analysis>", "exec"), namespace)
    for marker in (
        "compare-query",
        "aligned-absolute-only",
        "attribute-default-investigation",
        "attribute-contribution-facts",
        "attribute-explicit-default-after-refusal",
    ):
        exec(compile(block(guide, marker), "<guide-" + marker + ">", "exec"), namespace)
    for marker in (
        "comparison-endpoint-followup",
        "broadcast-reference",
        "source-and-result-filters",
    ):
        exec(compile(block(guide, marker), "<guide-" + marker + ">", "exec"), namespace)
    time_guide = (guides / "analysis/time-series.mdx").read_text()
    for marker in ("period-comparison-query", "shifted-period-comparison-query"):
        exec(compile(block(time_guide, marker), "<time-" + marker + ">", "exec"), namespace)
    statistics = (guides / "analysis/statistics.mdx").read_text()
    for marker in ("frame-partition-share", "pandas-paired-statistics"):
        exec(compile(block(statistics, marker), "<statistics-" + marker + ">", "exec"), namespace)
    east_total = namespace["east_total"]
    assert isinstance(east_total, mv.AnalysisFrame)
    assert east_total.to_pandas()[("value", "revenue")].tolist() == [500]
    normalized = namespace["normalized"]
    assert isinstance(normalized, mv.AnalysisFrame)
    normalized_rows = normalized.to_pandas()
    assert normalized_rows[("value", "total")].tolist() == [800, 800]
    assert normalized_rows[("value", "share")].tolist() == [0.25, 0.75]
    assert normalized_rows[("value", "rows")].tolist() == [2, 2]
    explained = namespace["explained"]
    assert isinstance(explained, mv.AnalysisFrame)
    rows = explained.to_pandas()
    assert rows[("value", "contribution_share.revenue")].tolist() == [0.5, 0.5]
    method = explained.method_facts[0].parents[0].metrics[0].method
    assert isinstance(method, FrameAdditiveMethod)
    assert method.change.value == -200
    assert namespace["original_explanation"] == explained.method_facts
    nonlinear = namespace["nonlinear_investigation"]
    assert isinstance(nonlinear, mv.AnalysisFrame) and nonlinear.method_facts == ()
    session = namespace["session"]
    assert isinstance(session, mv.Session)
    nonlinear_metric = namespace["nonlinear"]
    selected_region = namespace["region"]
    assert isinstance(nonlinear_metric, Deferred)
    assert isinstance(selected_region, Deferred)
    source = session.observe({"squared": nonlinear_metric})
    with pytest.raises(AnalysisError) as captured:
        source.attribute(by=(selected_region,))
    assert vars(captured.value)["reason"] == "unsupported_expression"
    assert captured.value.repair is not None
    assert captured.value.repair.help_target.display == "analysis.analysis_frame.attribute"


def test_documentation_scripts_run_without_fixture_model_loading(tmp_path: Path) -> None:
    """Public files must also work in fresh processes, including source-free reopen."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    pages = root / "site/src/content/docs/docs/latest"
    tutorial = "\n".join(
        (pages / page).read_text()
        for page in (
            "start/python.mdx",
            "analysis/dsl.mdx",
            "modeling/semantic-layer.mdx",
        )
    )

    def named(marker: str) -> str:
        match = re.search(
            r"\{/\* example: " + re.escape(marker) + r" \*/\}\s*```python\n(.*?)```",
            tutorial,
            re.DOTALL,
        )
        assert match is not None, marker
        return match.group(1)

    def snippets(page: str) -> list[str]:
        return re.findall(r"```python\n(.*?)```", (pages / page).read_text(), re.DOTALL)

    def write(project: Path, filename: str, source: str) -> None:
        path = project / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)

    def run(project: Path, script: str) -> None:
        completed = subprocess.run(
            [sys.executable, script],
            cwd=project,
            env={**os.environ, "PYTHONPATH": str(root)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    for filename, marker in (
        ("seed.py", "demo-seed"),
        ("models/datasources/warehouse.py", "demo-datasource"),
        ("models/semantic/sales/_domain.py", "demo-model"),
        ("analysis.py", "demo-analysis"),
    ):
        write(tmp_path, filename, named(marker))
    run(tmp_path, "seed.py")
    write(tmp_path, "quick.py", named("quick-analysis"))
    run(tmp_path, "quick.py")
    for page, filename in (
        ("analysis/dsl.mdx", "dsl_examples.py"),
        ("analysis/time-series.mdx", "time_examples.py"),
        ("analysis/statistics.mdx", "statistics_examples.py"),
    ):
        source = "\n".join(snippets(page))
        if page == "analysis/time-series.mdx":
            source += (
                "\nassert len(daily.to_pandas()) == 31\n"
                'assert rolling.to_pandas()[("value", "revenue")].iloc[4] == 100\n'
                'assert month_to_date.to_pandas()[("value", "revenue")].iloc[14] == 200\n'
            )
        write(tmp_path, filename, source)
        run(tmp_path, filename)
    write(
        tmp_path,
        "model_checks.py",
        "\n".join(named(marker) for marker in ("readiness-3", "readiness-1", "readiness-2")),
    )
    run(tmp_path, "model_checks.py")

    extended = tmp_path / "extended-model"
    write(extended, "seed.py", named("demo-seed"))
    write(extended, "add_cost.py", named("semantic-cost-data"))
    write(extended, "models/datasources/warehouse.py", named("demo-datasource"))
    write(
        extended,
        "models/semantic/sales/_domain.py",
        named("semantic-model") + "\n" + named("semantic-composition"),
    )
    write(
        extended,
        "check_metrics.py",
        "import marivo.analysis as mv\n"
        'session = mv.session.create("extended-model")\n'
        "metrics = session.catalog.metrics\n"
        'result = session.observe({name: metrics.get("sales." + name) for name in '
        '("revenue", "average_order_value", "gross_margin_rate")}).compute().to_pandas()\n'
        'assert result[("value", "revenue")].iloc[0] == 1800\n'
        'assert result[("value", "average_order_value")].iloc[0] == 300\n'
        'assert abs(result[("value", "gross_margin_rate")].iloc[0] - 0.4) < 1e-12\n',
    )
    run(extended, "seed.py")
    run(extended, "add_cost.py")
    run(extended, "check_metrics.py")
    for filename, source in zip(
        ("save_ids.py", "reopen.py"), snippets("analysis/results.mdx"), strict=True
    ):
        write(tmp_path, filename, source)
    run(tmp_path, "save_ids.py")
    (tmp_path / "models/semantic/sales/_domain.py").unlink()
    (tmp_path / "warehouse.duckdb").unlink()
    run(tmp_path, "reopen.py")

    calendar = tmp_path / "calendar-demo"
    write(calendar, "models/datasources/warehouse.py", named("demo-datasource"))
    for filename, source in zip(
        ("seed_calendar.py", "models/semantic/sales/_domain.py", "check_calendar.py"),
        [
            next(
                match.group(1)
                for match in re.finditer(
                    r"\{/\* example: " + marker + r" \*/\}\s*```python\n(.*?)```",
                    (pages / "modeling/semantic-layer.mdx").read_text(),
                    re.DOTALL,
                )
            )
            for marker in ("calendar-seed", "calendar-model", "calendar-read")
        ],
        strict=True,
    ):
        write(calendar, filename, source)
    run(calendar, "seed_calendar.py")
    run(calendar, "check_calendar.py")
