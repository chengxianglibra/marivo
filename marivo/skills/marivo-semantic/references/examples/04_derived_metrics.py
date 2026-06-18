"""Derived metric authoring: ratio, weighted_average, and linear.

Shows: declare base metrics first, then compose them into body-free
derived metrics using ms.ratio, ms.weighted_average, and ms.linear.
Derived metrics have no Python body — their computation comes entirely
from their composition components.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic as ms
from marivo.semantic.catalog import DerivedMetricDetails, SimpleMetricDetails

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
    parse=ms.strptime("%Y%m%d", ),
    is_default=True,
)
def log_date(table):
    return table.dt

# -- Base metrics: each has a body and declares entities ---

@ms.metric(
    entities=[orders],
    additivity="additive",
    name="gross_revenue",
    unit="CNY",
    ai_context={
        "business_definition": "Total order amount before refunds.",
        "guardrails": ["Validate refund exclusions before using as net revenue."],
    },
)
def gross_revenue(table):
    return table.amount.sum()

@ms.metric(
    entities=[orders],
    additivity="additive",
    name="refunds",
    unit="CNY",
    ai_context={
        "business_definition": "Total refund amount.",
        "guardrails": ["Ensure refund amounts are positive values."],
    },
)
def refunds(table):
    return table.refund_amount.sum()

@ms.metric(
    entities=[orders],
    additivity="additive",
    name="orders_count",
    unit="{order}",
    ai_context={
        "business_definition": "Number of orders.",
    },
)
def orders_count(table):
    return table.order_id.count()

@ms.metric(
    entities=[orders],
    additivity="additive",
    name="total_amount",
    unit="CNY",
    ai_context={
        "business_definition": "Total amount across all orders.",
    },
)
def total_amount(table):
    return table.amount.sum()

# -- Derived metrics: body-free, composed from base metrics ---

# ms.ratio: numerator / denominator (e.g. average order value)
aov = ms.ratio(
    name="aov",
    numerator=total_amount,
    denominator=orders_count,
    unit="CNY/{order}",
    ai_context={
        "business_definition": "Average order value: total amount divided by order count.",
    },
)

# ms.weighted_average: value / weight at the component level;
# analysis decompose() splits mix vs rate effects.
avg_revenue_per_order = ms.weighted_average(
    name="avg_revenue_per_order",
    value=gross_revenue,
    weight=orders_count,
    unit="CNY",
    ai_context={
        "business_definition": "Revenue per order, weighted by order count for mix-effect decomposition.",
    },
)

# ms.linear: add terms minus subtract terms (e.g. net revenue)
net_revenue = ms.linear(
    name="net_revenue",
    add=[gross_revenue],
    subtract=[refunds],
    unit="CNY",
    ai_context={
        "business_definition": "Revenue after refunds: gross revenue minus refunds.",
    },
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

    # Verify base metrics
    for metric_id in [
        "sales.gross_revenue",
        "sales.refunds",
        "sales.orders_count",
        "sales.total_amount",
    ]:
        details = catalog.get(metric_id).details()
        assert isinstance(details, SimpleMetricDetails)
        print(f"base metric: {details.ref} type={details.metric_type}")

    # Verify derived metrics
    for metric_id in [
        "sales.aov",
        "sales.avg_revenue_per_order",
        "sales.net_revenue",
    ]:
        details = catalog.get(metric_id).details()
        assert isinstance(details, DerivedMetricDetails)
        print(
            f"derived metric: {details.ref} type={details.metric_type} composition={details.composition}"
        )
