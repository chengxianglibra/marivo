"""Pattern: compare segmented frames across two non-overlapping windows.

When to use: you want a per-segment delta of a known metric between two
windows, with no time grain. Segments that only appear on one side are
returned with the missing side treated as zero for delta math and marked by
presence_status; lineage records the counts.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
cur = session.observe(
    mv.MetricRef(id=METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
)
prev = session.observe(
    mv.MetricRef(id=METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-09-30"},
    dimensions=[mv.DimensionRef(id="region")],
)
delta = session.compare(cur, prev, alignment=mv.AlignmentPolicy(kind="window_bucket"))
print(delta.summary())

# Expected output:
# kind='delta_frame'
# semantic_kind='segmented'
# columns=['region', 'presence_status', 'current', 'baseline', 'delta', 'pct_change']
