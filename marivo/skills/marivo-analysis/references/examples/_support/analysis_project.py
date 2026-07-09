"""Runner-only project fixture for marivo-analysis examples."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import ibis


@dataclass(frozen=True)
class AnalysisExampleProject:
    root: Path
    env: dict[str, str]


def _write_project(root: Path) -> None:
    db_path = root / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER, created_at DATE, amount DOUBLE, "
        "region VARCHAR, user_id INTEGER, state VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2025-07-01', 10.0, 'north', 100, 'FAILED'), "
        "(2, DATE '2025-08-01', 20.0, 'south', 200, 'SUCCEEDED'), "
        "(3, DATE '2025-09-01', 30.0, 'north', 300, 'SUCCEEDED'), "
        "(4, DATE '2026-04-01', 11.0, 'north', 100, 'SUCCEEDED'), "
        "(5, DATE '2026-05-01', 13.0, 'south', 200, 'FAILED'), "
        "(6, DATE '2026-06-01', 14.0, 'north', 300, 'SUCCEEDED'), "
        "(7, DATE '2026-07-01', 12.0, 'north', 100, 'FAILED'), "
        "(8, DATE '2026-08-01', 84.0, 'south', 200, 'FAILED'), "
        "(9, DATE '2026-09-01', 60.0, 'north', 300, 'SUCCEEDED')"
    )
    con.disconnect()

    (root / "marivo.toml").write_text('[project]\nname = "analysis-examples"\n')
    datasource_dir = root / "models" / "datasources"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(db_path)!r})\n"
    )

    semantic_dir = root / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "orders.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "\n"
        'warehouse = md.ref("datasource.warehouse")\n'
        "\n"
        "orders = ms.entity(\n"
        '    name="orders",\n'
        "    datasource=warehouse,\n"
        '    source=ms.table("orders"),\n'
        '    primary_key=["order_id"],\n'
        ")\n"
        "\n"
        '@ms.time_dimension(entity=orders, granularity="day")\n'
        "def created_at(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region\n"
        "\n"
        '@ms.metric(entities=[orders], additivity="additive", name="revenue")\n'
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "\n"
        '@ms.metric(entities=[orders], additivity="additive", name="failed_orders")\n'
        "def failed_orders(orders):\n"
        "    return (orders.state == 'FAILED').cast('int64').sum()\n"
        "\n"
        '@ms.metric(entities=[orders], additivity="additive", name="total_orders")\n'
        "def total_orders(orders):\n"
        "    return orders.order_id.count()\n"
        "\n"
        'ms.ratio(name="failure_rate", numerator=failed_orders, denominator=total_orders)\n'
        "\n"
        "amount = ms.measure_column(\n"
        '    name="amount", entity=orders, column="amount", additivity="additive"\n'
        ")\n"
        'order_revenue = ms.aggregate(name="order_revenue", measure=amount, agg="sum")\n'
        "\n"
        'ms.cumulative(name="cumulative_revenue", base=order_revenue, over=created_at)\n'
        "\n"
        "ms.cumulative(\n"
        '    name="mtd_revenue",\n'
        "    base=order_revenue,\n"
        "    over=created_at,\n"
        '    anchor=ms.grain_to_date(grain="month"),\n'
        ")\n"
    )


@contextmanager
def analysis_examples_project() -> Iterator[AnalysisExampleProject]:
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="marivo-analysis-examples-") as tmp:
        root = Path(tmp)
        _write_project(root)
        os.chdir(root)
        try:
            yield AnalysisExampleProject(root=root, env={})
        finally:
            os.chdir(previous)
