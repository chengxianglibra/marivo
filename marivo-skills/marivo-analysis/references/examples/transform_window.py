"""Pattern: clip a time-series MetricFrame to a smaller absolute window.

When to use: you already have a time series and need a narrower date range.
Output shape: same time-series frame shape with rows inside [start, end).
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
time_series_frame = session.observe(
    mv.MetricRef(id=METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-07-04"},
    grain="day",
)
clipped = session.transform.window(
    time_series_frame,
    window={"start": "2026-07-02", "end": "2026-07-03"},
)
print(clipped.summary())

# Expected output:
# kind='metric_frame'
# semantic_kind='time_series'
# columns=['bucket_start', 'revenue']
