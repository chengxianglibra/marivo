"""Opt-in PostgreSQL view and partitioned-table Dataset journey baseline (C5 plan §3.8).

PostgreSQL admission has no table-form gate: ``get_schema`` resolves any relation
through ``pg_attribute`` + ``to_regclass``. A live probe against this service
(PostgreSQL 17.11) confirmed ``pg_catalog`` reports relkind ``r`` for the base
table, ``v`` for a plain view, and ``p`` for a partitioned parent, and all three
return the identical 11-column ``pg_attribute`` row set (names, ``format_type``
strings, and nullability included). These tests convert that probe into full
reader journeys: an admin-created view and a range-partitioned parent each go
through the complete observe -> aggregate -> execute Dataset path.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.refs import ref
from tests.lazy_postgres_fixtures import registry_for
from tests.multisource_environment import postgres_analysis as pg

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1", reason="opt-in PostgreSQL service"
    ),
]
REVENUE = ref.metric("sales.revenue")
_COLUMNS = (
    "(id BIGINT, tenant TEXT, customer_id BIGINT, order_id BIGINT, "
    "amount NUMERIC(18,2), weight DOUBLE PRECISION, region TEXT, channel TEXT, "
    'day DATE, start DATE, "end" DATE)'
)
_ROWS = (
    "(1,10.25,'a','2026-02-02'),(2,20.50,'a','2026-02-03'),"
    "(3,30.75,'b','2026-02-04'),(4,NULL,'b','2026-02-05'),"
    "(5,-2.00,'c','2026-02-06'),(6,999.00,'outside','2026-03-01')"
)
# Hand-computed: 10.25 + 20.50 + 30.75 + (-2.00) + 999.00 = 1058.50 (the NULL
# row contributes nothing to sum).
_TOTAL = Decimal("1058.50")


def _pg_attribute_rows(
    admin: psycopg.Connection[tuple[object, ...]], relation: str
) -> list[tuple[object, ...]]:
    """Fetch the adapter's exact source-schema rows for one relation."""
    query = (
        "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), NOT a.attnotnull "
        "FROM pg_catalog.pg_attribute a "
        "WHERE a.attnum > 0 AND NOT a.attisdropped "
        "AND a.attrelid = pg_catalog.to_regclass(%s) ORDER BY a.attnum"
    )
    return list(admin.execute(query, (f"public.{relation}",)).fetchall())


def _relkind(admin: psycopg.Connection[tuple[object, ...]], relation: str) -> str:
    row = admin.execute(
        "SELECT c.relkind FROM pg_catalog.pg_class c WHERE c.oid = pg_catalog.to_regclass(%s)",
        (f"public.{relation}",),
    ).fetchone()
    assert isinstance(row, tuple) and isinstance(row[0], str)
    return row[0]


def test_view_source_full_dataset_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain view resolves through pg_attribute like a table and completes the journey."""
    table = "dataset_" + uuid4().hex
    view = table + "_view"
    with pg.connection(admin=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE TABLE {} " + _COLUMNS).format(sql.Identifier(table)))
            admin.execute(
                sql.SQL("INSERT INTO {} (id, amount, channel, day) VALUES " + _ROWS).format(
                    sql.Identifier(table)
                )
            )
            admin.execute(
                sql.SQL("CREATE VIEW {} AS SELECT * FROM {}").format(
                    sql.Identifier(view), sql.Identifier(table)
                )
            )
            # Probe-derived admission facts: the view is relkind 'v' and its
            # pg_attribute rows are identical to the base table's.
            assert _relkind(admin, view) == "v"
            assert _pg_attribute_rows(admin, view) == _pg_attribute_rows(admin, table)
            registry, sidecar = registry_for(view, monkeypatch)
            runtime = DatasetRuntime.create(tmp_path, "postgres-view")
            target = (
                runtime.sources(semantic_registry=registry, sidecar=sidecar)
                .observe(REVENUE)
                .aggregate()
            )
            frame = target.execute().to_pandas()
            assert frame["revenue"].tolist() == [_TOTAL]
            assert runtime.statistics.primary_queries == 1
            assert runtime.store.resources(runtime.session_ref) == ()
        finally:
            admin.execute(sql.SQL("DROP VIEW IF EXISTS {}").format(sql.Identifier(view)))
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))


def test_partitioned_source_full_dataset_journey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A range-partitioned parent admits directly and fans the journey across both partitions."""
    parent = "dataset_" + uuid4().hex + "_pt"
    february = parent + "_feb"
    march = parent + "_mar"
    with pg.connection(admin=True) as admin:
        try:
            admin.execute(
                sql.SQL("CREATE TABLE {} " + _COLUMNS + " PARTITION BY RANGE (day)").format(
                    sql.Identifier(parent)
                )
            )
            admin.execute(
                sql.SQL(
                    "CREATE TABLE {} PARTITION OF {} "
                    "FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')"
                ).format(sql.Identifier(february), sql.Identifier(parent))
            )
            admin.execute(
                sql.SQL(
                    "CREATE TABLE {} PARTITION OF {} "
                    "FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')"
                ).format(sql.Identifier(march), sql.Identifier(parent))
            )
            admin.execute(
                sql.SQL("INSERT INTO {} (id, amount, channel, day) VALUES " + _ROWS).format(
                    sql.Identifier(parent)
                )
            )
            # Probe-derived admission facts: the partitioned parent is relkind
            # 'p' and the standard row set splits 5 rows into February and 1
            # row into March.
            assert _relkind(admin, parent) == "p"
            children = admin.execute(
                "SELECT count(*) FROM pg_catalog.pg_inherits "
                "WHERE inhparent = pg_catalog.to_regclass(%s)",
                (f"public.{parent}",),
            ).fetchone()
            assert children == (2,)
            assert admin.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(february))
            ).fetchone() == (5,)
            assert admin.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(march))
            ).fetchone() == (1,)
            registry, sidecar = registry_for(parent, monkeypatch)
            runtime = DatasetRuntime.create(tmp_path, "postgres-partitioned")
            target = (
                runtime.sources(semantic_registry=registry, sidecar=sidecar)
                .observe(REVENUE)
                .aggregate()
            )
            frame = target.execute().to_pandas()
            assert frame["revenue"].tolist() == [_TOTAL]
            assert runtime.statistics.primary_queries == 1
            assert runtime.store.resources(runtime.session_ref) == ()
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(february)))
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(march)))
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(parent)))
