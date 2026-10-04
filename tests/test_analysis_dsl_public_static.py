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
category.where(category.value.eq("west")).where(True)
members.show()
fixed = members.execute()
fixed.execute()
window = mv.time_scope(start="2026-08-01", end="2026-09-01")
session.lifecycle.replay(ms.ref.state_model("sales.model"), window=window, seed=mv.from_inception())
session.lifecycle.replay(ms.ref.entity("sales.customer"), population=members, window=window, seed=mv.from_inception())
session.lifecycle.replay(ms.ref.state_model("sales.model"), population=fixed, window=window, seed=mv.from_inception())
history = session.lifecycle.replay(ms.ref.state_model("sales.model"), population=members, window=window, seed=mv.from_inception())
history.distribution()
history.execute().read(mv.in_state(ms.model_state(ms.ref.state_model("sales.model"), "done"), at=window.end))
category.where(category.value.eq("west")).execute().members().observe(ms.ref.metric("sales.revenue"))
observed = members.observe(ms.ref.metric("sales.revenue"), during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=ms.ref.relationship("sales.buyer"))
assert isinstance(observed, mv.LogicalNumericRelation)
observed.summarize(mv.mean()).compare(category)
observed.compare(observed).rollup()
observed.correlate(observed, method="partial")
mv.route(ms.ref.metric("sales.revenue"), through=(ms.ref.relationship("sales.buyer"),))
mv.sum("extra")
category.rank(order="ascending", ties="dense")
observed.rank(order="up", ties="average")
observed.rank(order="ascending", ties="dense", partition_by=(observed,))
ranking = observed.rank(order="ascending", ties="dense")
ranking.limit("1")
ranking.where(True)
table = mv.table(value=observed)
table.contract()
table.value
table.where(observed.value.gt(0))
mv.table(value=ranking)
table.execute().execute()
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
    assert 'Missing named argument "population"' in output
    assert 'Argument "population" to "replay"' in output
    assert 'Missing named argument "at" for "distribution"' in output
    assert 'Too many positional arguments for "model_state"' in output
    assert 'Argument 1 to "members"' in output
    assert 'Argument 1 to "read"' in output
    assert 'has no attribute "compare"' in output
    assert 'Argument 1 to "where"' in output
    assert 'has no attribute "show"' in output
    assert 'has no attribute "execute"' in output
    assert 'LogicalFixedAnalysisDomain" has no attribute "observe"' in output
    assert 'Argument 1 to "compare"' in output
    assert 'LogicalDifferenceRelation" has no attribute "rollup"' in output
    assert 'Argument "method" to "correlate"' in output
    assert 'Argument 1 to "route"' in output
    assert 'Too many arguments for "sum"' in output
    assert 'Argument "order" to "rank"' in output
    assert 'Argument "ties" to "rank"' in output
    assert 'Argument "partition_by" to "rank"' in output
    assert 'Argument 1 to "limit"' in output
    assert 'LogicalTable" has no attribute "contract"' in output
    assert 'LogicalTable" has no attribute "value"' in output
    assert 'LogicalTable" has no attribute "where"' in output
    assert 'MaterializedTable" has no attribute "execute"' in output
    assert 'Argument "value" to "table"' in output


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


def test_history_r76_pairs_fields_and_fixed_logical_returns_are_precise(tmp_path: Path) -> None:
    source = tmp_path / "history_types.py"
    source.write_text(
        """
from datetime import datetime, timezone
from typing_extensions import assert_type
import marivo.analysis as mv
import marivo.semantic as ms
def check(history: mv.LogicalHistoryResult, fixed: mv.MaterializedHistoryResult) -> None:
    at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    state = ms.model_state(model=ms.ref.state_model('commerce.model'), name='done')
    assert_type(history.read(mv.in_state(state, at=at)), mv.LogicalBooleanRelation)
    assert_type(fixed.read(mv.in_state(state, at=at)), mv.LogicalBooleanRelation)
    assert_type(history.distribution(at=(at,)), mv.LogicalStateDistributionResult)
    assert_type(fixed.distribution(at=(at,)).execute(), mv.MaterializedStateDistributionResult)
    assert_type(history.transitions(), mv.LogicalTransitionSummary)
    assert_type(fixed.transitions().execute(), mv.MaterializedTransitionSummary)
    assert_type(history.violations(), mv.LogicalViolationResult)
    assert_type(fixed.violations().execute(), mv.MaterializedViolationResult)
    assert_type(history.intervals(), mv.LogicalStateIntervalResult)
    assert_type(fixed.intervals().execute(), mv.MaterializedStateIntervalResult)
    assert_type(history.dwell(), mv.LogicalDwellSummary)
    assert_type(fixed.dwell().execute(), mv.MaterializedDwellSummary)
    assert_type(fixed.intervals().execute().state, mv.LogicalCategoryRelation)
    assert_type(fixed.intervals().observed_duration, mv.LogicalNumericRelation)
    assert_type(fixed.violations().occurred_at, mv.LogicalTemporalRelation)
    assert_type(fixed.violations().kind, mv.LogicalCategoryRelation)
    assert_type(fixed.distribution(at=(at,)).share_among_seeded, mv.LogicalNumericRelation)
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
