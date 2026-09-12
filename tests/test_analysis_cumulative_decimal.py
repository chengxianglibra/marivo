"""Exact Decimal cumulative values and weighted components survive publication."""

from decimal import Decimal

import ibis
import pytest

import marivo.analysis as mv
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref

pytestmark = pytest.mark.runtime


def _bootstrap_project(tmp_path) -> None:
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
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders', columns={'order_id': md.source_column('order_id', data_type='int64'), 'created_at': md.source_column('created_at', data_type='date'), 'amount': md.source_column('amount', data_type='decimal(18, 2)'), 'user_id': md.source_column('user_id', data_type='int64')}), primary_key=['order_id'])\n"
        "order_date = ms.time_dimension_column("
        "name='order_date', entity=orders, column='created_at', granularity='day')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive', unit='USD')\n"
        "user_id = ms.measure_column("
        "name='user_id', entity=orders, column='user_id', additivity='non_additive')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=order_date)\n"
        "mtd_gmv = ms.cumulative(name='mtd_gmv', base=gmv, over=order_date, "
        "anchor=ms.grain_to_date(grain=mv.grain('month')))\n"
        "trailing_2d_gmv = ms.cumulative(name='trailing_2d_gmv', base=gmv, "
        "over=order_date, anchor=ms.trailing(count=2, unit='day'))\n"
        "weighted_user = ms.weighted_mean(name='weighted_user', value=user_id, weight=amount)\n"
        "cum_weighted_user = ms.cumulative("
        "name='cum_weighted_user', base=weighted_user, over=order_date)\n",
        encoding="utf-8",
    )


def _seed(con) -> None:
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id BIGINT, created_at DATE, amount DECIMAL(18,2), user_id BIGINT"
        ")"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-06-01', 4.00, 100),"
        "(2, DATE '2026-06-02', 6.00, 100),"
        "(3, DATE '2026-07-01', 10.00, 101),"
        "(4, DATE '2026-07-02', 12.00, 102),"
        "(5, DATE '2026-07-02', 5.00, 101),"
        "(6, DATE '2026-07-03', 18.00, 103),"
        "(7, DATE '2026-07-03', 7.00, 102)"
    )


def _session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    con = ibis.duckdb.connect(str(tmp_path / "warehouse.duckdb"))
    try:
        _seed(con)
    finally:
        con.disconnect()
    return mv.session.get_or_create("cum_decimal", report_timezone="UTC")


def _by_day(frame):
    rows = frame.to_pandas()
    name = frame.schema.columns[-1].name
    if frame.schema.columns[-1].logical_type_id == "decimal":
        assert all(isinstance(value, Decimal) for value in rows[name])
    return {str(day): value for day, value in zip(rows["order_date"], rows[name], strict=True)}


def test_decimal_all_history_cumulative_succeeds(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = (
        session.observe(
            make_ref("sales.cum_gmv", SemanticKind.METRIC),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        )
        .with_time_axis(
            make_ref("sales.orders.order_date", SemanticKind.TIME_DIMENSION), grain=mv.grain("day")
        )
        .aggregate()
        .execute()
    )
    by_day = _by_day(frame)
    # June flow (4 + 6 = 10) is the all-history baseline; July flow accumulates.
    assert by_day == {
        "2026-07-01": pytest.approx(20.0),
        "2026-07-02": pytest.approx(37.0),
        "2026-07-03": pytest.approx(62.0),
    }
    assert frame.schema.columns[-1].logical_type_id == "decimal"


def test_decimal_grain_to_date_cumulative_succeeds(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = (
        session.observe(
            make_ref("sales.mtd_gmv", SemanticKind.METRIC),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        )
        .with_time_axis(
            make_ref("sales.orders.order_date", SemanticKind.TIME_DIMENSION), grain=mv.grain("day")
        )
        .aggregate()
        .execute()
    )
    by_day = _by_day(frame)
    # July 1 is a month boundary, so no seed; month-to-date accumulates July.
    assert by_day == {
        "2026-07-01": pytest.approx(10.0),
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(52.0),
    }
    assert frame.schema.columns[-1].logical_type_id == "decimal"


def test_decimal_grain_to_date_seeds_partial_first_period(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = (
        session.observe(
            make_ref("sales.mtd_gmv", SemanticKind.METRIC),
            time_scope=mv.time_scope(start="2026-07-02", end="2026-07-04"),
        )
        .with_time_axis(
            make_ref("sales.orders.order_date", SemanticKind.TIME_DIMENSION), grain=mv.grain("day")
        )
        .aggregate()
        .execute()
    )
    by_day = _by_day(frame)
    # window.start (July 2) is not a month boundary: the partial first period
    # seed is July 1's flow (10), added to every July bucket.
    assert by_day == {
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(52.0),
    }
    assert frame.schema.columns[-1].logical_type_id == "decimal"


def test_decimal_trailing_cumulative_succeeds(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = (
        session.observe(
            make_ref("sales.trailing_2d_gmv", SemanticKind.METRIC),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        )
        .with_time_axis(
            make_ref("sales.orders.order_date", SemanticKind.TIME_DIMENSION), grain=mv.grain("day")
        )
        .aggregate()
        .execute()
    )
    by_day = _by_day(frame)
    # 2-day trailing span ending at each bucket end (current + previous day).
    assert by_day == {
        "2026-07-01": pytest.approx(10.0),
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(42.0),
    }
    assert frame.schema.columns[-1].logical_type_id == "decimal"


def test_decimal_weighted_mean_cumulative_succeeds(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = (
        session.observe(
            make_ref("sales.cum_weighted_user", SemanticKind.METRIC),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        )
        .with_time_axis(
            make_ref("sales.orders.order_date", SemanticKind.TIME_DIMENSION), grain=mv.grain("day")
        )
        .aggregate()
        .execute()
    )
    by_day = _by_day(frame)
    # numerator/weight accumulate as DECIMAL, then divide; baseline June weight
    # is 4 + 6 = 10 and June numerator is 100*4 + 100*6 = 1000.
    assert by_day["2026-07-01"] == pytest.approx((1000 + 101 * 10) / (10 + 10))
    assert by_day["2026-07-02"] == pytest.approx(
        (1000 + 101 * 10 + 102 * 12 + 101 * 5) / (10 + 10 + 12 + 5)
    )
    assert frame.schema.columns[-1].logical_type_id == "float64"
