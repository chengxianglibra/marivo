"""Tests for semantic verification ledger behavior."""

from pathlib import Path

import marivo.datasource as md
from marivo.datasource.authoring import DuckDBSpec
from marivo.semantic import ledger as lg


def _duckdb_project_with_entity(tmp_path: Path, semantic_project_factory):
    """Create a project with a single entity backed by DuckDB."""
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {
            "order_id": [1, 2],
            "amount": [100, 200],
            "region": ["US", "EU"],
            "dt": ["20260610", "20260611"],
        },
    )
    con.disconnect()
    md.register(
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    return semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=ms.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            )
        },
        workspace_dir=tmp_path,
    )


# -- Entity verification auto-record tests -----------------------------------


def test_verify_object_entity_auto_records_verified(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    result = project.verify_object("sales.orders")

    assert result.status == "passed"
    assert result.kind == "entity"
    assert len(result.auto_recorded) == 1
    assert result.auto_recorded[0] == "sales.orders:entity_verified"

    # Verify the decision was persisted to the ledger.
    store = lg.LedgerStore(project.state_root)
    obj = store.read_object("sales.orders")
    assert obj is not None
    decision = next(d for d in obj.decisions if d.decision_kind == "entity_verified")
    assert decision.chosen == "passed"
    assert decision.qualifying_sources == ("live_datasource_probe",)
    assert decision.evidence_fingerprint.startswith("sha256:")


def test_stale_verification_raises_after_source_change(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    project.verify_object("sales.orders")
    assert project._is_entity_verified("sales.orders")

    # Rewrite the entity with a different source table name
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders_v2", {"order_id": [1], "amount": [100], "region": ["US"]})
    con.disconnect()

    project2 = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=ms.table('orders_v2'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    # The ledger still has the old fingerprint for "sales.orders", but the
    # entity now points to a different table, so the verification is stale.
    assert not project2._is_entity_verified("sales.orders")


# -- verify_object with project load failure ----------------------------------


def test_verify_object_reports_project_load_failed(semantic_project_factory) -> None:
    """When a file fails to load, verify_object returns project_load_failed
    instead of the misleading 'was not found' with kind=entity."""
    # Create a project whose metrics file calls a non-existent ms.max()
    project = semantic_project_factory(
        {
            "cdn/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='cdn')\n"
            ),
            "cdn/broken.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.max()  # does not exist\n"
            ),
        },
        load=False,
    )

    result = project.verify_object("cdn.total_billing_bandwidth")

    assert result.status == "failed"
    # The kind defaults to "entity" when the registry is unavailable
    assert result.kind == "entity"
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.kind == "project_load_failed"
    assert "project failed to load" in issue.message
    # The message should surface the real error, not "was not found"
    assert "was not found" not in issue.message
    # The real error mentions the broken file
    assert "broken.py" in issue.message


def test_verify_object_reports_load_errors_for_metric_ref(semantic_project_factory) -> None:
    """verify_object on a metric ref still gets project_load_failed when the
    project cannot load — not the old static_check_failed / 'not found'."""
    project = semantic_project_factory(
        {
            "cdn/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='cdn')\n"
            ),
            "cdn/bad.py": "raise RuntimeError('intentional load error')\n",
        },
        load=False,
    )

    result = project.verify_object("cdn.some_metric")

    assert result.status == "failed"
    assert result.issues[0].kind == "project_load_failed"
    assert "intentional load error" in result.issues[0].message


def test_verify_object_measure_returns_passed(semantic_project_factory) -> None:
    model = (
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'))\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
        "def amount(orders):\n"
        "    return orders.amount\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    result = project.verify_object("sales.orders.amount")

    assert result.status == "passed"
    assert result.kind == "measure"


def test_verify_object_known_ref_still_not_found_when_loaded(
    semantic_project_factory,
) -> None:
    """When the project loads successfully but the ref doesn't exist,
    verify_object still uses static_check_failed (not project_load_failed)."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n"
            ),
        },
        load=True,
    )
    assert project.is_ready()

    result = project.verify_object("sales.nonexistent_metric")

    assert result.status == "failed"
    assert result.issues[0].kind == "static_check_failed"
    assert "was not found" in result.issues[0].message
