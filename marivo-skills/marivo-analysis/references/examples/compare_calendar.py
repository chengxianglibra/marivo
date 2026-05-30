"""Pattern: compare two absolute time-series windows with calendar alignment.

When to use: you need calendar-aware matching with a session default calendar.
Output shape: a DeltaFrame with calendar alignment metadata.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fixtures.tiny_semantic import METRIC_ID, ensure_loaded

ensure_loaded(default_calendar="cn_holidays")

import marivo.analysis as mv  # noqa: E402

session = mv.session.active()
calendar_dir = session.project_root / ".marivo" / "calendar"
(calendar_dir / "cn_holidays.json").write_text(
    json.dumps(
        {
            "name": "cn_holidays",
            "holidays": [
                {"date": "2025-07-01", "holiday_id": "company-shutdown"},
                {"date": "2026-09-01", "holiday_id": "company-shutdown"},
            ],
            "adjusted_workdays": [
                {"date": "2026-09-05"},
            ],
        }
    ),
    encoding="utf-8",
)

cur = session.observe(
    mv.MetricRef(id=METRIC_ID),
    timescope={"start": "2026-09-01", "end": "2026-09-15"},
    grain="day",
)
base = session.observe(
    mv.MetricRef(id=METRIC_ID),
    timescope={"start": "2025-07-01", "end": "2025-07-31"},
    grain="day",
)
delta = session.compare(
    cur,
    base,
    alignment=mv.AlignmentPolicy(
        kind="holiday_and_dow_aligned",
        calendar=mv.CalendarRef(id="cn_holidays"),
        period="month",
    ),
)

assert delta.meta.alignment["kind"] == "holiday_and_dow_aligned"
assert delta.meta.alignment["calendar_info"]["mode"] == "holiday_and_dow_aligned"
assert delta.meta.alignment["calendar_info"]["align_period"] == "month"
assert delta.meta.alignment["calendar_info"]["matched_rows"] > 0
print(f"alignment={delta.meta.alignment['kind']!r}")
