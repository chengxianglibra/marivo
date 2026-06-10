"""Pattern: observe absolute timescopes as scalar and day-grain time series.

When to use: you have resolved user time language to explicit start/end dates.
Output shape: scalar frame for no grain, time_series frame for day grain.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

ensure_loaded()

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
scalar = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2026-09-01", "end": "2026-09-16"},
)
series = session.observe(
    mv.MetricRef(METRIC_ID),
    timescope={"start": "2026-09-01", "end": "2026-09-16"},
    grain="day",
)

assert scalar.meta.semantic_kind == "scalar"
assert series.meta.semantic_kind == "time_series"

print(f"scalar_kind={scalar.meta.semantic_kind!r}")
print(f"series_kind={series.meta.semantic_kind!r}")
