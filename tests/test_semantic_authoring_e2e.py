"""End-to-end test exercising the full authoring loop:

collect source evidence -> check authoring inputs -> author semantic objects
-> reload -> inspect authored object
"""

from __future__ import annotations

import ibis

from marivo.analysis.datasources.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.evidence import AiContextInput, DatasetSource, SamplePolicy
from marivo.semantic.reader import SemanticProject


def _fake_inspect_source(datasource, *, source, include_partitions=True):
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment="orders",
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, "pk", 1),
            ColumnMetadata("created_at", "DATE", False, "order date", 2),
            ColumnMetadata("amount", "DOUBLE", True, "gross amount", 3),
        ),
        partitions=(),
        warnings=(),
    )


def _backend_factory(_name):
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (order_id INT, created_at DATE, amount DOUBLE)")
    con.con.execute("INSERT INTO orders VALUES (1, DATE '2026-07-01', 10.0)")
    return con


def test_collect_check_author_reload_inspect(tmp_path):
    marivo_root = tmp_path / ".marivo"
    root = marivo_root / "semantic"
    (root / "sales").mkdir(parents=True)
    (marivo_root / "datasource").mkdir(parents=True)
    (marivo_root / "datasource" / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    project = SemanticProject(root=root)
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=_backend_factory
    )

    # 1. collect source evidence
    project.inspect_source_context(
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        sample_policy=SamplePolicy(mode="bounded_profile", limit=50),
    )

    # 2. check dataset inputs
    dataset_check = project.check_authoring_inputs(
        object_kind="dataset",
        subject_ref="sales.orders",
        datasource="warehouse",
        source=DatasetSource(kind="table", table="orders"),
        ai_context=AiContextInput(business_definition="Order fact rows."),
    )
    assert dataset_check.status == "supported"

    # 3. author + reload
    (root / "sales" / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    (root / "sales" / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "warehouse = md.ref('warehouse')\n"
        "orders = ms.dataset(name='orders', datasource=warehouse, source=ms.table('orders'))\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(),"
        " name='revenue', verification_mode='python_native')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
    project.reload()
    assert project.is_ready()

    # 4. cheap post-reload inspection
    inspected = project.inspect_authored_object("sales.revenue")
    assert not any(i.severity == "blocker" for i in inspected.issues)
