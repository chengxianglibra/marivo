"""Pattern: detect anomalies on an explicit metric time-series frame.

When to use: you want z-score anomaly flags and scores for a known metric
series. The fixture only creates the temporary active session; this example
uses an explicit tiny series so the detector has multiple points to score.
Output shape: an anomaly AttributionFrame with detection summary columns for
score, anomaly flag, direction, and threshold.
"""
# mypy: disable-error-code=import-untyped

from __future__ import annotations

import pandas as pd
from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

series = mv.MetricFrame.from_dataframe(
    pd.DataFrame(
        {
            "bucket": ["a", "b", "c", "d"],
            "value": [-100.0, 0.0, 0.0, 100.0],
        }
    ),
    metric_id=METRIC_ID,
    axes={},
    measure={"name": "revenue"},
    semantic_kind="time_series",
    semantic_model="sales",
    session=mv.session.active(),
)
candidates = mv.detect(series, threshold=1.0)
summary = candidates.summary()
print(f"kind={summary.kind!r}")
print(f"attribution_kind={candidates.meta.attribution_kind!r}")
print(f"row_count={summary.row_count}")
print(f"columns={summary.columns!r}")

# Expected output:
# kind='attribution_frame'
# attribution_kind='anomaly'
# row_count=4
# columns=['bucket', 'value', 'score', 'is_anomaly', 'direction', 'threshold']
