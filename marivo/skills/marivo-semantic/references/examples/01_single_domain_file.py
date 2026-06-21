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
    ai_context=ms.ai_context(
        business_definition="One row per order.",
        guardrails=["Exclude test orders when a test flag is present."],
    ),
)

log_date = ms.time_dimension_column(
    name="log_date",
    entity=orders,
    column="dt",
    granularity="day",
    parse=ms.strptime("%Y%m%d", ),
    is_default=True,
    ai_context=ms.ai_context(
        business_definition="Partition date used for default order reporting windows.",
        guardrails=["Use event time instead only when source SQL defines that axis."],
    ),
)

log_hour = ms.time_dimension_column(
    name="log_hour",
    entity=orders,
    column="hh",
    granularity="hour",
    parse=ms.hour_prefix("log_date", ),
    ai_context=ms.ai_context(
        business_definition="Hour partition used with log_date for hourly reporting windows.",
        guardrails=["Use full event timestamp only when source evidence defines that axis."],
    ),
)

event_ts = ms.time_dimension_column(
    name="event_ts",
    entity=orders,
    column="event_ts",
    granularity="minute",
    parse=ms.timestamp(timezone="UTC"),
    ai_context=ms.ai_context(
        business_definition="Minute-grain event timestamp for sub-day time-series analysis.",
        guardrails=["Use only when the analysis requires sub-day granularity (e.g. 5-minute buckets)."],
    ),
)

region = ms.dimension_column(
    name="region",
    entity=orders,
    column="region",
    ai_context=ms.ai_context(
        business_definition="Sales reporting region.",
        guardrails=["Do not treat missing region as a separate market."],
    ),
)

amount = ms.measure_column(
    name="amount",
    entity=orders,
    column="amount",
    additivity="additive",
    unit="USD",
    ai_context=ms.ai_context(
        business_definition="Gross order amount before refunds.",
    ),
)

revenue = ms.aggregate(
    name="revenue",
    measure=amount,
    agg="sum",
    ai_context=ms.ai_context(
        business_definition="Gross order amount before refunds.",
        guardrails=["Validate refund exclusions before using as net revenue."],
        synonyms=["sales", "gmv"],
        examples=["What was revenue by region last week?"],
    ),
)
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
    print("measure:", catalog.get("sales.orders.amount").details().ref)
    print("metric:", catalog.get("sales.revenue").details().ref)
