"""Public observation must preserve authored instants and report-day boundaries."""

from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs


@pytest.mark.parametrize("representation", ["declared_utc", "native_naive", "strptime"])
def test_report_day_buckets_preserve_declared_read_time_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, representation: str
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    database = tmp_path / "warehouse.duckdb"
    physical = "VARCHAR" if representation == "strptime" else "TIMESTAMP"
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
        "strptime": ", parse=ms.strptime('%Y-%m-%d %H:%M:%S', timezone='UTC')",
    }[representation]
    (semantic / "events.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('events', columns={'id': 'id', "
        "'happened_at': 'happened_at', "
        "'amount': 'amount'}), primary_key=['id'])\n"
        f"happened_at = ms.time_dimension_column(name='happened_at', entity=events, column='happened_at', is_default=True{parse})\n"
        "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())\n"
        ""
        "amount_value = amount\n"
        "time_value = happened_at\n"
        "@ms.metric()\n"
        "def revenue():\n"
        "    return amount_value.sum(time=time_value)\n"
    )
    monkeypatch.chdir(tmp_path)
    # The declared DuckDB source defaults to UTC for naive source timestamps.
    session = mv.session.get_or_create("report-days", timezone="Asia/Shanghai")
    grid = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
    logical = session.observe({"value": session.catalog.metrics.get("sales.revenue")}, by=(grid,))
    assert terminal_runs(session) == ()
    result = logical.compute().to_pandas()
    assert result["value"].tolist() == [1.0, 2.0]
    assert result["calendar:day"].tolist() == ["2026-07-01", "2026-07-02"]
    assert terminal_runs(session) == ()
    assert [
        value.isoformat()
        for value in result.attrs["frozen_coordinates"][0]["rows"]["nominal_start"]
    ] == [
        "2026-06-30T16:00:00+00:00",
        "2026-07-01T16:00:00+00:00",
    ]
    assert result.attrs["axes"][0]["descriptor"]["timezone"] == "Asia/Shanghai"
