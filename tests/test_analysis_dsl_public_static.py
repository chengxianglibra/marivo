"""Independent rejected call shapes at the public typing boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_public_dsl_rejects_wrong_kinds_methods_and_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "bad_public_analysis.py"
    source.write_text(
        """
import marivo.analysis as mv
import marivo.semantic as ms
session = mv.session.get_or_create("static", report_timezone="UTC")
members = session.members(ms.ref.entity("sales.customer"))
session.members(ms.ref.metric("sales.revenue"))
members.read(ms.ref.entity("sales.customer"))
category = members.read(ms.ref.dimension("sales.customer.region"))
assert isinstance(category, mv.LogicalCategoryRelation)
category.compare(category)
category.where(category.value.eq("west")).where(category.value.eq("west"))
members.show()
fixed = members.execute()
fixed.execute()
category.where(category.value.eq("west")).execute().members().observe(ms.ref.metric("sales.revenue"))
observed = members.observe(ms.ref.metric("sales.revenue"), during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=ms.ref.relationship("sales.buyer"))
assert isinstance(observed, mv.LogicalNumericRelation)
observed.summarize(mv.mean()).compare(category)
observed.compare(observed).rollup()
observed.correlate(observed, method="pearson")
mv.route(ms.ref.metric("sales.revenue"), through=(ms.ref.relationship("sales.buyer"),))
mv.sum("extra")
"""
    )
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            str(root / ".venv/bin/mypy"),
            "--no-pretty",
            "--no-color-output",
            "--python-version",
            "3.10",
            str(source),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    output = completed.stdout
    assert 'Argument 1 to "members"' in output
    assert 'Argument 1 to "read"' in output
    assert 'has no attribute "compare"' in output
    assert 'LogicalSelectedCategoryRelation" has no attribute "where"' in output
    assert 'has no attribute "show"' in output
    assert 'has no attribute "execute"' in output
    assert 'LogicalFixedAnalysisDomain" has no attribute "observe"' in output
    assert 'Argument 1 to "compare"' in output
    assert 'LogicalDifferenceRelation" has no attribute "rollup"' in output
    assert 'Argument "method" to "correlate"' in output
    assert 'Argument 1 to "route"' in output
    assert 'Too many arguments for "sum"' in output


def test_public_observation_allows_omitted_window_and_precise_narrowing(tmp_path: Path) -> None:
    source = tmp_path / "valid_observation.py"
    source.write_text(
        """
import marivo.analysis as mv
import marivo.semantic as ms
session = mv.session.get_or_create('static', report_timezone='UTC')
members = session.members(ms.ref.entity('sales.customer'))
metric = mv.runtime_metric.linear(add=[ms.ref.metric('sales.revenue'), ms.ref.metric('sales.revenue')], label='twice')
observed = members.observe(metric, via=ms.ref.relationship('sales.buyer'))
assert isinstance(observed, mv.LogicalNumericRelation)
observed.rollup().execute()
ratio = members.observe(ms.ref.metric('sales.aov'), via=ms.ref.relationship('sales.buyer'))
assert isinstance(ratio, mv.LogicalRatioRelation)
ratio.rollup().execute()
"""
    )
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [str(root / ".venv/bin/mypy"), "--no-pretty", "--python-version", "3.10", str(source)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
