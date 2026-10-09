"""Exact Decimal cumulative values and weighted components survive publication."""

from decimal import Decimal
from pathlib import Path

import ibis
import pytest
from ibis.backends.duckdb import Backend

import marivo.analysis as mv
import marivo.semantic as ms

pytestmark = pytest.mark.runtime


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
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=order_date)\n"
        "mtd_gmv = ms.cumulative(name='mtd_gmv', base=gmv, over=order_date, "
        "anchor=ms.grain_to_date(grain=mv.grain('month')))\n"
        "trailing_2d_gmv = ms.cumulative(name='trailing_2d_gmv', base=gmv, "
        "over=order_date, anchor=ms.trailing(count=2, unit='day'))\n"
        "decimal_user_id = ms.measure_column(name='decimal_user_id', entity=orders, column='decimal_user_id', additivity=ms.non_additive())\n"
        "decimal_amount = ms.measure_column(name='decimal_amount', entity=orders, column='decimal_amount', additivity=ms.additive_all(), unit='USD')\n"
        "weighted_user = ms.weighted_mean(name='weighted_user', value=decimal_user_id, weight=decimal_amount)\n"
        "cum_weighted_user = ms.cumulative("
        "name='cum_weighted_user', base=weighted_user, over=order_date)\n",
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


def _by_day(frame: mv.MaterializedGroupedNumericRelation) -> dict[str, Decimal | float | int]:
    rows = frame.to_pandas()
    value_type = next(
        column.logical_type_id
        for column in frame.state.realized_schema.columns
        if column.name == "value"
    )
    if value_type.startswith("decimal:"):
        assert all(isinstance(value, Decimal) for value in rows["value"])
    return {str(day)[:10]: value for day, value in zip(rows["group"], rows["value"], strict=True)}


def _observe(
    session: mv.Session, metric: str, start: str = "2026-07-01", end: str = "2026-07-04"
) -> mv.MaterializedGroupedNumericRelation:
    grid = mv.time_grid(during=mv.time_scope(start=start, end=end), grain=mv.grain("day"))
    result = (
        session.members(ms.ref.entity("sales.orders"))
        .observe(ms.ref.metric(metric), at=grid.end, by=(mv.member(),))
        .group_by(grid)
        .rollup()
        .execute()
    )

    assert isinstance(result, mv.MaterializedGroupedNumericRelation)
    return result


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
    assert (
        next(
            column.logical_type_id
            for column in frame.state.realized_schema.columns
            if column.name == "value"
        )
        == "decimal:38:2"
    )


def test_decimal_grain_to_date_cumulative_succeeds(
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
    assert (
        next(
            column.logical_type_id
            for column in frame.state.realized_schema.columns
            if column.name == "value"
        )
        == "decimal:38:2"
    )


def test_decimal_grain_to_date_seeds_partial_first_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session, "sales.mtd_gmv", "2026-07-02", "2026-07-04")
    by_day = _by_day(frame)
    # window.start (July 2) is not a month boundary: the partial first period
    # seed is July 1's flow (10), added to every July bucket.
    assert by_day == {
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(52.0),
    }
    assert (
        next(
            column.logical_type_id
            for column in frame.state.realized_schema.columns
            if column.name == "value"
        )
        == "decimal:38:2"
    )


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
    assert (
        next(
            column.logical_type_id
            for column in frame.state.realized_schema.columns
            if column.name == "value"
        )
        == "decimal:38:2"
    )


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
    assert (
        next(
            column.logical_type_id
            for column in frame.state.realized_schema.columns
            if column.name == "value"
        )
        == "float64"
    )
