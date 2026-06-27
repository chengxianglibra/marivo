"""Complete semantic model flow with one object verified before the next."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis

import marivo.datasource as md
import marivo.semantic as ms

HEADER = """
import marivo.datasource as md
import marivo.semantic as ms
"""

DOMAIN = """
ms.domain(name="sales")
warehouse = md.ref("warehouse")
"""

ORDERS_ENTITY = """
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(
        business_definition="One row per customer order.",
        guardrails=["Exclude test or cancelled orders when source policy defines them."],
    ),
)
"""

CUSTOMERS_ENTITY = """
customers = ms.entity(
    name="customers",
    datasource=warehouse,
    source=ms.table("customers"),
    primary_key=["customer_id"],
    ai_context=ms.ai_context(
        business_definition="One row per customer account.",
    ),
)
"""

ORDER_REGION_DIMENSION = """
order_region = ms.dimension_column(
    name="region",
    entity=orders,
    column="region",
    ai_context=ms.ai_context(
        business_definition="Order reporting region.",
    ),
)
"""

ORDER_CUSTOMER_ID_DIMENSION = """
order_customer_id = ms.dimension_column(
    name="customer_id",
    entity=orders,
    column="customer_id",
    ai_context=ms.ai_context(
        business_definition="Customer key recorded on the order.",
    ),
)
"""

CUSTOMER_ID_DIMENSION = """
customer_id = ms.dimension_column(
    name="customer_id",
    entity=customers,
    column="customer_id",
    ai_context=ms.ai_context(
        business_definition="Customer primary key.",
    ),
)
"""

CUSTOMER_COUNTRY_DIMENSION = """
customer_country = ms.dimension_column(
    name="country",
    entity=customers,
    column="country",
    ai_context=ms.ai_context(
        business_definition="Customer country used for sales segmentation.",
    ),
)
"""

ORDER_DATE_TIME_DIMENSION = """
order_date = ms.time_dimension_column(
    name="order_date",
    entity=orders,
    column="order_date",
    granularity="day",
    is_default=True,
    ai_context=ms.ai_context(
        business_definition="Date the order was placed.",
    ),
)
"""

AMOUNT_MEASURE = """
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
"""

REFUND_MEASURE = """
refund_amount = ms.measure_column(
    name="refund_amount",
    entity=orders,
    column="refund_amount",
    additivity="additive",
    unit="USD",
    ai_context=ms.ai_context(
        business_definition="Refund amount recorded on the order.",
    ),
)
"""

GROSS_REVENUE_METRIC = """
gross_revenue = ms.aggregate(
    name="gross_revenue",
    measure=amount,
    agg="sum",
    ai_context=ms.ai_context(
        business_definition="Total gross order amount.",
    ),
)
"""

REFUNDS_METRIC = """
refunds = ms.aggregate(
    name="refunds",
    measure=refund_amount,
    agg="sum",
    ai_context=ms.ai_context(
        business_definition="Total refunded amount.",
    ),
)
"""

ORDERS_COUNT_METRIC = """
orders_count = ms.count(
    name="orders_count",
    entity=orders,
    ai_context=ms.ai_context(
        business_definition="Number of orders.",
    ),
)
"""

RELATIONSHIP = """
orders_to_customers = ms.relationship(
    name="orders_to_customers",
    from_entity=orders,
    to_entity=customers,
    keys=[ms.join_on(order_customer_id, customer_id)],
    ai_context=ms.ai_context(
        business_definition="Many orders join to one customer by customer_id.",
    ),
)
"""

CROSS_ENTITY_METRIC = """
@ms.metric(
    entities=[orders, customers],
    root_entity=orders,
    additivity="additive",
    fanout_policy="aggregate_then_join",
    name="revenue_by_customer_country",
    ai_context=ms.ai_context(
        business_definition="Gross order amount analyzed through customer attributes.",
        guardrails=["Use customer dimensions only through the verified relationship."],
    ),
)
def revenue_by_customer_country(orders, customers):
    return orders.amount.sum()
"""

DERIVED_METRICS = """
aov = ms.ratio(
    name="aov",
    numerator=gross_revenue,
    denominator=orders_count,
    unit="USD/{order}",
    ai_context=ms.ai_context(
        business_definition="Average order value.",
    ),
)

avg_revenue_per_order = ms.weighted_average(
    name="avg_revenue_per_order",
    value=gross_revenue,
    weight=orders_count,
    unit="USD",
    ai_context=ms.ai_context(
        business_definition="Revenue per order weighted by order count.",
    ),
)

net_revenue = ms.linear(
    name="net_revenue",
    add=[gross_revenue],
    subtract=[refunds],
    unit="USD",
    ai_context=ms.ai_context(
        business_definition="Gross revenue minus refunds.",
    ),
)
"""


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER, customer_id INTEGER, order_date DATE, region VARCHAR, "
        "amount DOUBLE, refund_amount DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, 10, DATE '2026-06-01', 'US', 100.0, 0.0), "
        "(2, 20, DATE '2026-06-02', 'CA', 50.0, 5.0), "
        "(3, 10, DATE '2026-06-03', 'US', 25.0, 0.0)"
    )
    con.raw_sql("CREATE TABLE customers (customer_id INTEGER, country VARCHAR)")
    con.raw_sql("INSERT INTO customers VALUES (10, 'US'), (20, 'CA')")
    con.disconnect()

    semantic_dir = root / "models" / "semantic" / "sales"
    datasource_dir = root / "models" / "datasources"
    semantic_dir.mkdir(parents=True)
    datasource_dir.mkdir(parents=True)
    domain_file = semantic_dir / "_domain.py"
    (datasource_dir / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(db_path)!r})\n"
    )

    authored: list[str] = []

    def write_domain(*sections: str) -> None:
        authored.extend(sections)
        domain_file.write_text(HEADER + "\n".join(authored))

    def verify(ref: str) -> None:
        result = ms.verify_object(ref)
        print(f"verify {ref}:", result.status)
        if result.status == "failed":
            result.show()
            raise SystemExit(f"verify failed for {ref}")

    previous = Path.cwd()
    try:
        os.chdir(root)

        write_domain(DOMAIN)
        verify("sales")

        entity_brief = ms.prepare_entity(
            datasource="warehouse",
            source=ms.table("orders"),
            domain="sales",
            scope=md.unpruned(max_rows=100),
        )
        print("orders entity brief:", entity_brief.status)
        write_domain(ORDERS_ENTITY)
        verify("sales.orders")

        entity_brief = ms.prepare_entity(
            datasource="warehouse",
            source=ms.table("customers"),
            domain="sales",
            scope=md.unpruned(max_rows=100),
        )
        print("customers entity brief:", entity_brief.status)
        write_domain(CUSTOMERS_ENTITY)
        verify("sales.customers")

        dimension_brief = ms.prepare_dimension(
            entity="sales.orders",
            column="region",
            scope=md.unpruned(max_rows=100),
        )
        print("region dimension brief:", dimension_brief.status)
        write_domain(ORDER_REGION_DIMENSION)
        verify("sales.orders.region")

        dimension_brief = ms.prepare_dimension(
            entity="sales.orders",
            column="customer_id",
            scope=md.unpruned(max_rows=100),
        )
        print("order customer key brief:", dimension_brief.status)
        write_domain(ORDER_CUSTOMER_ID_DIMENSION)
        verify("sales.orders.customer_id")

        dimension_brief = ms.prepare_dimension(
            entity="sales.customers",
            column="customer_id",
            scope=md.unpruned(max_rows=100),
        )
        print("customer key brief:", dimension_brief.status)
        write_domain(CUSTOMER_ID_DIMENSION)
        verify("sales.customers.customer_id")

        dimension_brief = ms.prepare_dimension(
            entity="sales.customers",
            column="country",
            scope=md.unpruned(max_rows=100),
        )
        print("country dimension brief:", dimension_brief.status)
        write_domain(CUSTOMER_COUNTRY_DIMENSION)
        verify("sales.customers.country")

        time_brief = ms.prepare_time_dimension(
            entity="sales.orders",
            column="order_date",
            scope=md.unpruned(max_rows=100),
        )
        print("order date brief:", time_brief.status)
        write_domain(ORDER_DATE_TIME_DIMENSION)
        verify("sales.orders.order_date")

        measure_brief = ms.prepare_measure(
            entity="sales.orders",
            column="amount",
            scope=md.unpruned(max_rows=100),
        )
        print("amount measure brief:", measure_brief.status)
        write_domain(AMOUNT_MEASURE)
        verify("sales.orders.amount")

        measure_brief = ms.prepare_measure(
            entity="sales.orders",
            column="refund_amount",
            scope=md.unpruned(max_rows=100),
        )
        print("refund measure brief:", measure_brief.status)
        write_domain(REFUND_MEASURE)
        verify("sales.orders.refund_amount")

        metric_brief = ms.prepare_metric(
            entity="sales.orders",
            measure_columns=("amount",),
            scope=md.unpruned(max_rows=100),
        )
        print("gross revenue metric brief:", metric_brief.status)
        write_domain(GROSS_REVENUE_METRIC)
        verify("sales.gross_revenue")

        metric_brief = ms.prepare_metric(
            entity="sales.orders",
            measure_columns=("refund_amount",),
            scope=md.unpruned(max_rows=100),
        )
        print("refunds metric brief:", metric_brief.status)
        write_domain(REFUNDS_METRIC)
        verify("sales.refunds")

        metric_brief = ms.prepare_metric(
            entity="sales.orders",
            scope=md.unpruned(max_rows=100),
        )
        print("orders count metric brief:", metric_brief.status)
        write_domain(ORDERS_COUNT_METRIC)
        verify("sales.orders_count")

        relationship_brief = ms.prepare_relationship(
            from_entity="sales.orders",
            to_entity="sales.customers",
            keys=[("sales.orders.customer_id", "sales.customers.customer_id")],
            scope=md.unpruned(max_rows=100),
        )
        print("relationship brief:", relationship_brief.status)
        write_domain(RELATIONSHIP)
        verify("sales.orders_to_customers")

        cross_brief = ms.prepare_cross_entity_metric(
            root_entity="sales.orders",
            entities=("sales.orders", "sales.customers"),
            measure_columns=("amount",),
            scope=md.unpruned(max_rows=100),
        )
        print("cross brief:", cross_brief.status)
        write_domain(CROSS_ENTITY_METRIC)
        verify("sales.revenue_by_customer_country")

        ms.prepare_derived_metric(
            numerator="sales.gross_revenue",
            denominator="sales.orders_count",
        )
        write_domain(DERIVED_METRICS)
        for ref in ("sales.aov", "sales.avg_revenue_per_order", "sales.net_revenue"):
            verify(ref)

        readiness = ms.readiness(
            refs=(
                "sales.orders.region",
                "sales.orders.order_date",
                "sales.orders.amount",
                "sales.gross_revenue",
                "sales.aov",
                "sales.avg_revenue_per_order",
                "sales.net_revenue",
            )
        )
        print("readiness:", readiness.status)
    finally:
        os.chdir(previous)
