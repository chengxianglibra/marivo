"""Pattern: normalize a segmented MetricFrame to shares.

When to use: you need each segment's contribution to the total.
Output shape: same frame shape with the metric measure converted to shares.
"""

from __future__ import annotations

import pandas as pd
from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
segmented_frame = mv.MetricFrame.from_dataframe(
    pd.DataFrame(
        {
            "country": ["US", "CA", "MX"],
            "revenue": [120.0, 80.0, 40.0],
        }
    ),
    metric_id=METRIC_ID,
    axes={"country": {"role": "dimension", "column": "country"}},
    measure={"column": "revenue"},
    semantic_kind="segmented",
    semantic_model="sales",
    session=session,
)
share = mv.transform.normalize(segmented_frame, kind="share")
print(share.summary())

# Expected output:
# kind='metric_frame'
# semantic_kind='segmented'
# columns=['country', 'revenue']
