"""Closeout: preview, parity, readiness, and richness are separate signals."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import ibis

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.datasources.metadata import ColumnMetadata, PartitionMetadata, TableMetadata
from marivo.semantic.ir import TableSourceIR

MODEL = """
import marivo.datasource as md
import marivo.semantic as ms

ms.model(name="sales")
warehouse = md.ref("warehouse")

orders = ms.dataset(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Preview raw orders before analysis handoff."],
    },
)

@ms.time_field(
    dataset=orders,
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
    datasets=[orders],
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
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="drifted_revenue",
    verification_mode="sql_parity",
    source_sql="SELECT 999.0 AS drifted_revenue",
    source_dialect="duckdb",
    ai_context={
        "business_definition": "Gross order amount with intentionally drifted oracle.",
        "guardrails": ["Parity drift blocks readiness."],
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
    (semantic_dir / "_model.py").write_text(MODEL)

    previous = Path.cwd()
    try:
        os.chdir(root)
        mv.datasources.register(
            md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path))
        )
        project = ms.SemanticProject(root=root / ".marivo" / "semantic")
        project.load()

        def backend_factory(name: str) -> Any:
            return mv.datasources.build_backend(name)

        project.bind_datasource_access(
            inspect_source=fake_inspect_source,
            backend_factory=backend_factory,
        )

        # inspect_source_context folds source inspection and bounded preview
        pack = project.inspect_source_context(
            datasource="warehouse",
            source=ms.DatasetSource(kind="table", table="orders"),
            sample_policy=ms.SamplePolicy(
                mode="bounded_profile", limit=100, max_profiled_columns=50
            ),
        )
        print("source schema columns:", len(pack.schema))

        project.preview_dataset("sales.orders")
        project.parity_check("sales.drifted_revenue", backend_factory=backend_factory)
        report = project.readiness()
        richness = project.richness(
            demand=ms.DemandSignal(
                example_questions=("What was revenue by day?",),
                intents=("revenue trend",),
                build_purpose="Revenue analysis",
            )
        )
        print("readiness:", report.status)
        print(
            "unverified_metric:",
            "sales.unverified_revenue" in report.parity_summary.unverified_metrics,
        )
        print("parity_drifted:", "sales.drifted_revenue" in report.parity_summary.drifted_metrics)
        print("richness gaps:", len(richness.gaps))
    finally:
        os.chdir(previous)
