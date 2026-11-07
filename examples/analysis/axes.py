"""Bind all axes, then compare and correlate fixed observations."""

import sys
from datetime import date, timedelta
from uuid import uuid4

import marivo.analysis as mv

start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
split = start + timedelta(days=1)
end = split + timedelta(days=1)

session = mv.session.get_or_create(f"axes-{uuid4().hex[:8]}", timezone="UTC", domains=None)
catalog = session.catalog
metric = catalog.get("metric:sample.measurement")
assert metric.kind == "metric"
group = catalog.get("dimension:sample.readings.group")
assert group.kind == "dimension"
left_scope = session.time_scope(start=start, end=split)
right_scope = session.time_scope(start=split, end=end)
day = mv.grain("day")
left = session.observe({"value": metric}, by=(day, group), where=left_scope).compute()
left.show(n=10)
right = session.observe({"value": metric}, by=(day, group), where=right_scope).compute()

selected_value = "gamma"
conditioned = session.observe(
    {"combined": metric + metric},
    by=(day, group),
    where=(left_scope, group == selected_value),
).compute()
conditioned.show()

aligned = left.align(
    right,
    on=((left.axes[0], right.axes[0]), (left.axes[1], right.axes[1])),
    match=mv.ordinal(),
).compute()
aligned.show(n=10)

change = right.compare(
    {
        "reference": mv.baseline(
            left,
            on=((right.axes[0], left.axes[0]), (right.axes[1], left.axes[1])),
            match=mv.ordinal(),
        )
    }
).compute()
change.show(n=10)

association = aligned.correlate(
    aligned["left.value"], aligned["right.value"], method="pearson"
).compute()
association.show()

# Pair repeated coordinates on one explicit grid, then correlate within groups.
scope = session.time_scope(start=start, end=end)
series = session.observe({"value": metric}, by=(day, group), where=scope).compute()
composed = session.observe({"value": metric + metric}, by=(day, group), where=scope).compute()
paired = series.align(
    composed,
    on=((series.axes[0], composed.axes[0]), (series.axes[1], composed.axes[1])),
    match=None,
).compute()
paired.show(n=10)
temporal_association = paired.correlate(
    paired["left.value"],
    paired["right.value"],
    method="pearson",
    by=(paired.axes[1],),
).compute()
temporal_association.show()
