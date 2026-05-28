"""Pattern: observe relative windows as scalar and day-grain time series.

When to use: you want v1.2 relative-window inputs while controlling session timezone.
Output shape: scalar frame for no grain, time_series frame for day grain.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

ensure_loaded(timezone="UTC")

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
scalar = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"expr": "mtd", "as_of": "2026-09-15T12:00:00+00:00"},
)
series = session.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"expr": "mtd", "grain": "day", "as_of": "2026-09-15T12:00:00+00:00"},
)

assert scalar.meta.semantic_kind == "scalar"
assert series.meta.semantic_kind == "time_series"

print(f"scalar_kind={scalar.meta.semantic_kind!r}")
print(f"series_kind={series.meta.semantic_kind!r}")
