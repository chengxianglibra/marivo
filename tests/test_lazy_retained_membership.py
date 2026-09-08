"""Runtime proof of retained identity boundaries and independent time scopes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import duckdb
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import EngineTarget, ObjectTarget, S3Access
from marivo.analysis.observation.contracts import metric_definition, scope_payload
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from tests.lazy_adapter_fixtures import setup_adapter
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

pytestmark = pytest.mark.runtime

REVENUE = ref.metric("sales.revenue")
DAY = ref.time_dimension("sales.orders.order_time")
CHANNEL = ref.dimension("sales.orders.channel")
JANUARY = time_scope(start="2026-01-01", end="2026-02-01")
FEBRUARY = time_scope(start="2026-02-01", end="2026-03-01")


@pytest.mark.parametrize("version", ["snapshots", "validity"])
def test_resolved_engine_versioned_population_continues_after_membership_table_drop(
    tmp_path: Path,
    version: str,
) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    original = fixture.sources._owner.semantic_registry
    relation = original.relationships["sales.order_customer"]
    registry = replace(original, relationships=dict(original.relationships))
    registry.relationships["sales.order_member"] = replace(
        relation,
        semantic_id="sales.order_member",
        name="order_member",
        to_entity=f"sales.{version}",
    )
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    checkpoint = sources.population(ref.entity(f"sales.{version}"), time_scope=FEBRUARY).execute()
    assert checkpoint.to_pandas()["entity_identity"].tolist() == [(1,), (2,)]
    with duckdb.connect(str(fixture.database)) as database:
        database.execute(f"DROP TABLE {version}")
    start = len(fixture.runtime.statistics.statements)
    result = (
        sources.observe(REVENUE, population=checkpoint, time_scope=FEBRUARY).aggregate().execute()
    )
    assert result.to_pandas()["revenue"].tolist() == [140]
    assert all(
        f'"{version}"' not in sql for _, sql in fixture.runtime.statistics.statements[start:]
    )
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
    cold = DatasetRuntime.open(tmp_path, fixture.runtime.session_ref, target=fixture.runtime.target)
    retained = cold.artifact(checkpoint.state.artifact_ref)
    assert isinstance(retained, MaterializedPopulationDataset)
    cold_sources = cold.sources(semantic_registry=registry, sidecar=fixture.sources._owner.sidecar)
    continued = cold_sources.observe(REVENUE, population=retained, time_scope=FEBRUARY).aggregate()
    with patch.object(
        admission, "place", side_effect=AssertionError("cold binding performed placement")
    ):
        reused = continued.execute()
    assert reused.state.artifact_ref == result.state.artifact_ref
    assert not cold.statistics.statements and cold.statistics.worker_pid is None


@pytest.mark.parametrize("with_time", [False, True])
def test_engine_entity_unique_metric_coordinates_supply_identity_without_origin(
    tmp_path: Path,
    with_time: bool,
) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    logical = fixture.sources.observe(REVENUE).with_dimensions(CHANNEL)
    if with_time:
        logical = logical.with_time_axis(DAY, grain=grain("day"))
    checkpoint = logical.where(gt(REVENUE, 0)).execute()
    assert set(checkpoint.to_pandas()["entity_identity"]) >= {(1,), (2,), (3,)}
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DROP TABLE orders")
    start = len(fixture.runtime.statistics.statements)
    observed = fixture.sources.observe(ref.metric("sales.line_revenue"), population=checkpoint)
    assert isinstance(observed._root, LogicalRootHandle)
    assert isinstance(observed._root.inputs[0].root, MaterializedScanLeafHandle)
    result = observed.aggregate().execute()
    assert result.to_pandas()["line_revenue"].tolist() == [65]
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert (
        record.descriptor.population_authority.definition_fingerprint
        == checkpoint.definition_fingerprint
    )
    assert all('"orders"' not in sql for _, sql in fixture.runtime.statistics.statements[start:])
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_january_checkpoint_does_not_become_the_new_observation_scope(tmp_path: Path) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DELETE FROM snapshots")
        database.execute(
            "INSERT INTO snapshots (id, day) VALUES (1, DATE '2026-01-31'), (3, DATE '2026-01-31'), (2, DATE '2026-02-28')"
        )
    original = fixture.sources._owner.semantic_registry
    relation = original.relationships["sales.order_customer"]
    registry = replace(original, relationships=dict(original.relationships))
    registry.relationships["sales.order_member"] = replace(
        relation, semantic_id="sales.order_member", name="order_member", to_entity="sales.snapshots"
    )
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    membership = sources.population(ref.entity("sales.snapshots"), time_scope=JANUARY)
    checkpoint = membership.execute()
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DROP TABLE snapshots")
    february = sources.observe(REVENUE, population=checkpoint, time_scope=FEBRUARY).aggregate()
    unscoped = sources.observe(REVENUE, population=checkpoint).aggregate()
    assert metric_definition(february).time_scope == FEBRUARY
    assert metric_definition(unscoped).time_scope is None
    assert february.definition_fingerprint != unscoped.definition_fingerprint
    explicit, omitted = february.execute(), unscoped.execute()
    assert explicit.to_pandas()["revenue"].tolist() == [40]
    assert omitted.to_pandas()["revenue"].tolist() == [47]
    for value in (explicit, omitted):
        record = fixture.runtime.store.artifact(value.state.artifact_ref.ref)
        assert record is not None
        assert record.descriptor.population_authority.membership_scope == scope_payload(JANUARY)
        assert record.descriptor.population_authority.version_selection is not None
    cold = DatasetRuntime.open(tmp_path, fixture.runtime.session_ref, target=fixture.runtime.target)
    retained = cold.artifact(checkpoint.state.artifact_ref)
    assert isinstance(retained, MaterializedPopulationDataset)
    cold_sources = cold.sources(semantic_registry=registry, sidecar=fixture.sources._owner.sidecar)
    with patch.object(
        admission, "place", side_effect=AssertionError("cold binding performed placement")
    ):
        assert (
            cold_sources.observe(REVENUE, population=retained, time_scope=FEBRUARY)
            .aggregate()
            .execute()
            .state.artifact_ref
            == explicit.state.artifact_ref
        )
        assert (
            cold_sources.observe(REVENUE, population=retained)
            .aggregate()
            .execute()
            .state.artifact_ref
            == omitted.state.artifact_ref
        )
    assert not cold.statistics.statements and cold.statistics.worker_pid is None


def test_true_entity_time_multiplicity_is_rejected_before_identity_projection(
    tmp_path: Path,
) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    population = fixture.sources.population(ref.entity("sales.customers"))
    metric = fixture.sources.observe(
        REVENUE, population=population, time_scope=FEBRUARY
    ).with_time_axis(DAY, grain=grain("day"))
    checkpoint = metric.execute()
    identities = checkpoint.to_pandas()["entity_identity"].tolist()
    assert len(identities) > len(set(identities))
    with pytest.raises(DatasetConstructionError, match="Entity-unique"):
        fixture.sources.observe(REVENUE, population=checkpoint)


@pytest.mark.parametrize("kind", ["parquet", "object"])
def test_local_identity_never_implicitly_imports_into_source(
    tmp_path: Path,
    kind: Literal["parquet", "object"],
    request: pytest.FixtureRequest,
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    access: S3Access | None = None
    if kind == "object":
        value: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(value, S3Access)
        access = value
    runtime = DatasetRuntime.create(
        tmp_path, "local-membership", object_bindings=() if access is None else (access,)
    )
    if access is not None:
        runtime.target = ObjectTarget("fixture")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    checkpoint = sources.population(ref.entity("sales.customers")).execute()
    runtime.target = EngineTarget(next(iter(registry.datasources)))
    observed = sources.observe(REVENUE, population=checkpoint)
    with pytest.raises(DatasetCompilationError, match="source-required"):
        observed.execute()
    assert runtime.store.resources(runtime.session_ref) == ()


def test_logical_rollup_keeps_the_nearest_observations_selected_membership(tmp_path: Path) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    selected = fixture.sources.observe(REVENUE).where(gt(REVENUE, 0))
    followup = (
        fixture.sources.observe(ref.metric("sales.order_count"), population=selected)
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    output = followup.rollup(drop_dimensions=(CHANNEL,)).execute()
    assert output.to_pandas()["order_count"].tolist() == [4]
    record = fixture.runtime.store.artifact(output.state.artifact_ref.ref)
    assert record is not None
    assert (
        record.descriptor.population_authority.definition_fingerprint
        == selected.definition_fingerprint
    )


@pytest.mark.parametrize("version", ["snapshots", "validity"])
def test_retained_versioned_membership_keeps_independent_current_metric_versions(
    tmp_path: Path,
    version: str,
) -> None:
    from marivo.analysis.compiler.normalize import required_entities
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.semantic.ir import SemiAdditive, TimeFoldIR

    fixture = setup_adapter(tmp_path, "engine")
    with duckdb.connect(str(fixture.database)) as database:
        database.execute(f"DELETE FROM {version}")
        if version == "snapshots":
            database.execute(
                "INSERT INTO snapshots (id, amount, day) VALUES (1,10,DATE '2026-01-31'),(2,20,DATE '2026-01-31'),(1,100,DATE '2026-02-28'),(2,200,DATE '2026-02-28'),(3,900,DATE '2026-02-28'),(1,1000,DATE '2026-03-31'),(2,2000,DATE '2026-03-31'),(3,9000,DATE '2026-03-31')"
            )
        else:
            database.execute(
                "INSERT INTO validity (id, amount, start, \"end\") VALUES (1,10,DATE '2026-01-01',DATE '2026-02-01'),(2,20,DATE '2026-01-01',DATE '2026-02-01'),(1,100,DATE '2026-02-01',DATE '2026-03-01'),(2,200,DATE '2026-02-01',DATE '2026-03-01'),(3,900,DATE '2026-02-01',DATE '2026-03-01'),(1,1000,DATE '2026-03-01',NULL),(2,2000,DATE '2026-03-01',NULL),(3,9000,DATE '2026-03-01',NULL)"
            )
    original = fixture.sources._owner.semantic_registry
    registry = replace(original, metrics=dict(original.metrics), measures=dict(original.measures))
    entity = f"sales.{version}"
    axis = f"{entity}.snapshot_at" if version == "snapshots" else f"{entity}.valid_from"
    measure = f"{entity}.amount"
    registry.measures[measure] = replace(
        registry.measures[measure], additivity=SemiAdditive(axis, TimeFoldIR("last"))
    )
    registry.metrics["sales.version_value"] = replace(
        registry.metrics[REVENUE.path],
        semantic_id="sales.version_value",
        name="version_value",
        entities=(entity,),
        measure=measure,
        aggregation_target=measure,
    )
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    checkpoint = sources.population(ref.entity(entity), time_scope=JANUARY).execute()
    assert checkpoint.to_pandas()["entity_identity"].tolist() == [(1,), (2,)]
    with duckdb.connect(str(fixture.database)) as database:
        database.execute(f"UPDATE {version} SET amount=111 WHERE id=1 AND amount=100")
    for scope, expected in ((JANUARY, 30), (FEBRUARY, 311), (None, 3000)):
        logical = sources.observe(
            ref.metric("sales.version_value"), population=checkpoint, time_scope=scope
        ).aggregate()
        assert metric_definition(logical).time_scope == scope
        assert entity in {item.ref.path for item in required_entities(logical)}
        result = logical.execute()
        assert result.to_pandas()["version_value"].tolist() == [expected]
        record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
        assert record is not None
        assert record.descriptor.population_authority.membership_scope == scope_payload(JANUARY)
        assert checkpoint.to_pandas()["entity_identity"].tolist() == [(1,), (2,)]
    with duckdb.connect(str(fixture.database)) as database:
        database.execute(f"DROP TABLE {version}")
    new_scope = time_scope(start="2026-04-01", end="2026-05-01")
    with pytest.raises(MaterializationError):
        sources.observe(
            ref.metric("sales.version_value"), population=checkpoint, time_scope=new_scope
        ).aggregate().execute()
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_accidentally_unique_filtered_coordinates_do_not_authorize_membership(
    tmp_path: Path,
) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    metric = (
        fixture.sources.observe(
            REVENUE,
            population=fixture.sources.population(ref.entity("sales.customers")),
            time_scope=FEBRUARY,
        )
        .with_time_axis(DAY, grain=grain("day"))
        .where(gt(REVENUE, 50))
    )
    with pytest.raises(DatasetConstructionError, match="Entity-unique"):
        fixture.sources.observe(REVENUE, population=metric)
    checkpoint = metric.execute()
    assert checkpoint.to_pandas()["entity_identity"].tolist() == [(2,)]
    with pytest.raises(DatasetConstructionError, match="Entity-unique"):
        fixture.sources.observe(REVENUE, population=checkpoint)
