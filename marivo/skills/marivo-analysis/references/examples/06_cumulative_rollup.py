"""Pattern: roll up a cumulative frame to a coarser grain (period-end semantics).

When to use: you have observed a cumulative metric at a fine grain (e.g. day) and
need the period-end cumulative value at a coarser grain (e.g. month) without
re-querying the warehouse. Cumulative frames carry ``rollup_fold="last"`` so each
period contributes its last bucket — the period-end running total.
Output shape: a MetricFrame whose time axis is the target grain, with one row per
period holding the period-end cumulative value.
"""

from __future__ import annotations

import marivo.analysis as mv

session = mv.session.get_or_create(
    name="examples",
    default_calendar="cn_holidays",
)
catalog = session.catalog

# Observe a month-to-date cumulative revenue at day grain across 2026 Q2-Q3.
mtd = session.catalog.get("metric.sales.mtd_revenue")
day_frame = session.observe(
    mtd,
    time_scope={"start": "2026-04-01", "end": "2026-10-01"},
    grain="day",
)
day_frame.show()

# Roll up the time axis to month grain. Each month contributes its last day's
# MTD value — i.e. the full-month total, since MTD resets at each month start.
monthly = day_frame.transform.rollup(grain="month")
monthly.show()
print(f"kind={monthly.kind!r}")
print(f"row_count={len(monthly)}")

# Expected output:
# Two MetricFrame show() cards: the day-grain frame, then the month-grain rollup,
# followed by the printed kind/row_count lines.
