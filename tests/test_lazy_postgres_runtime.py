"""Opt-in real read-only PostgreSQL Group A Dataset acceptance."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import sql

from marivo.analysis import time_scope
from marivo.analysis.compiler.placement import SourceStep, place
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from tests.lazy_acceptance_capture import counts
from tests.lazy_postgres_fixtures import registry_for
from tests.multisource_environment import postgres_analysis as pg

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1", reason="opt-in PostgreSQL service"
    ),
]
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")


@pytest.fixture
def source_table() -> Iterator[str]:
    name = "dataset_" + uuid4().hex
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE TABLE {} (id BIGINT, tenant TEXT, customer_id BIGINT, order_id BIGINT, "
                "amount NUMERIC(18,2), weight DOUBLE PRECISION, region TEXT, channel TEXT, "
                'day DATE, start DATE, "end" DATE)'
            ).format(sql.Identifier(name))
        )
        admin.execute(
            sql.SQL(
                "INSERT INTO {} (id, amount, channel, day) VALUES "
                "(1,10.25,'a','2026-02-02'),(2,20.50,'a','2026-02-03'),"
                "(3,30.75,'b','2026-02-04'),(4,NULL,'b','2026-02-05'),"
                "(5,-2.00,'c','2026-02-06'),(6,999.00,'outside','2026-03-01')"
            ).format(sql.Identifier(name))
        )
        try:
            yield name
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


def test_group_a_source_reduction_and_retained_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_table: str,
) -> None:
    registry, sidecar = registry_for(source_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-group-a")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    observed = sources.observe(REVENUE, time_scope=time_scope(start="2026-02-01", end="2026-03-01"))
    grouped = observed.with_dimensions(CHANNEL).aggregate().where(gt(REVENUE, 0))
    target = grouped.rank(grouped.fields.metric(REVENUE)).limit(2).metric(REVENUE)
    assert runtime.statistics.events == {}
    graph = place(target)
    assert len(graph.steps) == 1 and isinstance(graph.steps[0], SourceStep)
    result = target.execute()
    frame = result.to_pandas()
    assert frame["revenue"].tolist() == [Decimal("30.75"), Decimal("30.75")]
    assert frame["channel"].tolist() == ["a", "b"]
    if "rank" in frame:
        assert frame["rank"].tolist() == [1, 2]
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 2
    primary = [statement for role, statement in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1
    assert "LIMIT 2" in primary[0] and "GROUP BY" in primary[0]
    assert not any("DESCRIBE" in statement for _, statement in runtime.statistics.statements)
    assert runtime.statistics.validation_queries > 0
    assert runtime.store.resources(runtime.session_ref) == ()
    before = counts(runtime)
    monkeypatch.delenv("MARIVO_TEST_POSTGRES_PASSWORD")
    assert target.execute().state.artifact_ref == result.state.artifact_ref
    assert counts(runtime) == before
    assert runtime.statistics.events.get("credential_resolution", 0) == 0
    folded = result.rollup(drop_dimensions=(CHANNEL,)).execute()
    assert folded.to_pandas()["revenue"].tolist() == [Decimal("61.50")]
    assert runtime.statistics.events.get("profile_resolution", 0) == 0


def test_invalid_identity_prevents_even_empty_publication_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_table: str,
) -> None:
    registry, sidecar = registry_for(source_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-validation")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("INSERT INTO {} (id, amount) VALUES (1, 1.0)").format(
                sql.Identifier(source_table)
            )
        )
    logical = sources.observe(REVENUE).where(gt(REVENUE, 1_000_000)).aggregate()
    with pytest.raises(MaterializationError, match="source validation failed"):
        logical.execute()
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("DELETE FROM {} WHERE amount=1.0").format(sql.Identifier(source_table))
        )
    result = logical.execute()
    assert result.to_pandas().empty or result.to_pandas()["revenue"].isna().all()


def test_large_source_small_output_and_all_reducers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_table: str,
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(source_table)))
        admin.execute(
            sql.SQL(
                "INSERT INTO {} (id, amount, channel, day) "
                "SELECT i, (i % 100)::numeric(18,2), (i % 10)::text, DATE '2026-02-02' "
                "FROM generate_series(1,20000) AS t(i)"
            ).format(sql.Identifier(source_table))
        )
    registry, sidecar = registry_for(source_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-large")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metrics = (
        REVENUE,
        ref.metric("sales.order_count"),
        ref.metric("sales.min_amount"),
        ref.metric("sales.max_amount"),
    )
    grouped = sources.observe(metrics).with_dimensions(CHANNEL).aggregate()
    target = grouped.rank(grouped.fields.metric(REVENUE)).limit(2)
    result = target.execute()
    frame = result.to_pandas()
    expected = sorted(
        [(sum(i % 100 for i in range(1, 20001) if i % 10 == group), group) for group in range(10)],
        reverse=True,
    )[:2]
    assert frame["revenue"].tolist() == [Decimal(total) for total, _ in expected]
    assert frame["order_count"].tolist() == [2000, 2000]
    assert frame["min_amount"].tolist() == [Decimal(group) for _, group in expected]
    assert frame["max_amount"].tolist() == [Decimal(90 + group) for _, group in expected]
    assert runtime.statistics.transferred_rows == 2
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.local_handoffs == ()


def test_cold_artifact_and_binding_hit_without_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_table: str,
) -> None:
    import subprocess
    import sys

    registry, sidecar = registry_for(source_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-cold")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    result = sources.observe(REVENUE).aggregate().execute()
    script = """
import sys
from decimal import Decimal
from pathlib import Path
from pytest import MonkeyPatch
from marivo.analysis.materialization.admission import DatasetRuntime
from tests.lazy_postgres_fixtures import registry_for
from marivo.refs import ref
REVENUE = ref.metric("sales.revenue")
with MonkeyPatch.context() as patch:
    registry, sidecar = registry_for(sys.argv[3], patch)
    patch.delenv("MARIVO_TEST_POSTGRES_PASSWORD")
    def forbidden(*args, **kwargs):
        raise AssertionError("Cold read or binding hit accessed the source")
    patch.setattr("marivo.analysis.materialization.admission._build_backend_from_effective", forbidden)
    runtime = DatasetRuntime.open(Path(sys.argv[1]), sys.argv[2])
    artifact = runtime.artifact(sys.argv[4])
    assert artifact.to_pandas()["revenue"].tolist() == [Decimal("1058.50")]
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    assert sources.observe(REVENUE).aggregate().execute().state.artifact_ref == artifact.state.artifact_ref
    assert runtime.statistics.events.get("credential_resolution", 0) == 0
print("cold-read-and-binding-hit-ok")
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path),
            runtime.session_ref,
            source_table,
            result.state.artifact_ref.ref,
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert "cold-read-and-binding-hit-ok" in completed.stdout


@pytest.mark.parametrize("population_only", [False, True])
def test_entity_identity_record_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_table: str,
    population_only: bool,
) -> None:
    registry, sidecar = registry_for(source_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "postgres-identity")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.population(ref.entity("sales.orders"))
        if population_only
        else sources.observe(REVENUE)
    )
    result = logical.execute()
    frame = result.to_pandas()
    assert len(frame) == 6
    identity = next(
        column.name for column in result.schema.columns if column.role_id == "entity_identity"
    )
    assert frame[identity].tolist() == [(i,) for i in range(1, 7)]


@pytest.mark.parametrize(
    "kind,physical,values,expected",
    [
        ("int64", "bigint", (2**53 + 1, -(2**53), None, 7), 8),
        ("float64", "double precision", (1.25, -2.5, None, 0), -1.25),
        ("float32", "real", (1.25, -2.5, None, 0), -1.25),
    ],
)
def test_scalar_numeric_and_null_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_table: str,
    kind: str,
    physical: str,
    values: tuple[int | float | None, ...],
    expected: int | float,
) -> None:
    with pg.connection(admin=True) as admin:
        admin.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(source_table)))
        admin.execute(
            sql.SQL("ALTER TABLE {} ALTER COLUMN amount TYPE {} USING amount::{}").format(
                sql.Identifier(source_table), sql.SQL(physical), sql.SQL(physical)
            )
        )
        with admin.cursor() as cursor:
            cursor.executemany(
                sql.SQL("INSERT INTO {} (id, amount) VALUES (%s,%s)").format(
                    sql.Identifier(source_table)
                ),
                [(i, amount) for i, amount in enumerate(values, start=1)],
            )
    registry, sidecar = registry_for(source_table, monkeypatch)
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    registry = replace(
        registry,
        entities={
            **registry.entities,
            "sales.orders": replace(
                entity,
                source=replace(
                    entity.source,
                    columns=tuple(
                        (name, replace(binding, data_type=kind))
                        if name == "amount"
                        else (name, binding)
                        for name, binding in entity.source.columns
                    ),
                ),
            ),
        },
    )
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "postgres-numeric")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    output = sources.observe([REVENUE, ref.metric("sales.order_count")]).aggregate().execute()
    frame = output.to_pandas()
    assert frame["revenue"].tolist() == [expected]
    assert frame["order_count"].tolist() == [3]


@pytest.mark.parametrize("point", ["output_reserved", "transfer", "after_rename", "before_commit"])
def test_publication_failure_is_atomic_and_retry_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_table: str,
    point: str,
) -> None:
    registry, sidecar = registry_for(source_table, monkeypatch)
    fired = False
    original = OSError("controlled publication fault")

    def fail(event: str) -> None:
        nonlocal fired
        if event == point and not fired:
            fired = True
            raise original

    runtime = DatasetRuntime.create(tmp_path, "postgres-atomic", event=fail)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(REVENUE).aggregate()
    with pytest.raises(OSError) as caught:
        logical.execute()
    assert caught.value is original and fired
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    result = logical.execute()
    assert result.to_pandas()["revenue"].tolist() == [Decimal("1058.50")]
