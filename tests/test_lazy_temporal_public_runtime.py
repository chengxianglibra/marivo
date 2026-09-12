"""Public observation must preserve authored instants and report-day boundaries."""

from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("representation", ["declared_utc", "native_naive", "strptime"])
def test_report_day_buckets_preserve_declared_read_time_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, representation: str
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    database = tmp_path / "warehouse.duckdb"
    physical = "VARCHAR" if representation == "strptime" else "TIMESTAMP"
    with duckdb.connect(str(database)) as connection:
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
    data_type = "string" if representation == "strptime" else "timestamp(6)"
    parse = {
        "declared_utc": ", parse=ms.timestamp(timezone='UTC')",
        "native_naive": "",
        "strptime": ", parse=ms.strptime('%Y-%m-%d %H:%M:%S', timezone='UTC')",
    }[representation]
    (semantic / "events.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('events', columns={'id': md.source_column('id', data_type='int64'), "
        f"'happened_at': md.source_column('happened_at', data_type={data_type!r}), "
        "'amount': md.source_column('amount', data_type='float64')}), primary_key=['id'])\n"
        f"happened_at = ms.time_dimension_column(name='happened_at', entity=events, column='happened_at', granularity='second', is_default=True{parse})\n"
        "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity='additive')\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
    )
    monkeypatch.chdir(tmp_path)
    # The declared DuckDB source defaults to UTC for naive source timestamps.
    session = mv.session.get_or_create("report-days", report_timezone="Asia/Shanghai")
    logical = (
        session.observe(
            ms.ref.metric("sales.revenue"),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        )
        .with_time_axis(ms.ref.time_dimension("sales.events.happened_at"), grain=mv.grain("day"))
        .aggregate()
    )
    assert session.runs().items == ()
    result = logical.execute().to_pandas()
    observed = {
        str(day)[:10]: value
        for day, value in zip(result["happened_at"], result["revenue"], strict=True)
    }
    assert observed == {"2026-07-01": 1.0, "2026-07-02": 2.0}
