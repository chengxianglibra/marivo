"""Real read-only PostgreSQL composed Metric and local suffix acceptance."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from psycopg import sql

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.operators.forecast_contracts import naive, periods
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_acceptance_capture import counts
from tests.lazy_postgres_fixtures import registry_for
from tests.lazy_scalar_source_fixtures import TimeFoldIR
from tests.multisource_environment import postgres_analysis as pg

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1", reason="opt-in PostgreSQL service"
    ),
]
CHANNEL = ref.dimension("sales.orders.channel")
TIME = ref.time_dimension("sales.orders.order_time")


def _method_registry(
    table: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = registry_for(table, monkeypatch)
    entities = dict(registry.entities)
    entity = entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.orders"] = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (name, replace(binding, data_type="float64") if name == "amount" else binding)
                for name, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    return registry, sidecar


@pytest.fixture
def method_table() -> Iterator[str]:
    name = "methods_" + uuid4().hex
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE TABLE {} (id BIGINT, tenant TEXT, customer_id BIGINT, order_id BIGINT, "
                "amount DOUBLE PRECISION, weight DOUBLE PRECISION, region TEXT, channel TEXT, "
                'day DATE, start DATE, "end" DATE)'
            ).format(sql.Identifier(name))
        )
        admin.execute(
            sql.SQL(
                "INSERT INTO {} (id, amount, weight, channel, day) VALUES "
                "(1,10,1,'a','2026-02-01'),(2,20,3,'a','2026-02-02'),"
                "(3,30,2,'b','2026-02-03'),(4,NULL,4,'b','2026-02-04'),"
                "(5,40,0,'b','2026-02-04')"
            ).format(sql.Identifier(name))
        )
        try:
            yield name
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.mark.parametrize(
    ("name", "expected", "total"),
    [
        ("mean_amount", [15.0, 35.0], 25.0),
        ("weighted_amount", [17.5, 30.0], 130.0 / 6.0),
        ("conversion_rate", [15.0, 35.0], 25.0),
    ],
)
def test_composed_metrics_preserve_sufficient_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
    name: str,
    expected: list[float],
    total: float,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-composed")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = ref.metric(f"sales.{name}")
    logical = sources.observe(metric).with_dimensions(CHANNEL).aggregate()
    result = logical.execute()
    frame = result.to_pandas().sort_values("channel")
    assert frame[name].astype(float).tolist() == pytest.approx(expected)
    primary = [statement for role, statement in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1 and "GROUP BY" in primary[0]
    assert runtime.statistics.transferred_rows == 2
    assert runtime.statistics.transferred_bytes > 0
    before = counts(runtime)
    monkeypatch.delenv("MARIVO_TEST_POSTGRES_PASSWORD")
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert counts(runtime) == before
    folded = result.rollup(drop_dimensions=(CHANNEL,)).execute().to_pandas()
    assert float(folded[name].iloc[0]) == pytest.approx(total)


def test_date_series_forecast_receives_complete_reduced_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-forecast")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
        .forecast(horizon=periods(2), model=naive())
    )
    frame = logical.execute().to_pandas()
    assert frame.forecast_value.tolist() == [40.0, 40.0]
    assert frame.training_row_count.tolist() == [4, 4]
    assert runtime.statistics.transferred_rows == 4
    primary = [statement for role, statement in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1 and "GROUP BY" in primary[0]


def test_grouped_kendall_uses_complete_source_reduction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-kendall")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
        .correlate(method="kendall")
    )
    frame = logical.execute().to_pandas()
    assert frame.coefficient.tolist() == pytest.approx([1.0])
    assert runtime.statistics.transferred_rows == 4


def test_grouped_compare_and_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-attribution")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(ref.metric("sales.mean_amount")).with_dimensions(CHANNEL).aggregate()
    result = logical.compare(logical).attribute(axes=(CHANNEL,)).execute()
    frame = result.to_pandas()
    assert frame.contribution.tolist() == pytest.approx([0.0, 0.0])
    assert runtime.statistics.primary_queries == 1


def _fold_registry(
    table: str,
    monkeypatch: pytest.MonkeyPatch,
    fold: Literal["first", "last", "mean", "min", "max"],
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Bind the orders measure to a sampled status axis with the given fold."""
    from tests.lazy_scalar_source_fixtures import fold_registry

    registry, sidecar = registry_for(table, monkeypatch)
    return fold_registry(registry, sidecar, TimeFoldIR(fold), amount_data_type="float64")


@pytest.fixture
def fold_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Admin-created timestamp inventory fixture; every status sits inside the window."""
    name = "fold_" + uuid4().hex
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE TABLE {} (id BIGINT, tenant TEXT, customer_id BIGINT, order_id BIGINT, "
                "amount DOUBLE PRECISION, weight DOUBLE PRECISION, region TEXT, channel TEXT, "
                'day TIMESTAMP, start DATE, "end" DATE)'
            ).format(sql.Identifier(name))
        )
        admin.execute(
            sql.SQL(
                "INSERT INTO {} (id, customer_id, amount, channel, day) VALUES "
                "(1, 1, 10, 'a', TIMESTAMP '2026-02-02 09:00:00'),"
                "(2, 1, 30, 'a', TIMESTAMP '2026-02-02 17:00:00'),"
                "(3, 2, 100, 'b', TIMESTAMP '2026-02-03 12:00:00'),"
                "(4, 2, 40, 'b', TIMESTAMP '2026-02-03 14:30:00')"
            ).format(sql.Identifier(name))
        )
        admin.execute(
            sql.SQL("UPDATE {} SET start = DATE '2026-02-04'").format(sql.Identifier(name))
        )
        try:
            yield name
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("first", [10.0, 100.0]),
        ("last", [30.0, 40.0]),
        ("mean", [20.0, 70.0]),
        ("min", [10.0, 40.0]),
        ("max", [30.0, 100.0]),
    ],
)
def test_status_fold_spatial_sums(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fold_table: str,
    kind: str,
    expected: list[float],
) -> None:
    """Live status-time folds lower through the shared fold with spatial sums."""
    registry, sidecar = _fold_registry(fold_table, monkeypatch, kind)
    runtime = DatasetRuntime.create(tmp_path, f"postgres-fold-{kind}")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
        )
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    frame = logical.execute().to_pandas().sort_values("channel")
    assert frame.revenue.astype(float).tolist() == pytest.approx(expected)
    # The lowering's status gate executed as a source validation.
    assert any(
        role == "validation_batch" and "__mv_status" in statement
        for role, statement in runtime.statistics.statements
    )
    primary = [statement for role, statement in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1 and "__mv_status" in primary[0]


def test_status_fold_versioned_metric_composite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fold_table: str,
) -> None:
    """A versioned Metric plus status-time fold stays on the shared lowering."""
    from marivo.semantic.ir import AiContextIR, DateParse, DimensionKind, SnapshotVersioningIR

    registry, sidecar = _fold_registry(fold_table, monkeypatch, "last")
    entities = dict(registry.entities)
    entity = entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.orders"] = replace(
        entity,
        versioning=SnapshotVersioningIR("snapshot", "sales.orders.snapshot_day", "day"),
    )
    dimensions = dict(registry.dimensions)
    dimensions["sales.orders.snapshot_day"] = replace(
        registry.dimensions["sales.orders.order_time"],
        semantic_id="sales.orders.snapshot_day",
        name="snapshot_day",
        is_default=False,
        kind=DimensionKind.TIME,
        granularity="day",
        parse=DateParse(),
        source_column="start",
        ai_context=AiContextIR(),
    )
    registry = replace(registry, entities=entities, dimensions=dimensions)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "postgres-fold-versioned")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    scope = time_scope(start="2026-02-01", end="2026-02-05")
    population = sources.population(ref.entity("sales.orders"), time_scope=scope)
    logical = (
        sources.observe(ref.metric("sales.revenue"), population=population, time_scope=scope)
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    frame = logical.execute().to_pandas().sort_values("channel")
    assert frame.revenue.astype(float).tolist() == pytest.approx([30.0, 40.0])
    assert any(
        role == "validation_batch" and "__mv_status" in statement
        for role, statement in runtime.statistics.statements
    )
    primary = [statement for role, statement in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1 and "__mv_status" in primary[0]


def test_relationship_dimension_reads_same_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    entities = dict(registry.entities)
    customer = entities["sales.customers"]
    assert isinstance(customer.source, TableSourceIR)
    # A separate semantic table binding over the same physical fixture keeps relation
    # identities independent while exercising actual PostgreSQL join validation.
    entities["sales.customers"] = replace(
        customer,
        source=replace(
            customer.source,
            table=method_table,
            database="public",
            columns=customer.source.columns,
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("UPDATE {} SET customer_id = id, region = channel").format(
                sql.Identifier(method_table)
            )
        )
    runtime = DatasetRuntime.create(tmp_path, "postgres-relations")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(ref.metric("sales.mean_amount"))
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
    )
    frame = logical.execute().to_pandas().sort_values("region")
    assert frame.mean_amount.tolist() == pytest.approx([15.0, 35.0])
    assert any(
        "JOIN" in statement
        for role, statement in runtime.statistics.statements
        if role == "primary"
    )


@pytest.mark.parametrize(
    "entity_name,case",
    [
        ("snapshots", "valid"),
        ("snapshots", "empty"),
        ("snapshots", "overlap"),
        ("validity", "valid"),
        ("validity", "empty"),
        ("validity", "overlap"),
        ("validity", "reversed"),
        ("validity", "closed_closed"),
        ("validity", "sentinel"),
    ],
)
def test_version_selection_preserves_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
    entity_name: str,
    case: str,
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.semantic.ir import ValidityVersioningIR

    registry, sidecar = _method_registry(method_table, monkeypatch)
    entities = dict(registry.entities)
    entity = entities[f"sales.{entity_name}"]
    assert isinstance(entity.source, TableSourceIR)
    entities[f"sales.{entity_name}"] = replace(
        entity,
        source=replace(
            entity.source,
            table=method_table,
            database="public",
            columns=entity.source.columns,
        ),
    )
    if isinstance(entity.versioning, ValidityVersioningIR):
        version = entity.versioning
        if case == "closed_closed":
            version = replace(version, interval="closed_closed")
        if case == "sentinel":
            version = replace(version, open_end=("9999-12-31",))
        entities[f"sales.{entity_name}"] = replace(
            entities[f"sales.{entity_name}"], versioning=version
        )
    registry = replace(registry, entities=entities)
    registry.freeze()
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL(
                "UPDATE {} SET day = DATE '2026-02-28', start = DATE '2026-02-01', \"end\" = NULL"
            ).format(sql.Identifier(method_table))
        )
        if case == "empty":
            admin.execute(
                sql.SQL("UPDATE {} SET day = DATE '2026-03-01', start = DATE '2026-03-01'").format(
                    sql.Identifier(method_table)
                )
            )
        elif case == "overlap":
            admin.execute(sql.SQL("UPDATE {} SET id = 1").format(sql.Identifier(method_table)))
        elif case == "reversed":
            admin.execute(
                sql.SQL(
                    "UPDATE {} SET start = DATE '2026-03-02', \"end\" = DATE '2026-03-01'"
                ).format(sql.Identifier(method_table))
            )
        elif case == "sentinel":
            admin.execute(
                sql.SQL("UPDATE {} SET \"end\" = DATE '9999-12-31'").format(
                    sql.Identifier(method_table)
                )
            )
    runtime = DatasetRuntime.create(tmp_path, "postgres-versions")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.population(
        ref.entity(f"sales.{entity_name}"),
        time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
    )
    if case in {"overlap", "reversed"} or (entity_name == "snapshots" and case == "empty"):
        with pytest.raises(MaterializationError):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
        return
    result = logical.execute()
    assert sorted(result.to_pandas().entity_identity.tolist()) == (
        [] if case == "empty" else [(1,), (2,), (3,), (4,), (5,)]
    )
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert (
        record is not None and record.descriptor.population_authority.version_selection is not None
    )


@pytest.mark.parametrize("unit", ["day", "week", "month", "quarter", "year"])
def test_civil_date_bucket_reduction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
    unit: Literal["day", "week", "month", "quarter", "year"],
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-date-bucket")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(ref.metric("sales.revenue"))
        .with_time_axis(TIME, grain=grain(unit))
        .aggregate()
    )
    frame = logical.execute().to_pandas()
    assert frame.revenue.sum() == pytest.approx(100.0)
    assert len(frame) == (4 if unit == "day" else 2 if unit == "week" else 1)


def test_entity_mean_preserves_identity_and_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-entity-mean")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = sources.observe(ref.metric("sales.mean_amount")).execute().to_pandas()
    assert sorted(frame.entity_identity.tolist()) == [(1,), (2,), (3,), (4,), (5,)]
    assert frame.mean_amount.dropna().sort_values().tolist() == [10.0, 20.0, 30.0, 40.0]
    assert frame.mean_amount.isna().sum() == 1


@pytest.mark.parametrize("name,total", [("conversion_rate", 25.0), ("weighted_amount", 130 / 6)])
def test_decimal_inputs_with_float_composed_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
    name: str,
    total: float,
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("ALTER TABLE {} ALTER COLUMN amount TYPE NUMERIC(18,2)").format(
                sql.Identifier(method_table)
            )
        )
    registry, sidecar = registry_for(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-decimal-composed")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    result = (
        sources.observe(ref.metric(f"sales.{name}")).with_dimensions(CHANNEL).aggregate().execute()
    )
    frame = result.rollup(drop_dimensions=(CHANNEL,)).execute().to_pandas()
    assert float(frame[name].iloc[0]) == pytest.approx(total)


def test_unresolved_decimal_mean_rejected_before_source_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    from marivo.analysis.compiler.errors import DatasetCompilationError

    registry, sidecar = registry_for(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-decimal-mean")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    with pytest.raises(DatasetCompilationError, match="Decimal"):
        sources.observe(ref.metric("sales.mean_amount")).aggregate().execute()
    assert runtime.statistics.events.get("backend_connect", 0) == 0
    assert runtime.statistics.primary_queries == 0


def test_time_discovery_consumes_complete_grouped_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-discovery")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
        .discover.point_anomalies()
    )
    result = logical.execute()
    assert runtime.statistics.transferred_rows == 4
    assert result.to_pandas() is not None


@pytest.mark.parametrize("weight", [-1.0, 0.0])
def test_signed_and_zero_weight_groups_follow_null_denominator_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
    weight: float,
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("UPDATE {} SET weight = %s").format(sql.Identifier(method_table)), (weight,)
        )
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-invalid-weights")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = sources.observe(ref.metric("sales.weighted_amount")).aggregate().execute().to_pandas()
    if weight == 0:
        assert frame.weighted_amount.isna().all()
    else:
        assert frame.weighted_amount.tolist() == [25.0]


def test_empty_and_null_composed_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(sql.SQL("UPDATE {} SET amount = NULL").format(sql.Identifier(method_table)))
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-null-composed")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = (
        sources.observe([ref.metric("sales.mean_amount"), ref.metric("sales.conversion_rate")])
        .aggregate()
        .execute()
        .to_pandas()
    )
    assert frame.mean_amount.isna().all() and frame.conversion_rate.isna().all()


def test_one_to_many_cross_root_ratio_keeps_contribution_grain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    entities = dict(registry.entities)
    entity = entities["sales.lines"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.lines"] = replace(
        entity, source=replace(entity.source, table=method_table, database="public")
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    with pg.connection(admin=True) as admin:
        admin.execute(sql.SQL("UPDATE {} SET order_id = 1").format(sql.Identifier(method_table)))
    runtime = DatasetRuntime.create(tmp_path, "postgres-contribution")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    result = (
        sources.observe(
            ref.metric("sales.cross_root_ratio"),
            population=sources.population(ref.entity("sales.orders")),
        )
        .aggregate()
        .execute()
    )
    assert result.to_pandas().cross_root_ratio.tolist() == pytest.approx([1.0])


@pytest.mark.parametrize("bad", ["duplicate", "missing"])
def test_relation_target_integrity_is_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
    bad: str,
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    registry, sidecar = _method_registry(method_table, monkeypatch)
    entities = dict(registry.entities)
    entity = entities["sales.customers"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.customers"] = replace(
        entity, source=replace(entity.source, table=method_table, database="public")
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("UPDATE {} SET customer_id = 999, region = channel").format(
                sql.Identifier(method_table)
            )
        )
        if bad == "duplicate":
            admin.execute(sql.SQL("UPDATE {} SET id = 1").format(sql.Identifier(method_table)))
    runtime = DatasetRuntime.create(tmp_path, "postgres-relation-invalid")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(ref.metric("sales.revenue"))
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
    )
    if bad == "duplicate":
        with pytest.raises(MaterializationError):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
    else:
        frame = logical.execute().to_pandas()
        assert frame.region.isna().all()
        assert frame.revenue.tolist() == [100.0]
