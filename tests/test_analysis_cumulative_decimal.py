"""DuckDB DECIMAL-backed cumulative observe regression (issue #111).

A DECIMAL(18,2) measure flowing through ``measure_column -> sum -> cumulative``
lands in the dense pandas post-processing as an object-dtype ``decimal.Decimal``
column, which pandas ``cumsum``/``rolling`` reject. This fixture proves the dense
paths (all-history, grain-to-date, trailing, and weighted-mean components)
establish a float64 numeric boundary before arithmetic and that genuinely
non-numeric values raise a structured ``DataTypeMismatchError``.
"""

from __future__ import annotations

from decimal import Decimal

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import DataTypeMismatchError
from marivo.analysis.intents._observe_dense import _coerce_numeric_value_series
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield


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
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "import marivo.analysis as mv\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
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
        "order_id INTEGER, created_at DATE, amount DECIMAL(18,2), user_id INTEGER"
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
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    return session_attach.get_or_create(
        name="cum_decimal", report_timezone="UTC", backends={"warehouse": lambda: con}
    )


def _by_day(frame):
    df = frame.to_pandas()
    measure_name = frame.meta.measure.get("name")
    if isinstance(measure_name, str) and measure_name in df.columns:
        df = df.rename(columns={measure_name: "value"})
    return {str(row.bucket_start.date()): row.value for row in df.itertuples()}


def test_coerce_numeric_value_series_normalizes_decimal():
    result = _coerce_numeric_value_series(
        pd.Series([Decimal("1.50"), Decimal("2.25")], dtype=object),
        location="cumulative trailing flow (value)",
    )
    assert result.dtype == "float64"
    assert result.tolist() == [1.5, 2.25]


def test_coerce_numeric_value_series_rejects_strings():
    with pytest.raises(DataTypeMismatchError) as exc_info:
        _coerce_numeric_value_series(
            pd.Series(["a", "b"], dtype=object),
            location="cumulative trailing flow (value)",
        )
    err = exc_info.value
    assert err.expected is not None and "float64" in err.expected
    assert err.received == "str"
    assert "cumulative trailing flow" in err.location
    assert err.repair is not None
    assert err.repair.kind == "inspect"


def test_decimal_all_history_cumulative_succeeds(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        grain=mv.grain("day"),
        session=session,
    )
    by_day = _by_day(frame)
    # June flow (4 + 6 = 10) is the all-history baseline; July flow accumulates.
    assert by_day == {
        "2026-07-01": pytest.approx(20.0),
        "2026-07-02": pytest.approx(37.0),
        "2026-07-03": pytest.approx(62.0),
    }
    assert frame.to_pandas()["cum_gmv"].dtype == "float64"


def test_decimal_grain_to_date_cumulative_succeeds(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = observe(
        make_ref("sales.mtd_gmv", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        grain=mv.grain("day"),
        session=session,
    )
    by_day = _by_day(frame)
    # July 1 is a month boundary, so no seed; month-to-date accumulates July.
    assert by_day == {
        "2026-07-01": pytest.approx(10.0),
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(52.0),
    }
    assert frame.to_pandas()["mtd_gmv"].dtype == "float64"


def test_decimal_grain_to_date_seeds_partial_first_period(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = observe(
        make_ref("sales.mtd_gmv", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-02", end="2026-07-04"),
        grain=mv.grain("day"),
        session=session,
    )
    by_day = _by_day(frame)
    # window.start (July 2) is not a month boundary: the partial first period
    # seed is July 1's flow (10), added to every July bucket.
    assert by_day == {
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(52.0),
    }
    assert frame.to_pandas()["mtd_gmv"].dtype == "float64"


def test_decimal_trailing_cumulative_succeeds(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = observe(
        make_ref("sales.trailing_2d_gmv", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        grain=mv.grain("day"),
        session=session,
    )
    by_day = _by_day(frame)
    # 2-day trailing span ending at each bucket end (current + previous day).
    assert by_day == {
        "2026-07-01": pytest.approx(10.0),
        "2026-07-02": pytest.approx(27.0),
        "2026-07-03": pytest.approx(42.0),
    }
    assert frame.to_pandas()["trailing_2d_gmv"].dtype == "float64"


def test_decimal_weighted_mean_cumulative_succeeds(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    frame = observe(
        make_ref("sales.cum_weighted_user", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        grain=mv.grain("day"),
        session=session,
    )
    by_day = _by_day(frame)
    # numerator/weight accumulate as DECIMAL, then divide; baseline June weight
    # is 4 + 6 = 10 and June numerator is 100*4 + 100*6 = 1000.
    assert by_day["2026-07-01"] == pytest.approx((1000 + 101 * 10) / (10 + 10))
    assert by_day["2026-07-02"] == pytest.approx(
        (1000 + 101 * 10 + 102 * 12 + 101 * 5) / (10 + 10 + 12 + 5)
    )
    assert frame.to_pandas()["cum_weighted_user"].dtype == "float64"
