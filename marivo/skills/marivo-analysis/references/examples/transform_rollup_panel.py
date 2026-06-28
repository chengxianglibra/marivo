"""Pattern: roll up a panel frame by dropping one dimension axis.

When to use: you have a (time x segment) panel and need the total time series.
Output shape: dropping the region dimension demotes the frame to time_series.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic domain and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
region = session.catalog.get("dimension.sales.orders.region")
panel_frame = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
    dimensions=[region],
)
rolled = session.transform.rollup(panel_frame, drop_axes=[region])
rolled.show()

# Expected output:
# kind='metric_frame' row_count=3
# columns=['bucket_start', 'value']
# semantic_shape='time_series' lineage_oneliner='observe -> transform'
