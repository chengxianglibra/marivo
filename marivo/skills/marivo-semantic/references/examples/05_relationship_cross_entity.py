"""Relationship and cross-entity metric authoring.

Shows: declare join-key dimensions, author a relationship, prepare a
cross-entity metric brief, then declare the cross-entity base metric with
an explicit root entity.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import _DuckDBSpec

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
    ),
)

customers = ms.entity(
    name="customers",
    datasource=warehouse,
    source=ms.table("customers"),
    primary_key=["customer_id"],
    ai_context=ms.ai_context(
        business_definition="One row per customer.",
    ),
)

order_customer_id = ms.dimension_column(
    name="customer_id",
    entity=orders,
    column="customer_id",
    ai_context=ms.ai_context(
        business_definition="Customer key recorded on the order.",
    ),
)

customer_id = ms.dimension_column(
    name="customer_id",
    entity=customers,
    column="customer_id",
    ai_context=ms.ai_context(
        business_definition="Customer primary key.",
    ),
)

country = ms.dimension_column(
    name="country",
    entity=customers,
    column="country",
    ai_context=ms.ai_context(
        business_definition="Customer country used for sales segmentation.",
    ),
)

orders_to_customers = ms.relationship(
    name="orders_to_customers",
    from_entity=orders,
    to_entity=customers,
    keys=[ms.join_on(order_customer_id, customer_id)],
)

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

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (1, 10, 100.0), (2, 20, 50.0), (3, 10, 25.0)")
    con.raw_sql("CREATE TABLE customers (customer_id INTEGER, country VARCHAR)")
    con.raw_sql("INSERT INTO customers VALUES (10, 'US'), (20, 'CA')")
    con.disconnect()

    semantic_dir = root / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(DOMAIN)

    previous = Path.cwd()
    try:
        os.chdir(root)
        md.register(_DuckDBSpec(name="warehouse", path=str(db_path)))

        # Discovery-first relationship evidence: gather bounded join-key
        # evidence before authoring the semantic relationship.
        warehouse = md.ref("warehouse")
        relationship_evidence = md.discover_relationship(
            from_side=md.JoinSide(warehouse, md.table("orders"), columns=("customer_id",)),
            to_side=md.JoinSide(warehouse, md.table("customers"), columns=("customer_id",)),
            scope=md.latest_partition(),
            key_sample_size=500,
        )
        relationship_evidence.show()

        for ref in ("sales.orders", "sales.customers"):
            verify = ms.verify_object(ref)
            print(f"verify {ref}:", verify.status)

        brief = ms.prepare_cross_entity_metric(
            root_entity="sales.orders",
            entities=("sales.orders", "sales.customers"),
            measure_columns=("amount",),
            scope=md.latest_partition(),
        )
        print("cross brief:", brief.status)
        print("join paths:", len(brief.join_paths))

        for ref in ("sales.orders_to_customers", "sales.revenue_by_customer_country"):
            verify = ms.verify_object(ref)
            print(f"verify {ref}:", verify.status)

        catalog = ms.load(workspace_dir=root)
        print("relationship:", catalog.get("sales.orders_to_customers").details().ref)
        print("cross metric:", catalog.get("sales.revenue_by_customer_country").details().ref)
    finally:
        os.chdir(previous)
