"""Native reducers and selection over immutable history, including source removal."""

from datetime import timedelta
from pathlib import Path

import pytest

from marivo.analysis.domains.lifecycle import MaterializedLifecycleDataset
from marivo.analysis.domains.lifecycle_reducers import in_state
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_lifecycle_fixtures import END, MODEL, START, history, setup_lifecycle

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize(
    "method", ["distribution", "transitions", "dwell", "violations", "selection"]
)
def test_native_reducer_and_cold_result(tmp_path: Path, retained: bool, method: str) -> None:
    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    original = history(sources)
    h = original.execute() if retained else original
    if retained:
        database.unlink()
    if method == "selection":
        selected_result = h.select_subjects(
            in_state(ModelStateHandle(MODEL, "done"), at=END)
        ).execute()
        assert isinstance(selected_result, MaterializedPopulationDataset)
        assert len(selected_result.to_pandas()) == 1
        return
    if method == "distribution":
        result = h.distribution(at=(START, END)).execute()
    elif method == "transitions":
        result = h.transitions().execute()
    elif method == "dwell":
        result = h.dwell().execute()
    else:
        result = h.violations().execute()
    assert isinstance(result, MaterializedLifecycleDataset)
    rows = result.to_pandas()
    if method == "distribution":
        assert rows.subject_count.tolist() == [1, 0, 1, 1]
    elif method == "transitions":
        assert rows.transition_count.tolist() == [1]
    elif method == "dwell":
        assert rows.completed_count.tolist() == [1, 0]
        assert rows.mean_duration.iloc[0] == timedelta(hours=3)
    else:
        assert len(rows) == 1
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(result.state.artifact_ref)
    assert isinstance(cold, MaterializedLifecycleDataset)
    assert cold.to_pandas().equals(rows)


@pytest.mark.parametrize("retained", [False, True])
def test_grouped_distribution_uses_retained_subjects(tmp_path: Path, retained: bool) -> None:
    import duckdb

    from marivo.refs import ref

    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    original = history(sources)
    h = original.execute() if retained else original
    if retained:
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE started_rows")
            connection.execute("DROP TABLE finished_rows")
    result = h.distribution(
        at=(END, START), axes=(ref.dimension("sales.customers.region"),)
    ).execute()
    rows = result.to_pandas()
    assert rows.groupby("as_of").subject_count.sum().tolist() == [1, 2]
    assert set(rows.region.dropna()) == {"EU"}
    assert rows.region.isna().any()


@pytest.mark.parametrize("method", ["distribution", "transitions", "dwell", "violations"])
def test_local_result_filter_is_source_offline(tmp_path: Path, method: str) -> None:
    from marivo.analysis.materialization.targets import LocalTarget
    from marivo.analysis.observation.predicates import eq, is_null

    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    h = history(sources).execute()
    runtime.target = LocalTarget()
    if method == "distribution":
        result = h.distribution(at=(START, END)).execute()
        predicate = eq(result.fields.get("model_state"), "open")
    elif method == "transitions":
        result = h.transitions().execute()
        predicate = eq(result.fields.get("transition_count"), 1)
    elif method == "dwell":
        result = h.dwell().execute()
        predicate = is_null(result.fields.get("mean_duration"))
    else:
        result = h.violations().execute()
        predicate = eq(result.fields.get("violation_kind"), "illegal_transition")
    database.unlink()
    filtered = result.where(predicate).execute()
    assert len(filtered.to_pandas()) == (2 if method == "distribution" else 1)


@pytest.mark.parametrize("retained", [False, True])
def test_metric_lifecycle_population_metric_and_event_loop(tmp_path: Path, retained: bool) -> None:
    from marivo.refs import ref
    from tests.lazy_event_runtime_fixtures import journey

    runtime, sources, _ = setup_lifecycle(tmp_path, engine=True)
    metric = sources.observe(
        ref.metric("sales.revenue"), population=sources.population(ref.entity("sales.customers"))
    )
    h = history(sources, population=metric)
    selection = (h.execute() if retained else h).select_subjects(
        in_state(ModelStateHandle(MODEL, "done"), at=END)
    )
    selected = selection.execute() if retained else selection
    output = sources.observe(ref.metric("sales.revenue"), population=selected).execute()
    assert output.to_pandas().entity_identity.tolist() == [(1,)]
    events = journey(sources, population=selected).execute().to_pandas()
    assert set(events.entity_identity) == {(1,)}
    continued_history = history(sources, population=selected).execute().to_pandas()
    assert set(continued_history.entity_identity) == {(1,)}


@pytest.mark.parametrize(
    "consumer", ["selection", "filter", "sample", "metric", "event", "lifecycle"]
)
def test_unknown_member_blocks_every_continuation(tmp_path: Path, consumer: str) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.observation.predicates import eq
    from marivo.analysis.observation.sampling import engine_sample
    from marivo.refs import ref
    from tests.lazy_adapter_runtime_worker import snapshot
    from tests.lazy_event_runtime_fixtures import journey

    runtime, sources, _ = setup_lifecycle(tmp_path, engine=True)
    h = history(sources, complete=False).execute()
    selected = h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END))
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        if consumer == "filter":
            selected.where(eq(ref.dimension("sales.customers.region"), "absent")).execute()
        elif consumer == "sample":
            selected.sample(engine_sample(target_rows=1, seed=42)).execute()
        elif consumer == "metric":
            sources.observe(ref.metric("sales.revenue"), population=selected).execute()
        elif consumer == "event":
            journey(sources, population=selected).execute()
        elif consumer == "lifecycle":
            history(sources, population=selected).execute()
        else:
            selected.execute()
    assert snapshot(runtime)["dataset_artifacts"] == before["dataset_artifacts"]
    assert runtime.store.resources(runtime.session_ref) == ()


def test_equal_empty_histories_keep_distinct_censored_axis_membership(tmp_path: Path) -> None:
    import duckdb

    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.observation.predicates import eq
    from marivo.refs import ref

    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM started_rows")
        connection.execute("DELETE FROM finished_rows")
        connection.execute("UPDATE customers SET region = CAST(id AS VARCHAR)")
    histories = [
        history(
            sources,
            complete=False,
            population=sources.population(ref.entity("sales.customers")).where(
                eq(ref.dimension("sales.customers.region"), str(k))
            ),
        ).execute()
        for k in (1, 2)
    ]
    assert histories[0].to_pandas().empty and histories[1].to_pandas().empty
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DROP TABLE started_rows")
        connection.execute("DROP TABLE finished_rows")
    for k, h in enumerate(histories, 1):
        rows = (
            h.distribution(at=(END,), axes=(ref.dimension("sales.customers.region"),))
            .execute()
            .to_pandas()
        )
        assert rows.region.tolist() == [str(k)] * 2
        assert rows.coverage_censored_subject_count.tolist() == [1, 1]
        assert rows.known_subject_count.tolist() == [0, 0]
        assert rows.share.isna().all()
        with pytest.raises(MaterializationError):
            h.select_subjects(in_state(ModelStateHandle(MODEL, "open"), at=END)).execute()


def test_recovered_coverage_prefix_is_known_only_before_boundary(tmp_path: Path) -> None:
    from ibis.backends.duckdb import Backend

    from marivo.analysis.domains.completeness import EventCoverageReceiptV1, EventCoverageRequestV1
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_lifecycle_fixtures import receipt

    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)

    def provider(backend: Backend, request: EventCoverageRequestV1) -> EventCoverageReceiptV1:
        return receipt(request, prefix=True)

    runtime.event_coverage_provider = provider
    h = history(sources, complete=False).execute()
    database.unlink()
    selected = h.select_subjects(
        in_state(ModelStateHandle(MODEL, "done"), at=START + timedelta(hours=3))
    ).execute()
    assert selected.to_pandas().entity_identity.tolist() == [(1,)]
    with pytest.raises(MaterializationError):
        h.select_subjects(
            in_state(ModelStateHandle(MODEL, "done"), at=START + timedelta(hours=4))
        ).execute()
