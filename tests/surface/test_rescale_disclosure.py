"""The explicit scaling entry point has one executable public disclosure path."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo.semantic as ms
from marivo._authoring.model import AuthoringCapability
from marivo._help.render import render_help_text
from marivo.cli import main
from marivo.semantic._capabilities.registry import REGISTRY
from tests.shared_fixtures import DslCaseFactory


def test_rescale_callable_and_cli_share_the_bounded_native_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text, _, _ = render_help_text("semantic.rescale")
    assert render_help_text(ms.rescale)[0] == text
    assert text.startswith("Target: semantic.rescale\nKind: callable\n")
    for fact in (
        "finite, strictly positive",
        "known source unit",
        "Boolean and dynamic divisors are rejected",
        "preserves stage, source roots, time restrictions and sum permissions",
        "cross-dimension assertions and divisor=1",
        "No conversion is inferred",
        "Fixed duration factors",
    ):
        assert fact in text
    budget = REGISTRY.render_budget(REGISTRY.render_class("rescale"))
    assert len(text.splitlines()) <= budget.max_lines + 4
    assert len(text) <= budget.max_codepoints + 100

    main(["help", "semantic.rescale"])
    captured = capsys.readouterr()
    expected = re.sub(
        r"marivo\.help\(([\"'])([a-z_][a-z0-9_.]*)\1\)",
        lambda match: "marivo help " + match.group(2),
        text,
    )
    assert captured.out == expected + "\n"
    assert captured.err == ""


def test_rescale_help_and_bilingual_site_examples_execute_their_actual_code(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    descriptor = REGISTRY.by_canonical_id("rescale")
    assert isinstance(descriptor, AuthoringCapability)
    assert descriptor.minimal_example is not None
    root = Path(__file__).resolve().parents[2] / "site/src/content/docs"
    examples: list[str] = []
    for language in ("docs", "zh-cn/docs"):
        source = (root / language / "latest/modeling/semantic-layer.mdx").read_text()
        match = re.search(
            r"\{/\* example: semantic-rescale \*/\}\s*```python\n(.*?)```",
            source,
            re.DOTALL,
        )
        assert match is not None
        examples.append(match.group(1))
    assert examples[0] == examples[1]

    case = analysis_dsl_case_factory("j2", revenue_unit="B")
    byte_count = case.catalog.fields.get("sales.order.amount")
    namespace: dict[str, object] = {
        "ms": ms,
        "total_bytes": byte_count.sum(),
        "hourly_p95_bytes": byte_count.quantile(0.95),
    }
    exec(compile(descriptor.minimal_example, "<rescale-help-example>", "exec"), namespace)
    help_result = namespace["daily_bandwidth"]
    assert isinstance(help_result, Deferred)
    exec(compile(examples[0], "<rescale-site-example>", "exec"), namespace)
    metrics = {"help_bandwidth": help_result}
    for name in ("daily_bandwidth", "p95_bandwidth"):
        value = namespace[name]
        assert isinstance(value, Deferred)
        metrics[name] = value
    frame = case.session.observe(metrics).compute()
    rows = frame.to_pandas()
    assert rows["help_bandwidth"].tolist() == pytest.approx(
        [660 / (86400 * 1024**3)], rel=1e-12, abs=0
    )
    assert rows["daily_bandwidth"].tolist() == pytest.approx(
        [660 / (86400 * 1024**3)], rel=1e-12, abs=0
    )
    assert rows["p95_bandwidth"].tolist() == pytest.approx(
        [160 / (3600 * 1024**3)], rel=1e-12, abs=0
    )
    assert {column.unit for column in frame._schema.values} == {"GiB/s"}
