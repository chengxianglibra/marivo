"""Exact Decimal publication and mixed numeric RuntimeMetric composition."""

from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms

pytestmark = pytest.mark.runtime


@pytest.fixture
def decimal_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "decimal"\n')
    datasource = tmp_path / "models" / "datasources"
    semantic = tmp_path / "models" / "semantic" / "sales"
    datasource.mkdir(parents=True)
    semantic.mkdir(parents=True)
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='warehouse.duckdb')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
    )
    (semantic / "orders.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders', columns={'id': md.source_column('id', data_type='int64'), "
        "'day': md.source_column('day', data_type='date'), "
        "'amount': md.source_column('amount', data_type='decimal(12,2)'), "
        "'fee': md.source_column('fee', data_type='float64')}), primary_key=['id'])\n"
        "day = ms.time_dimension_column(name='day', entity=orders, column='day', granularity='day')\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity='additive')\n"
        "fee_value = ms.measure_column(name='fee_value', entity=orders, column='fee', additivity='additive')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "fee = ms.aggregate(name='fee', measure=fee_value, agg='sum')\n"
    )
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, day DATE, amount DECIMAL(12,2), fee DOUBLE)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'2026-07-01',15.75,2.0), (2,'2026-07-01',4.25,3.0)"
        )
    return mv.session.get_or_create("decimal", report_timezone="UTC")


def test_decimal_measure_and_retained_schema_agree(decimal_session: mv.Session) -> None:
    result = (
        decimal_session.observe(
            ms.ref.metric("sales.gmv"),
            time_scope=mv.time_scope(start="2026-07-01", end="2026-07-02"),
        )
        .with_time_axis(ms.ref.time_dimension("sales.orders.day"), grain=mv.grain("day"))
        .aggregate()
        .execute()
    )
    rows = result.to_pandas()
    assert isinstance(rows["gmv"].iloc[0], Decimal)
    assert rows["gmv"].iloc[0] == Decimal("20.00")
    assert result.schema.columns[-1].logical_type_id == "decimal"
    assert Decimal("100.00") - rows["gmv"].iloc[0] == Decimal("80.00")


def test_runtime_ratio_composes_float_metric_with_decimal_metric(
    decimal_session: mv.Session,
) -> None:
    expression = mv.runtime_metric.ratio(
        ms.ref.metric("sales.fee"), ms.ref.metric("sales.gmv"), label="fee_rate"
    )
    result = decimal_session.observe(expression).aggregate().execute()
    assert result.to_pandas()["fee_rate"].tolist() == pytest.approx([0.25])
    assert result.schema.columns[-1].logical_type_id == "float64"
