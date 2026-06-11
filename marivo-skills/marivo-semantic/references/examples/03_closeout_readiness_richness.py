"""Closeout: readiness folds preview, parity, and richness signals."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.metadata import ColumnMetadata, PartitionMetadata, TableMetadata
from marivo.semantic.ir import TableSourceIR

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


def fake_inspect_source(
    datasource: str, *, source: TableSourceIR, include_partitions: bool = True
) -> TableMetadata:
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment="Orders fact table.",
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, "Primary order id", 1),
            ColumnMetadata("dt", "DATE", False, "Partition date", 2),
            ColumnMetadata("amount", "DOUBLE", True, "Gross order amount", 3),
        ),
        partitions=(PartitionMetadata("dt", type="DATE"),),
        warnings=(),
    )


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

        project.bind_datasource_access(
            inspect_source=fake_inspect_source,
            backend_factory=md.connect,
        )

        # inspect_source_context folds source inspection and bounded preview
        pack = project.inspect_source_context(
            datasource="warehouse",
            source=ms.TableSource(table="orders"),
            sample_policy=ms.BoundedProfilePolicy(limit=100, max_profiled_columns=50),
        )
        print("source schema columns:", len(pack.schema))

        report = project.readiness(
            refs=("sales.orders", "sales.unverified_revenue", "sales.drifted_revenue"),
            demand=None,
            preview_limit=20,
        )
        print("readiness:", report.status)
        print("blockers:", [issue.kind for issue in report.blockers])
        print("warnings:", [issue.kind for issue in report.warnings])
        print(
            "unverified_metric:",
            "sales.unverified_revenue" in report.parity_summary.unverified_metrics,
        )
        print("parity_drifted:", "sales.drifted_revenue" in report.parity_summary.drifted_metrics)
        print("richness gaps:", len(report.richness_summary.gaps))
    finally:
        os.chdir(previous)
