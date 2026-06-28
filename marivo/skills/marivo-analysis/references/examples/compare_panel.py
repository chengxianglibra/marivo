"""Pattern: compare panel frames across two windows of equal grain.

When to use: you want a (bucket x segment) delta. AlignmentPolicy controls
how time buckets are aligned across the two windows; segment keys are
always outer-joined. One-sided bucket/segment cells treat the missing side as
zero for delta math and use presence_status to mark new or churned rows.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
cur = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="day",
    dimensions=[session.catalog.get("dimension.sales.orders.region")],
)
prev = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
    grain="day",
    dimensions=[session.catalog.get("dimension.sales.orders.region")],
)
delta = session.compare(cur, prev, alignment=mv.window_bucket())
delta.show()

# Expected output:
# kind='delta_frame'
# semantic_kind='panel'
# columns=['bucket_start', 'bucket_start_b', 'region', 'presence_status', 'current', 'baseline', 'delta', 'pct_change', 'pct_change_status']
