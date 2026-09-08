"""Real immutable engine and versioned object adapter integration."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import EngineReceipt, ObjectReceipt
from marivo.analysis.materialization.targets import EngineTarget, ObjectTarget, S3Access
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_local_fixtures import REVENUE, setup_local

pytestmark = pytest.mark.runtime


def test_engine_population_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(
        tmp_path, "engine", target=EngineTarget(next(iter(registry.datasources)))
    )
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.population(ref.entity("sales.customers"))
    result = logical.execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert isinstance(record.descriptor.storage_receipt, EngineReceipt)
    assert result.to_pandas()["entity_identity"].tolist() == [(1,), (2,), (3,), (4,)]
    assert runtime.statistics.transferred_rows == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert runtime.statistics.primary_queries == 0


def test_engine_checkpoint_observe_never_replays_membership_source(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(
        tmp_path, "checkpoint", target=EngineTarget(next(iter(registry.datasources)))
    )
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    population = sources.population(ref.entity("sales.customers")).execute()
    with duckdb.connect(str(database)) as db:
        db.execute("DROP TABLE customers")
    logical = sources.observe(ref.metric("sales.revenue"), population=population)
    result = logical.execute()
    assert result.to_pandas()["revenue"].fillna(-1).tolist() == [40, 100, 7, -1]
    assert not any('"customers"' in sql for _, sql in runtime.statistics.statements)
    assert runtime.store.resources(runtime.session_ref) == ()


def test_object_metric_round_trip(tmp_path: Path, lazy_s3_access: S3Access) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(
        tmp_path, "object", target=ObjectTarget("fixture"), object_bindings=(lazy_s3_access,)
    )
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(
        ref.metric("sales.revenue"), population=sources.population(ref.entity("sales.customers"))
    )
    result = logical.execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert isinstance(record.descriptor.storage_receipt, ObjectReceipt)
    assert result.to_pandas()["revenue"].fillna(-1).tolist() == [40, 100, 7, -1]
    assert all(
        isinstance(part.storage_receipt, ObjectReceipt) for part in record.descriptor.retained_parts
    )
    assert runtime.store.resources(runtime.session_ref) == ()


def test_engine_rows_remain_native_after_source_is_removed(tmp_path: Path) -> None:
    runtime, sources, database = setup_local(tmp_path)
    runtime.target = EngineTarget(next(iter(sources._owner.semantic_registry.datasources)))
    result = sources.observe(REVENUE).execute()
    database.rename(tmp_path / "offline.duckdb")
    filtered = result.where(gt(REVENUE, 15))
    logical = filtered.rank(filtered.fields.metric(REVENUE)).limit(2)
    output = logical.execute()
    assert output.to_pandas()["revenue"].tolist() == [100, 30]
    assert runtime.statistics.transferred_rows == 0
    assert runtime.statistics.worker_pid is None
    assert runtime.statistics.events.get("profile_resolution", 0) == 0
    assert runtime.statistics.events.get("credential_resolution", 0) == 0


def test_engine_metric_projection_selects_one_of_two_metrics_without_origin(tmp_path: Path) -> None:
    from tests.lazy_local_fixtures import COUNT

    runtime, sources, database = setup_local(tmp_path)
    runtime.target = EngineTarget(next(iter(sources._owner.semantic_registry.datasources)))
    original = sources.observe([REVENUE, COUNT]).execute()
    database.rename(tmp_path / "unavailable.duckdb")
    for selected, dropped in ((REVENUE, "order_count"), (COUNT, "revenue")):
        projected = original.metric(selected).execute()
        frame = projected.to_pandas()
        assert dropped not in frame.columns
        assert frame[selected.path.split(".")[-1]].fillna(-1).tolist() == (
            [10, 30, 100, -1, 0, 7] if selected == REVENUE else [1, 1, 1, 0, 1, 1]
        )
        assert runtime.statistics.transferred_rows == 0
        assert runtime.statistics.worker_pid is None
        assert runtime.statistics.events.get("profile_resolution", 0) == 0


def test_engine_checkpoint_keeps_membership_when_new_source_parameters_change(
    tmp_path: Path,
) -> None:
    from marivo.analysis.compiler.normalize import captured_parameters
    from tests.lazy_execution_fixtures import controlled_json_source

    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    with controlled_json_source() as server:
        registry, sidecar = make_execution_registry(database, api_url=server.url)
        runtime = DatasetRuntime.create(
            tmp_path, "captured-engine", target=EngineTarget(next(iter(registry.datasources)))
        )
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        entity, metric = ref.entity("sales.api"), ref.metric("sales.api_value")
        with sources.source_bindings({entity: {"tenant": "alpha"}}):
            definition = sources.population(entity)
        checkpoint = definition.execute()
        original_requests = len(server.requests)
        with sources.source_bindings({entity: {"tenant": "beta"}}):
            observed = sources.observe(metric, population=checkpoint)
        assert len(captured_parameters(observed)) == 1
        output = observed.execute()
        assert output.to_pandas()["api_value"].tolist() == [25]
        assert len(server.requests) == original_requests + 1
        assert all("beta" in value for value in server.requests[original_requests:])
        assert checkpoint.to_pandas()["entity_identity"].tolist() == [(1,)]
        assert runtime.store.resources(runtime.session_ref) == ()
