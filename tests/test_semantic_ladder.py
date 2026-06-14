"""Tests for ladder guard rails: prepare_* requires verify_object first."""

from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.semantic import ledger as lg
from marivo.semantic.errors import LadderOrderError


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
                "@ms.time_dimension(entity=orders, data_type='string', "
                "granularity='day', date_format='%Y%m%d')\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.metric(entities=[orders], additivity='additive', "
                "decomposition=ms.sum(), )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            )
        },
        workspace_dir=tmp_path,
    )


def _duckdb_project_with_two_entities(tmp_path: Path, semantic_project_factory):
    """Create a project with two entities backed by DuckDB."""
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {
            "order_id": [1, 2],
            "customer_id": [10, 20],
            "amount": [100, 200],
        },
    )
    con.create_table(
        "customers",
        {
            "customer_id": [10, 20],
            "country": ["US", "EU"],
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
                "customers = ms.entity(name='customers', datasource='warehouse', "
                "source=ms.table('customers'))\n"
                "@ms.dimension(entity=orders)\n"
                "def customer_id(orders):\n"
                "    return orders.customer_id\n"
                "@ms.dimension(entity=customers)\n"
                "def customer_id(customers):\n"
                "    return customers.customer_id\n"
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


# -- Ladder guard rail tests -------------------------------------------------


def test_prepare_dimensions_raises_without_verify(tmp_path: Path, semantic_project_factory) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    with pytest.raises(LadderOrderError, match="prepare_dimensions"):
        project.prepare_dimensions(entity="sales.orders", columns=["region"])


def test_prepare_time_dimension_raises_without_verify(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    with pytest.raises(LadderOrderError, match="prepare_time_dimension"):
        project.prepare_time_dimension(entity="sales.orders", column="dt")


def test_prepare_metric_raises_without_verify(tmp_path: Path, semantic_project_factory) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    with pytest.raises(LadderOrderError, match="prepare_metric"):
        project.prepare_metric(entity="sales.orders", measure_columns=["amount"])


def test_prepare_dimensions_works_after_verify(tmp_path: Path, semantic_project_factory) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    project.verify_object("sales.orders")

    result = project.prepare_dimensions(entity="sales.orders", columns=["region"])
    assert isinstance(result, tuple)


def test_prepare_metric_works_after_verify(tmp_path: Path, semantic_project_factory) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    project.verify_object("sales.orders")

    result = project.prepare_metric(entity="sales.orders", measure_columns=["amount"])
    assert result is not None


def test_prepare_relationship_checks_both_entities(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_two_entities(tmp_path, semantic_project_factory)

    # Verify only one entity
    project.verify_object("sales.orders")

    # Should raise for the unverified entity
    with pytest.raises(LadderOrderError, match=r"sales\.customers"):
        project.prepare_relationship(
            from_entity="sales.orders",
            to_entity="sales.customers",
            from_dimensions=["sales.orders.customer_id"],
            to_dimensions=["sales.customers.customer_id"],
        )


def test_prepare_relationship_works_after_both_verified(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_two_entities(tmp_path, semantic_project_factory)

    project.verify_object("sales.orders")
    project.verify_object("sales.customers")

    # After both entities are verified, the ladder guard passes.
    # The actual prepare_relationship may fail on dimension probing if
    # the dimension bodies can't be resolved in this test context, but
    # that's a separate concern from the ladder guard.
    try:
        result = project.prepare_relationship(
            from_entity="sales.orders",
            to_entity="sales.customers",
            from_dimensions=["sales.orders.customer_id"],
            to_dimensions=["sales.customers.customer_id"],
        )
        assert result is not None
    except LadderOrderError:
        pytest.fail("LadderOrderError should not be raised after both entities are verified")


def test_ladder_error_message_teaches(tmp_path: Path, semantic_project_factory) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    with pytest.raises(LadderOrderError) as exc_info:
        project.prepare_dimensions(entity="sales.orders", columns=["region"])

    err = exc_info.value
    assert err.kind == "ladder_order"
    assert "sales.orders" in err.message
    assert "verify_object" in err.message
    assert err.hint is not None
    assert "verify_object" in err.hint


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
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', "
                "source=ms.table('orders_v2'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    # The ledger still has the old fingerprint for "sales.orders"
    # but the entity now points to a different table, so it's stale
    assert not project2._is_entity_verified("sales.orders")

    with pytest.raises(LadderOrderError, match="prepare_dimensions"):
        project2.prepare_dimensions(entity="sales.orders", columns=["region"])


def test_unknown_entity_skips_guard(tmp_path: Path, semantic_project_factory) -> None:
    """If the entity ref is not in the registry, the guard skips and the
    downstream code handles the NOT_FOUND error."""
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    # "sales.nonexistent" is not in the registry at all — guard should not
    # fire; the downstream prepare code will raise its own error.
    from marivo.semantic.errors import SemanticRuntimeError

    with pytest.raises(SemanticRuntimeError):
        project.prepare_dimensions(entity="sales.nonexistent", columns=["x"])
