"""Pattern: observe multiple same-scope metrics in one frame.

When to use: you need a report-style view of several metrics over the same
window and grain, then want to drill down on one metric without re-querying.
Output shape: a multi-column MetricFrame; frame.metric(id) returns an
arity-1 frame for downstream operators.
"""

from __future__ import annotations

import marivo.analysis as mv

session = mv.session.get_or_create(
    name="examples",
    default_calendar="cn_holidays",
)
catalog = session.catalog

frame = session.observe(
    [
        catalog.get("metric.sales.revenue"),
        catalog.get("metric.sales.total_orders"),
        catalog.get("metric.sales.failed_orders"),
    ],
    time_scope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
frame.show()
print(f"metrics={frame.metrics!r}")
print(f"columns={frame.columns!r}")

revenue = frame.metric("sales.revenue")
print(f"revenue.arity={revenue.arity!r}")
revenue.show()

# Expected output:
# Multi-metric MetricFrame show() card with bucket_start + three value columns,
# then the metrics/columns lines, then the arity-1 projection show() card.
