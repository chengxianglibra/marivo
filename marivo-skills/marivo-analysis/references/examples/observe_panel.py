"""Pattern: observe a metric on a (time x segment) panel.

When to use: you need both a time series and a per-segment breakdown
("daily revenue by region").
Output shape: a panel MetricFrame with bucket_start, dimension columns, and the metric value.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
panel = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "day"},
    dimensions=[mv.DimensionRef(id="region")],
)
print(panel.summary())

# Expected output:
# kind='metric_frame'
# semantic_kind='panel'
# columns=['bucket_start', 'region', 'revenue']
