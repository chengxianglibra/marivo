"""Pattern: observe a metric segmented by one dimension.

When to use: you want per-segment totals for a known metric, with no time
grain (e.g. "revenue by region across the whole window").
Output shape: a segmented MetricFrame with one row per segment.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
by_region = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
    session=session,
)
print(by_region.summary())

# Expected output:
# kind='metric_frame'
# semantic_kind='segmented'
# columns=['region', 'revenue']
