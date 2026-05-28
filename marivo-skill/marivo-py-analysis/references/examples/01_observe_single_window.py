"""Pattern: observe a single metric for one window.

When to use: you need the value of a known metric inside a fixed time window.
Output shape: a MetricFrame with one row and one metric value column.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
cur = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30"},
)
print(cur.summary())
print(cur.preview(limit=5))

# Expected output:
# kind='metric_frame'
# row_count=1
# columns=['value']
