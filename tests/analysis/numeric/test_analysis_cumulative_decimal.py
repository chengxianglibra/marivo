"""Exact Decimal cumulative values and weighted components survive publication."""

from decimal import Decimal
from pathlib import Path

import ibis
import pytest
from ibis.backends.duckdb import Backend

import marivo.analysis as mv
from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType


def _bootstrap_project(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Data')\n",
        encoding="utf-8",
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='warehouse.duckdb')\n",
        encoding="utf-8",
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "import marivo.analysis as mv\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders', columns={'order_id': 'order_id', 'created_at': 'created_at', 'amount': 'amount', 'user_id': 'user_id', 'decimal_user_id': 'decimal_user_id', 'decimal_amount': 'decimal_amount'}), primary_key=['order_id'])\n"
        "order_date = ms.time_dimension_column("
        "name='order_date', entity=orders, column='created_at', granularity='day', is_default=True)\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='USD')\n"
        "user_id = ms.measure_column("
        "name='user_id', entity=orders, column='user_id', additivity=ms.non_additive())\n"
        "decimal_user_id = ms.measure_column(name='decimal_user_id', entity=orders, column='decimal_user_id', additivity=ms.non_additive())\n"
        "decimal_amount = ms.measure_column(name='decimal_amount', entity=orders, column='decimal_amount', additivity=ms.additive_all(), unit='USD')\n"
        "finalized = ms.finalize()\n"
        "amount_value = finalized.resolve(amount)\n"
        "user_value = finalized.resolve(decimal_user_id)\n"
        "weight_value = finalized.resolve(decimal_amount)\n"
        "time_value = finalized.resolve(order_date)\n"
        "history = mv.all_history()\n"
        "month_rule = mv.to_date(grain=mv.grain('month'))\n"
        "from datetime import timedelta\n"
        "trailing_rule = mv.trailing_duration(duration=timedelta(days=2))\n"
        "@ms.metric()\n"
        "def gmv():\n"
        "    return amount_value.sum(time=time_value)\n"
        "@ms.metric()\n"
        "def cum_gmv():\n"
        "    return gmv.window(history)\n"
        "@ms.metric()\n"
        "def mtd_gmv():\n"
        "    return gmv.window(month_rule)\n"
        "@ms.metric()\n"
        "def trailing_2d_gmv():\n"
        "    return gmv.window(trailing_rule)\n"
        "@ms.metric()\n"
        "def weighted_user():\n"
        "    return user_value.weighted_mean(weight=weight_value, time=time_value)\n"
        "@ms.metric()\n"
        "def cum_weighted_user():\n"
        "    return weighted_user.window(history)\n",
        encoding="utf-8",
    )


def _seed(con: Backend) -> None:
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id BIGINT, created_at DATE, amount DECIMAL(18,2), user_id BIGINT, decimal_user_id DECIMAL(18,6)"
        ")"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-06-01', 4.00, 100, 100.000000),"
        "(2, DATE '2026-06-02', 6.00, 100, 100.000000),"
        "(3, DATE '2026-07-01', 10.00, 101, 101.000000),"
        "(4, DATE '2026-07-02', 12.00, 102, 102.000000),"
        "(5, DATE '2026-07-02', 5.00, 101, 101.000000),"
        "(6, DATE '2026-07-03', 18.00, 103, 103.000000),"
        "(7, DATE '2026-07-03', 7.00, 102, 102.000000)"
    )

    con.raw_sql("ALTER TABLE orders ADD COLUMN decimal_amount DECIMAL(18,6)")
    con.raw_sql("UPDATE orders SET decimal_amount = amount")


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    con = ibis.duckdb.connect(str(tmp_path / "warehouse.duckdb"))
    try:
        _seed(con)
    finally:
        con.disconnect()
    return mv.session.get_or_create("cum_decimal", report_timezone="UTC")


def _by_day(frame: mv.Artifact) -> dict[str, Decimal | float | int]:
    rows = frame.to_pandas()
    if isinstance(frame._manifest.data.schema.values[0].dtype, DecimalDataType):
        assert all(isinstance(value, Decimal) for value in rows[("value", "value")])
    return {
        str(day)[:10]: value
        for day, value in zip(rows.iloc[:, 0], rows[("value", "value")], strict=True)
    }


def _observe(
    session: mv.Session, metric: str, start: str = "2026-07-01", end: str = "2026-07-04"
) -> mv.Artifact:
    return session.observe(
        {"value": session.catalog.metrics.get(metric)},
        during=session.time_scope(start=start, end=end).by(mv.grain("day")),
    ).execute()


def test_decimal_all_history_cumulative_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, "sales.cum_gmv", "2026-07-01", "2026-07-04")
    by_day = _by_day(frame)
    # June flow (4 + 6 = 10) is the all-history baseline; July flow accumulates.
    assert by_day == {
        "2026-07-01": pytest.approx(20.0),
        "2026-07-02": pytest.approx(37.0),
        "2026-07-03": pytest.approx(62.0),
    }
    assert frame._manifest.data.schema.values[0].dtype == DecimalDataType(38, 2)


def test_decimal_grain_to_date_cumulative_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, "sales.mtd_gmv", "2026-07-01", "2026-07-04")
    by_day = _by_day(frame)
    # July 1 is a month boundary, so no seed; month-to-date accumulates July.
    assert by_day == {
        "2026-07-01": pytest.approx(10.0),
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(52.0),
    }
    assert frame._manifest.data.schema.values[0].dtype == DecimalDataType(38, 2)

    partial_frame = _observe(session, "sales.mtd_gmv", "2026-07-02", "2026-07-04")
    partial_by_day = _by_day(partial_frame)
    # window.start (July 2) is not a month boundary: the partial first period
    # seed is July 1's flow (10), added to every July bucket.
    assert partial_by_day == {
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(52.0),
    }


def test_decimal_trailing_cumulative_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, "sales.trailing_2d_gmv", "2026-07-01", "2026-07-04")
    by_day = _by_day(frame)
    # 2-day trailing span ending at each bucket end (current + previous day).
    assert by_day == {
        "2026-07-01": pytest.approx(10.0),
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(42.0),
    }
    assert frame._manifest.data.schema.values[0].dtype == DecimalDataType(38, 2)


def test_decimal_weighted_mean_cumulative_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, "sales.cum_weighted_user", "2026-07-01", "2026-07-04")
    by_day = _by_day(frame)
    # numerator/weight accumulate as DECIMAL, then divide; baseline June weight
    # is 4 + 6 = 10 and June numerator is 100*4 + 100*6 = 1000.
    assert by_day["2026-07-01"] == Decimal("100.500000")
    assert by_day["2026-07-02"] == pytest.approx(3739 / 37)
    assert frame._manifest.data.schema.values[0].dtype == ScalarDataType("float64")
