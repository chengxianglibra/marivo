"""Pattern: normalize a segmented MetricFrame to shares.

When to use: you need each segment's contribution to the total.
Output shape: same frame shape with the metric measure converted to shares.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
segmented_frame = session.observe(
    session.catalog.get(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    dimensions=[session.catalog.get("sales.orders.region")],
)
share = session.transform.normalize(segmented_frame, mode="share")
print(share.summary())

# Expected output:
# kind='metric_frame' row_count=2
# columns=['region', 'value']
# semantic_shape='segmented' lineage_oneliner='observe -> transform'
