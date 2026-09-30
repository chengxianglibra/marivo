"""Exact Decimal publication and mixed numeric RuntimeMetric composition."""

from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError

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
        "source=md.table('orders', columns={'id': 'id', "
        "'day': 'day', "
        "'amount': 'amount', "
        "'fee': 'fee', 'decimal_fee': 'decimal_fee'}), primary_key=['id'])\n"
        "day = ms.time_dimension_column(name='day', entity=orders, column='day', granularity='day', is_default=True)\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
        "fee_value = ms.measure_column(name='fee_value', entity=orders, column='fee', additivity=ms.additive_all())\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "fee = ms.aggregate(name='fee', measure=fee_value, agg='sum')\n"
        "decimal_fee = ms.measure_column(name='decimal_fee', entity=orders, column='decimal_fee', additivity=ms.additive_all())\n"
        "normalized_fee = ms.aggregate(name='normalized_fee', measure=decimal_fee, agg='sum')\n"
    )
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, day DATE, amount DECIMAL(12,2), fee DOUBLE, decimal_fee DECIMAL(12,2))"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'2026-07-01',15.75,2.0,2.00), (2,'2026-07-01',4.25,3.0,3.00)"
        )
    return mv.session.get_or_create("decimal", report_timezone="UTC")


def test_decimal_measure_and_retained_schema_agree(decimal_session: mv.Session) -> None:
    result = (
        decimal_session.members(ms.ref.entity("sales.orders"))
        .observe(
            ms.ref.metric("sales.gmv"), during=mv.time_scope(start="2026-07-01", end="2026-07-02")
        )
        .rollup()
        .execute()
    )
    rows = result.to_pandas()
    assert isinstance(rows["value"].iloc[0], Decimal)
    assert rows["value"].iloc[0] == Decimal("20.00")
    assert (
        next(
            column for column in result.state.realized_schema.columns if column.name == "value"
        ).logical_type_id
        == "decimal:38:2"
    )
    assert Decimal("100.00") - rows["value"].iloc[0] == Decimal("80.00")


def test_runtime_ratio_requires_explicit_decimal_normalization(
    decimal_session: mv.Session,
) -> None:
    expression = mv.runtime_metric.ratio(
        ms.ref.metric("sales.fee"), ms.ref.metric("sales.gmv"), label="fee_rate"
    )
    members = decimal_session.members(ms.ref.entity("sales.orders"))
    scope = mv.time_scope(start="2026-07-01", end="2026-07-02")
    with pytest.raises(AnalysisError):
        members.observe(expression, during=scope).rollup().execute()
    expression = mv.runtime_metric.ratio(
        ms.ref.metric("sales.normalized_fee"), ms.ref.metric("sales.gmv"), label="fee_rate"
    )
    result = (
        decimal_session.members(ms.ref.entity("sales.orders"))
        .observe(expression, during=mv.time_scope(start="2026-07-01", end="2026-07-02"))
        .rollup()
        .execute()
    )
    assert result.to_pandas()["value"].tolist() == [Decimal("0.25")]
    assert (
        next(
            column for column in result.state.realized_schema.columns if column.name == "value"
        ).logical_type_id
        == "decimal:38:6"
    )
