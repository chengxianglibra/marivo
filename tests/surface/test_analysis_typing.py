"""Independent rejected call shapes at the public typing boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.support.paths import PROJECT_ROOT


def test_public_dsl_rejects_wrong_kinds_methods_and_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "bad_public_analysis.py"
    source.write_text(
        """
import marivo.analysis as mv
import marivo.semantic as ms
mv.LogicalSelectedCategoryRelation
mv.MaterializedSelectedCategoryRelation
session = mv.session.get_or_create("static", report_timezone="UTC")
members = session.members(ms.ref.entity("sales.customer"))
session.members(ms.ref.metric("sales.revenue"))
members.read(ms.ref.entity("sales.customer"))
members.observe(ms.ref.metric("sales.revenue"), via=ms.ref.entity("sales.order"), by=(ms.ref.dimension("sales.customer.region"),))
members.observe(ms.ref.metric("sales.revenue"), coordinates=(ms.ref.dimension("sales.customer.region"),))
members.observe(ms.ref.metric("sales.revenue"), by=(ms.ref.metric("sales.revenue"),))
members.observe(ms.ref.metric("sales.revenue"), by=(ms.ref.entity("sales.customer"),))
members.observe(ms.ref.metric("sales.revenue"), by=mv.member())
members.group_by(ms.ref.dimension("sales.customer.region")).observe(ms.ref.metric("sales.revenue"))
category = members.read(ms.ref.dimension("sales.customer.region"))
assert isinstance(category, mv.LogicalCategoryRelation)
category.compare(category)
category.where(category.value.eq("west")).where(True)
members.show()
fixed = members.execute()
fixed.execute()
window = mv.time_scope(start="2026-08-01", end="2026-09-01")
grid = mv.time_grid(during=window, grain=mv.grain("month"))
members.each(grid)
grid.window
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
mv.path(ms.ref.metric("sales.revenue"))
mv.sum("extra")
category.rank(order="ascending", ties="dense")
observed.group_by(mv.member())
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
    root = PROJECT_ROOT
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
    assert 'Argument "via" to "observe"' in output
    assert 'Unexpected keyword argument "coordinates"' in output
    assert 'Argument "by" to "observe"' in output
    assert 'Argument 1 to "group_by"' in output
    assert 'has no attribute "LogicalSelectedCategoryRelation"' in output
    assert 'has no attribute "MaterializedSelectedCategoryRelation"' in output
    assert 'LogicalAnalysisDomain" has no attribute "group_by"' in output
    assert 'has no attribute "compare"' in output
    assert 'Argument 1 to "where"' in output
    assert 'has no attribute "show"' in output
    assert 'LogicalAnalysisDomain" has no attribute "each"' in output
    assert 'TimeGrid" has no attribute "window"' in output
    assert 'has no attribute "execute"' in output
    assert 'LogicalFixedAnalysisDomain" has no attribute "observe"' in output
    assert 'Argument 1 to "compare"' in output
    assert 'LogicalDifferenceRelation" has no attribute "rollup"' in output
    assert 'Argument "method" to "correlate"' in output
    assert 'Argument 1 to "path"' in output
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
from typing_extensions import assert_type
assert_type(mv.member(), mv.MemberAxis)
session = mv.session.get_or_create('static', report_timezone='UTC')
members = session.members(ms.ref.entity('sales.customer'))
metric = mv.runtime_metric.linear(add=[ms.ref.metric('sales.revenue'), ms.ref.metric('sales.revenue')], label='twice')
observed = members.observe(metric, via=ms.ref.relationship('sales.buyer'))
assert isinstance(observed, mv.LogicalNumericRelation)
observed.rollup().execute()
ratio = members.observe(ms.ref.metric('sales.aov'), via=ms.ref.relationship('sales.buyer'))
assert isinstance(ratio, mv.LogicalRatioRelation)
ratio.rollup().execute()
orders = session.members(ms.ref.entity('sales.order'))
grouped_observed = orders.observe(ms.ref.metric('sales.revenue'), by=(ms.ref.dimension('sales.order.channel'),))
assert isinstance(grouped_observed, mv.LogicalNumericRelation)
grouped_observed.rollup().execute()
grouped_with_none = orders.observe(ms.ref.metric('sales.revenue'), via=None, by=(ms.ref.dimension('sales.order.channel'),))
assert isinstance(grouped_with_none, mv.LogicalNumericRelation)
grouped_with_none.rollup().execute()
individual = members.observe(ms.ref.metric('sales.revenue'), by=(mv.member(),))
category = members.read(ms.ref.dimension('sales.customer.region'))
assert isinstance(category, mv.LogicalCategoryRelation)
selected = category.where(category.value.eq('east'))
assert_type(selected, mv.LogicalCategoryRelation)
assert_type(selected.where(selected.value.eq('east')), mv.LogicalCategoryRelation)
fixed_category = selected.execute()
assert_type(fixed_category, mv.MaterializedCategoryRelation)
assert_type(fixed_category.where(fixed_category.value.eq('east')), mv.LogicalCategoryRelation)
classified = members.observe(ms.ref.metric('sales.revenue'), by=(category,))
assert isinstance(classified, mv.LogicalNumericRelation)
"""
    )
    root = PROJECT_ROOT
    completed = subprocess.run(
        [str(root / ".venv/bin/mypy"), "--no-pretty", "--python-version", "3.10", str(source)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_history_pairs_fields_and_fixed_logical_returns_are_precise(tmp_path: Path) -> None:
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
    root = PROJECT_ROOT
    completed = subprocess.run(
        [str(root / ".venv/bin/mypy"), "--no-pretty", "--python-version", "3.10", str(source)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_retired_grouping_calls_fail_static_typechecking(tmp_path: Path) -> None:
    source = tmp_path / "retired_grouping.py"
    source.write_text(
        """
import marivo.analysis as mv
import marivo.semantic as ms

def rejected(
    members: mv.LogicalAnalysisDomain,
    fixed_members: mv.MaterializedAnalysisDomain,
    selected_members: mv.LogicalFixedAnalysisDomain,
    category: mv.LogicalCategoryRelation,
    fixed_category: mv.MaterializedCategoryRelation,
    selected_category: mv.LogicalCategoryRelation,
    fixed_selected_category: mv.MaterializedCategoryRelation,
    boolean: mv.LogicalBooleanRelation,
    fixed_boolean: mv.MaterializedBooleanRelation,
    selected_boolean: mv.LogicalSelectedBooleanRelation,
    fixed_selected_boolean: mv.MaterializedSelectedBooleanRelation,
    temporal: mv.LogicalTemporalRelation,
    fixed_temporal: mv.MaterializedTemporalRelation,
    selected_temporal: mv.LogicalSelectedTemporalRelation,
    fixed_selected_temporal: mv.MaterializedSelectedTemporalRelation,
    numeric: mv.LogicalNumericRelation,
) -> None:
    members.group_by()
    fixed_members.group_by()
    selected_members.group_by()
    members.count()
    category.group_by()
    fixed_category.group_by()
    selected_category.group_by()
    fixed_selected_category.group_by()
    boolean.group_by()
    fixed_boolean.group_by()
    selected_boolean.group_by()
    fixed_selected_boolean.group_by()
    temporal.group_by()
    fixed_temporal.group_by()
    selected_temporal.group_by()
    fixed_selected_temporal.group_by()
    members.observe(ms.ref.metric("sales.revenue"), groups=members)
    numeric.group_by(groups=members)
    mv.GroupedAnalysisDomain
"""
    )
    completed = subprocess.run(
        [str(PROJECT_ROOT / ".venv/bin/mypy"), "--no-pretty", str(source)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    for owner in (
        "LogicalAnalysisDomain",
        "MaterializedAnalysisDomain",
        "LogicalFixedAnalysisDomain",
        "LogicalCategoryRelation",
        "MaterializedCategoryRelation",
        "LogicalBooleanRelation",
        "MaterializedBooleanRelation",
        "LogicalSelectedBooleanRelation",
        "MaterializedSelectedBooleanRelation",
        "LogicalTemporalRelation",
        "MaterializedTemporalRelation",
        "LogicalSelectedTemporalRelation",
        "MaterializedSelectedTemporalRelation",
    ):
        assert f'{owner}" has no attribute "group_by"' in completed.stdout
    assert 'has no attribute "count"' in completed.stdout
    assert 'Unexpected keyword argument "groups" for "observe"' in completed.stdout
    assert 'Unexpected keyword argument "groups" for "group_by"' in completed.stdout
    assert 'has no attribute "GroupedAnalysisDomain"' in completed.stdout
