"""Pattern: use derive_metric_frame for governed custom Ibis metric output.

When to use: a custom Ibis calculation must re-enter the typed metric flow —
custom joins, raw table scans, or bespoke aggregations that Marivo does not
model directly.
Output shape: a validated MetricFrame persisted with full lineage.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

ensure_loaded()

import marivo.analysis as mv  # noqa: E402
import marivo.datasource as md  # noqa: E402

session = mv.session.current()

warehouse = md.ref("datasource.tiny_orders")
metric = session.catalog.get(f"metric.{METRIC_ID}")
created_at = session.catalog.get("time_dimension.sales.orders.created_at")
region = session.catalog.get("dimension.sales.orders.region")

frame = session.derive_metric_frame(
    metric=metric,
    query=mv.ibis_query(
        datasource=warehouse,
        build=lambda db, ctx: db.table("orders"),
    ),
    columns=mv.metric_columns(
        value="amount",
        time=mv.time_column(column="created_at", ref=created_at),
        dimensions=[mv.dimension_column(column="region", ref=region)],
    ),
    timescope={"start": "2025-07-01", "end": "2026-09-30"},
    grain="day",
    label="custom_revenue_by_region",
)

assert frame.kind == "metric_frame"
assert frame.meta.metric_id == METRIC_ID
frame.show()

# Expected output:
# MetricFrame ref=frame_... metric=sales.revenue shape=panel rows=6
# columns: order_id | created_at | amount | region | user_id | state
# preview: 5 bounded rows shown; call .to_pandas() for terminal custom analysis
