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

session = mv.session.current()
created_at = session.catalog.get("sales.orders.created_at")
region = session.catalog.get("sales.orders.region")
cur = session.observe(
    session.catalog.get(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
base = session.observe(
    session.catalog.get(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
delta = session.compare(cur, base, alignment=mv.window_bucket())
attribution = session.attribute(delta, axes=[created_at])
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
    session.catalog.get(DERIVED_RATIO_METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
)
ratio_base = session.observe(
    session.catalog.get(DERIVED_RATIO_METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
)
ratio_delta = session.compare(ratio_cur, ratio_base)
ratio_components = ratio_delta.components()
print(f"component_kind={ratio_components.meta.composition_kind!r}")
print(f"component_columns={list(ratio_components.to_pandas().columns)!r}")

# Component-aware time-series ratio metric: decompose change by bucket.
ratio_cur_series = session.observe(
    session.catalog.get(DERIVED_RATIO_METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
)
ratio_base_series = session.observe(
    session.catalog.get(DERIVED_RATIO_METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
    grain="month",
)
ratio_series_delta = session.compare(
    ratio_cur_series,
    ratio_base_series,
    alignment=mv.window_bucket(),
)
ratio_bucket_attr = session.attribute(
    ratio_series_delta,
    axes=[created_at],
)
print(ratio_bucket_attr.summary())

# Component-aware panel ratio metric: decompose each bucket by segment.
ratio_cur_panel = session.observe(
    session.catalog.get(DERIVED_RATIO_METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="month",
    dimensions=[region],
)
ratio_base_panel = session.observe(
    session.catalog.get(DERIVED_RATIO_METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
    grain="month",
    dimensions=[region],
)
ratio_panel_delta = session.compare(
    ratio_cur_panel,
    ratio_base_panel,
    alignment=mv.window_bucket(),
)
ratio_panel_attr = session.attribute(
    ratio_panel_delta,
    axes=[region],
)
print(ratio_panel_attr.summary())
