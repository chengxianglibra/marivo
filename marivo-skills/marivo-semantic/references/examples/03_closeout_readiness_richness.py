"""Closeout: structural readiness check and richness reporting.

Shows: readiness as a pure structural check, then richness and parity
as separate dedicated APIs. Readiness requires no datasource connection.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis

import marivo.datasource as md

DOMAIN = """
import marivo.datasource as md
import marivo.semantic as ms

ms.domain(name="sales")
warehouse = md.ref("warehouse")

orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Preview raw orders before analysis handoff."],
    },
)

@ms.time_dimension(
    entity=orders,
    name="order_date",
    data_type="date",
    granularity="day",
    ai_context={
        "business_definition": "Daily order partition.",
        "guardrails": ["Use as the default reporting window axis."],
    },
)
def order_date(table):
    return table.dt.cast("date")

@ms.metric(
    entities=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="unverified_revenue",
    ai_context={
        "business_definition": "Gross order amount.",
        "guardrails": ["Unverified until parity or source evidence is supplied."],
    },
verification_mode="python_native",)
def unverified_revenue(table):
    return table.amount.sum()

@ms.metric(
    entities=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="drifted_revenue",
    verification_mode="sql_parity",
    source_sql="SELECT 999.0 AS drifted_revenue",
    source_dialect="duckdb",
    ai_context={
        "business_definition": "Gross order amount with intentionally drifted oracle.",
        "guardrails": ["Parity drift warns in readiness."],
    },
)
def drifted_revenue(table):
    return table.amount.sum()
"""


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "orders.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, dt DATE, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (1, DATE '2026-01-01', 10.0)")
    con.disconnect()

    semantic_dir = root / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(DOMAIN)

    previous = Path.cwd()
    try:
        os.chdir(root)
        md.register(md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)))
        from marivo.semantic.reader import SemanticProject

        project = SemanticProject(root=root / ".marivo" / "semantic")
        project.load()

        # Structural readiness: pure in-memory check, no backend required.
        report = project.readiness(
            refs=("sales.orders", "sales.unverified_revenue", "sales.drifted_revenue"),
        )
        print("readiness:", report.status)
        print("blockers:", [issue.kind for issue in report.blockers])
        print("warnings:", [issue.kind for issue in report.warnings])
        print("abandoned:", len(report.abandoned))

        # Richness is a separate advisory API.
        richness = project.richness()
        print("richness gaps:", len(richness.gaps))
    finally:
        os.chdir(previous)
