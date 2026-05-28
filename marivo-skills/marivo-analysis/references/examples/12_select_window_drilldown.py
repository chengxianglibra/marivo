"""Pattern: select an anomaly window and drill into it with transform.window(...).

When to use: discover surfaced point anomalies, you want to clip the original
series to the rank-1 anomaly window for closer inspection. Single-bucket
candidates need a half-open follow-up window, so we extend the end forward.
Output shape: a time-series MetricFrame restricted to the chosen window.
"""

from __future__ import annotations

import pandas as pd
from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
series = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"},
)
anomalies = session.discover.point_anomalies(series, threshold=1.0)
print(f"anomalies.row_count={anomalies.meta.row_count}")
if anomalies.meta.row_count:
    hit = anomalies.select(rank=1, attribute="window")
    assert isinstance(hit, mv.AbsoluteWindow)
    drill_end = (pd.Timestamp(hit.end) + pd.offsets.MonthBegin(1)).date().isoformat()
    drill = mv.AbsoluteWindow(start=hit.start, end=drill_end)
    local = session.transform.window(series, window=drill)
    print(f"local.kind={local.meta.kind!r}")
    print(f"local.row_count={local.meta.row_count}")

# Expected output:
# anomalies.row_count=1
# local.kind='metric_frame'
# local.row_count=1
