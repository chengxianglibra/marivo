"""Tests for project loading and exact catalog lookup behavior."""

from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import DuckDBSpec
from marivo.semantic.errors import (
    SemanticDecoratorError,
    SemanticLoadFailed,
    SemanticRuntimeError,
)


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
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('orders'))\n"
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


# -- Project-level loading and exact lookup -----------------------------------


def test_require_entity_is_static_without_audit_side_effects(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    result = ms.SemanticCatalog(project).require(ms.ref.entity("sales.orders"))

    assert result.kind.value == "entity"
    assert result.ref == ms.ref.entity("sales.orders")
    assert not hasattr(result, "contract")
    assert not (Path(project.state_root) / "evidence").exists()


def test_require_entity_uses_current_loaded_project(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    first = ms.SemanticCatalog(project).require(ms.ref.entity("sales.orders"))
    assert first.ref == ms.ref.entity("sales.orders")

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
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('orders_v2'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    second = ms.SemanticCatalog(project2).require(ms.ref.entity("sales.orders"))
    assert second.ref == first.ref
    assert not (Path(project2.state_root) / "evidence").exists()


# -- verify_object with project load failure ----------------------------------


def test_catalog_construction_reports_project_load_failed(semantic_project_factory) -> None:
    """A catalog is unavailable when its project failed to compile."""
    # Create a project whose metrics file calls a non-existent ms.max()
    project = semantic_project_factory(
        {
            "cdn/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='cdn', owner='Mina Zhang')\n"
            ),
            "cdn/broken.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.max()  # does not exist\n"
            ),
        },
        load=False,
    )

    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        ms.SemanticCatalog(project)
    assert "broken.py" in str(exc_info.value)


def test_catalog_construction_preserves_project_load_error(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "cdn/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='cdn', owner='Mina Zhang')\n"
            ),
            "cdn/bad.py": "raise RuntimeError('intentional load error')\n",
        },
        load=False,
    )

    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        ms.SemanticCatalog(project)
    assert "intentional load error" in str(exc_info.value)


def test_outside_loader_context_has_project_layout_repair() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.domain(name="sales", owner="Explicit Owner")

    error = exc_info.value
    assert error.kind == "outside_loader_context"
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "authoring"
    assert "models/semantic/<domain>/<module>.py" in (error.repair.snippet or "")
    assert error.repair.preserves_evidence is True


@pytest.mark.parametrize(
    ("files", "kind", "target", "snippet"),
    [
        (
            {"sales/models.py": "import marivo.semantic as ms\n"},
            "domain_file_missing",
            "domain",
            "models/semantic/<domain>/_domain.py",
        ),
        (
            {
                "sales/_domain.py": (
                    "import marivo.semantic as ms\n"
                    "ms.domain(name='wrong', owner='Explicit Owner')\n"
                )
            },
            "domain_file_mismatch",
            "domain",
            "ms.domain(name='<domain>'",
        ),
        (
            {
                "sales/_domain.py": (
                    "import marivo.semantic as ms\n"
                    "ms.domain(name='sales', owner='Explicit Owner')\n"
                ),
                "sales/broken.py": "raise RuntimeError('broken source')\n",
            },
            "organization_error",
            "authoring",
            None,
        ),
    ],
)
def test_layout_load_errors_have_registry_backed_structured_repairs(
    semantic_project_factory,
    files: dict[str, str],
    kind: str,
    target: str,
    snippet: str | None,
) -> None:
    project = semantic_project_factory(files, load=False)

    result = project.load()

    error = next(error for error in result.errors if error.kind == kind)
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == target
    assert error.repair.preserves_evidence is True
    if snippet is not None:
        assert snippet in (error.repair.snippet or "")


def test_require_measure_returns_current_entry(semantic_project_factory) -> None:
    model = (
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Mina Zhang')\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
        "def amount(orders):\n"
        "    return orders.amount\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    result = ms.SemanticCatalog(project).require(ms.ref.measure("sales.orders.amount"))

    assert result.kind.value == "measure"
    assert result.ref == ms.ref.measure("sales.orders.amount")


def test_require_unknown_ref_is_not_found_when_loaded(
    semantic_project_factory,
) -> None:
    """Exact lookup requires membership in the immutable loaded catalog."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
        },
        load=True,
    )
    assert project.is_ready()

    with pytest.raises(SemanticRuntimeError) as exc_info:
        ms.SemanticCatalog(project).require(ms.ref.metric("sales.nonexistent_metric"))
    assert exc_info.value.kind == "not_found"
