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

orders = ms.dataset(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context={
        "business_definition": "One row per order.",
        "guardrails": ["Exclude test orders when a test flag is present."],
    },
)

@ms.time_field(
    dataset=orders,
    name="log_date",
    data_type="string",
    granularity="day",
    date_format="yyyymmdd",
    is_default=True,
    ai_context={
        "business_definition": "Partition time field for order reporting windows.",
        "guardrails": ["Use event time only when source evidence defines it."],
    },
)
def log_date(table):
    return table.dt

@ms.time_field(
    dataset=orders,
    name="log_hour",
    data_type="string",
    granularity="hour",
    date_format="HH",
    required_prefix="log_date",
    ai_context={
        "business_definition": "Hour partition used with log_date for hourly reporting windows.",
        "guardrails": ["Use full event timestamp only when source evidence defines that axis."],
    },
)
def log_hour(table):
    return table.hh

@ms.time_field(
    dataset=orders,
    name="event_ts",
    data_type="timestamp",
    granularity="minute",
    ai_context={
        "business_definition": "Minute-grain event timestamp for sub-day time-series analysis.",
        "guardrails": ["Use only when the analysis requires sub-day granularity (e.g. 5-minute buckets)."],
    },
)
def event_ts(table):
    return table.event_ts

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
verification_mode="python_native",)
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

    project = ms.SemanticProject(workspace_dir=root)
    project.load()
    print("partition time field:", project.describe("sales.orders.log_date").semantic_id)
    print("hour partition time field:", project.describe("sales.orders.log_hour").semantic_id)
    print("minute time field:", project.describe("sales.orders.event_ts").semantic_id)
    print("metric:", project.describe("sales.revenue").semantic_id)
