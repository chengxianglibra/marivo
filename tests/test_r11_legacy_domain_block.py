"""Old text-backed domain execution must stop before Run and source access."""

from pathlib import Path
from unittest.mock import patch

import pytest

from marivo.analysis.compiler.placement import SourceStep, place
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.source_preparation import prepared_source
from marivo.analysis.observation.predicates import eq
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.refs import ref
from tests.lazy_local_fixtures import setup_local
from tests.test_lazy_distinct_runtime import _setup as setup_distinct

pytestmark = pytest.mark.runtime


def test_attribution_source_route_rejects_before_run(tmp_path: Path) -> None:
    runtime, metric, _database = setup_distinct(tmp_path)
    delta = metric.compare(metric)
    with pytest.raises(MaterializationError, match="R6 migration") as caught:
        delta.attribute(axes=(ref.dimension("sales.orders.channel"),)).execute()
    assert caught.value.stage == "source_admission"
    assert runtime.last_run_ref is None
    assert runtime.statistics.submissions == []


def test_retired_metric_old_route_rejects_before_run(tmp_path: Path) -> None:
    runtime, metric, _database = setup_distinct(tmp_path)
    with pytest.raises(MaterializationError, match="R5 route is retired") as caught:
        metric.execute()
    assert caught.value.stage == "source_admission"
    assert runtime.last_run_ref is None
    assert runtime.statistics.submissions == []


@pytest.mark.parametrize(
    ("metric_name", "expected"),
    [("sales.revenue", 147.0), ("sales.order_count", 5)],
)
def test_basic_metric_aggregate_uses_source_session(
    tmp_path: Path, metric_name: str, expected: float
) -> None:
    runtime, sources, _database = setup_local(tmp_path)
    with patch(
        "marivo.analysis.materialization.duckdb_execution.DuckDBExecutionAdapter.statement",
        side_effect=AssertionError("legacy text adapter used"),
    ):
        result = sources.observe(ref.metric(metric_name)).aggregate().execute()
    assert result.to_pandas().iloc[0, 0] == expected
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 1
    assert runtime.statistics.primary_queries == 1
    assert all(submission.state == "succeeded" for submission in runtime.statistics.submissions)


def test_basic_grouped_count_uses_source_session(tmp_path: Path) -> None:
    runtime, sources, _database = setup_local(tmp_path)
    grouped = sources.observe(ref.metric("sales.order_count")).with_dimensions(
        ref.dimension("sales.orders.channel")
    )
    result = grouped.aggregate().execute()
    assert result.to_pandas().to_dict("records") == [{"channel": "web", "order_count": 5}]
    assert [submission.role for submission in runtime.statistics.submissions] == [
        "validation_batch",
        "primary",
    ]


def test_basic_population_uses_session_issued_read_without_text_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, _database = setup_local(tmp_path)
    issued: list[tuple[SourceSession, CompiledRead]] = []
    original_batches = SourceSession.batches

    def observed_batches(
        session: SourceSession, read: CompiledRead, *, chunk_size: int
    ) -> SourceBatchStream:
        issued.append((session, read))
        return original_batches(session, read, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", observed_batches)
    with patch(
        "marivo.analysis.materialization.duckdb_execution.DuckDBExecutionAdapter.statement",
        side_effect=AssertionError("legacy text adapter used"),
    ):
        result = sources.population(ref.entity("sales.customers")).execute()
    assert result.to_pandas().shape[0] == 4
    assert issued
    assert all(session._closed and not session._streams for session, _ in issued)
    assert [read.sql for _, read in issued] == [
        submission.sql for submission in runtime.statistics.submissions
    ]
    assert all(submission.state == "succeeded" for submission in runtime.statistics.submissions)


def test_basic_population_filter_uses_bound_session_source(tmp_path: Path) -> None:
    runtime, sources, _database = setup_local(tmp_path)
    selected = sources.population(ref.entity("sales.customers")).where(
        eq(ref.dimension("sales.customers.region"), "EU")
    )
    rows = selected.execute().to_pandas().entity_identity.tolist()
    assert rows == [(1,), (3,)]
    assert runtime.statistics.primary_queries == 1


def test_association_old_route_rejects_before_run(tmp_path: Path) -> None:
    runtime, sources, _database = setup_local(tmp_path)
    logical = sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]
    ).correlate(method="pearson")
    with pytest.raises(MaterializationError, match="R8 migration") as caught:
        logical.execute()
    assert caught.value.stage == "source_admission"
    assert runtime.last_run_ref is None
    assert runtime.statistics.submissions == []


def test_legacy_preparation_cannot_open_a_source_directly(tmp_path: Path) -> None:
    runtime, metric, _database = setup_distinct(tmp_path)
    source_step = next(step for step in place(metric).steps if isinstance(step, SourceStep))
    with (
        patch(
            "marivo.analysis.materialization.source_preparation._build_backend_from_effective",
            side_effect=AssertionError("legacy source opened"),
        ),
        pytest.raises(MaterializationError) as caught,
        prepared_source(runtime, "run_probe", source_step, {}, []),
    ):
        pass
    assert caught.value.stage == "source_admission"
