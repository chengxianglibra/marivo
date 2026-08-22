"""SQLite-backed semantic lifecycle and analysis integration."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.semantic.catalog import SemanticCatalog


def _seed_orders(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                amount REAL NOT NULL,
                created_at TIMESTAMP NOT NULL
            );
            INSERT INTO orders VALUES
                (1, 10.0, '2026-07-01 10:00:00'),
                (2, 20.0, '2026-07-02 11:00:00');
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_sqlite_agent_native_authoring_journey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    database_path = tmp_path / "warehouse.sqlite"
    _seed_orders(database_path)
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.sqlite(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/orders.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms

                orders = ms.entity(
                    name="orders",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.table("orders"),
                    ai_context=ms.ai_context(
                        business_definition="Accepted sales orders.",
                        guardrails=["Use only accepted order rows."],
                    ),
                )
                amount = ms.measure_column(
                    name="amount",
                    entity=orders,
                    column="amount",
                    additivity="additive",
                    unit="USD",
                    ai_context=ms.ai_context(
                        business_definition="Accepted order amount in USD.",
                    ),
                )
                created_at = ms.time_dimension_column(
                    name="created_at",
                    entity=orders,
                    column="created_at",
                    granularity="second",
                    parse=ms.timestamp(timezone="UTC"),
                    is_default=True,
                )
                revenue = ms.aggregate(
                    name="revenue",
                    measure=amount,
                    agg="sum",
                    unit="USD",
                    ai_context=ms.ai_context(
                        business_definition="Sum of accepted order amount.",
                        guardrails=["Do not mix currencies."],
                    ),
                )
                """
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    revenue = catalog.require(ms.ref.metric("sales.revenue")).ref
    amount = catalog.require(ms.ref.measure("sales.orders.amount")).ref
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=5),
        columns=("order_id", "amount", "created_at"),
    )
    sql_result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT COUNT(*) AS order_count FROM orders",
        reason="Confirm the physical order population before typed analysis.",
        limit=10,
        timeout_seconds=5,
        project_root=tmp_path,
    )

    assert catalog.require(revenue).ref == revenue
    assert sql_result.rows == ({"order_count": 2},)
    assert catalog.preview(revenue, scope=snapshot.scope).status == "passed"
    readiness = catalog.readiness(refs=[revenue])
    assert revenue in readiness.analysis_ready_inputs
    source_health = catalog.source_health(
        [revenue],
        checks=[ms.source_check.not_null(amount)],
        scope=md.unpruned(max_rows=10, timeout_seconds=5),
    )
    readiness_after_health = catalog.readiness(refs=[revenue])

    assert source_health.status == "current"
    assert tuple(check.kind for check in source_health.checks) == (
        "connectivity",
        "schema",
        "not_null",
    )
    assert source_health.checks[-1].user_data_queried is True
    assert source_health.checks[-1].scopes == (
        (ms.ref.entity("sales.orders"), md.unpruned(max_rows=10, timeout_seconds=5)),
    )
    readiness_before_payload = readiness.to_dict()
    readiness_after_payload = readiness_after_health.to_dict()
    readiness_before_payload.pop("checked_at")
    readiness_after_payload.pop("checked_at")
    assert readiness_after_payload == readiness_before_payload

    session = mv.session.get_or_create(
        name="sqlite-revenue",
        question="What is total SQLite revenue?",
    )
    frame = session.observe(
        revenue,
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
    )
    result = frame.to_pandas()

    assert result["revenue"].iloc[0] == pytest.approx(30.0)
