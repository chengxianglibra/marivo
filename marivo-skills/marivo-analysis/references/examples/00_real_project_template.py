# marivo-example: template
"""Template: start an analysis from a real Marivo project.

Copy this into a project that already has `marivo/semantic/` definitions.
Replace the session name, metric id, calendar, and timescope values
with the project-specific analysis target.
"""

from __future__ import annotations

import marivo.analysis as mv
import marivo.semantic as ms

session_name = "revenue-investigation"
metric_id = "sales.revenue"
default_calendar = "cn_holidays"
timescope = {"start": "2026-05-01", "end": "2026-06-01"}
grain = "day"

catalog = ms.load()
available_metric_ids = catalog.list(kind="metric").ids()
if metric_id not in available_metric_ids:
    raise SystemExit(
        f"Metric {metric_id!r} was not found. Available metrics: {available_metric_ids}"
    )

session = mv.session.get_or_create(
    name=session_name,
    default_calendar=default_calendar,
)

frame = session.observe(
    session.catalog.get(metric_id),
    timescope=timescope,
    grain=grain,
)

print(frame.summary())
print(frame.preview(limit=20))
