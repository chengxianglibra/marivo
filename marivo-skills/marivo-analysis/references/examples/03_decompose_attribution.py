"""Pattern: decompose a time-series delta by bucket.

When to use: you need a runnable attribution frame that ranks which time
buckets contributed most to a bucket-aligned metric delta.
Output shape: an AttributionFrame with one row per bucket and contribution
columns.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import DERIVED_RATIO_METRIC_ID, METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
cur = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"},
)
base = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30", "grain": "month"},
)
delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
attribution = session.decompose(delta, axis=mv.DimensionRef(id="bucket_start"))
summary = attribution.summary()
print(f"kind={summary.kind!r}")
print(f"row_count={summary.row_count}")
print(f"columns={summary.columns!r}")

# Expected output:
# kind='attribution_frame'
# row_count=3
# columns=['bucket_start', 'delta', 'contribution', 'pct_contribution', 'rank']

# Component-aware ratio metric: observe two windows and compare.
ratio_cur = session.observe(
    mv.MetricRef(id=DERIVED_RATIO_METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-09-30"},
)
ratio_base = session.observe(
    mv.MetricRef(id=DERIVED_RATIO_METRIC_ID),
    window={"start": "2025-07-01", "end": "2025-09-30"},
)
ratio_delta = session.compare(ratio_cur, ratio_base)
ratio_components = ratio_delta.components()
print(f"component_kind={ratio_components.meta.decomposition_kind!r}")
print(f"component_columns={list(ratio_components.to_pandas().columns)!r}")
