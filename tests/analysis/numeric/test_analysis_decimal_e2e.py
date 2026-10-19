"""Exact Decimal publication and mixed native Metric composition."""

from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.artifact_data import DataSchema
from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType


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
        "decimal_fee = ms.measure_column(name='decimal_fee', entity=orders, column='decimal_fee', additivity=ms.additive_all())\n"
        "finalized = ms.finalize()\n"
        "amount_value = finalized.resolve(amount)\n"
        "fee_row = finalized.resolve(fee_value)\n"
        "decimal_fee_row = finalized.resolve(decimal_fee)\n"
        "time_value = finalized.resolve(day)\n"
        "@ms.metric()\n"
        "def gmv():\n"
        "    return amount_value.sum(time=time_value)\n"
        "@ms.metric()\n"
        "def fee():\n"
        "    return fee_row.sum(time=time_value)\n"
        "@ms.metric()\n"
        "def normalized_fee():\n"
        "    return decimal_fee_row.sum(time=time_value)\n"
    )
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, day DATE, amount DECIMAL(12,2), fee DOUBLE, decimal_fee DECIMAL(12,2))"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'2026-07-01',15.75,2.0,2.00), (2,'2026-07-01',4.25,3.0,3.00)"
        )
    return mv.session.get_or_create("decimal", report_timezone="UTC")


def _observed(session: mv.Session, *, overall: bool = False) -> mv.AnalysisFrame:
    return session.observe(
        {
            "gmv": session.catalog.metrics.get("sales.gmv"),
            "fee": session.catalog.metrics.get("sales.fee"),
        },
        during=session.time_scope(start="2026-07-01", end="2026-07-02"),
        by=() if overall else session.members(ms.ref.entity("sales.orders")),
    )


def test_decimal_measure_and_retained_schema_agree(decimal_session: mv.Session) -> None:
    result = _observed(decimal_session, overall=True).compute()
    rows = result.to_pandas()
    assert isinstance(rows[("value", "gmv")].iloc[0], Decimal)
    assert rows[("value", "gmv")].iloc[0] == Decimal("20.00")
    assert isinstance(result._schema, DataSchema)
    assert result._schema.values[0].dtype == DecimalDataType(38, 2)
    assert Decimal("100.00") - rows[("value", "gmv")].iloc[0] == Decimal("80.00")


def test_ratio_accepts_independent_numeric_components(decimal_session: mv.Session) -> None:
    session = decimal_session
    gmv = session.catalog.metrics.get("sales.gmv")
    fee = session.catalog.metrics.get("sales.fee")
    normalized_fee = session.catalog.metrics.get("sales.normalized_fee")
    for numerator in (fee, normalized_fee):
        observed = session.observe(
            {"rate": numerator / gmv},
            during=session.time_scope(start="2026-07-01", end="2026-07-02"),
        )
        result = observed.compute()
        assert isinstance(result._schema, DataSchema)
        assert result.to_pandas()[("value", "rate")].tolist() == [0.25]
        assert isinstance(result._schema, DataSchema)
        assert result._schema.values[0].dtype == ScalarDataType("float64")
    components = _observed(session).compute().to_pandas()
    assert components[("value", "fee")].sum() / float(components[("value", "gmv")].sum()) == 0.25


@pytest.mark.parametrize("exported", [False, True])
def test_mixed_linear_retains_each_component(decimal_session: mv.Session, exported: bool) -> None:
    observed = _observed(decimal_session)
    if exported:
        frame = observed.compute().to_pandas()
        net = frame[("value", "gmv")].map(float) - frame[("value", "fee")]
        assert net.sum() == 15.0
    else:
        totals = _observed(decimal_session, overall=True)
        result = totals.select({"net": totals["gmv"] - totals["fee"]}).compute()
        assert result.to_pandas()[("value", "net")].tolist() == [15.0]
