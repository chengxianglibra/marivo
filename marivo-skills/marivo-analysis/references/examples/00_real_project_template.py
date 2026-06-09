# marivo-example: template
"""Template: start an analysis from a real Marivo project.

Copy this into a project that already has `.marivo/semantic/` definitions.
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

project = ms.find_project()
if project is None:
    raise SystemExit("No .marivo/semantic project found. Run this from a Marivo project root.")

result = project.load()
if result.errors:
    raise SystemExit(result.errors)

available_metric_ids = [metric.semantic_id for metric in project.list_metrics()]
if metric_id not in available_metric_ids:
    raise SystemExit(
        f"Metric {metric_id!r} was not found. Available metrics: {available_metric_ids}"
    )

session = mv.session.get_or_create(
    name=session_name,
    default_calendar=default_calendar,
)

frame = session.observe(
    mv.MetricRef(id=metric_id),
    timescope=timescope,
    grain=grain,
)

print(frame.summary())
print(frame.preview(limit=20))
