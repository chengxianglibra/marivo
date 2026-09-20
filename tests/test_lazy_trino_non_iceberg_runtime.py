"""Opt-in real SELECT-only Trino non-Iceberg (memory catalog) Dataset acceptance."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_acceptance_capture import counts
from tests.lazy_scalar_source_fixtures import capture_submissions, registry_for
from tests.multisource_environment import trino_analysis as trino

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_TRINO_NON_ICEBERG_TEST") != "1",
        reason="opt-in Trino non-Iceberg memory catalog",
    ),
]
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")
TIME = ref.time_dimension("sales.orders.order_time")
DATABASE = ("noniceberg", "analysis")
CATALOGS_OBSERVATION = "SELECT connector_name FROM system.metadata.catalogs WHERE catalog_name = ?"
# Hand-computed over the fixture rows (NULL amount excluded from sums and
# counts, zero weights included with zero contribution):
#   revenue = 10.25 + 20.5 + 30.75 - 2.0 + 999.0 = 1058.5
#   weighted numerator = 10.25*1 + 20.5*2 + 30.75*4 + 999.0*1 = 1173.25
#   weighted denominator = 1 + 2 + 4 + 0 + 1 = 8 -> 1173.25 / 8 = 146.65625


def noniceberg_registry(tmp_path: Path, table: str) -> tuple[Registry, CompiledExpressionSidecar]:
    """Re-point the shared fixture entity at a noniceberg memory-catalog table."""
    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=table)
    entities = dict(registry.entities)
    entity = entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.orders"] = replace(
        entity,
        source=replace(
            entity.source,
            table=table,
            database=DATABASE,
            columns=tuple(
                (
                    name,
                    replace(binding, data_type="decimal(9, 2)") if name == "amount" else binding,
                )
                for name, binding in entity.source.columns
            ),
        ),
    )
    datasources = {
        name: replace(value, fields={**value.fields, "catalog": trino.NON_ICEBERG_CATALOG})
        for name, value in registry.datasources.items()
    }
    registry = replace(registry, entities=entities, datasources=datasources)
    registry.freeze()
    return registry, sidecar


@pytest.fixture
def source_table() -> Iterator[str]:
    """Seed one disposable five-type memory table; admin prepares, then drops.

    The columns carry the plan-required physical types (bigint varchar double
    date decimal) in the standard fixture shape the registry declares.
    """
    table = "noniceberg_" + uuid4().hex
    with trino.connection(admin=True, catalog=trino.NON_ICEBERG_CATALOG) as con:
        cur = con.cursor()
        try:
            cur.execute(
                f"CREATE TABLE {trino.NON_ICEBERG_CATALOG}.analysis.{table}"
                " (id BIGINT, tenant VARCHAR, customer_id BIGINT, order_id BIGINT,"
                " amount DECIMAL(9, 2), weight DOUBLE, region VARCHAR, channel VARCHAR,"
                ' day DATE, "start" DATE, "end" DATE)'
            ).fetchall()
            cur.execute(
                f"INSERT INTO {trino.NON_ICEBERG_CATALOG}.analysis.{table}"
                " (id, amount, channel, day, weight) VALUES"
                " (1, 10.25, 'a', DATE '2026-02-02', 1.0),"
                " (2, 20.5, 'a', DATE '2026-02-03', 2.0),"
                " (3, 30.75, 'b', DATE '2026-02-04', 4.0),"
                " (4, NULL, 'b', DATE '2026-02-05', 2.0),"
                " (5, -2.0, 'c', DATE '2026-02-06', 0.0),"
                " (6, 999.0, 'outside', DATE '2026-03-01', 1.0)"
            ).fetchall()
            yield table
        finally:
            cur.execute(
                f"DROP TABLE IF EXISTS {trino.NON_ICEBERG_CATALOG}.analysis.{table}"
            ).fetchall()
            cur.close()


def test_full_journey(tmp_path: Path, source_table: str) -> None:
    registry, sidecar = noniceberg_registry(tmp_path, source_table)
    runtime = DatasetRuntime.create(tmp_path, "noniceberg-journey")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = (
        sources.observe(
            (
                REVENUE,
                ref.metric("sales.order_count"),
                ref.metric("sales.min_amount"),
                ref.metric("sales.max_amount"),
            )
        )
        .aggregate()
        .execute()
        .to_pandas()
    )
    assert frame.revenue.tolist() == [1058.5]
    assert frame.order_count.tolist() == [5]
    assert frame.min_amount.tolist() == [-2.0]
    assert frame.max_amount.tolist() == [999.0]
    grouped = (
        sources.observe(REVENUE, time_scope=time_scope(start="2026-02-01", end="2026-03-01"))
        .with_dimensions(CHANNEL)
        .aggregate()
        .where(gt(REVENUE, 0))
    )
    ranked = grouped.rank(grouped.fields.metric(REVENUE)).limit(2)
    ranked_frame = ranked.execute().to_pandas()
    assert list(zip(ranked_frame.channel, ranked_frame.revenue, strict=True)) == [
        ("a", 30.75),
        ("b", 30.75),
    ]
    daily = (
        sources.observe(REVENUE, time_scope=time_scope(start="2026-02-01", end="2026-03-01"))
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
        .execute()
        .to_pandas()
        .sort_values("order_time")
    )
    assert daily.order_time.astype(str).tolist() == [
        "2026-02-02",
        "2026-02-03",
        "2026-02-04",
        "2026-02-05",
        "2026-02-06",
    ]
    # The 2026-02-05 order carries a NULL amount, so its day bucket sums to NA.
    daily_expectation = [
        ("2026-02-02", 10.25),
        ("2026-02-03", 20.5),
        ("2026-02-04", 30.75),
        ("2026-02-06", -2.0),
    ]
    daily = daily.loc[daily.revenue.notna()]
    assert list(zip(daily.order_time.astype(str), daily.revenue, strict=True)) == daily_expectation
    ratio = (
        sources.observe(
            (REVENUE, ref.metric("sales.order_count"), ref.metric("sales.conversion_rate")),
            time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
        )
        .aggregate()
        .execute()
        .to_pandas()
    )
    # In-scope revenue 10.25 + 20.5 + 30.75 - 2.0 = 59.5 over 4 counted orders.
    assert ratio.revenue.tolist() == [59.5]
    assert ratio.order_count.tolist() == [4]
    assert ratio.conversion_rate.tolist() == [59.5 / 4]
    weighted = (
        sources.observe(ref.metric("sales.weighted_amount")).aggregate().execute().to_pandas()
    )
    assert weighted.weighted_amount.tolist() == [146.65625]
    population = sources.population(ref.entity("sales.orders")).execute().to_pandas()
    assert population.entity_identity.tolist() == [(i,) for i in range(1, 7)]
    assert runtime.statistics.primary_queries == 1
    before = counts(runtime)
    replay = ranked.execute()
    assert replay.state.artifact_ref == ranked.execute().state.artifact_ref
    assert counts(runtime) == before
    assert runtime.store.resources(runtime.session_ref) == ()


def test_decimal_amount_journey_exact(tmp_path: Path, source_table: str) -> None:
    from decimal import Decimal

    registry, sidecar = noniceberg_registry(tmp_path, source_table)
    runtime = DatasetRuntime.create(tmp_path, "noniceberg-decimal")
    frame = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .aggregate()
        .execute()
        .to_pandas()
    )
    assert Decimal(str(frame.revenue.iloc[0])) == Decimal("1058.50")
    assert runtime.store.resources(runtime.session_ref) == ()


def test_receipt_audit_free_of_iceberg_metadata(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = capture_submissions(monkeypatch)
    registry, sidecar = noniceberg_registry(tmp_path, source_table)
    runtime = DatasetRuntime.create(tmp_path, "noniceberg-receipts")
    frame = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .with_dimensions(CHANNEL)
        .aggregate()
        .execute()
        .to_pandas()
    )
    # a=30.75, b=30.75, outside=999.0, c=-2.0
    assert sorted(frame.revenue.tolist()) == [-2.0, 30.75, 30.75, 999.0]
    assert runtime.statistics.primary_queries == 1
    sql_texts = [str(entry["sql"]) for entry in submitted]
    assert sql_texts, "Expected captured driver submissions"
    for banned in ("$partitions", "$files", "$snapshots", "$properties"):
        assert not any(banned in sql for sql in sql_texts), banned
    observations = [sql for sql in sql_texts if sql == CATALOGS_OBSERVATION]
    assert len(observations) == 1
    assert any("connector_name" in sql for sql in sql_texts)
    assert not any(re.search(r"\biceberg\b", sql) for sql in sql_texts)
    assert runtime.store.resources(runtime.session_ref) == ()


def test_partitions_internal_table_rejected() -> None:
    import ibis

    from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter

    with trino.connection(catalog=trino.NON_ICEBERG_CATALOG) as con:
        adapter = TrinoExecutionAdapter(ibis.trino.from_connection(con))
        try:
            with pytest.raises(MaterializationError, match=r"\$partitions"):
                adapter.get_schema("orders$partitions")
        finally:
            adapter.finish()
