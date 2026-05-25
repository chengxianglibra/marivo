"""Pattern: decompose a scalar delta into total attribution.

When to use: you need a runnable v1 attribution frame for the total change
between two scalar metric observations. Current v1 examples do not segment
``observe`` output by dimension, so this example intentionally does not pass
``by="region"``.
Output shape: an AttributionFrame with one row for the total driver and
contribution columns.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

cur = mv.observe(
    METRIC_ID,
    slice={"created_at": {"op": "between", "value": ["2026-07-01", "2026-09-30"]}},
)
base = mv.observe(
    METRIC_ID,
    slice={"created_at": {"op": "between", "value": ["2025-07-01", "2025-09-30"]}},
)
delta = mv.compare(cur, base, compare_type="yoy")
attribution = mv.decompose(delta)
summary = attribution.summary()
print(f"kind={summary.kind!r}")
print(f"row_count={summary.row_count}")
print(f"columns={summary.columns!r}")

# Expected output:
# kind='attribution_frame'
# row_count=1
# columns=['driver', 'delta', 'contribution', 'pct_contribution', 'rank']
