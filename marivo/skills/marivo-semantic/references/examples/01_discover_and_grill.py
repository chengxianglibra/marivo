"""Discover datasource evidence, then stop for one semantic decision."""

from __future__ import annotations

import marivo.datasource as md
import marivo.semantic as ms

md.help_text("discover_entity")
ms.help("entity")

warehouse = md.ref("datasource.warehouse")
orders = md.table("orders")
scope = md.unpruned(max_rows=100)

test_result = md.test(warehouse)
print("datasource test:", test_result.ok)

entity_evidence = md.discover_entity(warehouse, orders, scope=scope)
entity_evidence.show()

dimension_evidence = md.discover_dimensions(
    warehouse,
    orders,
    columns=("region", "status"),
    scope=scope,
)
dimension_evidence.show()

time_evidence = md.discover_time_dimensions(
    warehouse,
    orders,
    columns=("order_date",),
    scope=scope,
)
time_evidence.show()

measure_evidence = md.discover_measures(
    warehouse,
    orders,
    columns=("amount",),
    scope=scope,
)
measure_evidence.show()

status_values = md.discover_dimension_values(
    warehouse,
    orders,
    column="status",
    limit=5,
    scope=scope,
)
status_values.show()

catalog = ms.load()
catalog.domains.show()

print(
    "GRILL: Should status='refunded' be modeled as an excluded order state "
    "or as a refund policy dimension for downstream revenue metrics?"
)
