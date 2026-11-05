"""Small default-gate probes for the closed semantic object consumption surface."""

import hashlib
from pathlib import Path

from tests.support.typing import check


def test_semantic_object_positive_and_rejected_roles(tmp_path: Path) -> None:
    from tests.support.paths import PROJECT_ROOT

    probe_id = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    source = tmp_path / f"semantic_object_rejections_{probe_id}.py"
    program = """from typing import Literal

import marivo.analysis as mv
import marivo.semantic as ms

def rejected(session: mv.Session, metric: ms.Metric, amount: ms.Measure, dimension: ms.Dimension) -> None:
    metric.value  # invalid: removed conversion
    metric.ref  # invalid: removed conversion
    session.catalog.get(metric)  # invalid: object is already closed
    metric + amount  # invalid: stage mismatch
    dimension + 1  # invalid: classification is not a quantity
    dimension.sum()  # invalid: classification has no sum permission
    session.observe({"bad": amount})  # invalid: unfinished row stage
    session.observe({"bad": metric > 0})  # invalid: boolean metric role
    ms.CatalogEntry  # invalid: removed public family
    ms.MetricEntry  # invalid: removed public variant

def rejected_binding(amount: ms.Measure, paid_at: ms.TimeDimension) -> None:
    @ms.metric()
    def revenue_at(*, event: ms.TimeParameter) -> ms.MetricExpression[Literal["numeric"]]:
        return amount.sum(time=event)
    revenue_at(event=paid_at)  # invalid: decorator returns a Metric expression
    revenue_at.bind_time(event="paid_at")  # invalid: use a TimeDimension

def rejected_sample(metric: ms.Metric, paid_at: ms.TimeDimension, region: ms.Dimension) -> None:
    metric.per(region, grain=mv.grain("day"))  # invalid: requires a TimeDimension
    metric.per((paid_at,), grain=mv.grain("day"))  # invalid: requires one field
    metric.per(paid_at, grain=None)  # invalid: use a builtin Grain
    metric.per(paid_at, mv.grain("day"))  # invalid: grain is keyword-only
"""
    source.write_text(program)
    completed = check(PROJECT_ROOT / "tests/typing/semantic_object_contract.py", source)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "semantic_object_contract.py:" not in output, output
    for line, text in enumerate(program.splitlines(), 1):
        if "# invalid:" in text:
            assert f"{source}:{line}: error:" in output, output
