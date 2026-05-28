"""Pattern: keep the largest decreases from a DeltaFrame.

When to use: you compared two segmented frames and need the segments with the
largest negative delta values.
Output shape: a DeltaFrame with at most ``limit`` rows.
"""

from __future__ import annotations

import pandas as pd
from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
current = mv.MetricFrame.from_dataframe(
    pd.DataFrame(
        {
            "country": ["US", "CA", "MX", "BR"],
            "revenue": [90.0, 70.0, 35.0, 10.0],
        }
    ),
    metric_id=METRIC_ID,
    axes={"country": {"role": "dimension", "column": "country"}},
    measure={"column": "revenue"},
    semantic_kind="segmented",
    semantic_model="sales",
    session=session,
)
baseline = mv.MetricFrame.from_dataframe(
    pd.DataFrame(
        {
            "country": ["US", "CA", "MX", "BR"],
            "revenue": [120.0, 80.0, 55.0, 15.0],
        }
    ),
    metric_id=METRIC_ID,
    axes={"country": {"role": "dimension", "column": "country"}},
    measure={"column": "revenue"},
    semantic_kind="segmented",
    semantic_model="sales",
    session=session,
)
delta_frame = session.compare(
    current,
    baseline,
    alignment=mv.AlignmentPolicy(kind="window_bucket"),
)
top_decreases = session.transform.topk(delta_frame, by="delta", limit=3, order="decrease")
print(top_decreases.summary())

# Expected output:
# kind='delta_frame'
# semantic_kind='segmented'
# columns=['country', 'current', 'baseline', 'delta', 'pct_change']
