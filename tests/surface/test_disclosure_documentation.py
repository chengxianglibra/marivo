"""Check the public calls in the current disclosure examples against live Help."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import CallableInput
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize(
    ("filename", "receivers"),
    (
        (
            "docs/specs/analysis/lazy-analysis-dsl-examples.md",
            {
                "parents": mv.Population,
                "attributes": mv.AnalysisFrame,
                "row_summary": mv.AnalysisFrame,
                "variance": mv.AnalysisFrame,
                "change.artifact": mv.Artifact,
            },
        ),
        (
            "docs/specs/analysis/lazy-analysis-dsl-scenario-validation.md",
            {
                "focus": mv.Population,
                "detail": mv.AnalysisFrame,
                "rolling": mv.AnalysisFrame,
                "saved.artifact": mv.Artifact,
            },
        ),
        ("docs/specs/semantic/datasource-layer.md", {"snapshot": md.DiscoverySnapshot}),
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
    descriptor = REGISTRY.by_canonical_id("session.attribute")
    assert isinstance(descriptor, CallableInput)
    namespace = {
        "mv": mv,
        "session": session,
        "revenue": revenue,
        "region": region,
        "current_period": august,
        "baseline_period": july,
    }
    exec(compile(descriptor.example.code, "<attribute-help-example>", "exec"), namespace)
    default = namespace["result"].compute()
    assert default.method_facts == ()
    assert default.to_pandas()[("value", "absolute.revenue")].tolist() == [-20, -50, 0]
    result = session.attribute(
        mv.AttributeEndpoint({"revenue": revenue}, during=august),
        mv.AttributeEndpoint({"revenue": revenue}, during=july),
        by=(region,),
        contribution=True,
    ).compute()
    assert result.to_pandas()[("value", "contribution.revenue")].tolist() == [-20, -50, 0]
    facts = result.method_facts
    assert len(facts) == 1 and facts[0].parents[0].metrics[0].method.change.value == -70
    saved = result.where(result["contribution.revenue"] < 0).persist()
    reopened = session.persisted_frame(saved.artifact.id)
    assert reopened.method_facts == facts
    assert reopened.to_pandas()[("value", "contribution.revenue")].tolist() == [-20, -50]
    nonlinear = revenue * revenue
    left = mv.AttributeEndpoint({"squared": nonlinear}, during=august)
    right = mv.AttributeEndpoint({"squared": nonlinear}, during=july)
    with pytest.raises(AnalysisError) as error:
        session.attribute(left, right, by=(region,), contribution=True)
    assert error.value.reason == "unsupported_expression"
    assert "analysis.session.attribute" in str(error.value)
    assert session.attribute(left, right, by=(region,)).compute().method_facts == ()


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
    guides = root / "site/src/content/docs" / language / "latest/guides"
    tutorial = (guides / "python-quick-start.mdx").read_text()
    guide = (guides / "comparison-and-attribution.mdx").read_text()

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
        "attribute-default-investigation",
        "attribute-contribution-facts",
        "attribute-explicit-default-after-refusal",
    ):
        exec(compile(block(guide, marker), "<guide-" + marker + ">", "exec"), namespace)
    explained = namespace["explained"]
    assert isinstance(explained, mv.AnalysisFrame)
    rows = explained.to_pandas()
    assert rows[("value", "contribution_share.revenue")].tolist() == [0.5, 0.5]
    assert explained.method_facts[0].parents[0].metrics[0].method.change.value == -200
    assert namespace["original_explanation"] == explained.method_facts
    nonlinear = namespace["nonlinear_investigation"]
    assert isinstance(nonlinear, mv.AnalysisFrame) and nonlinear.method_facts == ()
    session = namespace["session"]
    assert isinstance(session, mv.Session)
    with pytest.raises(AnalysisError) as captured:
        session.attribute(
            mv.AttributeEndpoint({"squared": namespace["nonlinear"]}, during=namespace["august"]),
            mv.AttributeEndpoint({"squared": namespace["nonlinear"]}, during=namespace["july"]),
            by=(namespace["region"],),
            contribution=True,
        )
    assert captured.value.reason == "unsupported_expression"
    assert captured.value.repair.help_target.display == "analysis.session.attribute"
