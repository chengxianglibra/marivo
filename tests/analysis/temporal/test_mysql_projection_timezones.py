"""Native Ibis SQL retains conversion dependencies during zone inspection."""

import ibis
import pytest

from marivo.analysis.compiler.source_time import localize
from marivo.analysis.materialization.graph_source_execution import _mysql_source_timezones
from marivo.analysis.materialization.temporal_sql import lower_temporal


@pytest.mark.parametrize(
    "consumer", ["grouped_count", "distinct_count", "filtered_count", "raw_count"]
)
def test_generated_mysql_sql_keeps_required_conversion_zones(consumer: str) -> None:
    table = ibis.table({"clock": "timestamp", "id": "int64"}, name="events")
    projected = table.mutate(zoned=localize("America/New_York", table.clock))
    expression = (
        projected.group_by("zoned").aggregate().count()
        if consumer == "grouped_count"
        else projected.select("zoned").distinct().count()
        if consumer == "distinct_count"
        else projected.filter(projected.zoned.notnull()).count()
        if consumer == "filtered_count"
        else projected.count()
    ).name("n")
    sql = str(ibis.to_sql(lower_temporal(expression, "mysql"), dialect="mysql"))
    assert "CONVERT_TZ(" in sql
    if consumer == "grouped_count":
        assert "GROUP BY\n    1" in sql
    assert _mysql_source_timezones(sql) == (
        () if consumer == "raw_count" else ("+00:00", "America/New_York")
    )
    # Inspection must not mutate the actual generated query text.
    assert sql == str(ibis.to_sql(lower_temporal(expression, "mysql"), dialect="mysql"))
