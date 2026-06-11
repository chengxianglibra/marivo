"""Shared fixtures for the Python-native Marivo test suite."""

from __future__ import annotations

import re

import pytest

from tests.shared_fixtures import sales_orders_template


@pytest.fixture(autouse=True)
def _reset_analysis_session_process_state():
    from marivo.analysis.session._runtime import reset_process_state

    reset_process_state()
    yield
    reset_process_state()


@pytest.fixture(scope="session")
def _sales_orders_template_path():
    """Session-scoped: ensure the DuckDB template is built once per worker."""
    return sales_orders_template()


@pytest.fixture
def semantic_project_factory(tmp_path):
    """Create a SemanticProject from a mapping of project-relative files."""

    def _make(files: dict[str, str], load: bool = True, models: list[str] | None = None):
        from marivo.semantic.reader import SemanticProject

        marivo_root = tmp_path / ".marivo"
        root = marivo_root / "semantic"
        root.mkdir(parents=True, exist_ok=True)
        for rel, src in files.items():
            full = marivo_root / rel if rel.startswith("datasource/") else root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(src)

        declared_datasources = {
            match.group("name")
            for src in files.values()
            for match in re.finditer(r"datasource=(?P<quote>['\"])(?P<name>[^'\"]+)(?P=quote)", src)
            if "." not in match.group("name")
        }
        for datasource_name in declared_datasources:
            datasource_file = marivo_root / "datasource" / f"{datasource_name}.py"
            if datasource_file.exists():
                continue
            datasource_file.parent.mkdir(parents=True, exist_ok=True)
            datasource_file.write_text(
                "import marivo.datasource as md\n"
                f"{datasource_name} = md.DatasourceSpec(name={datasource_name!r}, "
                "backend_type='duckdb', path=':memory:')\n"
                f"md.datasource({datasource_name})\n"
            )

        project = SemanticProject(workspace_dir=tmp_path)
        if load:
            project.load(models=models)
        return project

    return _make


def bootstrap_sales_project(tmp_path, *, with_time: bool = True) -> None:
    """Create a ready semantic project on disk for analysis tests."""
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    time_dimension = (
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = md.ref('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))\n"
        "\n"
        f"{time_dimension}"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), "
        "name='revenue', verification_mode='python_native')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
