"""Real additive/component Attribution across exact Metric operand authorities."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis import time_scope
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.operators import registry as implementations
from marivo.analysis.operators.attribution import MaterializedAttributionDataset
from marivo.analysis.operators.registry import ImplementationRegistration
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_attribute_runtime_evidence import record as _record
from tests.lazy_compare_runtime_fixtures import independent_sources
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_retained_fixtures import setup_retained
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime
CHANNEL = ref.dimension("sales.orders.channel")
Method = Literal["additive", "component"]


def _metric(sources: LazySources, method: Method, *, current: bool) -> LogicalMetricDataset:
    metric = ref.metric("sales.revenue" if method == "additive" else "sales.mean_amount")
    return (
        sources.observe(
            metric,
            time_scope=time_scope(
                start="2026-02-02" if current else "2026-02-03",
                end="2026-02-03" if current else "2026-02-04",
            ),
        )
        .with_dimensions(CHANNEL)
        .aggregate()
    )


@pytest.mark.parametrize("states", ["LL", "LM", "ML", "MM"])
@pytest.mark.parametrize("method", ["additive", "component"])
@pytest.mark.parametrize("execution", ["native", "pandas"])
def test_all_metric_operand_authorities_feed_exact_attribution(
    tmp_path: Path, states: str, method: Method, execution: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _manifest()
    fixture = setup_retained(tmp_path)
    with duckdb.connect(str(fixture.database)) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, amount, channel, day) VALUES (?, ?, ?, ?)",
            (
                (1, 10, "a", "2026-02-02"),
                (2, 20, "a", "2026-02-02"),
                (3, 90, "b", "2026-02-02"),
                (4, 20, "a", "2026-02-03"),
                (5, 30, "b", "2026-02-03"),
                (6, 30, "b", "2026-02-03"),
                (7, 30, "b", "2026-02-03"),
            ),
        )
    left, right = (
        _metric(fixture.sources, method, current=True),
        _metric(fixture.sources, method, current=False),
    )
    current = left.execute() if states[0] == "M" else left
    baseline = right.execute() if states[1] == "M" else right
    if states == "MM":
        fixture.database.rename(tmp_path / "warehouse.offline")
    original = implementations.implementation

    def selected(dataset: LogicalDataset) -> ImplementationRegistration:
        registered = original(dataset)
        return (
            replace(registered, source_adapter=None)
            if execution == "pandas" and registered.operator_id == "delta.attribute"
            else registered
        )

    monkeypatch.setattr(implementations, "implementation", selected)
    before = snapshot(fixture.runtime)
    logical = current.compare(baseline).attribute(axes=[CHANNEL], top_k=1)
    assert snapshot(fixture.runtime) == before
    result = logical.execute()
    assert isinstance(result, MaterializedAttributionDataset)
    local_executions = fixture.runtime.statistics.events.get("local_execution_started", 0)
    primary_queries = fixture.runtime.statistics.primary_queries
    frame = result.to_pandas()
    assert frame.channel.tolist()[0] == "b" and frame.channel.isna().tolist() == [False, True]
    assert [tuple(mask) for mask in frame.other_mask] == [(False,), (True,)]
    if method == "additive":
        assert frame.current_value.tolist() == [90, 30]
        assert frame.baseline_value.tolist() == [90, 20]
        assert frame.contribution.tolist() == [0, 10]
        assert frame.overall_delta.tolist() == [10, 10]
    else:
        assert frame.current_value.tolist() == pytest.approx([90 / 3, 30 / 3])
        assert frame.baseline_value.tolist() == pytest.approx([90 / 4, 20 / 4])
        assert frame.contribution.tolist() == pytest.approx([7.5, 5])
        assert frame.overall_delta.tolist() == pytest.approx([12.5, 12.5])
    assert frame.share_of_negative_pool.isna().all()
    assert len(result.findings().items) == 2
    assert (local_executions == 0) == (execution == "native")
    assert primary_queries == 1
    if states == "MM":
        assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    run = fixture.runtime.store.run(result.state.producing_run_ref)
    assert run is not None
    assert run.input_artifact_refs == tuple(
        operand.state.artifact_ref.ref
        for operand in (current, baseline)
        if isinstance(operand, MaterializedDataset)
    )
    completed = snapshot(fixture.runtime)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(fixture.runtime) == completed
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
    _record(
        f"operand-{execution}-{method}-{states}",
        candidate,
        {
            "method": method,
            "states": states,
            "artifact": result.state.artifact_ref.ref,
            "input_refs": run.input_artifact_refs,
            "current": frame.current_value.tolist(),
            "baseline": frame.baseline_value.tolist(),
            "contributions": frame.contribution.tolist(),
            "overall_delta": frame.overall_delta.tolist(),
            "primary_queries": primary_queries,
            "local_executions": local_executions,
            "after": completed,
        },
    )
    if states == "MM":
        ranked = logical.rank(logical.fields.get("contribution")).limit(1).execute()
        values = ranked.to_pandas()
        assert values.contribution.tolist() == pytest.approx(
            [10.0 if method == "additive" else 7.5]
        )
        assert values["rank"].tolist() == [1]
        assert ranked.findings().items == ()
        assert ranked.evidence_digest.finding_count == 0
        assert (fixture.runtime.statistics.events.get("local_execution_started", 0) == 0) == (
            execution == "native"
        )
        assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0


@pytest.mark.parametrize("method", ["additive", "component"])
def test_independent_equal_argument_sources_continue_from_local_compare_into_attribution(
    tmp_path: Path, method: Method
) -> None:
    candidate = _manifest()
    metric = ref.metric("sales.revenue" if method == "additive" else "sales.mean_amount")
    with independent_sources(tmp_path, metric=metric) as (runtime, left, right, calls):
        current = left.with_dimensions(CHANNEL).aggregate()
        baseline = right.with_dimensions(CHANNEL).aggregate()
        result = current.compare(baseline).attribute(axes=(CHANNEL,)).execute()
        assert runtime.statistics.primary_queries == 2
        assert runtime.statistics.events.get("local_execution_started", 0) > 0
        assert len(calls) == len(set(calls)) == 2
        frame = result.to_pandas()
        assert frame.current_value.tolist() == [11]
        assert frame.baseline_value.tolist() == [29]
        assert frame.contribution.tolist() == [-18]
        assert frame.share_of_positive_pool.isna().all()
        assert frame.share_of_negative_pool.tolist() == [1]
        assert frame.channel.isna().all()
        assert len(result.findings().items) == 1
        assert runtime.store.resources(runtime.session_ref) == ()
        _record(
            f"independent-sources-{method}",
            candidate,
            {
                "method": method,
                "connection_arguments": [":memory:", ":memory:"],
                "connection_ids": calls,
                "artifact": result.state.artifact_ref.ref,
                "current": frame.current_value.tolist(),
                "baseline": frame.baseline_value.tolist(),
                "contributions": frame.contribution.tolist(),
                "primary_queries": runtime.statistics.primary_queries,
                "local_executions": runtime.statistics.events.get("local_execution_started", 0),
                "after": snapshot(runtime),
            },
        )
