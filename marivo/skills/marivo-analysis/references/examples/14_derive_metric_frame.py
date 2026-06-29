"""Pattern: use derive_metric_frame for governed custom Ibis metric output.

When to use: a custom Ibis calculation must re-enter the typed metric flow —
custom joins, raw table scans, or bespoke aggregations that Marivo does not
model directly.
Output shape: a validated MetricFrame persisted with full lineage.
"""

from __future__ import annotations

from typing import Any

import marivo.analysis as mv
import marivo.datasource as md

print(mv.help_text("derive_metric_frame").splitlines()[0])

session = mv.session.get_or_create(
    name="examples",
    default_calendar="cn_holidays",
)

warehouse = md.ref("datasource.warehouse")
metric = session.catalog.get("metric.sales.revenue")
created_at = session.catalog.get("time_dimension.sales.orders.created_at")
region = session.catalog.get("dimension.sales.orders.region")


def revenue_by_day_region(db: Any, ctx: Any) -> Any:
    orders = db.table("orders")
    return orders.group_by([orders.created_at, orders.region]).aggregate(
        revenue=orders.amount.sum()
    )


frame = session.derive_metric_frame(
    metric=metric,
    query=mv.ibis_query(
        datasource=warehouse,
        build=revenue_by_day_region,
    ),
    columns=mv.metric_columns(
        value="revenue",
        time=mv.time_column(column="created_at", ref=created_at),
        dimensions=[mv.dimension_column(column="region", ref=region)],
    ),
    timescope={"start": "2025-07-01", "end": "2026-09-30"},
    grain="day",
    label="custom_revenue_by_day_region",
)

assert frame.kind == "metric_frame"
assert frame.meta.metric_id == "sales.revenue"
frame.show()
print(f"contract_kind={frame.contract().kind!r}")

# Expected output:
# MetricFrame ref=frame_... metric=sales.revenue shape=panel rows=9
# columns: created_at | region | revenue
# preview: 5 bounded rows shown; call .to_pandas() for terminal custom analysis
