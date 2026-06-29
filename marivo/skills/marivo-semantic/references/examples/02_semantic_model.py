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
ms.domain(name="sales", owner='Mina Zhang')
warehouse = md.ref("datasource.warehouse")
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

    def verify(ref: ms.SemanticRef) -> None:
        result = ms.verify_object(ref)
        print(f"verify {ref.id}:", result.status)
        if result.status == "failed":
            result.show()
            raise SystemExit(f"verify failed for {ref.id}")

    previous = Path.cwd()
    try:
        os.chdir(root)
        warehouse = md.ref("datasource.warehouse")
        orders_source = md.table("orders")
        customers_source = md.table("customers")
        scope = md.unpruned(max_rows=100)

        write_domain(DOMAIN)
        verify(ms.ref("domain.sales"))

        orders_entity = md.discover_entity(
            warehouse,
            orders_source,
            scope=scope,
        )
        orders_entity.show()
        write_domain(ORDERS_ENTITY)
        verify(ms.ref("entity.sales.orders"))

        customers_entity = md.discover_entity(
            warehouse,
            customers_source,
            scope=scope,
        )
        customers_entity.show()
        write_domain(CUSTOMERS_ENTITY)
        verify(ms.ref("entity.sales.customers"))

        region_discovery = md.discover_dimensions(
            warehouse,
            orders_source,
            columns=("region",),
            scope=scope,
        )
        region_discovery.show()
        write_domain(ORDER_REGION_DIMENSION)
        verify(ms.ref("dimension.sales.orders.region"))

        order_customer_discovery = md.discover_dimensions(
            warehouse,
            orders_source,
            columns=("customer_id",),
            scope=scope,
        )
        order_customer_discovery.show()
        write_domain(ORDER_CUSTOMER_ID_DIMENSION)
        verify(ms.ref("dimension.sales.orders.customer_id"))

        customer_id_discovery = md.discover_dimensions(
            warehouse,
            customers_source,
            columns=("customer_id",),
            scope=scope,
        )
        customer_id_discovery.show()
        write_domain(CUSTOMER_ID_DIMENSION)
        verify(ms.ref("dimension.sales.customers.customer_id"))

        country_discovery = md.discover_dimensions(
            warehouse,
            customers_source,
            columns=("country",),
            scope=scope,
        )
        country_discovery.show()
        write_domain(CUSTOMER_COUNTRY_DIMENSION)
        verify(ms.ref("dimension.sales.customers.country"))

        order_date_discovery = md.discover_time_dimensions(
            warehouse,
            orders_source,
            columns=("order_date",),
            scope=scope,
        )
        order_date_discovery.show()
        write_domain(ORDER_DATE_TIME_DIMENSION)
        verify(ms.ref("time_dimension.sales.orders.order_date"))

        amount_discovery = md.discover_measures(
            warehouse,
            orders_source,
            columns=("amount",),
            scope=scope,
        )
        amount_discovery.show()
        write_domain(AMOUNT_MEASURE)
        verify(ms.ref("measure.sales.orders.amount"))

        refund_discovery = md.discover_measures(
            warehouse,
            orders_source,
            columns=("refund_amount",),
            scope=scope,
        )
        refund_discovery.show()
        write_domain(REFUND_MEASURE)
        verify(ms.ref("measure.sales.orders.refund_amount"))

        ms.help("aggregate")
        write_domain(GROSS_REVENUE_METRIC)
        verify(ms.ref("metric.sales.gross_revenue"))

        ms.help("aggregate")
        write_domain(REFUNDS_METRIC)
        verify(ms.ref("metric.sales.refunds"))

        ms.help("count")
        write_domain(ORDERS_COUNT_METRIC)
        verify(ms.ref("metric.sales.orders_count"))

        relationship_discovery = md.discover_relationship(
            from_side=md.JoinSide(warehouse, orders_source, columns=("customer_id",)),
            to_side=md.JoinSide(warehouse, customers_source, columns=("customer_id",)),
            scope=scope,
        )
        relationship_discovery.show()
        write_domain(RELATIONSHIP)
        verify(ms.ref("relationship.sales.orders_to_customers"))

        ms.help("metric")
        write_domain(CROSS_ENTITY_METRIC)
        verify(ms.ref("metric.sales.revenue_by_customer_country"))

        ms.help("ratio")
        ms.help("weighted_average")
        ms.help("linear")
        write_domain(DERIVED_METRICS)
        for ref in (
            ms.ref("metric.sales.aov"),
            ms.ref("metric.sales.avg_revenue_per_order"),
            ms.ref("metric.sales.net_revenue"),
        ):
            verify(ref)

        readiness = ms.readiness(
            refs=(
                ms.ref("dimension.sales.orders.region"),
                ms.ref("time_dimension.sales.orders.order_date"),
                ms.ref("measure.sales.orders.amount"),
                ms.ref("metric.sales.gross_revenue"),
                ms.ref("metric.sales.aov"),
                ms.ref("metric.sales.avg_revenue_per_order"),
                ms.ref("metric.sales.net_revenue"),
            )
        )
        print("readiness:", readiness.status)
    finally:
        os.chdir(previous)
