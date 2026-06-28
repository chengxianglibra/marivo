"""Pattern: keep the largest decreases from a DeltaFrame.

When to use: you compared two segmented frames and need the segments with the
largest negative delta values.
Output shape: a DeltaFrame with at most ``limit`` rows.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
current = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    dimensions=[session.catalog.get("dimension.sales.orders.region")],
)
baseline = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
    dimensions=[session.catalog.get("dimension.sales.orders.region")],
)
delta_frame = session.compare(
    current,
    baseline,
    alignment=mv.window_bucket(),
)
top_decreases = session.transform.topk(delta_frame, by="delta", limit=3, order="decrease")
top_decreases.show()

# Expected output:
# kind='delta_frame' row_count=2
# columns=['region', 'presence_status', 'current', 'baseline', 'delta', 'pct_change', 'pct_change_status']
# semantic_shape='segmented' lineage_oneliner='observe -> observe -> compare -> transform'
