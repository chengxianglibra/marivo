"""Actual guarded suffix execution and v3 publication, without source replay."""

from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from marivo.analysis.compiler.placement import place
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import LocalPolicy
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators import registry
from marivo.analysis.operators.registry import ImplementationRegistration
from tests.lazy_local_fixtures import COUNT, REVENUE, pandas_methods, setup_local

pytestmark = pytest.mark.runtime


def test_artifact_suffix_publishes_and_reuses_without_source(tmp_path: Path) -> None:
    with pandas_methods("metric.where"):
        runtime, sources, database = setup_local(tmp_path)
        retained = sources.observe([REVENUE, COUNT]).execute()
        record = runtime.store.artifact(retained.state.artifact_ref.ref)
        assert record is not None
        database.rename(database.with_suffix(".offline"))
        filtered = retained.where(gt(retained.fields.metric(REVENUE), 5))
        ranked = filtered.rank(filtered.fields.metric(REVENUE))
        logical = ranked.limit(3).metric(REVENUE)
        assert len(place(logical).local_steps) == 4
        result = logical.execute()
        rows = result.to_pandas()
        assert rows["revenue"].tolist() == [100.0, 30.0, 10.0]
        assert rows["rank"].tolist() == [1, 2, 3]
        assert rows["entity_identity"].tolist() == [(3,), (2,), (1,)]
        assert runtime.statistics.primary_queries == runtime.statistics.validation_queries == 0
        handoffs = runtime.statistics.local_handoffs
        assert len(handoffs) == 4
        assert all(first[1] == second[0] for first, second in pairwise(handoffs))
        assert runtime.statistics.worker_pid is not None
        assert runtime.store.resources(runtime.session_ref) == ()
        recovered = logical.execute()
        assert recovered.state.artifact_ref == result.state.artifact_ref
        assert runtime.statistics.events == {"reconciliation": 1}
        reopened = DatasetRuntime.open(tmp_path, runtime.session_ref)
        cold = reopened.artifact(result.state.artifact_ref)
        assert cold.to_pandas().equals(rows)


def test_selected_source_prefix_feeds_one_local_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    filtered = source.where(gt(REVENUE, 5))
    logical = filtered.rank(filtered.fields.metric(REVENUE)).limit(2)
    original = registry.implementation

    def restricted(dataset: LogicalDataset) -> ImplementationRegistration:
        value = original(dataset)
        return replace(value, source_adapter=None) if value.operator_id == "metric.rank" else value

    monkeypatch.setattr(registry, "implementation", restricted)
    assert [step.implementation.operator_id for step in place(logical).local_steps] == [
        "metric.rank",
        "metric.limit",
    ]
    result = logical.execute()
    assert result.to_pandas()["revenue"].tolist() == [100.0, 30.0]
    assert runtime.statistics.primary_queries == 1
    assert len(runtime.statistics.local_handoffs) == 2
    assert runtime.store.resources(runtime.session_ref) == ()


def test_input_guard_precedes_local_limit_and_leaves_no_artifact(tmp_path: Path) -> None:
    with pandas_methods("metric.rank"):
        runtime, sources, _ = setup_local(tmp_path)
        retained = sources.observe(REVENUE).execute()
        ranked = retained.rank(retained.fields.metric(REVENUE))
        runtime.local_policy = replace(LocalPolicy(), max_input_rows=5)
        with pytest.raises(MaterializationError):
            ranked.limit(1).execute()
        assert runtime.last_run_ref is not None
        run = runtime.store.run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
        assert runtime.store.resources(runtime.session_ref) == ()


def test_partitioned_time_rank_matches_independent_values_and_source(tmp_path: Path) -> None:
    from marivo.analysis import grain
    from marivo.refs import ref

    runtime, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE, population=sources.population(ref.entity("sales.customers")))
    source = (
        source.with_dimensions(ref.dimension("sales.customers.region"))
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
        .aggregate()
    )
    expected = (
        source.rank(
            source.fields.metric(REVENUE), ties="dense", partition_by=(source.fields.get("region"),)
        )
        .execute()
        .to_pandas()
    )
    with pandas_methods("metric.rank"):
        retained = source.execute()
        local_rank = retained.rank(
            retained.fields.metric(REVENUE),
            ties="dense",
            partition_by=(retained.fields.get("region"),),
        ).execute()
        actual = local_rank.to_pandas()
        assert actual.equals(expected)
        finite = actual.loc[actual["revenue"].notna()]
        assert finite["revenue"].tolist() == [30.0, 10.0, 7.0, 0.0, 100.0]
        assert finite["rank"].tolist() == [1, 2, 3, 4, 1]
        assert runtime.statistics.primary_queries == 0


@pytest.mark.parametrize(
    "point",
    [
        "local_worker_reserved",
        "local_worker_terminal",
        "output_reserved",
        "after_rename",
        "quality",
        "evidence",
        "insert_artifact",
        "after_commit",
    ],
)
def test_local_failure_atomicity_and_committed_readback(tmp_path: Path, point: str) -> None:
    with pandas_methods("metric.where"):
        from tests.lazy_materialization_crash_worker import snapshot

        runtime, sources, _ = setup_local(tmp_path)
        retained = sources.observe(REVENUE).execute()

        def fail_at(current: str) -> None:
            if current == point:
                raise RuntimeError("private-literal-must-not-leak")

        runtime._hook = fail_at
        logical = retained.where(gt(retained.fields.metric(REVENUE), 5))
        if point == "after_commit":
            assert logical.execute().to_pandas()["revenue"].tolist() == [10.0, 30.0, 100.0, 7.0]
        else:
            with pytest.raises(MaterializationError) as failure:
                logical.execute()
            assert "private-literal" not in str(failure.value)
        state = snapshot(runtime)
        counts = state["counts"]
        assert isinstance(counts, dict)
        assert (
            counts["dataset_artifacts"]
            == counts["dataset_evidence"]
            == (2 if point == "after_commit" else 1)
        )
        assert counts["analysis_action_run_terminals"] == 2
        assert counts["action_resource_journal"] == 0


@pytest.mark.parametrize("method", ["compile", "to_pyarrow_batches"])
def test_selected_source_failure_never_runs_replacement_pandas(
    tmp_path: Path, method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ibis.backends.duckdb import Backend

    runtime, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    target = source.rank(source.fields.metric(REVENUE)).limit(2)
    original = registry.implementation

    def registrations(dataset: LogicalDataset) -> ImplementationRegistration:
        current = original(dataset)
        return (
            replace(current, source_adapter=None)
            if current.operator_id == "metric.rank"
            else current
        )

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("private source failure canary")

    monkeypatch.setattr(registry, "implementation", registrations)
    monkeypatch.setattr(Backend, method, broken)
    with pytest.raises(MaterializationError) as error:
        target.execute()
    assert "canary" not in str(error.value)
    assert runtime.statistics.local_handoffs == ()
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert runtime.store.resources(runtime.session_ref) == ()


def test_unknown_current_placement_does_not_override_an_exact_binding_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import admission

    _, sources, _ = setup_local(tmp_path)
    logical = sources.observe(REVENUE)
    retained = logical.execute()

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("binding hit must not select a new physical implementation")

    monkeypatch.setattr(admission, "place", unexpected)
    assert logical.execute().state.artifact_ref == retained.state.artifact_ref


def test_composite_identity_tie_breaker_survives_local_publication(tmp_path: Path) -> None:
    with pandas_methods("metric.rank"):
        import duckdb

        from marivo.analysis.observation.contracts import source_owner_of
        from marivo.refs import ref
        from tests.lazy_observation_fixtures import _metric

        runtime, sources, database = setup_local(tmp_path)
        with duckdb.connect(str(database)) as connection:
            connection.execute("UPDATE composite SET amount=10")
        owner = source_owner_of(sources.observe(REVENUE))
        metric = ref.metric("sales.composite_value")
        catalog = replace(
            owner.semantic_registry,
            metrics={
                **owner.semantic_registry.metrics,
                metric.path: _metric("composite_value", "composite", "sum"),
            },
        )
        catalog.freeze()
        source = runtime.sources(semantic_registry=catalog, sidecar=owner.sidecar).observe(metric)
        retained = source.execute()
        result = retained.rank(retained.fields.metric(metric)).limit(2).execute().to_pandas()
        assert result["entity_identity"].tolist() == [("a", 1), ("a", 2)]
        assert result["rank"].tolist() == [1, 2]


def test_projection_drops_rank_value_and_preserves_order_for_later_limit(tmp_path: Path) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    source = sources.observe([REVENUE, COUNT])
    expected = source.rank(source.fields.metric(REVENUE)).metric(COUNT).limit(3).execute()
    with pandas_methods("metric.rank"):
        retained = source.execute()
        ranked = retained.rank(retained.fields.metric(REVENUE))
        result = ranked.metric(COUNT).limit(3).execute()
        frame = result.to_pandas()
        assert "revenue" not in frame.columns
        assert frame["entity_identity"].tolist() == [(3,), (2,), (1,)]
        assert frame["rank"].tolist() == [1, 2, 3]
        assert frame.equals(expected.to_pandas())
        assert runtime.statistics.primary_queries == runtime.statistics.validation_queries == 0


@pytest.mark.parametrize(
    "policy,diagnostic",
    [
        (replace(LocalPolicy(), max_input_bytes=1), "byte limit"),
        (replace(LocalPolicy(), max_batch_bytes=1), "row group"),
        (replace(LocalPolicy(), max_method_rows=5), "method"),
        (replace(LocalPolicy(), max_intermediate_bytes=1), "byte limit"),
        (replace(LocalPolicy(), max_output_rows=5), "output"),
        (replace(LocalPolicy(), max_output_bytes=1), "output"),
        (replace(LocalPolicy(), max_worker_rss=1), "memory"),
        (replace(LocalPolicy(), deadline_seconds=0.001), "deadline"),
    ],
)
def test_bound_runtime_policy_reaches_worker_and_failure_is_atomic(
    tmp_path: Path, policy: LocalPolicy, diagnostic: str
) -> None:
    with pandas_methods("metric.metric"):
        from tests.lazy_materialization_crash_worker import snapshot

        runtime, sources, _ = setup_local(tmp_path)
        retained = sources.observe(REVENUE).execute()
        before = snapshot(runtime)
        runtime.local_policy = policy
        with pytest.raises(MaterializationError, match=diagnostic):
            retained.metric(REVENUE).execute()
        after = snapshot(runtime)
        before_tables, after_tables = before["tables"], after["tables"]
        assert isinstance(before_tables, dict) and isinstance(after_tables, dict)
        for field in ("dataset_artifacts", "dataset_evidence", "action_resource_journal"):
            assert after_tables[field] == before_tables[field]
        assert runtime.last_run_ref is not None
        run = runtime.store.run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
