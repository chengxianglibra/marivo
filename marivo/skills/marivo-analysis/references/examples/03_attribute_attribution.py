"""Pattern: attribute a time-series delta by bucket.

When to use: you need a runnable attribution frame that ranks which time
buckets contributed most to a bucket-aligned metric delta.
Output shape: an AttributionFrame with one row per bucket and contribution
columns.
"""

from __future__ import annotations

import marivo.analysis as mv

session = mv.session.get_or_create(
    name="examples",
    default_calendar="cn_holidays",
)
created_at = session.catalog.get("time_dimension.sales.orders.created_at")
revenue = session.catalog.get("metric.sales.revenue")
cur = session.observe(
    revenue,
    time_scope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
base = session.observe(
    revenue,
    time_scope={"start": "2025-07-01", "end": "2025-10-01"},
    grain="month",
)
delta = session.compare(cur, base, alignment=mv.window_bucket())
attribution = session.attribute(delta, axes=[created_at])
attribution.show()
print(f"kind={attribution.kind!r}")
print(f"row_count={len(attribution)}")
print(f"columns={attribution.columns!r}")

# Expected output:
# AttributionFrame show() card, then printed kind/row_count/columns lines.
