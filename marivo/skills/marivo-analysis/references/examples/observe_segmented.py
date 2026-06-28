"""Pattern: observe a derived metric segmented by one dimension.

When to use: you want per-segment values for a known metric, with a time filter
but no time grain (e.g. "failure rate by region within a quarter").
Output shape: a segmented MetricFrame with one row per segment.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import DERIVED_RATIO_METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
by_region = session.observe(
    session.catalog.get(f"metric.{DERIVED_RATIO_METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    dimensions=[session.catalog.get("dimension.sales.orders.region")],
)
by_region.show()

# Expected output:
# MetricFrame show() card: identity line, columns, bounded rows, available footer.
