"""Public observation must preserve authored instants and report-day boundaries."""

from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCase

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize(
    "representation", ["declared_utc", "native_naive", "native_aware", "strptime"]
)
def test_report_day_buckets_preserve_declared_read_time_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, representation: str
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    database = tmp_path / "warehouse.duckdb"
    physical = (
        "VARCHAR"
        if representation == "strptime"
        else "TIMESTAMPTZ"
        if representation == "native_aware"
        else "TIMESTAMP"
    )
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET TimeZone='UTC'")
        connection.execute(
            f"CREATE TABLE events (id BIGINT, happened_at {physical}, amount DOUBLE)"
        )
        connection.execute(
            "INSERT INTO events VALUES (1, '2026-07-01 15:59:00', 1.0), (2, '2026-07-01 16:01:00', 2.0)"
        )
    reader = ibis.duckdb.connect(str(database))
    try:
        assert reader.raw_sql("SELECT current_setting('TimeZone')").fetchone() == ("UTC",)
    finally:
        reader.disconnect()
    datasource = tmp_path / "models" / "datasources"
    semantic = tmp_path / "models" / "semantic" / "sales"
    datasource.mkdir(parents=True)
    semantic.mkdir(parents=True)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "temporal-cutover"\n')
    (datasource / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
    )
    parse = {
        "declared_utc": ", parse=ms.timestamp(timezone='UTC')",
        "native_naive": "",
        "native_aware": "",
        "strptime": ", parse=ms.strptime('%Y-%m-%d %H:%M:%S', timezone='UTC')",
    }[representation]
    (semantic / "events.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('events', columns={'id': 'id', "
        "'happened_at': 'happened_at', "
        "'amount': 'amount'}), primary_key=['id'])\n"
        f"happened_at = ms.time_dimension_column(name='happened_at', entity=events, column='happened_at', granularity='second', is_default=True{parse})\n"
        "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
    )
    monkeypatch.chdir(tmp_path)
    # The declared DuckDB source defaults to UTC for naive source timestamps.
    session = mv.session.get_or_create("report-days", report_timezone="Asia/Shanghai")
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-07-01", end="2026-07-03"), grain=mv.grain("day")
    )
    logical = (
        session.members(ms.ref.entity("sales.events"))
        .observe(ms.ref.metric("sales.revenue"), during=grid, by=(ms.ref.entity("sales.events"),))
        .group_by(grid)
        .rollup()
    )
    assert session.runs().items == ()
    result = logical.execute().to_pandas()
    assert result["value"].tolist() == [1.0, 2.0]
    assert result["group"].tolist() == ["2026-06-30T16:00:00+00:00", "2026-07-01T16:00:00+00:00"]


def test_changed_driver_timezone_rejects_before_source_read(
    retained_coordinates_case: DslCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from marivo.analysis.errors import AnalysisError
    from marivo.datasource import timezone as source_timezone
    from marivo.datasource.adapters import SourceSession

    case = retained_coordinates_case
    logical = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.entity("sales.customer"),),
    )
    probe = source_timezone.probe_engine_timezone

    def changed(backend: object) -> source_timezone.DatasourceEngineTimezone:
        return replace(probe(backend), engine_timezone_name="Asia/Shanghai")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("changed timezone submitted a business read")

    monkeypatch.setattr(source_timezone, "probe_engine_timezone", changed)
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    with pytest.raises(AnalysisError, match="timezone changed"):
        logical.execute()
    assert all(run.lifecycle != "succeeded" for run in case.session.runs().items)
