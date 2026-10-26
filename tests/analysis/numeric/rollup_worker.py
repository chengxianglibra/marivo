"""Explicit source aggregation publication and independent terminal cold reads."""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import terminal_runs
from tests.support.json import Json, obj, read

DAY_ROWS = (
    (1, "2026-07-01", "10.05", "2.50", 3),
    (2, "2026-07-01", "20.10", "1.25", 1),
    (3, "2026-07-02", "23.99", "0.10", 1),
)
METRIC_NAMES = ("net_amount", "net_qty", "amount_mean")
# Independent month totals over DAY_ROWS: gmv=54.14, cost=3.85, qty=5,
# count=3. The bounded Decimal add/subtract chain reduces scale to zero, yielding
# net_amount=4; qty+count-qty=3 and mean(qty)=5/3.
MONTH_EXPECTED = {
    "net_amount": "4",
    "net_qty": "3",
    "amount_mean": repr(5 / 3),
}


def _author(project: Path, *, mean_measure: str = "quantity") -> None:
    (project / "marivo.toml").write_text('[project]\nname = "rollup-equation"\n')
    datasource = project / "models" / "datasources"
    semantic = project / "models" / "semantic" / "sales"
    datasource.mkdir(parents=True)
    semantic.mkdir(parents=True)
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='warehouse.duckdb')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
    )
    (semantic / "orders.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders', columns={'id': 'id', 'order_day': 'day', "
        "'amount': 'amount', 'cost': 'cost', 'quantity': 'quantity'}), primary_key=['id'])\n"
        "day = ms.time_dimension_column(name='day', entity=orders, column='order_day', "
        "parse=None, is_default=True)\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', "
        "additivity=ms.additive_all())\n"
        "cost = ms.measure_column(name='cost', entity=orders, column='cost', "
        "additivity=ms.additive_all())\n"
        "quantity = ms.measure_column(name='quantity', entity=orders, column='quantity', "
        "additivity=ms.additive_all())\n"
        ""
        "orders_value = orders\n"
        "day_value = day\n"
        "amount_value = amount\n"
        "cost_value = cost\n"
        "quantity_value = quantity\n"
        "@ms.metric()\ndef gmv():\n    return amount_value.sum(time=day_value)\n"
        "@ms.metric()\ndef cost_total():\n    return cost_value.sum(time=day_value)\n"
        "@ms.metric()\ndef qty_total():\n    return quantity_value.sum(time=day_value)\n"
        "@ms.metric()\ndef qty_count():\n    return orders_value.count(time=day_value)\n"
        "@ms.metric()\ndef amount_mean():\n"
        f"    return {mean_measure}_value.mean(time=day_value)\n"
    )


def author_decimal_mean_project(project: Path) -> Path:
    """Create the native Decimal mean model and an empty typed source table."""
    import duckdb

    _author(project, mean_measure="amount")
    with duckdb.connect(str(project / "warehouse.duckdb")) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, day DATE, amount DECIMAL(12,2), "
            "cost DECIMAL(12,2), quantity BIGINT)"
        )
    return project


def _values(result: mv.AnalysisFrame) -> dict[str, Json]:
    frame = result.to_pandas()
    assert isinstance(frame["net_amount"].iloc[0], Decimal)
    assert isinstance(frame["net_qty"].iloc[0], Decimal)
    assert isinstance(frame["amount_mean"].iloc[0], float)
    return {name: str(frame[name].iloc[0]) for name in METRIC_NAMES}


def _dtypes(result: mv.AnalysisFrame) -> dict[str, Json]:
    frame = result.to_pandas()
    return {name: str(frame[name].dtype) for name in METRIC_NAMES}


def run(mode: str, project: Path) -> dict[str, Json]:
    import duckdb

    os.chdir(project)
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    manifest = project / "rollup.json"
    if mode == "produce":
        _author(project)
        with duckdb.connect(str(project / "warehouse.duckdb")) as connection:
            connection.execute(
                "CREATE TABLE orders(id BIGINT, day DATE, amount DECIMAL(12,2), "
                "cost DECIMAL(12,2), quantity BIGINT)"
            )
            connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", DAY_ROWS)
        session = mv.session.get_or_create("equation", timezone="UTC")
        gmv = session.catalog.get("metric:sales.gmv").value
        qty_total = session.catalog.get("metric:sales.qty_total").value
        metrics_by_name = {
            "net_amount": gmv + session.catalog.get("metric:sales.cost_total").value - gmv,
            "net_qty": qty_total + session.catalog.get("metric:sales.qty_count").value - qty_total,
            "amount_mean": session.catalog.get("metric:sales.amount_mean").value,
        }
        scope = session.time_scope(start="2026-07-01", end="2026-08-01")
        original = session.observe(
            metrics_by_name,
            by=mv.product(scope, session.members(session.catalog.get("entity:sales.orders").value)),
        ).persist()
        monthly = session.observe(metrics_by_name, by=(scope,)).persist()
        artifacts: dict[str, Json] = {
            name: {"artifact": result.artifact.id, "snapshot": snapshot(result)}
            for name, result in (("original", original), ("monthly", monthly))
        }
        state: dict[str, Json] = {"session": session.id, "artifacts": artifacts}
        manifest.write_text(json.dumps(state, sort_keys=True))
        return {"pid": os.getpid(), "warm_monthly": _values(monthly), "dtypes": _dtypes(monthly)}
    assert mode in {"offline", "cold"}
    state = read(manifest)
    session_id = state["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "execute", forbidden),
    ):
        session = mv.session.resume(session_id)
        before = len(terminal_runs(session))
        artifacts = obj(state["artifacts"])
        for item in artifacts.values():
            saved = obj(item)
            artifact_id = saved["artifact"]
            assert isinstance(artifact_id, str)
            assert snapshot(session.persisted_frame(artifact_id)) == saved["snapshot"]
        monthly_id = obj(artifacts["monthly"])["artifact"]
        assert isinstance(monthly_id, str)
        monthly = session.persisted_frame(monthly_id)
        values, dtypes = _values(monthly), _dtypes(monthly)
        assert len(terminal_runs(session)) == before
    return {"pid": os.getpid(), "values": values, "dtypes": dtypes}


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], Path(sys.argv[2])), sort_keys=True, allow_nan=False))
