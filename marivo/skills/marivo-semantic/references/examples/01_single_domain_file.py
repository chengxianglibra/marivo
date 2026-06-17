"""Single-file semantic domain with partition time dimension preference."""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic as ms

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
        "guardrails": ["Exclude test orders when a test flag is present."],
    },
)

@ms.time_dimension(
    entity=orders,
    name="log_date",
    granularity="day",
    parse=ms.strptime("%Y%m%d", data_type="string"),
    is_default=True,
    ai_context={
        "business_definition": "Partition time dimension for order reporting windows.",
        "guardrails": ["Use event time only when source evidence defines it."],
    },
)
def log_date(table):
    return table.dt

@ms.time_dimension(
    entity=orders,
    name="log_hour",
    granularity="hour",
    parse=ms.hour_prefix("log_date", data_type="string"),
    ai_context={
        "business_definition": "Hour partition used with log_date for hourly reporting windows.",
        "guardrails": ["Use full event timestamp only when source evidence defines that axis."],
    },
)
def log_hour(table):
    return table.hh

@ms.time_dimension(
    entity=orders,
    name="event_ts",
    granularity="minute",
    parse=ms.timestamp(timezone="UTC"),
    ai_context={
        "business_definition": "Minute-grain event timestamp for sub-day time-series analysis.",
        "guardrails": ["Use only when the analysis requires sub-day granularity (e.g. 5-minute buckets)."],
    },
)
def event_ts(table):
    return table.event_ts

@ms.dimension(
    entity=orders,
    name="region",
    ai_context={
        "business_definition": "Sales reporting region.",
        "guardrails": ["Do not infer market ownership from this dimension alone."],
    },
)
def region(table):
    return table.region

@ms.metric(
    entities=[orders],
    additivity="additive",
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
    semantic_dir = root / "models" / "semantic" / "sales"
    datasource_dir = root / "models" / "datasources"
    semantic_dir.mkdir(parents=True)
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "_domain.py").write_text(DOMAIN)

    catalog = ms.load(workspace_dir=root)
    print("partition time dimension:", catalog.get("sales.orders.log_date").details().ref)
    print("hour partition time dimension:", catalog.get("sales.orders.log_hour").details().ref)
    print("minute time dimension:", catalog.get("sales.orders.event_ts").details().ref)
    print("metric:", catalog.get("sales.revenue").details().ref)
