"""Tests for SemanticProject.verify_object."""

from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import DuckDBSpec
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError


def test_verify_object_static_domain_passes(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
        }
    )

    result = project.verify_object(ms.ref("domain.sales"))

    assert result.status == "passed"
    assert result.kind == "domain"
    assert result.scan is None


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


def test_verify_object_blocks_missing_datasource(tmp_path: Path, semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.missing'), source=ms.table('orders'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object(ms.ref("entity.sales.orders"))

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
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object(
        ms.ref("entity.sales.orders"), scope=md.ScanScope(partition=None, max_rows=5)
    )

    assert result.status == "passed"
    assert result.kind == "entity"
    assert result.scan is not None
    assert result.scan.partition_resolution == "unpruned"


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
                "source=ms.table('orders'))\n"
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
                "source=ms.table('orders'))\n"
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
                "source=ms.table('orders'))\n"
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
                "source=ms.table('orders'))\n"
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
                "source=ms.table('orders'))\n"
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
                "source=ms.table('orders'))\n"
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
                "source=ms.table('orders'), ai_context=ms.ai_context(business_definition='One row per order.', "
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
    assert report.status == "ready"
