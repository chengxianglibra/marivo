"""Tests for SemanticProject.verify_object."""

import inspect
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import DuckDBSpec
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.reader import SemanticProject


class _QuerySpy:
    def __init__(self) -> None:
        self.user_data_queries = 0


@pytest.fixture
def query_spy(monkeypatch: pytest.MonkeyPatch) -> _QuerySpy:
    from ibis.backends.duckdb import Backend

    spy = _QuerySpy()
    original_execute = Backend.execute

    def counted_execute(self: Backend, expr: object, *args: object, **kwargs: object) -> object:
        spy.user_data_queries += 1
        return original_execute(self, expr, *args, **kwargs)

    monkeypatch.setattr(Backend, "execute", counted_execute)
    return spy


def test_verify_object_static_domain_passes(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
        }
    )

    result = project.verify_object(ms.ref("domain.sales"))

    assert result.status == "passed"
    assert result.kind == "domain"
    assert result.validation_level == "static"
    assert result.runtime_checked is False
    assert not hasattr(result, "scope")
    assert not hasattr(result, "scan")


def test_verify_object_signatures_have_no_scope() -> None:
    assert "scope" not in inspect.signature(SemanticCatalog.verify_object).parameters
    assert "scope" not in inspect.signature(SemanticProject.verify_object).parameters


@pytest.mark.parametrize("ref", ["sales", "domain.sales"])
def test_verify_object_rejects_string_refs(semantic_project_factory, ref: str) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
        }
    )

    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.verify_object(ref)  # type: ignore[arg-type]

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "SemanticRef" in str(exc_info.value)


def test_verify_object_does_not_require_datasource_connectivity(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.missing'), source=md.table('orders'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object(ms.ref("entity.sales.orders"))

    assert result.status == "passed"
    assert result.validation_level == "static"
    assert result.runtime_checked is False


def test_entity_verification_is_static(
    tmp_path: Path, semantic_project_factory, query_spy: _QuerySpy
) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "dt": ["20260612"]})
    con.disconnect()
    md.register(
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object(ms.ref("entity.sales.orders"))

    assert query_spy.user_data_queries == 0
    assert result.status == "passed"
    assert result.kind == "entity"
    assert result.validation_level == "static"
    assert result.runtime_checked is False
    assert not hasattr(result, "scope")
    assert not hasattr(result, "scan")


def test_catalog_verification_is_static_for_every_object_kind(
    tmp_path: Path, semantic_project_factory, query_spy: _QuerySpy
) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount DOUBLE, "
        "created_at TIMESTAMP)"
    )
    con.raw_sql("CREATE TABLE customers (id INTEGER)")
    con.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(db_path)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/objects.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = md.ref('datasource.warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
                "customers = ms.entity(name='customers', datasource=warehouse, source=md.table('customers'))\n"
                "order_customer_id = ms.dimension_column(name='customer_id', entity=orders, column='customer_id')\n"
                "customer_id = ms.dimension_column(name='id', entity=customers, column='id')\n"
                "created_at = ms.time_dimension_column(name='created_at', entity=orders, column='created_at', granularity='day', parse=ms.timestamp(timezone='UTC'))\n"
                "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity='additive')\n"
                "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
                "revenue_ratio = ms.ratio(name='revenue_ratio', numerator=revenue, denominator=revenue)\n"
                "ms.relationship(name='orders_to_customers', from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer_id, customer_id)])\n"
            ),
        },
        workspace_dir=tmp_path,
    )
    catalog = SemanticCatalog(project)
    refs_and_kinds = (
        ("domain.sales", "domain"),
        ("entity.sales.orders", "entity"),
        ("dimension.sales.orders.customer_id", "dimension"),
        ("time_dimension.sales.orders.created_at", "time_dimension"),
        ("measure.sales.orders.amount", "measure"),
        ("metric.sales.revenue", "metric"),
        ("metric.sales.revenue_ratio", "derived_metric"),
        ("relationship.sales.orders_to_customers", "relationship"),
    )

    for ref, kind in refs_and_kinds:
        result = catalog.verify_object(catalog.get(ref))
        assert result.status == "passed"
        assert result.kind == kind

    assert query_spy.user_data_queries == 0


# -- Static verification without audit persistence ------------------------------


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
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    return semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            )
        },
        workspace_dir=tmp_path,
    )


def test_verify_time_dimension_passes_without_audit_side_effects(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_time_dimension_and_metric(tmp_path, semantic_project_factory)

    result = project.verify_object(ms.ref("time_dimension.sales.orders.dt"))

    assert result.status == "passed"
    assert result.kind == "time_dimension"
    assert not hasattr(result, "auto" + "_recorded")
    assert not (Path(project.state_root) / "evidence").exists()


def test_verify_metric_passes_without_audit_side_effects(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_time_dimension_and_metric(tmp_path, semantic_project_factory)

    result = project.verify_object(ms.ref("metric.sales.revenue"))

    assert result.status == "passed"
    assert result.kind == "metric"
    assert not hasattr(result, "auto" + "_recorded")
    assert not (Path(project.state_root) / "evidence").exists()


def test_verify_metric_handles_semi_additive_without_persistence(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
                "@ms.metric(entities=[orders], additivity=ms.semi_additive(over=dt, fold='mean'))\n"
                "def inventory(orders):\n"
                "    return orders.amount.mean()\n"
            )
        }
    )

    result = project.verify_object(ms.ref("metric.sales.inventory"))

    assert result.status == "passed"
    assert result.kind == "metric"
    assert not hasattr(result, "auto" + "_recorded")
    assert not (Path(project.state_root) / "evidence").exists()


def test_verify_object_is_repeatable_without_persistence(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_time_dimension_and_metric(tmp_path, semantic_project_factory)

    result1 = project.verify_object(ms.ref("metric.sales.revenue"))
    assert result1.status == "passed"
    assert not (Path(project.state_root) / "evidence").exists()

    result2 = project.verify_object(ms.ref("metric.sales.revenue"))
    assert result2.status == "passed"
    assert not (Path(project.state_root) / "evidence").exists()


def test_verify_time_dimension_reloads_changed_declaration_without_stale_state(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "amount": [100]})
    con.disconnect()
    md.register(
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result1 = project.verify_object(ms.ref("time_dimension.sales.orders.dt"))
    assert result1.status == "passed"

    # Re-author with a different granularity. Verification should read the
    # current source declaration directly, with no stale sidecar state.
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='month', parse=ms.strptime('%Y%m%d'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result2 = project.verify_object(ms.ref("time_dimension.sales.orders.dt"))
    assert result2.status == "passed"
    assert not (Path(project.state_root) / "evidence").exists()


def test_verify_dimension_passes_without_audit_side_effects(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "region": ["US"]})
    con.disconnect()
    md.register(
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object(ms.ref("dimension.sales.orders.region"))
    assert result.status == "passed"
    assert result.kind == "dimension"
    assert not hasattr(result, "auto" + "_recorded")
    assert not (Path(project.state_root) / "evidence").exists()


def test_verify_derived_metric_passes_without_audit_side_effects(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "amount": [100]})
    con.disconnect()
    md.register(
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
                "revenue_ratio = ms.ratio(\n"
                "    name='revenue_ratio',\n"
                "    numerator=revenue,\n"
                "    denominator=revenue,\n"
                ")\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object(ms.ref("metric.sales.revenue_ratio"))
    assert result.status == "passed"
    assert result.kind == "derived_metric"
    assert not hasattr(result, "auto" + "_recorded")
    assert not (Path(project.state_root) / "evidence").exists()


def test_readiness_does_not_require_audit_decisions(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "amount": [100], "dt": ["20260610"]})
    con.disconnect()
    md.register(
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'), ai_context=ms.ai_context(business_definition='One row per order.', "
                "guardrails=['Exclude test orders.']))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'), "
                "ai_context=ms.ai_context(business_definition='Order day.', guardrails=['Day partition key only.']))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
                "@ms.metric(entities=[orders], additivity='additive', "
                "ai_context=ms.ai_context(business_definition='Sum of amount.', guardrails=['Additive across order date only.']))\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            )
        },
        workspace_dir=tmp_path,
    )

    report = project.readiness()
    blocker_kinds = {b.kind for b in report.blockers}
    assert "missing_business_definition" not in blocker_kinds
    assert report.status == "blocked"
    assert blocker_kinds == {"snapshot_missing"}
