"""Public catalog loading, low-level pipeline loading and reader bridge contracts."""

from __future__ import annotations

from pathlib import Path

import marivo.semantic as ms
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.loader import LoadResult, load_project


def _write_minimal_project(tmp_path: Path) -> None:
    semantic = tmp_path / "models" / "semantic" / "sales"
    ds = tmp_path / "models" / "datasources"
    semantic.mkdir(parents=True)
    ds.mkdir(parents=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (semantic / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "orders_ref = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        "amount_ref = ms.measure_column(name='amount', entity=orders_ref, column='amount', "
        "additivity=ms.additive_all())\n"
        "\n"
        ""
        "amount = amount_ref\n"
        "\n"
        "@ms.metric()\n"
        "def revenue():\n"
        "    return amount.sum()\n"
    )


def test_ms_load_returns_semantic_catalog(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    assert isinstance(catalog, SemanticCatalog)


# ---------------------------------------------------------------------------
# loader.load_project is the low-level pipeline returning a LoadResult.
# ---------------------------------------------------------------------------


def test_load_project_returns_load_result(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    semantic_root = tmp_path / "models" / "semantic"
    result = load_project(semantic_root)
    assert isinstance(result, LoadResult)
    assert result.status == "ready"
    assert result.compiled_state is not None
    assert result.registry is not None


def test_load_project_rejects_workspace_root_with_ms_load_hint(tmp_path: Path) -> None:
    """Passing a workspace root (not models/semantic/) errors with the correct
    usage in the hint, so the public entry is discoverable from the failure."""
    _write_minimal_project(tmp_path)
    result = load_project(tmp_path)
    assert result.status == "errored"
    assert result.errors
    hints = " ".join((getattr(e, "hint", "") or "") for e in result.errors)
    assert "ms.load(workspace_dir=" in hints


# ---------------------------------------------------------------------------
# SemanticProject.catalog() is the explicit bridge: LoadResult -> SemanticCatalog.
# ---------------------------------------------------------------------------


def test_project_catalog_returns_semantic_catalog(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    result = project.load()
    assert isinstance(result, LoadResult)
    catalog = project.catalog()
    assert isinstance(catalog, SemanticCatalog)


def test_ms_load_and_project_catalog_agree(tmp_path: Path) -> None:
    """Both the public entry and the reader bridge yield the same catalog."""
    _write_minimal_project(tmp_path)
    from marivo.semantic.reader import SemanticProject

    via_public = ms.load(workspace_dir=tmp_path)
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    via_bridge = project.catalog()
    assert via_public.definition_fingerprint == via_bridge.definition_fingerprint
