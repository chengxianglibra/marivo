"""Pattern: surface segment outliers using MAD on a segmented MetricFrame.

When to use: you have a segmented or panel MetricFrame and want to flag
segments whose values stand far from the peer median.
Output shape: CandidateSet[cross_sectional_outlier]; tiny fixtures often
return zero rows because MAD requires at least three peers with spread.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
metric = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    dimensions=[session.catalog.get("dimension.sales.orders.region")],
)
outliers = session.discover.cross_sectional_outliers(
    metric,
    threshold=3.0,
    peer_scope=[session.catalog.get("dimension.sales.orders.region")],
)
print(f"outliers.objective={outliers.meta.objective!r}")
print(f"outliers.row_count={outliers.meta.row_count}")

# Expected output:
# outliers.objective='cross_sectional_outliers'
# outliers.row_count=0
