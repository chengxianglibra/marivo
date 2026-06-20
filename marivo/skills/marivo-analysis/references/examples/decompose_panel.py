"""Pattern: decompose a panel delta along one segment axis.

When to use: you have a (bucket x segment) delta and want to know each
segment's contribution within each bucket. Output has bucket-local rank
and percent of bucket total.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
region = session.catalog.get("sales.orders.region")
cur = session.observe(
    session.catalog.get(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="day",
    dimensions=[region],
)
prev = session.observe(
    session.catalog.get(METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
    grain="day",
    dimensions=[region],
)
delta = session.compare(cur, prev, alignment=mv.window_bucket())
attribution = session.decompose(delta, axis=region)
print(attribution.summary())

# Expected output:
# kind='attribution_frame'
# semantic_kind='panel'
# columns=['bucket_start', 'region', 'contribution', 'pct_contribution', 'rank']
