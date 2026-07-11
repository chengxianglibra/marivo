"""Acquire one source snapshot, project evidence, then grill one decision."""

from __future__ import annotations

import marivo.datasource as md
import marivo.semantic as ms

md.help("authoring")
ms.help("entity")

warehouse = md.ref("datasource.warehouse")
orders = md.table("orders")
scope = md.unpruned(max_rows=100, timeout_seconds=30)

test_result = md.test(warehouse)
print("datasource test:", test_result.ok)

inspection = md.inspect(warehouse, orders)
inspection.show()
snapshot = inspection.sample(
    scope=scope,
    columns=("order_id", "region", "status", "order_date", "amount"),
    persist_values=False,
)

entity_evidence = snapshot.entity(columns=("order_id",))
entity_evidence.show()

dimension_evidence = snapshot.dimensions(columns=("region", "status"))
dimension_evidence.show()

time_evidence = snapshot.time_dimensions(columns=("order_date",))
time_evidence.show()

measure_evidence = snapshot.measures(columns=("amount",))
measure_evidence.show()

status_values = snapshot.values("status", limit=5)
status_values.show()

catalog = ms.load()
catalog.domains.show()

print(
    "The snapshot reports observed values only; uncommon formats and semantic "
    "judgments remain agent-owned."
)
print(
    "GRILL: Should status='refunded' be modeled as an excluded order state "
    "or as a refund policy dimension for downstream revenue metrics?"
)
