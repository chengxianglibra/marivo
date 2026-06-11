"""Pattern: observe a metric with grain — day grain, hourly grain, and dynamic sub-day grain.

When to use: you need a time-series at a specific granularity.
  - Day or coarser grains use a legacy string: ``grain="day"``.
  - Sub-day single-unit grains use ``grain="hour"`` when the time field
    has hour-level or finer base granularity.
  - Dynamic sub-day grains (multi-bucket) use token strings such as
    ``grain="5minute"`` when the time field has minute-level or finer base
    granularity.

The tiny fixture in this example uses a day-granularity time field, so
only day grain is demonstrated with a live call.  Sub-day patterns are
shown as commented examples that would work with a minute-level time field.

Output shape: MetricFrame[time_series] when grain is specified.
"""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

# Setup: load the tiny semantic model and attach an examples session.
ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()

# --- String grain (day or coarser) ------------------------------------
# Single-unit calendar grains use the token string form.
# This works with the tiny fixture's day-granularity time field.
series_day = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
    grain="day",
)

print(f"day_series_kind={series_day.meta.semantic_kind!r}")

# --- Dynamic sub-day grain patterns (API reference) ------------------
# The patterns below require a time field with minute-level or finer base
# granularity (e.g. ``granularity="minute"`` on a timestamp column).
# The tiny fixture uses day-level granularity, so these calls are shown
# as reference patterns that would work with a suitable semantic model.

# Grain token string: sub-day multi-bucket grains.
# series_5min_token = session.observe(
#     mv.MetricRef("ops.hits"),
#     timescope={"start": "2026-06-03 00:00:00", "end": "2026-06-03 01:00:00"},
#     grain="5minute",
# )

# Hourly grain on a timestamp-type time field.
# series_hourly = session.observe(
#     mv.MetricRef("ops.hits"),
#     timescope={"start": "2026-06-03 00:00:00", "end": "2026-06-03 06:00:00"},
#     grain="hour",
# )

# --- Base granularity rule -------------------------------------------
# If the requested grain is finer than the time field's base granularity,
# the planner raises GrainUnsupportedError.  For example:
#   - Requesting grain="5minute" on a day-level time field is rejected.
#   - Requesting grain="hour" on a date-type time field is rejected
#     unless the time field declares granularity="hour" with required_prefix.

# Expected output:
# kind='time_series' for day-grain and sub-day-grain observe calls.
# Sub-day grains produce bucket timestamps at the requested resolution.
