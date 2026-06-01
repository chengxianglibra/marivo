"""Single-file semantic model with partition time field preference."""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic as ms

MODEL = """
import marivo.datasource as md
import marivo.semantic as ms

ms.model(name="sales")
warehouse = md.ref("warehouse")

@ms.dataset(
    name="orders",
    datasource=warehouse,
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Exclude test orders when a test flag is present."],
    },
)
def orders(backend):
    return backend.table("orders")

@ms.time_field(
    dataset=orders,
    name="order_date",
    data_type="date",
    granularity="day",
    ai_context={
        "business_definition": "Partition time field for order reporting windows.",
        "guardrails": ["Use event time only when source evidence defines it."],
    },
)
def order_date(table):
    return table.dt.cast("date")

@ms.field(
    dataset=orders,
    name="region",
    ai_context={
        "business_definition": "Sales reporting region.",
        "guardrails": ["Do not infer market ownership from this field alone."],
    },
)
def region(table):
    return table.region

@ms.metric(
    datasets=[orders],
    additivity="additive",
    decomposition=ms.sum(),
    name="revenue",
    ai_context={
        "business_definition": "Gross order amount before refunds.",
        "guardrails": ["Validate refund exclusions before using as net revenue."],
        "synonyms": ["sales", "gmv"],
        "examples": ["What was revenue by region last week?"],
    },
)
def revenue(table):
    return table.amount.sum()
"""

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    semantic_dir = root / ".marivo" / "semantic" / "sales"
    datasource_dir = root / ".marivo" / "datasource"
    semantic_dir.mkdir(parents=True)
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    (semantic_dir / "_model.py").write_text(MODEL)

    project = ms.SemanticProject(root=root / ".marivo" / "semantic")
    project.load()
    print("partition time field:", project.describe("sales.order_date").semantic_id)
    print("metric:", project.describe("sales.revenue").semantic_id)
