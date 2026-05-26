"""Pattern: assess a MetricFrame before downstream analysis."""

from __future__ import annotations

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

ensure_loaded()

import marivo.analysis_py as mv  # noqa: E402

session = mv.session.active()
frame = mv.observe(
    mv.MetricRef(id=METRIC_ID),
    window={"start": "2026-07-01", "end": "2026-07-14", "grain": "day"},
    session=session,
)
report = mv.assess_quality(frame, session=session)

assert report.meta.kind == "quality_report"
assert report.meta.report_shape == "metric"
assert {"check_id", "check_kind", "severity", "details_json"}.issubset(report.columns)
print(report.summary())
