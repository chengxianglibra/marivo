"""Tests for SemanticProject.verify_object."""

from pathlib import Path

import marivo.datasource as md
from marivo.semantic import ledger as lg


def test_verify_object_static_domain_passes(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"}
    )

    result = project.verify_object("sales")

    assert result.status == "passed"
    assert result.kind == "domain"
    assert result.scan is None


def test_verify_object_blocks_missing_datasource(tmp_path: Path, semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='missing', source=ms.table('orders'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object("sales.orders")

    assert result.status == "failed"
    assert result.issues[0].kind == "datasource_unreachable"


def test_verify_object_scoped_entity_preview_passes(
    tmp_path: Path, semantic_project_factory
) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "dt": ["20260612"]})
    con.disconnect()
    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object("sales.orders", scope=md.ScanScope(partition=None, max_rows=5))

    assert result.status == "passed"
    assert result.kind == "entity"
    assert result.scan is not None
    assert result.scan.partition_resolution == "unpruned"


# -- Auto-recording tests -------------------------------------------------------


def _duckdb_project_with_time_dimension_and_metric(tmp_path: Path, semantic_project_factory):
    """Create a project with an entity, time dimension, and metric backed by DuckDB."""
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {
            "order_id": [1, 2],
            "amount": [100, 200],
            "dt": ["20260610", "20260611"],
        },
    )
    con.disconnect()
    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    return semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', "
                "source=ms.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d', data_type='string'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            )
        },
        workspace_dir=tmp_path,
    )


def test_verify_time_dimension_auto_records_identity(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_time_dimension_and_metric(tmp_path, semantic_project_factory)

    result = project.verify_object("sales.orders.dt")

    assert result.status == "passed"
    assert result.kind == "time_dimension"
    assert len(result.auto_recorded) == 1
    assert result.auto_recorded[0] == "sales.orders.dt:time_dimension_identity"

    # Verify the decision was persisted to the ledger.
    store = lg.LedgerStore(project.state_root)
    obj = store.read_object("sales.orders.dt")
    assert obj is not None
    assert any(d.decision_kind == "time_dimension_identity" for d in obj.decisions)
    td_decision = next(d for d in obj.decisions if d.decision_kind == "time_dimension_identity")
    assert td_decision.chosen == "string/day"
    assert td_decision.agreement_confidence == "high"
    assert td_decision.qualifying_sources == ("semantic_declaration",)
    assert td_decision.evidence_fingerprint.startswith("sha256:")


def test_verify_metric_auto_records_decomposition(tmp_path: Path, semantic_project_factory) -> None:
    project = _duckdb_project_with_time_dimension_and_metric(tmp_path, semantic_project_factory)

    result = project.verify_object("sales.revenue")

    assert result.status == "passed"
    assert result.kind == "metric"
    assert len(result.auto_recorded) == 1
    assert result.auto_recorded[0] == "sales.revenue:metric_composition"

    # Verify the decision was persisted to the ledger.
    store = lg.LedgerStore(project.state_root)
    obj = store.read_object("sales.revenue")
    assert obj is not None
    assert any(d.decision_kind == "metric_composition" for d in obj.decisions)
    m_decision = next(d for d in obj.decisions if d.decision_kind == "metric_composition")
    assert m_decision.chosen == "simple"
    assert m_decision.agreement_confidence == "high"
    assert m_decision.qualifying_sources == ("semantic_declaration",)
    assert m_decision.evidence_fingerprint.startswith("sha256:")


def test_verify_auto_record_idempotent(tmp_path: Path, semantic_project_factory) -> None:
    """Second verify_object call should not duplicate the auto-recorded decision."""
    project = _duckdb_project_with_time_dimension_and_metric(tmp_path, semantic_project_factory)

    result1 = project.verify_object("sales.revenue")
    assert result1.status == "passed"
    assert len(result1.auto_recorded) == 1

    store = lg.LedgerStore(project.state_root)
    count_after_first = len(store.read_object("sales.revenue").decisions)

    result2 = project.verify_object("sales.revenue")
    assert result2.status == "passed"
    assert len(result2.auto_recorded) == 1

    count_after_second = len(store.read_object("sales.revenue").decisions)
    assert count_after_second == count_after_first, (
        "Second verify should not duplicate the decision"
    )


def test_verify_auto_record_replaces_on_fingerprint_change(
    tmp_path: Path, semantic_project_factory
) -> None:
    """If the declaration changes (different fingerprint), verify replaces the old decision."""
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "amount": [100]})
    con.disconnect()
    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', "
                "source=ms.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d', data_type='string'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result1 = project.verify_object("sales.orders.dt")
    assert result1.auto_recorded == ("sales.orders.dt:time_dimension_identity",)

    store = lg.LedgerStore(project.state_root)
    first_fp = store.read_object("sales.orders.dt").decisions[0].evidence_fingerprint

    # Re-author with a different granularity — the fingerprint should change.
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', "
                "source=ms.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='month', parse=ms.strptime('%Y%m%d', data_type='string'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result2 = project.verify_object("sales.orders.dt")
    assert result2.auto_recorded == ("sales.orders.dt:time_dimension_identity",)

    obj = store.read_object("sales.orders.dt")
    assert len(obj.decisions) == 1, "Stale decision should be replaced, not accumulated"
    second_fp = obj.decisions[0].evidence_fingerprint
    assert second_fp != first_fp, "Fingerprint should differ after declaration change"
    assert obj.decisions[0].chosen == "string/month"


def test_verify_dimension_no_auto_record(tmp_path: Path, semantic_project_factory) -> None:
    """Plain dimensions should not auto-record any decisions."""
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "region": ["US"]})
    con.disconnect()
    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', "
                "source=ms.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object("sales.orders.region")
    assert result.status == "passed"
    assert result.kind == "dimension"
    assert result.auto_recorded == ()


def test_verify_derived_metric_auto_records_decomposition(
    tmp_path: Path, semantic_project_factory
) -> None:
    """Derived metrics should auto-record metric_composition with kind=derived_metric."""
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "amount": [100]})
    con.disconnect()
    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', "
                "source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
                "revenue_ratio = ms.ratio(\n"
                "    name='revenue_ratio',\n"
                "    numerator='sales.revenue',\n"
                "    denominator='sales.revenue',\n"
                ")\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object("sales.revenue_ratio")
    assert result.status == "passed"
    assert result.kind == "derived_metric"
    assert result.auto_recorded == ("sales.revenue_ratio:metric_composition",)

    store = lg.LedgerStore(project.state_root)
    obj = store.read_object("sales.revenue_ratio")
    assert obj is not None
    d = next(d for d in obj.decisions if d.decision_kind == "metric_composition")
    assert d.chosen == "ratio"
    assert d.cited_source is not None


def test_verify_clears_readiness_unresolved_clarification(
    tmp_path: Path, semantic_project_factory
) -> None:
    """After verify_object auto-records, readiness should not report unresolved_clarification."""
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "amount": [100], "dt": ["20260610"]})
    con.disconnect()
    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', "
                "source=ms.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d', data_type='string'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            )
        },
        workspace_dir=tmp_path,
    )

    # Before verify, readiness should flag unresolved_clarification.
    report_before = project.readiness()
    blocker_kinds = [b.kind for b in report_before.blockers]
    assert "unresolved_clarification" in blocker_kinds, (
        "Expected unresolved_clarification before verify"
    )

    # Auto-record decisions via verify_object.
    project.verify_object("sales.orders.dt")
    project.verify_object("sales.revenue")

    # After verify, readiness should not flag unresolved_clarification.
    report_after = project.readiness()
    blocker_kinds_after = [b.kind for b in report_after.blockers]
    assert "unresolved_clarification" not in blocker_kinds_after, (
        "unresolved_clarification should be resolved after verify_object auto-records"
    )
