"""Reproduce Group A Dataset receipt and a separately labeled physical-plan diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from psycopg import sql

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.refs import ref
from tests.lazy_postgres_fixtures import registry_for
from tests.multisource_environment import postgres_analysis as pg


def collect() -> dict[str, object]:
    """Execute a disposable real Dataset, then EXPLAIN its exact primary SQL as reader."""
    table = "receipt_" + uuid4().hex
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE TABLE {} (id BIGINT, tenant TEXT, customer_id BIGINT, order_id BIGINT, "
                "amount NUMERIC(18,2), weight DOUBLE PRECISION, region TEXT, channel TEXT, "
                'day DATE, start DATE, "end" DATE)'
            ).format(sql.Identifier(table))
        )
        try:
            admin.execute(
                sql.SQL(
                    "INSERT INTO {} (id, amount, channel, day) "
                    "SELECT i, (i % 100)::numeric(18,2), (i % 10)::text, DATE '2026-02-02' "
                    "FROM generate_series(1,20000) AS t(i)"
                ).format(sql.Identifier(table))
            )
            with (
                TemporaryDirectory(prefix="marivo-pg-receipt-") as project,
                pytest.MonkeyPatch.context() as patch,
            ):
                registry, sidecar = registry_for(table, patch)
                runtime = DatasetRuntime.create(Path(project), "postgres-live-receipt")
                sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
                revenue = ref.metric("sales.revenue")
                metrics = (
                    revenue,
                    ref.metric("sales.order_count"),
                    ref.metric("sales.min_amount"),
                    ref.metric("sales.max_amount"),
                )
                grouped = (
                    sources.observe(metrics)
                    .with_dimensions(ref.dimension("sales.orders.channel"))
                    .aggregate()
                )
                result = grouped.rank(grouped.fields.metric(revenue)).limit(2).execute()
                frame = result.to_pandas()
                expected = [
                    {
                        "channel": str(group),
                        "revenue": Decimal(
                            sum(i % 100 for i in range(1, 20001) if i % 10 == group)
                        ),
                        "order_count": 2000,
                        "min_amount": Decimal(group),
                        "max_amount": Decimal(90 + group),
                        "rank": rank,
                    }
                    for rank, group in enumerate((9, 8), 1)
                ]
                assert frame.to_dict("records") == expected
                statements = tuple(runtime.statistics.statements)
                primary = [statement for role, statement in statements if role == "primary"]
                assert len(primary) == 1
                with pg.connection() as reader:
                    privileges = reader.execute(
                        "SELECT current_user, current_setting('transaction_read_only'), "
                        "has_database_privilege(current_user,current_database(),'CREATE'), "
                        "has_database_privilege(current_user,current_database(),'TEMP'), "
                        "has_schema_privilege(current_user,'public','CREATE')"
                    ).fetchone()
                    assert privileges == (pg.READER, "on", False, False, False)
                    server = reader.execute("SHOW server_version").fetchone()
                    plan = reader.execute(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + primary[0]
                    ).fetchone()
                    assert plan is not None
                assert runtime.statistics.statements == list(statements)
                return {
                    "dataset_acceptance": True,
                    "baseline": "f43c86430203a2c5814ef2d4d0294a71a0581eed",
                    "backend": "postgres",
                    "fixture": {
                        "table": table,
                        "rows": 20000,
                        "table_kind": "ordinary local table",
                        "amount": "numeric(18,2)",
                        "seed": "i=1..20000; amount=i%100; channel=i%10",
                    },
                    "diagnostic_versions": {
                        "server": server[0] if server else None,
                        **{
                            name: version(name) for name in ("ibis-framework", "psycopg", "pyarrow")
                        },
                    },
                    "reader_privileges": {
                        "user": privileges[0],
                        "read_only": privileges[1],
                        "database_create": privileges[2],
                        "temporary": privileges[3],
                        "schema_create": privileges[4],
                    },
                    "output": expected,
                    "primary_queries": runtime.statistics.primary_queries,
                    "primary_rows": runtime.statistics.transferred_rows,
                    "primary_arrow_bytes": runtime.statistics.transferred_bytes,
                    "validation_queries": runtime.statistics.validation_queries,
                    "local_handoffs": runtime.statistics.local_handoffs,
                    "statements_by_role": dict(Counter(role for role, _ in statements)),
                    "submitted_statements": [
                        {
                            "role": role,
                            "sql": statement,
                            "sha256": hashlib.sha256(statement.encode()).hexdigest(),
                        }
                        for role, statement in statements
                    ],
                    "owned_resources_after_execution": len(
                        runtime.store.resources(runtime.session_ref)
                    ),
                    "server_scan_metrics": {
                        "capture": "separate diagnostic query after successful Dataset Run; not original Run telemetry",
                        "command": "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)",
                        "primary_sql_sha256": hashlib.sha256(primary[0].encode()).hexdigest(),
                        "reader": pg.READER,
                        "plan": plan[0],
                        "interpretation": "Actual Rows and Actual Loops are per plan node; parent buffers include child work and must not be summed. Ordinary table: no partition-pruning claim.",
                    },
                    "fixture_cleanup": "admin drops only this disposable table in finally",
                    "independent_expected": expected,
                    "reproduce": ".venv/bin/python -m tests.multisource_environment.postgres_receipt --output <receipt.json>",
                }
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))


def _encode(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(collect(), default=_encode, indent=2, sort_keys=True) + "\n")
    print("PostgreSQL Dataset and separate EXPLAIN receipt written.")
