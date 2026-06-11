"""Source inspection to authoring assessment.

Shows: inspect table/column context, then run assess_authoring for a metric and
branch on status.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import ibis

import marivo.semantic as ms

with tempfile.TemporaryDirectory() as tmp:
    workspace = Path(tmp)
    root = workspace / ".marivo" / "semantic"
    datasource_dir = workspace / ".marivo" / "datasource"
    root.mkdir(parents=True)
    datasource_dir.mkdir(parents=True)

    db_path = workspace / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    try:
        con.con.execute("CREATE TABLE orders (order_id INT, dt DATE, amount DOUBLE, paid BOOLEAN)")
        con.con.execute(
            "INSERT INTO orders VALUES "
            "(1, DATE '2026-07-01', 10.0, true), "
            "(2, DATE '2026-07-01', 20.0, true), "
            "(3, DATE '2026-07-02', 5.0, false)"
        )
        con.con.execute("COMMENT ON TABLE orders IS 'Orders fact table'")
        con.con.execute("COMMENT ON COLUMN orders.amount IS 'Gross order amount'")
    finally:
        con.disconnect()

    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec("
        "name='warehouse', backend_type='duckdb', path="
        f"{str(db_path)!r})\n"
        "md.datasource(warehouse)\n"
    )

    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=workspace)
    table_context = project.inspect_table("warehouse", ms.table("orders"))
    column_contexts = project.inspect_columns(
        "warehouse",
        ms.table("orders"),
        columns=("amount", "paid"),
    )
    print("source columns:", list(table_context.columns))
    print("amount sample values:", list(column_contexts[0].sample_values))

    assessment = project.assess_authoring(
        object_kind="metric",
        subject_ref="sales.revenue",
        sources=(
            ms.AuthoringSourceInput(
                role="primary",
                datasource="warehouse",
                source=ms.TableSource(table="orders"),
                columns=("amount", "paid"),
            ),
        ),
        semantic_refs=("sales.orders",),
    )
    print("assessment status:", assessment.status)
    print("issue kinds:", [issue.kind for issue in assessment.issues])
