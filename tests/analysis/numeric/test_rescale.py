"""Real local execution and stored values for explicit semantic rescaling."""

from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.session.core import Session
from tests.shared_fixtures import catalog_value

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
flows = ms.entity(name="flows", datasource=ms.ref.datasource("warehouse"), source=md.table("flows"), primary_key=["id"])
category = ms.dimension_column(name="category", entity=flows, column="category")
day = ms.time_dimension_column(name="day", entity=flows, column="day", is_default=True)
total_in_flow_value = ms.measure_column(name="total_in_flow_value", entity=flows, column="bytes", unit="By", additivity=ms.additive_all())
total_in_p95_flow_value = ms.measure_column(name="total_in_p95_flow_value", entity=flows, column="p95_bytes", unit="By", additivity=ms.additive_all())
amount = ms.measure_column(name="amount", entity=flows, column="amount", unit="CNY", additivity=ms.additive_all())
fixed = ms.measure_column(name="fixed", entity=flows, column="bytes", unit="By", additivity=ms.additive_all(except_=(day,)), status_time_dimension=day)
@ms.metric()
def daily_bandwidth():
    return ms.rescale(total_in_flow_value.sum(), divisor=86400 * 1024**3, unit="GiB/s")
@ms.metric()
def hourly_p95_bandwidth():
    return ms.rescale(total_in_p95_flow_value.sum(), divisor=3600 * 1024**3, unit="GiB/s")
@ms.measure()
def row_daily_bandwidth():
    return ms.rescale(total_in_flow_value, divisor=86400 * 1024**3, unit="GiB/s")
"""


@pytest.fixture
def traffic_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    (tmp_path / "marivo.toml").write_text('[project]\nname="rescale"\n')
    datasource = tmp_path / "models/datasources"
    datasource.mkdir(parents=True)
    source_path = tmp_path / "traffic.duckdb"
    (datasource / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(source_path)!r})\n"
    )
    semantic = tmp_path / "models/semantic/traffic"
    semantic.mkdir(parents=True)
    (semantic / "_domain.py").write_text(
        'import marivo.semantic as ms\nms.domain(name="traffic", owner="Analytics")\n'
    )
    (semantic / "flows.py").write_text(_MODEL)
    with duckdb.connect(str(source_path)) as connection:
        connection.execute(
            "CREATE TABLE flows(id INTEGER, category VARCHAR, day DATE, bytes BIGINT, "
            "p95_bytes BIGINT, amount DECIMAL(8,2))"
        )
        # Daily source amounts encode two and three GiB/s; hourly P95 amounts
        # encode seven and eleven GiB/s. The third group contains present NULLs.
        connection.execute(
            "INSERT INTO flows VALUES "
            "(1, 'present', '2026-10-01', 185542587187200, 27058293964800, 1.25),"
            "(2, 'present', '2026-10-01', 278313880780800, 42520176230400, 2.75),"
            "(3, 'null', '2026-10-01', NULL, NULL, NULL)"
        )
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    return mv.session.get_or_create("rescale", timezone="UTC")


def test_bandwidth_formulas_named_local_and_row_execution_keep_values_and_units(
    traffic_session: Session,
) -> None:
    session = traffic_session
    catalog = session.catalog
    daily = catalog_value(catalog, "traffic.flows.total_in_flow_value", kind="field")
    hourly = catalog_value(catalog, "traffic.flows.total_in_p95_flow_value", kind="field")
    group = catalog_value(catalog, "traffic.flows.category", kind="field")
    metrics = {
        "daily": catalog.get("metric:traffic.daily_bandwidth").value,
        "hourly": catalog.get("metric:traffic.hourly_p95_bandwidth").value,
        "local_daily": ms.rescale(daily.sum(), divisor=86400 * 1024**3, unit="GiB/s"),
        "local_hourly": ms.rescale(hourly.sum(), divisor=3600 * 1024**3, unit="GiB/s"),
        "row_daily": catalog_value(
            catalog, "traffic.flows.row_daily_bandwidth", kind="field"
        ).sum(),
        "combined_rows": (
            ms.rescale(daily, divisor=86400 * 1024**3, unit="GiB/s")
            + ms.rescale(hourly, divisor=3600 * 1024**3, unit="GiB/s")
        ).sum(),
        "ordinary": daily.sum() / (86400 * 1024**3),
    }
    saved = session.observe(metrics, by=(group,)).persist()
    saved_data = saved.to_pandas()
    result = saved_data.set_index(saved_data.columns[0])
    for name in ("daily", "local_daily", "row_daily", "ordinary"):
        assert result.loc["present", name] == 5.0
    for name in ("hourly", "local_hourly"):
        assert result.loc["present", name] == 18.0
    assert result.loc["present", "combined_rows"] == 23.0
    assert all(pd.isna(result.loc["null", name]) for name in metrics)
    assert {column.name: column.unit for column in saved._schema.values} == {
        name: "By" if name == "ordinary" else "GiB/s" for name in metrics
    }
    assert catalog.get("measure:traffic.flows.total_in_flow_value").definition.unit == "By"
    reopened = session.persisted_frame(saved.artifact.id)
    assert reopened.to_pandas().equals(saved_data)
    assert reopened._schema.values == saved._schema.values


def test_rescale_decimal_division_and_divisor_one_keep_existing_numeric_null_rules(
    traffic_session: Session,
) -> None:
    session = traffic_session
    amount = catalog_value(session.catalog, "traffic.flows.amount", kind="field")
    group = catalog_value(session.catalog, "traffic.flows.category", kind="field")
    frame = session.observe(
        {
            "row": ms.rescale(amount, divisor=Decimal("0.1"), unit="scaled_CNY").sum(),
            "metric": ms.rescale(amount.sum(), divisor=Decimal("0.1"), unit="scaled_CNY"),
            "ordinary": (amount / Decimal("0.1")).sum(),
            "assertion": ms.rescale(amount.sum(), divisor=1, unit="other_dimension"),
        },
        by=(group,),
    ).compute()
    data = frame.to_pandas()
    result = data.set_index(data.columns[0])
    assert result.loc["present", "row"] == 40.0
    assert result.loc["present", "metric"] == 40.0
    assert result.loc["present", "ordinary"] == 40.0
    assert result.loc["present", "assertion"] == 4.0
    assert all(
        pd.isna(result.loc["null", name]) for name in ("row", "metric", "ordinary", "assertion")
    )
    assert {column.name: column.unit for column in frame._schema.values} == {
        "row": "scaled_CNY",
        "metric": "scaled_CNY",
        "ordinary": "CNY",
        "assertion": "other_dimension",
    }
