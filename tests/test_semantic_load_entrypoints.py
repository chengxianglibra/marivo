"""Issue #79: semantic load entry points are layered and documented.

There were three semantic load entry points that looked interchangeable but were
not:

* ``ms.load(workspace_dir=...)`` -> ``SemanticCatalog`` (the public, agent-facing
  entry);
* ``marivo.semantic.loader.load_project(root)`` -> ``LoadResult`` (the low-level
  two-pass pipeline, accepting only ``models/semantic/``);
* ``SemanticProject.load()`` -> ``LoadResult`` (the reader-level entry).

The public entry returns a browseable ``SemanticCatalog``; the other two return a
raw ``LoadResult`` with no documented way to reach a ``SemanticCatalog``.  These
tests lock the layering: ``ms.load`` stays the single public entry returning a
``SemanticCatalog``, ``loader.load_project`` stays the low-level pipeline returning
a ``LoadResult``, and ``SemanticProject.catalog()`` is the explicit, documented
bridge from a reader-level ``LoadResult`` to a ``SemanticCatalog``.
"""

from __future__ import annotations

import inspect

import pytest

import marivo.semantic as ms
from marivo.semantic import loader
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed
from marivo.semantic.loader import LoadResult, load_project


def _write_minimal_project(tmp_path) -> None:
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
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def revenue(table):\n"
        "    return table.amount.sum()\n"
    )


# ---------------------------------------------------------------------------
# ms.load is the public entry: a callable function, not a module.
# ---------------------------------------------------------------------------


def test_ms_load_is_a_function_not_a_module() -> None:
    """`from marivo.semantic import load` yields the function, not a module."""
    from marivo.semantic import load

    assert callable(load)
    assert not inspect.ismodule(load)
    assert load.__name__ == "load"


def test_ms_load_returns_semantic_catalog(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    assert isinstance(catalog, SemanticCatalog)


# ---------------------------------------------------------------------------
# loader.load_project is the low-level pipeline returning a LoadResult.
# ---------------------------------------------------------------------------


def test_load_project_returns_load_result(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    semantic_root = tmp_path / "models" / "semantic"
    result = load_project(semantic_root)
    assert isinstance(result, LoadResult)
    assert result.status == "ready"
    assert result.compiled_state is not None
    assert result.registry is not None


def test_load_project_is_not_star_exported() -> None:
    """The low-level pipeline is not part of loader's public star surface."""
    assert "load_project" not in loader.__all__


def test_load_project_rejects_workspace_root_with_ms_load_hint(tmp_path) -> None:
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


def test_project_catalog_returns_semantic_catalog(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    result = project.load()
    assert isinstance(result, LoadResult)
    catalog = project.catalog()
    assert isinstance(catalog, SemanticCatalog)


def test_project_catalog_raises_when_not_ready(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    with pytest.raises(SemanticLoadFailed):
        project.catalog()


def test_ms_load_and_project_catalog_agree(tmp_path) -> None:
    """Both the public entry and the reader bridge yield the same catalog."""
    _write_minimal_project(tmp_path)
    from marivo.semantic.reader import SemanticProject

    via_public = ms.load(workspace_dir=tmp_path)
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    via_bridge = project.catalog()
    assert via_public.definition_fingerprint == via_bridge.definition_fingerprint


def test_ms_load_failure_does_not_leave_partial_catalog(tmp_path) -> None:
    """A failed public load raises instead of returning a partial catalog."""
    semantic = tmp_path / "models" / "semantic" / "sales"
    semantic.mkdir(parents=True)
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='wrong_name', owner='Mina Zhang')\n"
    )
    with pytest.raises(SemanticLoadFailed):
        ms.load(workspace_dir=tmp_path)
