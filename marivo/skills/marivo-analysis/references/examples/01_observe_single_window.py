"""Pattern: observe a single metric for one window.

When to use: you need the value of a known metric inside a fixed time window.
Output shape: a MetricFrame with one row and one metric value column.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.current()
cur = session.observe(
    session.catalog.get(f"metric.{METRIC_ID}"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
)
cur.show()

# Expected output:
# MetricFrame identity line, columns preview, bounded rows, available footer.
