"""Synthetic readings; values are additive fixture quantities in one unit."""

from ibis.common.deferred import Deferred

import marivo.datasource as md
import marivo.semantic as ms

ms.domain(name="sample", owner="Example Maintainer")
readings = ms.entity(
    name="readings",
    datasource=ms.ref.datasource("local"),
    source=md.table("readings"),
    primary_key=["reading_id"],
)
observed_at = ms.time_dimension_column(name="observed_at", entity=readings, column="observed_at")
group = ms.dimension_column(name="group", entity=readings, column="group_name")
value = ms.measure_column(
    name="value", entity=readings, column="value", additivity=ms.additive_all(), unit="unit"
)
total: Deferred = value.sum(time=observed_at)


@ms.metric()
def measurement() -> Deferred:
    return total
