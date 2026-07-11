"""Runner-only project fixture for marivo-semantic examples."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import ibis


@dataclass(frozen=True)
class SemanticExampleProject:
    root: Path
    warehouse_ref: str
    orders_table: str
    env: dict[str, str]


def _write_project(root: Path) -> None:
    db_path = root / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER, customer_id INTEGER, order_date DATE, "
        "region VARCHAR, status VARCHAR, amount DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, 10, DATE '2026-06-01', 'US', 'paid', 100.0), "
        "(2, 20, DATE '2026-06-02', 'CA', 'paid', 50.0), "
        "(3, 10, DATE '2026-06-03', 'US', 'refunded', 25.0)"
    )
    con.disconnect()

    (root / "marivo.toml").write_text('[project]\nname = "semantic-examples"\n')
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
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(\n"
        "    name='orders',\n"
        "    datasource=warehouse,\n"
        "    source=md.table('orders'),\n"
        "    primary_key=['order_id'],\n"
        "    ai_context=ms.ai_context(\n"
        "        business_definition='One row per customer order.',\n"
        "    ),\n"
        ")\n"
    )


@contextmanager
def semantic_examples_project() -> Iterator[SemanticExampleProject]:
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="marivo-semantic-examples-") as tmp:
        root = Path(tmp)
        _write_project(root)
        os.chdir(root)
        try:
            yield SemanticExampleProject(
                root=root,
                warehouse_ref="datasource.warehouse",
                orders_table="orders",
                env={},
            )
        finally:
            os.chdir(previous)
